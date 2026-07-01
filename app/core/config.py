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
    remaining = dict(values)
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
    game_state_training_provider: str = ""

    tts_provider: str = "kokoro"
    kokoro_base_url: str = "http://localhost:8880/v1"
    kokoro_voice: str = "af_heart"
    openrouter_tts_model: str = ""
    openrouter_voice: str = ""
    tts_speed: float = 1.0
    tts_volume: float = 1.0

    # "openrouter" (web-grounded chat completion, text-only) or "searxng" (self-hosted
    # metasearch with a real image-search endpoint).
    web_search_provider: str = "openrouter"
    searxng_base_url: str = "http://localhost:8080"

    host: str = "0.0.0.0"
    port: int = 8000

    # Max number of most-recent chat messages sent to the LLM as context. 0 = no limit.
    context_window_messages: int = 20

    wake_word_enabled: bool = False
    wake_word_phrase: str = "Hey Buddy"
    wake_word_max_failures: int = 3

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

    # Passive game-state OCR awareness (quest/location/character) - opt-in, Windows only.
    game_state_ocr_enabled: bool = False
    game_state_poll_interval_seconds: int = 90
    # How often (seconds) the poller captures+OCRs a frame locally while building up the batch
    # sent to the LLM once per poll interval. Cheap - no LLM call happens per capture.
    game_state_capture_interval_seconds: int = 1
    # Dedicated model for background game-state extraction. Falls back to openrouter_model if empty.
    game_state_model: str = ""
    # Game-state "trainer" pass - re-interprets low-confidence OCR frames (screenshot + OCR text)
    # with a vision-capable model to build per-process training data (a document of interpretation
    # notes), fed into future extraction passes for that process. Falls back to openrouter_model
    # if empty.
    game_state_training_enabled: bool = False
    game_state_training_model: str = ""

    google_tts_api_key: str = ""
    google_tts_voice: str = "en-US-Chirp3-HD-Aoede"

    igdb_client_id: str = ""
    igdb_client_secret: str = ""
    steam_api_key: str = ""
    steam_id: str = ""

    # Gates app/core/debug_log.py recording - off by default so full, untruncated prompts/replies
    # (which can be large) aren't kept in memory unless the user is actively debugging.
    debug_mode_enabled: bool = False


settings = Settings()
