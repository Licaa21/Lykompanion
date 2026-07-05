from typing import Literal

from pydantic import BaseModel


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    # Data URL (or raw base64) of an image the user attached via the Send Image modal.
    image: str | None = None
    # When true (narration is on), the frontend pushes reply toasts to the overlay itself, timed to
    # each narrated sentence — so the backend skips its own fixed-timer reply-toast push to avoid
    # duplicates. See app/api/chat.py / web/js/app/narration.js.
    client_overlay_toasts: bool = False


class ChatResponse(BaseModel):
    reply: str
    narration_volume: float = 1.0
    stop_listening: bool = False
    transcript: str | None = None
    youtube_play: dict | None = None
    youtube_control: dict | None = None
    youtube_playlist: dict | None = None


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
    user_display_name: str = "You"
    openrouter_model: str
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    llm_provider: str = "openrouter"
    memory_extraction_provider: str = ""
    game_state_provider: str = ""
    google_ai_studio_api_key: str | None = None
    google_ai_studio_api_key_set: bool = False
    custom_openai_base_url: str = ""
    custom_openai_api_key: str | None = None
    custom_openai_api_key_set: bool = False
    memory_extraction_model: str | None = None
    overlay_enabled: bool = False
    overlay_edit_hotkey: str = "Ctrl+Shift+O"
    overlay_edit_phrase_enabled: bool = False
    overlay_edit_phrase: str = "Edit overlay"
    game_state_ocr_enabled: bool = False
    game_state_poll_interval_seconds: int = 90
    game_state_capture_interval_seconds: int = 1
    game_state_visual_diff_threshold_percent: float = 12.0
    game_state_visual_diff_noise_floor_percent: float = 1.5
    game_state_max_consecutive_skips: int = 0
    game_state_model: str | None = None
    game_state_ocr_similarity_threshold: float = 0.9
    game_state_ocr_max_width: int = 1600
    game_state_empty_ocr_warn_threshold: int = 10
    game_state_visual_diff_thumbnail_size: int = 64
    game_state_capture_frame_timeout_seconds: float = 6.0
    game_state_capture_cursor_enabled: bool = False
    game_state_training_enabled: bool = False
    proactive_messages_enabled: bool = False
    proactive_min_interval_minutes: int = 15
    memory_rag_limit: int = 30
    web_search_provider: Literal["openrouter", "searxng"] = "openrouter"
    searxng_base_url: str = "http://localhost:8080"
    tts_provider: Literal["kokoro", "openrouter", "chirp3"] = "kokoro"
    kokoro_base_url: str = "http://localhost:8880/v1"
    kokoro_voice: str | None = None
    openrouter_tts_model: str | None = None
    openrouter_voice: str | None = None
    google_tts_api_key: str | None = None
    google_tts_api_key_set: bool = False
    google_tts_voice: str | None = None
    transcription_enabled: bool = False
    transcription_model: str | None = None
    openrouter_api_key: str | None = None
    openrouter_api_key_set: bool = False
    openrouter_management_key: str | None = None
    openrouter_management_key_set: bool = False
    narration_speed: float = 1.0
    narration_volume: float = 1.0
    narrate_enabled: bool = True
    sfx_enabled: bool = True
    context_window_messages: int = 20
    screenshot_max_width: int = 960
    wake_word_enabled: bool = False
    wake_word_phrase: str = "Hey Buddy"
    wake_word_max_failures: int = 3
    sleep_word_enabled: bool = False
    sleep_word_phrase: str = "Go to sleep"
    vad_threshold: int = 8
    # Keep in sync with Settings.vad_silence_ms (app/core/config.py).
    vad_silence_ms: int = 3000
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
    steamgriddb_api_key: str | None = None
    steamgriddb_api_key_set: bool = False

    spotify_client_id: str | None = None
    spotify_connected: bool = False
    spotify_display_name: str | None = None

    youtube_client_id: str | None = None
    youtube_client_secret: str | None = None
    youtube_client_secret_set: bool = False
    youtube_connected: bool = False
    youtube_channel_title: str | None = None

    debug_mode_enabled: bool = False


class GameStateTracker(BaseModel):
    id: str
    label: str
    description: str = ""
    locked: bool = False
    overlay: bool = True
    value: str | None = None


class TrackerInput(BaseModel):
    id: str | None = None
    label: str
    description: str = ""
    overlay: bool = True


class TrainingDataDocument(BaseModel):
    content: str = ""


class GameSession(BaseModel):
    session_id: str
    name: str
    updated_at: str | None = None


class GameSessionCreate(BaseModel):
    name: str


class GameSessionRename(BaseModel):
    name: str


class GameStateResponse(BaseModel):
    enabled: bool
    tracking: bool
    process: str | None = None
    session_id: str | None = None
    session_name: str | None = None
    trackers: list[GameStateTracker] = []
    extraction_call_count: int = 0
    extraction_cost_usd: float = 0.0


class ProcessEntry(BaseModel):
    process: str


class PendingProcessResponse(BaseModel):
    processes: list[str] = []


class ChatHistoryResponse(BaseModel):
    chats: list[dict]


class ChatHistorySaveRequest(BaseModel):
    chats: list[dict]


class AccountBalance(BaseModel):
    provider: str = "openrouter"
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
    scope: Literal["user", "game", "session"] = "user"
    process: str | None = None
    session_id: str | None = None
    saved_at: str | None = None


class MemoryCreate(BaseModel):
    content: str
    scope: Literal["user", "game", "session"] | None = None
    process: str | None = None
    session_id: str | None = None


class MemoryUpdate(BaseModel):
    content: str
    process: str | None = None
    session_id: str | None = None


class ObservationEntry(BaseModel):
    id: str
    process: str
    session_id: str | None = None
    content: str
    confidence: float | None = None
    observed_at: str | None = None


class GamingJournalSession(BaseModel):
    session_id: str
    name: str
    updated_at: str | None = None
    active: bool = False
    memories: list[MemoryEntry] = []
    observations: list[ObservationEntry] = []


class GamingJournalGame(BaseModel):
    process: str
    title: str
    cover_url: str | None = None
    description: str | None = None
    tracked: bool = False
    memories: list[MemoryEntry] = []
    sessions: list[GamingJournalSession] = []
    date_added: str | None = None
    last_played: str | None = None


class GameArtRecord(BaseModel):
    title: str
    cover_url: str | None = None
    description: str | None = None
    source: str | None = None
    title_overridden: bool = False
    updated_at: str | None = None


class GameArtTitleUpdate(BaseModel):
    title: str


class ReminderEntry(BaseModel):
    id: str
    message: str
    process: str
    interval_minutes: int
    next_fire_at: str


class AlarmEntry(BaseModel):
    id: str
    message: str
    process: str
    fire_at: str


class PendingNotification(BaseModel):
    id: str
    text: str


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


class ProviderEndpointInfo(BaseModel):
    tag: str
    provider_name: str
    pricing_prompt: float | None = None
    pricing_completion: float | None = None
    context_length: int | None = None
    quantization: str | None = None
    uptime_last_30m: float | None = None
    latency_last_30m: float | None = None
    throughput_last_30m: float | None = None


class ProviderRoutingConfig(BaseModel):
    only: list[str] = []
    sort: Literal["price", "throughput", "latency"] | None = None
    allow_fallbacks: bool = True
    max_price_prompt: float | None = None
    max_price_completion: float | None = None
