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
    stop_listening_seconds: int | None = None


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
    openrouter_voice_model: str | None = None
    tts_provider: Literal["kokoro", "openrouter"] = "kokoro"
    kokoro_voice: str | None = None
    openrouter_tts_model: str | None = None
    openrouter_voice: str | None = None
    openrouter_api_key: str | None = None
    openrouter_api_key_set: bool = False
    narration_speed: float = 1.0
    narration_volume: float = 1.0
    context_window_messages: int = 20

    igdb_client_id: str | None = None
    igdb_client_secret: str | None = None
    igdb_client_secret_set: bool = False
    steam_api_key: str | None = None
    steam_api_key_set: bool = False
    steam_id: str | None = None


class CustomInstructions(BaseModel):
    instructions: str = ""


class MemoryEntry(BaseModel):
    id: str
    content: str


class MemoryCreate(BaseModel):
    content: str


class MemoryUpdate(BaseModel):
    content: str


class UsageStats(BaseModel):
    request_count: int = 0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_cost_usd: float = 0.0
