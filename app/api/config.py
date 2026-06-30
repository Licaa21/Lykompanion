from fastapi import APIRouter

from app.core.config import persist_env_value, settings
from app.models.schemas import CompanionConfig
from app.services.llm import client as llm_client

router = APIRouter(prefix="/api/config", tags=["config"])


@router.get("", response_model=CompanionConfig)
async def get_config() -> CompanionConfig:
    return CompanionConfig(
        openrouter_model=settings.openrouter_model,
        openrouter_voice_model=settings.openrouter_voice_model,
        memory_extraction_model=settings.memory_extraction_model or None,
        game_state_ocr_enabled=settings.game_state_ocr_enabled,
        game_state_poll_interval_seconds=settings.game_state_poll_interval_seconds,
        game_state_model=settings.game_state_model or None,
        google_tts_api_key_set=bool(settings.google_tts_api_key),
        google_tts_voice=settings.google_tts_voice or None,
        tts_provider=settings.tts_provider,
        kokoro_voice=settings.kokoro_voice,
        openrouter_tts_model=settings.openrouter_tts_model,
        openrouter_voice=settings.openrouter_voice,
        openrouter_api_key_set=bool(settings.openrouter_api_key),
        narration_speed=settings.tts_speed,
        narration_volume=settings.tts_volume,
        context_window_messages=settings.context_window_messages,
        igdb_client_id=settings.igdb_client_id,
        igdb_client_secret_set=bool(settings.igdb_client_secret),
        steam_api_key_set=bool(settings.steam_api_key),
        steam_id=settings.steam_id,
    )


@router.put("", response_model=CompanionConfig)
async def update_config(config: CompanionConfig) -> CompanionConfig:
    settings.openrouter_model = config.openrouter_model
    persist_env_value("OPENROUTER_MODEL", config.openrouter_model)

    settings.tts_provider = config.tts_provider
    persist_env_value("TTS_PROVIDER", config.tts_provider)

    if config.kokoro_voice is not None:
        settings.kokoro_voice = config.kokoro_voice
        persist_env_value("KOKORO_VOICE", config.kokoro_voice)
    if config.openrouter_tts_model is not None:
        settings.openrouter_tts_model = config.openrouter_tts_model
        persist_env_value("OPENROUTER_TTS_MODEL", config.openrouter_tts_model)
    if config.openrouter_voice is not None:
        settings.openrouter_voice = config.openrouter_voice
        persist_env_value("OPENROUTER_VOICE", config.openrouter_voice)
    if config.openrouter_api_key:
        settings.openrouter_api_key = config.openrouter_api_key
        llm_client.client.api_key = config.openrouter_api_key
        persist_env_value("OPENROUTER_API_KEY", config.openrouter_api_key)
    if config.openrouter_voice_model is not None:
        settings.openrouter_voice_model = config.openrouter_voice_model
        persist_env_value("OPENROUTER_VOICE_MODEL", config.openrouter_voice_model)
    if config.memory_extraction_model is not None:
        settings.memory_extraction_model = config.memory_extraction_model
        persist_env_value("MEMORY_EXTRACTION_MODEL", config.memory_extraction_model)

    settings.game_state_ocr_enabled = config.game_state_ocr_enabled
    persist_env_value("GAME_STATE_OCR_ENABLED", str(config.game_state_ocr_enabled))

    settings.game_state_poll_interval_seconds = config.game_state_poll_interval_seconds
    persist_env_value("GAME_STATE_POLL_INTERVAL_SECONDS", str(config.game_state_poll_interval_seconds))

    if config.game_state_model is not None:
        settings.game_state_model = config.game_state_model
        persist_env_value("GAME_STATE_MODEL", config.game_state_model)
    if config.google_tts_api_key:
        settings.google_tts_api_key = config.google_tts_api_key
        persist_env_value("GOOGLE_TTS_API_KEY", config.google_tts_api_key)
    if config.google_tts_voice is not None:
        settings.google_tts_voice = config.google_tts_voice
        persist_env_value("GOOGLE_TTS_VOICE", config.google_tts_voice)

    settings.tts_speed = config.narration_speed
    persist_env_value("TTS_SPEED", str(config.narration_speed))

    settings.tts_volume = config.narration_volume
    persist_env_value("TTS_VOLUME", str(config.narration_volume))

    settings.context_window_messages = config.context_window_messages
    persist_env_value("CONTEXT_WINDOW_MESSAGES", str(config.context_window_messages))

    if config.igdb_client_id is not None:
        settings.igdb_client_id = config.igdb_client_id
        persist_env_value("IGDB_CLIENT_ID", config.igdb_client_id)
    if config.igdb_client_secret:
        settings.igdb_client_secret = config.igdb_client_secret
        persist_env_value("IGDB_CLIENT_SECRET", config.igdb_client_secret)
    if config.steam_api_key:
        settings.steam_api_key = config.steam_api_key
        persist_env_value("STEAM_API_KEY", config.steam_api_key)
    if config.steam_id is not None:
        settings.steam_id = config.steam_id
        persist_env_value("STEAM_ID", config.steam_id)

    return await get_config()
