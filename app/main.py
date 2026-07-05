import asyncio
import faulthandler
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
    backup,
    chat,
    chats,
    config,
    debug,
    game_state,
    gaming_journal,
    instructions,
    memory,
    models,
    overlay,
    profile,
    provider_routing,
    reminders,
    screenshot,
    spotify_oauth,
    system,
    tts,
    usage,
    voice,
    youtube_oauth,
    youtube_search,
)
from app.core.chats import prune_empty_chats
from app.services import overlay_process
from app.services.llm.game_state_extraction import run_game_state_poller
from app.services.llm.reminder_poller import run_reminder_poller
from app.services.usage_overlay import run_usage_overlay_poller

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    prune_empty_chats()
    poller_task = asyncio.create_task(run_game_state_poller())
    reminder_task = asyncio.create_task(run_reminder_poller())
    usage_overlay_task = asyncio.create_task(run_usage_overlay_poller())
    try:
        yield
    finally:
        poller_task.cancel()
        reminder_task.cancel()
        usage_overlay_task.cancel()
        # Await the cancelled tasks so shutdown doesn't log "Task was destroyed but it is pending".
        await asyncio.gather(poller_task, reminder_task, usage_overlay_task, return_exceptions=True)
        # Kill the native overlay if it's still running.
        overlay_process.stop()


# Dev-uvicorn coverage for silent native crashes (run_app.py already enables it pointing at
# data/crash_log.txt - don't override that handler when running as the desktop app).
if not faulthandler.is_enabled():
    faulthandler.enable()

app = FastAPI(title="Lykompanion", lifespan=lifespan)

# Per-launch API token (set by run_app.py before the server starts). When present, every /api/*
# request must carry it - header for fetch() calls, query param for <img>/<audio> element loads,
# which can't send headers. Unset (dev server started directly via uvicorn) means no auth.
API_TOKEN = os.environ.get("LYKO_API_TOKEN", "")

# Spotify/YouTube's OAuth redirects are plain browser GETs with no way to attach our custom
# header/query token, so they must be exempt from the check below. Safe: each callback route
# validates its own one-shot CSRF "state" param (see app/api/spotify_oauth.py /
# app/api/youtube_oauth.py), which is what actually prevents an unrelated request from completing
# someone else's pending authorization.
_TOKEN_EXEMPT_PATHS = {"/api/spotify/oauth/callback", "/api/youtube/oauth/callback"}


@app.middleware("http")
async def _require_api_token(request: Request, call_next):
    if (
        API_TOKEN
        and request.url.path.startswith("/api/")
        and request.url.path not in _TOKEN_EXEMPT_PATHS
    ):
        supplied = request.headers.get("x-lyko-token") or request.query_params.get("token") or ""
        if not hmac.compare_digest(supplied, API_TOKEN):
            return Response(status_code=401)
    return await call_next(request)

app.include_router(chat.router)
app.include_router(chats.router)
app.include_router(overlay.router)
app.include_router(voice.router)
app.include_router(tts.router)
app.include_router(screenshot.router)
app.include_router(system.router)
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
app.include_router(provider_routing.router)
app.include_router(backup.router)
app.include_router(spotify_oauth.router)
app.include_router(youtube_oauth.router)
app.include_router(youtube_search.router)

from app.services.llm.web_search_tool import SEARXNG_HEADERS

# Must match the headers web_search_tool.py's _image_candidate_loads used to validate this same
# URL - a Referer (e.g. google.com) trips some hosts' hotlink protection that a referer-less
# request passes, so a candidate that validated fine could 403 here if the headers differed.
_PROXY_HEADERS = {
    **SEARXNG_HEADERS,
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
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
