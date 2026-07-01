from typing import Literal

from pydantic import BaseModel


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    include_screenshot: bool = False


class ChatResponse(BaseModel):
    reply: str
    narration_volume: float = 1.0
    stop_listening: bool = False


class ChatTitleRequest(BaseModel):
    user_message: str
    assistant_message: str


class ChatTitleResponse(BaseModel):
    title: str


class TTSRequest(BaseModel):
    text: str
    voice: str | None = None
    speed: float | None = None


class CompanionConfig(BaseModel):
    openrouter_model: str
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_voice_model: str | None = None
    memory_extraction_model: str | None = None
    game_state_ocr_enabled: bool = False
    game_state_poll_interval_seconds: int = 90
    game_state_model: str | None = None
    tesseract_cmd: str | None = None
    tts_provider: Literal["kokoro", "openrouter", "chirp3"] = "kokoro"
    kokoro_base_url: str = "http://localhost:8880/v1"
    kokoro_voice: str | None = None
    openrouter_tts_model: str | None = None
    openrouter_voice: str | None = None
    google_tts_api_key: str | None = None
    google_tts_api_key_set: bool = False
    google_tts_voice: str | None = None
    openrouter_api_key: str | None = None
    openrouter_api_key_set: bool = False
    openrouter_management_key: str | None = None
    openrouter_management_key_set: bool = False
    narration_speed: float = 1.0
    narration_volume: float = 1.0
    context_window_messages: int = 20
    screenshot_max_width: int = 960
    wake_word_enabled: bool = False
    wake_word_phrase: str = "Hey Buddy"
    wake_word_max_failures: int = 3
    vad_threshold: int = 8
    vad_silence_ms: int = 1200
    vad_min_speech_ms: int = 300
    pre_roll_ms: int = 1000
    post_roll_ms: int = 500
    screenshot_jpeg_quality: int = 70

    igdb_client_id: str | None = None
    igdb_client_secret: str | None = None
    igdb_client_secret_set: bool = False
    steam_api_key: str | None = None
    steam_api_key_set: bool = False
    steam_id: str | None = None


class GameStateResponse(BaseModel):
    enabled: bool
    tracking: bool
    process: str | None = None
    activity: str | None = None
    location: str | None = None
    quest: str | None = None
    character: str | None = None
    notable_choice: str | None = None


class ProcessEntry(BaseModel):
    process: str


class PendingProcessResponse(BaseModel):
    processes: list[str] = []


class ChatHistoryResponse(BaseModel):
    chats: list[dict]


class ChatHistorySaveRequest(BaseModel):
    chats: list[dict]


class AccountBalance(BaseModel):
    available: bool
    spent_usd: float | None = None
    limit_usd: float | None = None
    remaining_usd: float | None = None
    is_free_tier: bool = False
    reason: str | None = None


class CustomInstructions(BaseModel):
    instructions: str = ""


class MemoryEntry(BaseModel):
    id: str
    content: str
    process: str | None = None


class MemoryCreate(BaseModel):
    content: str
    process: str | None = None


class MemoryUpdate(BaseModel):
    content: str
    process: str | None = None


class UsageRecord(BaseModel):
    timestamp: str
    source: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float


class DebugRequestEntry(BaseModel):
    id: str
    timestamp: str
    source: str
    model: str
    messages: list[dict]
    tools: list[str] | None = None
    reply: str | None = None
    tool_calls: list[dict] | None = None
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    duration_ms: float | None = None
