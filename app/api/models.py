import asyncio

from fastapi import APIRouter

from app.services.llm.client import list_models as list_openrouter_models
from app.services.tts.chirp3 import list_voices as list_chirp3_voices
from app.services.tts.kokoro import list_voices as list_kokoro_voices

router = APIRouter(prefix="/api/models", tags=["models"])


@router.get("/llm")
async def get_llm_models() -> list[dict]:
    """Chat-capable models available on OpenRouter that also accept raw audio and image input,
    since the main chat model doubles as the voice model (voice messages go straight to it - this
    is a voice companion, not a text chatbot) and can also be handed its own screenshots via the
    take_screenshot tool."""
    models = await list_openrouter_models()
    return [
        m
        for m in models
        if "text" in m["output_modalities"] and "audio" in m["input_modalities"] and "image" in m["input_modalities"]
    ]


@router.get("/llm/text")
async def get_text_llm_models() -> list[dict]:
    """Chat-capable models with no input-modality requirement - for background passes (memory
    extraction, game-state extraction) that only ever receive plain text, never audio or images."""
    models = await list_openrouter_models()
    return [m for m in models if "text" in m["output_modalities"]]


@router.get("/llm/vision")
async def get_vision_llm_models() -> list[dict]:
    """Chat-capable models that accept image input - for the game-state training pass, which is
    sent a screenshot alongside the OCR text."""
    models = await list_openrouter_models()
    return [m for m in models if "text" in m["output_modalities"] and "image" in m["input_modalities"]]


@router.get("/tts")
async def get_tts_models() -> dict:
    """TTS options: local Kokoro voices and real OpenRouter Speech-category models.

    Must filter on "speech" output modality, not "audio" — models like Lyria
    have "audio" output but are music generators, not TTS, and 400 on the
    audio.speech endpoint ("Model does not exist").
    """
    openrouter_models = await list_openrouter_models()
    speech_models = [m for m in openrouter_models if "speech" in m["output_modalities"]]
    kokoro_voices, chirp3_voices = await asyncio.gather(list_kokoro_voices(), list_chirp3_voices())
    return {"kokoro_voices": kokoro_voices, "openrouter_speech_models": speech_models, "chirp3_voices": chirp3_voices}
