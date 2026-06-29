from fastapi import APIRouter

from app.services.llm.client import list_models as list_openrouter_models
from app.services.tts.kokoro import list_voices as list_kokoro_voices

router = APIRouter(prefix="/api/models", tags=["models"])


@router.get("/llm")
async def get_llm_models() -> list[dict]:
    """All chat-capable models available on OpenRouter."""
    models = await list_openrouter_models()
    return [m for m in models if "text" in m["output_modalities"]]


@router.get("/tts")
async def get_tts_models() -> dict:
    """TTS options: local Kokoro voices and real OpenRouter Speech-category models.

    Must filter on "speech" output modality, not "audio" — models like Lyria
    have "audio" output but are music generators, not TTS, and 400 on the
    audio.speech endpoint ("Model does not exist").
    """
    openrouter_models = await list_openrouter_models()
    speech_models = [m for m in openrouter_models if "speech" in m["output_modalities"]]
    kokoro_voices = await list_kokoro_voices()
    return {"kokoro_voices": kokoro_voices, "openrouter_speech_models": speech_models}


@router.get("/voice-input")
async def get_voice_input_models() -> list[dict]:
    """OpenRouter models that accept raw audio as input, for direct voice-to-LLM chat."""
    models = await list_openrouter_models()
    return [m for m in models if "audio" in m["input_modalities"]]
