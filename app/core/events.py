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


# --- Native overlay window hook ---
# The desktop launcher (run_app.py) runs the server in the same process and registers a
# callable here so the layout editor can temporarily lift the overlay window's win32
# click-through styles (JS alone can't - they're window styles, not CSS). Unregistered
# (dev uvicorn, overlay open in a normal browser tab) it's a harmless no-op.

_overlay_click_through_setter = None


def register_overlay_click_through_setter(setter) -> None:
    global _overlay_click_through_setter
    _overlay_click_through_setter = setter


def set_overlay_click_through(enabled: bool) -> None:
    if _overlay_click_through_setter is not None:
        _overlay_click_through_setter(enabled)
