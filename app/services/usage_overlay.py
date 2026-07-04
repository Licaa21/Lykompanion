"""Periodically pushes the OpenRouter account balance and this app session's LLM
cost-so-far to the overlay's standalone "Stats" panel (see overlay_process.push_stats /
overlay.cpp's `stats` command). Independent of game-state tracking - shows whenever the
overlay is running, whether or not a game is being tracked."""

import asyncio
import logging
from datetime import datetime, timezone

from app.core.usage import load_usage_records
from app.services import overlay_process
from app.services.llm.client import fetch_account_balance

logger = logging.getLogger(__name__)

_POLL_INTERVAL_SECONDS = 60

# Stamped at import time (once per app process) - "session cost" is everything spent since
# this process started, not since the overlay or a game launched.
_session_started_at = datetime.now(timezone.utc).isoformat()


def _session_cost_usd() -> float:
    return sum(
        r.get("cost_usd") or 0
        for r in load_usage_records()
        if (r.get("timestamp") or "") >= _session_started_at
    )


async def _tick() -> None:
    rows: list[list[str]] = []
    balance = await fetch_account_balance("openrouter")
    if balance.available and balance.remaining_usd is not None:
        rows.append(["Balance", f"${balance.remaining_usd:.2f}"])
    rows.append(["Session cost", f"${_session_cost_usd():.3f}"])
    overlay_process.push_stats("Usage", rows)


async def run_usage_overlay_poller() -> None:
    """Long-lived background loop, started at app startup alongside the other pollers."""
    while True:
        try:
            await _tick()
        except Exception:
            logger.exception("Usage overlay poller tick failed")
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)
