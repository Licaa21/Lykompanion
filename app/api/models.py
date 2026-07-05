import asyncio

from fastapi import APIRouter

from app.models.schemas import ProviderEndpointInfo
from app.services.llm.client import list_model_endpoints, list_models
from app.services.tts.chirp3 import list_voices as list_chirp3_voices
from app.services.tts.kokoro import list_voices as list_kokoro_voices

router = APIRouter(prefix="/api/models", tags=["models"])


@router.get("/llm")
async def get_llm_models(provider: str = "openrouter", force: bool = False) -> list[dict]:
    """Chat-capable models that also accept raw audio and image input, since the main chat model
    doubles as the voice model (voice messages go straight to it - this is a voice companion, not
    a text chatbot) and can also be handed its own screenshots via the take_screenshot tool.

    Only OpenRouter's catalog carries the modality metadata needed to filter on this - other
    providers (Google AI Studio, a custom endpoint) return a bare model id list, so every model
    is returned unfiltered and the user picks based on what they know the model supports.
    """
    models = await list_models(provider, force=force)
    if provider != "openrouter":
        return models
    return [
        m
        for m in models
        if "text" in m["output_modalities"] and "audio" in m["input_modalities"] and "image" in m["input_modalities"]
    ]


@router.get("/llm/text")
async def get_text_llm_models(provider: str = "openrouter", force: bool = False) -> list[dict]:
    """Chat-capable models with no input-modality requirement - for the memory extraction pass,
    which only ever receives plain text, never audio or images."""
    models = await list_models(provider, force=force)
    if provider != "openrouter":
        return models
    return [m for m in models if "text" in m["output_modalities"]]


@router.get("/llm/audio")
async def get_audio_llm_models(force: bool = False) -> list[dict]:
    """Dedicated speech-to-text models (Whisper, Chirp, Parakeet, etc.) - for transcription mode,
    where one of these transcribes voice messages before the text reaches the main chat model.

    These are a distinct OpenRouter model category (output_modalities == ["transcription"]), not
    regular chat-completion models that merely accept audio input alongside text (e.g. Gemini,
    GPT-4o) - those still show up in /llm and cost far more per call than a purpose-built ASR
    model. They're also called through OpenRouter's separate /audio/transcriptions endpoint, not
    chat completions, so this list (and transcription mode) is OpenRouter-only.
    """
    models = await list_models("openrouter", force=force)
    return [m for m in models if m["output_modalities"] == ["transcription"]]


@router.get("/llm/vision")
async def get_vision_llm_models(provider: str = "openrouter", force: bool = False) -> list[dict]:
    """Chat-capable models that accept image input - for the game-state extraction pass, which is
    sent the poll window's first/last frames as screenshots alongside the OCR text."""
    models = await list_models(provider, force=force)
    if provider != "openrouter":
        return models
    return [m for m in models if "text" in m["output_modalities"] and "image" in m["input_modalities"]]


@router.get("/providers/{model_id:path}")
async def get_model_providers(model_id: str, force: bool = False) -> list[ProviderEndpointInfo]:
    """Real providers OpenRouter currently routes this model through, with per-provider pricing/
    context/quantization/uptime - powers the "Providers" picker next to a model dropdown so the
    user can restrict routing to specific providers. OpenRouter-only."""
    endpoints = await list_model_endpoints(model_id, force=force)
    return [ProviderEndpointInfo(**e) for e in endpoints]


@router.get("/tts")
async def get_tts_models(force: bool = False) -> dict:
    """TTS options: local Kokoro voices and real OpenRouter Speech-category models.

    Must filter on "speech" output modality, not "audio" — models like Lyria
    have "audio" output but are music generators, not TTS, and 400 on the
    audio.speech endpoint ("Model does not exist").
    """
    # A transient OpenRouter failure must not 500 the whole endpoint - that would empty every
    # voice dropdown (Kokoro and Chirp included) until the user hits "Refresh Model Lists".
    try:
        openrouter_models = await list_models("openrouter", force=force)
    except Exception:
        openrouter_models = []
    speech_models = [m for m in openrouter_models if "speech" in m["output_modalities"]]
    kokoro_voices, chirp3_voices = await asyncio.gather(list_kokoro_voices(), list_chirp3_voices())
    return {"kokoro_voices": kokoro_voices, "openrouter_speech_models": speech_models, "chirp3_voices": chirp3_voices}
