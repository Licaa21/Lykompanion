import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Query
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from app.api import (
    chat,
    chats,
    config,
    debug,
    game_state,
    instructions,
    memory,
    models,
    profile,
    reminders,
    screenshot,
    tts,
    usage,
    voice,
)
from app.services.llm.game_state_extraction import run_game_state_poller
from app.services.llm.reminder_poller import run_reminder_poller

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    poller_task = asyncio.create_task(run_game_state_poller())
    reminder_task = asyncio.create_task(run_reminder_poller())
    try:
        yield
    finally:
        poller_task.cancel()
        reminder_task.cancel()


app = FastAPI(title="Lykompanion", lifespan=lifespan)

app.include_router(chat.router)
app.include_router(chats.router)
app.include_router(voice.router)
app.include_router(tts.router)
app.include_router(screenshot.router)
app.include_router(config.router)
app.include_router(models.router)
app.include_router(instructions.router)
app.include_router(memory.router)
app.include_router(usage.router)
app.include_router(game_state.router)
app.include_router(debug.router)
app.include_router(profile.router)
app.include_router(reminders.router)

_PROXY_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.google.com/",
}

@app.get("/api/proxy/image")
async def proxy_image(url: str = Query(...)) -> Response:
    """Fetch an external image server-side and relay it to the browser.
    Bypasses hotlink protection and SearXNG image proxy auth issues."""
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=10) as client:
            r = await client.get(url, headers=_PROXY_HEADERS)
            content_type = r.headers.get("content-type", "image/jpeg")
            return Response(content=r.content, media_type=content_type)
    except Exception:
        return Response(status_code=502)


WEB_DIR = Path(__file__).resolve().parent.parent / "web"
app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
