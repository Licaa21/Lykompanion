from fastapi import APIRouter, HTTPException

from app.core import spotify_auth, youtube_auth
from app.core.config import persist_env_values, settings
from app.models.schemas import CompanionConfig

router = APIRouter(prefix="/api/config", tags=["config"])


@router.get("", response_model=CompanionConfig)
async def get_config() -> CompanionConfig:
    return CompanionConfig(
        user_display_name=settings.user_display_name,
        openrouter_model=settings.openrouter_model,
        openrouter_base_url=settings.openrouter_base_url,
        llm_provider=settings.llm_provider,
        memory_extraction_provider=settings.memory_extraction_provider,
        game_state_provider=settings.game_state_provider,
        google_ai_studio_api_key_set=bool(settings.google_ai_studio_api_key),
        custom_openai_base_url=settings.custom_openai_base_url,
        custom_openai_api_key_set=bool(settings.custom_openai_api_key),
        memory_extraction_model=settings.memory_extraction_model or None,
        overlay_enabled=settings.overlay_enabled,
        overlay_edit_hotkey=settings.overlay_edit_hotkey,
        overlay_edit_phrase_enabled=settings.overlay_edit_phrase_enabled,
        overlay_edit_phrase=settings.overlay_edit_phrase,
        game_state_ocr_enabled=settings.game_state_ocr_enabled,
        game_state_poll_interval_seconds=settings.game_state_poll_interval_seconds,
        game_state_capture_interval_seconds=settings.game_state_capture_interval_seconds,
        game_state_visual_diff_threshold_percent=settings.game_state_visual_diff_threshold_percent,
        game_state_visual_diff_noise_floor_percent=settings.game_state_visual_diff_noise_floor_percent,
        game_state_max_stale_seconds=settings.game_state_max_stale_seconds,
        game_state_model=settings.game_state_model or None,
        game_state_ocr_similarity_threshold=settings.game_state_ocr_similarity_threshold,
        game_state_ocr_max_width=settings.game_state_ocr_max_width,
        game_state_empty_ocr_warn_threshold=settings.game_state_empty_ocr_warn_threshold,
        game_state_visual_diff_thumbnail_size=settings.game_state_visual_diff_thumbnail_size,
        game_state_capture_frame_timeout_seconds=settings.game_state_capture_frame_timeout_seconds,
        game_state_capture_cursor_enabled=settings.game_state_capture_cursor_enabled,
        game_state_training_enabled=settings.game_state_training_enabled,
        proactive_messages_enabled=settings.proactive_messages_enabled,
        proactive_min_interval_minutes=settings.proactive_min_interval_minutes,
        memory_rag_limit=settings.memory_rag_limit,
        google_tts_api_key_set=bool(settings.google_tts_api_key),
        google_tts_voice=settings.google_tts_voice or None,
        transcription_enabled=settings.transcription_enabled,
        transcription_model=settings.transcription_model or None,
        web_search_provider=settings.web_search_provider,
        searxng_base_url=settings.searxng_base_url,
        tts_provider=settings.tts_provider,
        kokoro_base_url=settings.kokoro_base_url,
        kokoro_voice=settings.kokoro_voice,
        openrouter_tts_model=settings.openrouter_tts_model,
        openrouter_voice=settings.openrouter_voice,
        openrouter_api_key_set=bool(settings.openrouter_api_key),
        openrouter_management_key_set=bool(settings.openrouter_management_key),
        narration_speed=settings.tts_speed,
        narration_volume=settings.tts_volume,
        narrate_enabled=settings.narrate_enabled,
        sfx_enabled=settings.sfx_enabled,
        context_window_messages=settings.context_window_messages,
        wake_word_enabled=settings.wake_word_enabled,
        wake_word_phrase=settings.wake_word_phrase,
        sleep_word_enabled=settings.sleep_word_enabled,
        sleep_word_phrase=settings.sleep_word_phrase,
        wake_word_max_failures=settings.wake_word_max_failures,
        vad_threshold=settings.vad_threshold,
        vad_silence_ms=settings.vad_silence_ms,
        vad_min_speech_ms=settings.vad_min_speech_ms,
        pre_roll_ms=settings.pre_roll_ms,
        post_roll_ms=settings.post_roll_ms,
        screenshot_max_width=settings.screenshot_max_width,
        screenshot_jpeg_quality=settings.screenshot_jpeg_quality,
        igdb_client_id=settings.igdb_client_id,
        igdb_client_secret_set=bool(settings.igdb_client_secret),
        steam_api_key_set=bool(settings.steam_api_key),
        steam_id=settings.steam_id,
        steamgriddb_api_key_set=bool(settings.steamgriddb_api_key),
        spotify_client_id=settings.spotify_client_id,
        spotify_connected=spotify_auth.is_connected(),
        spotify_display_name=spotify_auth.get_display_name(),
        youtube_client_id=settings.youtube_client_id,
        youtube_client_secret_set=bool(settings.youtube_client_secret),
        youtube_connected=youtube_auth.is_connected(),
        youtube_channel_title=youtube_auth.get_channel_title(),
        debug_mode_enabled=settings.debug_mode_enabled,
    )


