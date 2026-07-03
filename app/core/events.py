"""In-process pub/sub bus feeding the in-game overlay's SSE stream.

Publishers (chat endpoints, reminder poller) run on the same asyncio event loop as the
subscribers, so plain ``put_nowait`` fan-out is safe — no locks, no cross-thread handoff.
Events are transient by design: no subscriber connected means the event is simply dropped
(the overlay is a live mirror, not a store — the chat log remains the source of truth).
"""

import asyncio

_subscribers: set[asyncio.Queue] = set()

# A subscriber that stopped draining (dead connection not yet reaped) must not grow its
# queue forever - past this point its events are dropped instead.
_MAX_QUEUED = 100


def publish(event: dict) -> None:
    for queue in list(_subscribers):
        if queue.qsize() < _MAX_QUEUED:
            queue.put_nowait(event)


def subscribe() -> asyncio.Queue:
    queue: asyncio.Queue = asyncio.Queue()
    _subscribers.add(queue)
    return queue


def unsubscribe(queue: asyncio.Queue) -> None:
    _subscribers.discard(queue)


# --- Cross-thread publish ---
# The global hotkey listener (run_app.py) runs on its own OS thread with a Win32 message
# loop, not on the uvicorn server's asyncio loop - calling publish() (mutates asyncio.Queue
# objects) directly from there is not thread-safe. The FastAPI lifespan registers the running
# loop here at startup so cross-thread callers can hop onto it via call_soon_threadsafe.

_loop: asyncio.AbstractEventLoop | None = None


def set_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _loop
    _loop = loop


def publish_threadsafe(event: dict) -> None:
    if _loop is not None:
        _loop.call_soon_threadsafe(publish, event)


# --- Native overlay window hooks ---
# The desktop launcher (run_app.py) runs the server in the same process and registers one
# callable per overlay widget window here so the layout editor can temporarily lift each
# window's win32 click-through styles (JS alone can't - they're window styles, not CSS).
# There is one small native window per widget (toasts, game-state panel) rather than a
# single full-screen one - see CLAUDE.md's in-game overlay section for why. Unregistered
# (dev uvicorn, an overlay page open in a normal browser tab) this is a harmless no-op.

_overlay_click_through_setters: list = []


def register_overlay_click_through_setter(setter) -> None:
    _overlay_click_through_setters.append(setter)


def set_overlay_click_through(enabled: bool) -> None:
    for setter in _overlay_click_through_setters:
        setter(enabled)


# --- Overlay edit-mode state ---
# Single source of truth for whether the layout editor is open, so both the HTTP endpoint
# (Settings button, always enabling) and the global hotkey (toggling, any thread) drive the
# same state instead of duplicating the click-through + SSE broadcast logic.

_overlay_editing = False


def is_overlay_editing() -> bool:
    return _overlay_editing


def set_overlay_edit_mode(enabled: bool, *, threadsafe: bool = False) -> bool:
    global _overlay_editing
    _overlay_editing = enabled
    set_overlay_click_through(not enabled)
    event = {"type": "edit_mode", "enabled": enabled}
    publish_threadsafe(event) if threadsafe else publish(event)
    return enabled


def toggle_overlay_edit_mode(*, threadsafe: bool = False) -> bool:
    return set_overlay_edit_mode(not _overlay_editing, threadsafe=threadsafe)
