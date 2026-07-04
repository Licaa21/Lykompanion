from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_PATH = Path(__file__).resolve().parent.parent.parent / ".env"


def persist_env_value(key: str, value: str) -> None:
    """Write a single KEY=value pair into the .env file. For multiple keys, prefer
    persist_env_values to avoid a read/write round-trip per key."""
    persist_env_values({key: value})


def persist_env_values(values: dict[str, str]) -> None:
    """Write multiple KEY=value pairs into the .env file in a single read/write pass."""
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
    # A newline inside a value (e.g. pasted into a key field) would corrupt the whole file.
    remaining = {k: str(v).replace("\r", " ").replace("\n", " ") for k, v in values.items()}
    for i, line in enumerate(lines):
        for key, value in list(remaining.items()):
            if line.startswith(f"{key}="):
                lines[i] = f"{key}={value}"
                del remaining[key]
                break
    for key, value in remaining.items():
        lines.append(f"{key}={value}")
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Shown next to the user's own messages in chat, instead of the default "You".
    user_display_name: str = "You"

    openrouter_api_key: str = ""
    openrouter_management_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_model: str = "anthropic/claude-3.5-sonnet"

    # Google AI Studio (Gemini) reuses the OpenAI SDK client via Google's OpenAI-compatibility
    # endpoint - only an API key is needed, the base_url is a fixed constant (see llm/client.py).
    google_ai_studio_api_key: str = ""

    # Any other OpenAI-compatible API (self-hosted, a different aggregator, etc).
    custom_openai_base_url: str = ""
    custom_openai_api_key: str = ""

    # Which provider backs each LLM feature: "openrouter" | "google_ai_studio" | "custom".
    # The per-feature *_provider settings below default to "" (inherit llm_provider), matching
    # how the corresponding *_model settings already default to "" (inherit openrouter_model).
    llm_provider: str = "openrouter"
    memory_extraction_provider: str = ""
    game_state_provider: str = ""

    tts_provider: str = "kokoro"
    kokoro_base_url: str = "http://localhost:8880/v1"
    kokoro_voice: str = "af_heart"
    openrouter_tts_model: str = ""
    openrouter_voice: str = ""
    tts_speed: float = 1.0
    tts_volume: float = 1.0
    # Whether replies are narrated aloud at all - the checkbox used to be client-side only and
    # reset to checked on every restart.
    narrate_enabled: bool = True
    # Short synthesized sound effects (message sent, tool calls, memory saved/removed) - purely
    # cosmetic, client-side only, but persisted like every other toggle.
    sfx_enabled: bool = True

    # "openrouter" (web-grounded chat completion, text-only) or "searxng" (self-hosted
    # metasearch with a real image-search endpoint).
    web_search_provider: str = "openrouter"
    searxng_base_url: str = "http://localhost:8080"

    host: str = "127.0.0.1"
    port: int = 8000

    # Max number of most-recent chat messages sent to the LLM as context. 0 = no limit.
    context_window_messages: int = 20

    wake_word_enabled: bool = False
    wake_word_phrase: str = "Hey Buddy"
    wake_word_max_failures: int = 3

    # Spoken command that turns hands-free OFF (mirror of the wake word). Detected client-side while
    # hands-free is on; also enforced as an LLM backstop (stop_listening on stop-intent).
    sleep_word_enabled: bool = False
    sleep_word_phrase: str = "Go to sleep"

    vad_threshold: int = 8
    vad_silence_ms: int = 3000
    vad_min_speech_ms: int = 300
    # Padding kept around the detected speech window before finalizing the utterance, so the
    # first/last word or breath doesn't get clipped.
    pre_roll_ms: int = 1000
    post_roll_ms: int = 500

    # Screenshot downscaling before sending to the LLM - lower values cut image token cost.
    screenshot_max_width: int = 960
    screenshot_jpeg_quality: int = 70

    # Dedicated model for background memory extraction. Falls back to openrouter_model if empty.
    memory_extraction_model: str = ""

    # RAG-lite memory injection for the chat prompt: once the active game/playthrough has more
    # than this many game/session-scoped memories, only the most relevant/recent ones (up to
    # this cap) are injected instead of all of them. User-scoped memories are always injected
    # in full. 0 = disabled (inject everything, the old behavior).
    memory_rag_limit: int = 30

    # Native in-game overlay (overlay/overlay.exe) - spawned while a game is tracked, fed toasts
    # and game-state over its named-pipe API. Opt-in, Windows only, needs the built exe.
    overlay_enabled: bool = False
    # User-configurable global hotkey that toggles the overlay's edit mode (default matches the
    # overlay's historical hardcoded combo). Pushed to the overlay via a live pipe command and
    # persisted into its layout JSON so a fresh launch also picks it up.
    overlay_edit_hotkey: str = "Ctrl+Shift+O"
    # Spoken phrase that enters overlay edit mode directly (bypasses the LLM entirely) - detected
    # client-side by the same always-listening mechanism as wake_word/sleep_word, independent of
    # hands-free mic state. No-ops if the overlay isn't running.
    overlay_edit_phrase_enabled: bool = False
    overlay_edit_phrase: str = "edit overlay"

    # Passive game-state OCR awareness (quest/location/character) - opt-in, Windows only.
    game_state_ocr_enabled: bool = False
    game_state_poll_interval_seconds: int = 90
    # How often (seconds) the poller captures+OCRs a frame locally while building up the batch
    # sent to the LLM once per poll interval. Cheap - no LLM call happens per capture.
    game_state_capture_interval_seconds: int = 1
    # If every frame in a poll window dedupes away as OCR-text-identical (see
    # _SIMILARITY_THRESHOLD in game_state_extraction.py), the LLM pass is normally skipped
    # entirely - correct for a frozen/paused screen, but it would also miss a minimal-UI game
    # where the HUD text never changes even though the player is genuinely moving through the
    # world. As a fallback in that case, the window's first and last raw screenshots are compared
    # via a coarse pixel diff (0-100%); if they differ by at least this much, the window is sent
    # through anyway on pixels alone. A real frozen screen has ~0% diff and stays skipped. Set to
    # 100 to disable (never force a window through on visual diff alone).
    game_state_visual_diff_threshold_percent: float = 12.0
    # Dedicated model for background game-state extraction. Falls back to openrouter_model if empty.
    game_state_model: str = ""
    # Self-training: lets the extraction pass maintain a per-process notes document (how to decode
    # this game's HUD/UI from OCR text), fed back into every future extraction pass for that
    # process. No separate trainer model - the extraction model sees the screenshots itself.
    game_state_training_enabled: bool = False
    # Proactive companion: lets the game-state extraction pass speak up unprompted (a tip, a
    # comment on something it saw on screen) as a normal chat message. Off by default; the
    # interval is a hard floor between two proactive messages regardless of what the model wants.
    proactive_messages_enabled: bool = False
    proactive_min_interval_minutes: int = 15

    google_tts_api_key: str = ""
    google_tts_voice: str = "en-US-Chirp3-HD-Aoede"

    # Transcribes voice messages with a dedicated ASR model (via OpenRouter's audio/transcriptions
    # endpoint - a separate API from chat completions, only OpenRouter exposes it) before sending
    # the resulting text to the main chat model, instead of sending raw audio straight to it. Lets
    # the main model be text/image-only. No fallback to openrouter_model - must be picked
    # explicitly, since the whole point is decoupling from a main model that may not support audio.
    transcription_enabled: bool = False
    transcription_model: str = ""

    igdb_client_id: str = ""
    igdb_client_secret: str = ""
    steam_api_key: str = ""
    steam_id: str = ""

    # Registers the OAuth app with Spotify (see app/api/spotify_oauth.py) - PKCE means no Client
    # Secret is needed anywhere in that flow, just this (non-secret) Client ID.
    spotify_client_id: str = ""

    # Gates app/core/debug_log.py recording - off by default so full, untruncated prompts/replies
    # (which can be large) aren't kept in memory unless the user is actively debugging.
    debug_mode_enabled: bool = False


settings = Settings()
