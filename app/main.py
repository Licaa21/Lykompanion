import asyncio
import hmac
import ipaddress
import logging
import os
import socket
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, Query, Request
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from app.api import (
    chat,
    chats,
    config,
    debug,
    game_state,
    gaming_journal,
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
        # Await the cancelled tasks so shutdown doesn't log "Task was destroyed but it is pending".
        await asyncio.gather(poller_task, reminder_task, return_exceptions=True)


app = FastAPI(title="Lykompanion", lifespan=lifespan)

# Per-launch API token (set by run_app.py before the server starts). When present, every /api/*
# request must carry it - header for fetch() calls, query param for <img>/<audio> element loads,
# which can't send headers. Unset (dev server started directly via uvicorn) means no auth.
API_TOKEN = os.environ.get("LYKO_API_TOKEN", "")


@app.middleware("http")
async def _require_api_token(request: Request, call_next):
    if API_TOKEN and request.url.path.startswith("/api/"):
        supplied = request.headers.get("x-lyko-token") or request.query_params.get("token") or ""
        if not hmac.compare_digest(supplied, API_TOKEN):
            return Response(status_code=401)
    return await call_next(request)

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
app.include_router(gaming_journal.router)
app.include_router(debug.router)
app.include_router(profile.router)
app.include_router(reminders.router)

_PROXY_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.google.com/",
}

_PROXY_MAX_BYTES = 10 * 1024 * 1024


def _is_private_url(url: str) -> bool:
    """True when the URL's host resolves (only) to private/loopback/link-local addresses -
    blocks using the proxy to probe the local machine or LAN (SSRF)."""
    host = urlparse(url).hostname
    if not host:
        return True
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return True
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return True
    return False


@app.get("/api/proxy/image")
async def proxy_image(url: str = Query(...)) -> Response:
    """Fetch an external image server-side and relay it to the browser.
    Bypasses hotlink protection and SearXNG image proxy auth issues.

    Hardened against use as an open relay: http(s) only, no private/loopback targets (except
    the user's own configured SearXNG instance, which legitimately runs on localhost), and a
    response size cap."""
    from app.core.config import settings

    if urlparse(url).scheme not in ("http", "https"):
        return Response(status_code=400)
    is_searxng = bool(settings.searxng_base_url) and url.startswith(settings.searxng_base_url.rstrip("/"))
    if not is_searxng and _is_private_url(url):
        return Response(status_code=403)
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=10) as client:
            async with client.stream("GET", url, headers=_PROXY_HEADERS) as r:
                content_type = r.headers.get("content-type", "image/jpeg")
                body = b""
                async for chunk in r.aiter_bytes():
                    body += chunk
                    if len(body) > _PROXY_MAX_BYTES:
                        return Response(status_code=502)
                return Response(content=body, media_type=content_type)
    except Exception:
        return Response(status_code=502)


WEB_DIR = Path(__file__).resolve().parent.parent / "web"
app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
