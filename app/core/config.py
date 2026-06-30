from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_PATH = Path(__file__).resolve().parent.parent.parent / ".env"


def persist_env_value(key: str, value: str) -> None:
    """Write a KEY=value pair into the .env file, updating it in place if already present."""
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
    for i, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[i] = f"{key}={value}"
            break
    else:
        lines.append(f"{key}={value}")
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_model: str = "anthropic/claude-3.5-sonnet"
    openrouter_voice_model: str = ""

    tts_provider: str = "kokoro"
    kokoro_base_url: str = "http://localhost:8880/v1"
    kokoro_voice: str = "af_heart"
    openrouter_tts_model: str = ""
    openrouter_voice: str = ""
    tts_speed: float = 1.0
    tts_volume: float = 1.0

    host: str = "0.0.0.0"
    port: int = 8000

    # Max number of most-recent chat messages sent to the LLM as context. 0 = no limit.
    context_window_messages: int = 20

    # Dedicated model for background memory extraction. Falls back to openrouter_model if empty.
    memory_extraction_model: str = ""

    # Passive game-state OCR awareness (quest/location/character) - opt-in, Windows only.
    game_state_ocr_enabled: bool = False
    game_state_poll_interval_seconds: int = 90
    # Dedicated model for background game-state extraction. Falls back to openrouter_model if empty.
    game_state_model: str = ""
    # Full path to tesseract.exe, only needed if it's not on PATH after installing Tesseract OCR.
    tesseract_cmd: str = ""

    google_tts_api_key: str = ""
    google_tts_voice: str = "en-US-Chirp3-HD-Aoede"

    igdb_client_id: str = ""
    igdb_client_secret: str = ""
    steam_api_key: str = ""
    steam_id: str = ""


settings = Settings()