@router.put("", response_model=CompanionConfig)
async def update_config(config: CompanionConfig) -> CompanionConfig:
    env_updates: dict[str, str] = {}

    settings.user_display_name = config.user_display_name.strip() or "You"
    env_updates["USER_DISPLAY_NAME"] = settings.user_display_name

    settings.openrouter_model = config.openrouter_model
    env_updates["OPENROUTER_MODEL"] = config.openrouter_model

    if config.openrouter_base_url:
        settings.openrouter_base_url = config.openrouter_base_url
        env_updates["OPENROUTER_BASE_URL"] = config.openrouter_base_url

    settings.web_search_provider = config.web_search_provider
    env_updates["WEB_SEARCH_PROVIDER"] = config.web_search_provider
    if config.searxng_base_url:
        settings.searxng_base_url = config.searxng_base_url
        env_updates["SEARXNG_BASE_URL"] = config.searxng_base_url

    settings.tts_provider = config.tts_provider
    env_updates["TTS_PROVIDER"] = config.tts_provider

    if config.kokoro_base_url:
        settings.kokoro_base_url = config.kokoro_base_url
        env_updates["KOKORO_BASE_URL"] = config.kokoro_base_url
    if config.kokoro_voice is not None:
        settings.kokoro_voice = config.kokoro_voice
        env_updates["KOKORO_VOICE"] = config.kokoro_voice
    if config.openrouter_tts_model is not None:
        settings.openrouter_tts_model = config.openrouter_tts_model
        env_updates["OPENROUTER_TTS_MODEL"] = config.openrouter_tts_model
    if config.openrouter_voice is not None:
        settings.openrouter_voice = config.openrouter_voice
        env_updates["OPENROUTER_VOICE"] = config.openrouter_voice
    if config.openrouter_api_key is not None:
        settings.openrouter_api_key = config.openrouter_api_key
        env_updates["OPENROUTER_API_KEY"] = config.openrouter_api_key
    if config.openrouter_management_key is not None:
        settings.openrouter_management_key = config.openrouter_management_key
        env_updates["OPENROUTER_MANAGEMENT_KEY"] = config.openrouter_management_key

    settings.llm_provider = config.llm_provider or "openrouter"
    env_updates["LLM_PROVIDER"] = settings.llm_provider
    settings.memory_extraction_provider = config.memory_extraction_provider
    env_updates["MEMORY_EXTRACTION_PROVIDER"] = config.memory_extraction_provider
    settings.game_state_provider = config.game_state_provider
    env_updates["GAME_STATE_PROVIDER"] = config.game_state_provider

    if config.google_ai_studio_api_key is not None:
        settings.google_ai_studio_api_key = config.google_ai_studio_api_key
        env_updates["GOOGLE_AI_STUDIO_API_KEY"] = config.google_ai_studio_api_key
    if config.custom_openai_base_url:
        settings.custom_openai_base_url = config.custom_openai_base_url
        env_updates["CUSTOM_OPENAI_BASE_URL"] = config.custom_openai_base_url
    if config.custom_openai_api_key is not None:
        settings.custom_openai_api_key = config.custom_openai_api_key
        env_updates["CUSTOM_OPENAI_API_KEY"] = config.custom_openai_api_key

    if config.memory_extraction_model is not None:
        settings.memory_extraction_model = config.memory_extraction_model
        env_updates["MEMORY_EXTRACTION_MODEL"] = config.memory_extraction_model

    overlay_toggled = settings.overlay_enabled != config.overlay_enabled
    settings.overlay_enabled = config.overlay_enabled
    env_updates["OVERLAY_ENABLED"] = str(config.overlay_enabled)
    if overlay_toggled:
        # Apply live: stop the running overlay when disabled, or spawn + prime it
        # when enabled mid-session, instead of waiting for an app restart.
        from app.services.llm import game_state_extraction

        game_state_extraction.apply_overlay_enabled(config.overlay_enabled)

    hotkey_changed = settings.overlay_edit_hotkey != config.overlay_edit_hotkey
    settings.overlay_edit_hotkey = config.overlay_edit_hotkey
    env_updates["OVERLAY_EDIT_HOTKEY"] = config.overlay_edit_hotkey
    if hotkey_changed:
        from app.services import overlay_process

        parts = [p.strip().lower() for p in config.overlay_edit_hotkey.split("+") if p.strip()]
        mods, key = parts[:-1], (parts[-1] if parts else "o")
        overlay_process.set_hotkey(mods, key)  # live: re-registers in an already-running overlay
        overlay_process.write_hotkey_to_layout_file(mods, key)  # next launch also picks it up

    settings.overlay_edit_phrase_enabled = config.overlay_edit_phrase_enabled
    env_updates["OVERLAY_EDIT_PHRASE_ENABLED"] = str(config.overlay_edit_phrase_enabled)
    settings.overlay_edit_phrase = config.overlay_edit_phrase
    env_updates["OVERLAY_EDIT_PHRASE"] = config.overlay_edit_phrase

    settings.game_state_ocr_enabled = config.game_state_ocr_enabled
    env_updates["GAME_STATE_OCR_ENABLED"] = str(config.game_state_ocr_enabled)

    settings.game_state_poll_interval_seconds = config.game_state_poll_interval_seconds
    env_updates["GAME_STATE_POLL_INTERVAL_SECONDS"] = str(config.game_state_poll_interval_seconds)

    settings.game_state_capture_interval_seconds = config.game_state_capture_interval_seconds
    env_updates["GAME_STATE_CAPTURE_INTERVAL_SECONDS"] = str(config.game_state_capture_interval_seconds)

    settings.game_state_visual_diff_threshold_percent = config.game_state_visual_diff_threshold_percent
    env_updates["GAME_STATE_VISUAL_DIFF_THRESHOLD_PERCENT"] = str(config.game_state_visual_diff_threshold_percent)

    settings.game_state_visual_diff_noise_floor_percent = config.game_state_visual_diff_noise_floor_percent
    env_updates["GAME_STATE_VISUAL_DIFF_NOISE_FLOOR_PERCENT"] = str(config.game_state_visual_diff_noise_floor_percent)

    settings.game_state_max_stale_seconds = config.game_state_max_stale_seconds
    env_updates["GAME_STATE_MAX_STALE_SECONDS"] = str(config.game_state_max_stale_seconds)

    if config.game_state_model is not None:
        settings.game_state_model = config.game_state_model
        env_updates["GAME_STATE_MODEL"] = config.game_state_model

    settings.game_state_ocr_similarity_threshold = config.game_state_ocr_similarity_threshold
    env_updates["GAME_STATE_OCR_SIMILARITY_THRESHOLD"] = str(config.game_state_ocr_similarity_threshold)

    settings.game_state_ocr_max_width = config.game_state_ocr_max_width
    env_updates["GAME_STATE_OCR_MAX_WIDTH"] = str(config.game_state_ocr_max_width)

    settings.game_state_empty_ocr_warn_threshold = config.game_state_empty_ocr_warn_threshold
    env_updates["GAME_STATE_EMPTY_OCR_WARN_THRESHOLD"] = str(config.game_state_empty_ocr_warn_threshold)

    settings.game_state_visual_diff_thumbnail_size = config.game_state_visual_diff_thumbnail_size
    env_updates["GAME_STATE_VISUAL_DIFF_THUMBNAIL_SIZE"] = str(config.game_state_visual_diff_thumbnail_size)

    settings.game_state_capture_frame_timeout_seconds = config.game_state_capture_frame_timeout_seconds
    env_updates["GAME_STATE_CAPTURE_FRAME_TIMEOUT_SECONDS"] = str(config.game_state_capture_frame_timeout_seconds)

    settings.game_state_capture_cursor_enabled = config.game_state_capture_cursor_enabled
    env_updates["GAME_STATE_CAPTURE_CURSOR_ENABLED"] = str(config.game_state_capture_cursor_enabled)

    settings.game_state_training_enabled = config.game_state_training_enabled
    env_updates["GAME_STATE_TRAINING_ENABLED"] = str(config.game_state_training_enabled)

    settings.proactive_messages_enabled = config.proactive_messages_enabled
    env_updates["PROACTIVE_MESSAGES_ENABLED"] = str(config.proactive_messages_enabled)
    settings.proactive_min_interval_minutes = config.proactive_min_interval_minutes
    env_updates["PROACTIVE_MIN_INTERVAL_MINUTES"] = str(config.proactive_min_interval_minutes)

    settings.memory_rag_limit = config.memory_rag_limit
    env_updates["MEMORY_RAG_LIMIT"] = str(config.memory_rag_limit)

    if config.google_tts_api_key is not None:
        settings.google_tts_api_key = config.google_tts_api_key
        env_updates["GOOGLE_TTS_API_KEY"] = config.google_tts_api_key
    if config.google_tts_voice is not None:
        settings.google_tts_voice = config.google_tts_voice
        env_updates["GOOGLE_TTS_VOICE"] = config.google_tts_voice

    settings.transcription_enabled = config.transcription_enabled
    env_updates["TRANSCRIPTION_ENABLED"] = str(config.transcription_enabled)
    if config.transcription_model is not None:
        settings.transcription_model = config.transcription_model
        env_updates["TRANSCRIPTION_MODEL"] = config.transcription_model

    settings.tts_speed = config.narration_speed
    env_updates["TTS_SPEED"] = str(config.narration_speed)

    settings.tts_volume = config.narration_volume
    env_updates["TTS_VOLUME"] = str(config.narration_volume)

    settings.narrate_enabled = config.narrate_enabled
    env_updates["NARRATE_ENABLED"] = str(config.narrate_enabled)

    settings.sfx_enabled = config.sfx_enabled
    env_updates["SFX_ENABLED"] = str(config.sfx_enabled)

    settings.context_window_messages = config.context_window_messages
    env_updates["CONTEXT_WINDOW_MESSAGES"] = str(config.context_window_messages)

    settings.wake_word_enabled = config.wake_word_enabled
    env_updates["WAKE_WORD_ENABLED"] = str(config.wake_word_enabled)
    settings.wake_word_phrase = config.wake_word_phrase
    env_updates["WAKE_WORD_PHRASE"] = config.wake_word_phrase
    settings.sleep_word_enabled = config.sleep_word_enabled
    env_updates["SLEEP_WORD_ENABLED"] = str(config.sleep_word_enabled)
    settings.sleep_word_phrase = config.sleep_word_phrase
    env_updates["SLEEP_WORD_PHRASE"] = config.sleep_word_phrase
    settings.wake_word_max_failures = config.wake_word_max_failures
    env_updates["WAKE_WORD_MAX_FAILURES"] = str(config.wake_word_max_failures)

    settings.vad_threshold = config.vad_threshold
    env_updates["VAD_THRESHOLD"] = str(config.vad_threshold)
    settings.vad_silence_ms = config.vad_silence_ms
    env_updates["VAD_SILENCE_MS"] = str(config.vad_silence_ms)
    settings.vad_min_speech_ms = config.vad_min_speech_ms
    env_updates["VAD_MIN_SPEECH_MS"] = str(config.vad_min_speech_ms)

    settings.pre_roll_ms = config.pre_roll_ms
    env_updates["PRE_ROLL_MS"] = str(config.pre_roll_ms)
    settings.post_roll_ms = config.post_roll_ms
    env_updates["POST_ROLL_MS"] = str(config.post_roll_ms)

    settings.screenshot_max_width = config.screenshot_max_width
    env_updates["SCREENSHOT_MAX_WIDTH"] = str(config.screenshot_max_width)

    settings.screenshot_jpeg_quality = config.screenshot_jpeg_quality
    env_updates["SCREENSHOT_JPEG_QUALITY"] = str(config.screenshot_jpeg_quality)

    if config.igdb_client_id is not None:
        settings.igdb_client_id = config.igdb_client_id
        env_updates["IGDB_CLIENT_ID"] = config.igdb_client_id
    if config.igdb_client_secret is not None:
        settings.igdb_client_secret = config.igdb_client_secret
        env_updates["IGDB_CLIENT_SECRET"] = config.igdb_client_secret
    if config.steam_api_key is not None:
        settings.steam_api_key = config.steam_api_key
        env_updates["STEAM_API_KEY"] = config.steam_api_key
    if config.steam_id is not None:
        settings.steam_id = config.steam_id
        env_updates["STEAM_ID"] = config.steam_id
    if config.steamgriddb_api_key is not None:
        settings.steamgriddb_api_key = config.steamgriddb_api_key
        env_updates["STEAMGRIDDB_API_KEY"] = config.steamgriddb_api_key

    if config.spotify_client_id is not None:
        settings.spotify_client_id = config.spotify_client_id
        env_updates["SPOTIFY_CLIENT_ID"] = config.spotify_client_id

    if config.youtube_client_id is not None:
        settings.youtube_client_id = config.youtube_client_id
        env_updates["YOUTUBE_CLIENT_ID"] = config.youtube_client_id
    if config.youtube_client_secret is not None:
        settings.youtube_client_secret = config.youtube_client_secret
        env_updates["YOUTUBE_CLIENT_SECRET"] = config.youtube_client_secret

    settings.debug_mode_enabled = config.debug_mode_enabled
    env_updates["DEBUG_MODE_ENABLED"] = str(config.debug_mode_enabled)

    persist_env_values(env_updates)

    return await get_config()


# Secret settings that can be cleared individually (the ✕ next to each key field), mapping the
# settings-field name the frontend sends to its .env variable. Allowlisted so this endpoint can
# only ever blank a known key, never arbitrary settings.
_CLEARABLE_KEYS = {
    "openrouter_api_key": "OPENROUTER_API_KEY",
    "openrouter_management_key": "OPENROUTER_MANAGEMENT_KEY",
    "google_ai_studio_api_key": "GOOGLE_AI_STUDIO_API_KEY",
    "google_tts_api_key": "GOOGLE_TTS_API_KEY",
    "custom_openai_api_key": "CUSTOM_OPENAI_API_KEY",
    "igdb_client_secret": "IGDB_CLIENT_SECRET",
    "youtube_client_secret": "YOUTUBE_CLIENT_SECRET",
    "steam_api_key": "STEAM_API_KEY",
    "steamgriddb_api_key": "STEAMGRIDDB_API_KEY",
}


@router.delete("/key/{field}", response_model=CompanionConfig)
async def clear_key(field: str) -> CompanionConfig:
    """Immediately clear one stored secret (its ✕ button), no full Save needed. Blanks the live
    setting and persists the empty value to .env."""
    env_var = _CLEARABLE_KEYS.get(field)
    if env_var is None:
        raise HTTPException(status_code=400, detail="Unknown or non-clearable key field.")
    setattr(settings, field, "")
    persist_env_values({env_var: ""})
    return await get_config()
