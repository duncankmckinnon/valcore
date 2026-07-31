"""In-process pub/sub so the runner can push run progress to SSE subscribers."""

import asyncio
from collections.abc import AsyncIterator

_QUEUE_MAXSIZE = 256
_CLOSE_SENTINEL: dict = {}


def _evict_oldest_progress(queue: "asyncio.Queue[dict]") -> bool:
    """Drop the oldest ``progress`` event from a full queue, preserving order and others.

    Returns True if a progress event was dropped, making room for a new event.
    """
    buffered: list[dict] = []
    while True:
        try:
            buffered.append(queue.get_nowait())
        except asyncio.QueueEmpty:
            break
    dropped = False
    for event in buffered:
        if not dropped and event.get("type") == "progress":
            dropped = True
            continue
        queue.put_nowait(event)
    return dropped


class EventBus:
    """Fan-out of per-run events to each subscriber's own bounded queue.

    Slow subscribers never block publishers: when a queue is full the oldest
    ``progress`` event is dropped to make room. ``row``, ``started``, and ``finished``
    events are never dropped.
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, list[asyncio.Queue[dict]]] = {}

    def publish(self, run_id: str, event: dict) -> None:
        """Deliver an event to every subscriber of a run without blocking."""
        for queue in list(self._subscribers.get(run_id, ())):
            self._offer(queue, event)

    def _offer(self, queue: "asyncio.Queue[dict]", event: dict) -> None:
        """Enqueue an event, evicting old progress under pressure rather than blocking."""
        try:
            queue.put_nowait(event)
            return
        except asyncio.QueueFull:
            pass
        if event.get("type") == "progress":
            return
        if _evict_oldest_progress(queue):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass

    async def subscribe(self, run_id: str) -> AsyncIterator[dict]:
        """Yield events published to ``run_id`` until the run is closed."""
        queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        self._subscribers.setdefault(run_id, []).append(queue)
        try:
            while True:
                event = await queue.get()
                if event is _CLOSE_SENTINEL:
                    return
                yield event
        finally:
            subscribers = self._subscribers.get(run_id)
            if subscribers is not None and queue in subscribers:
                subscribers.remove(queue)
            if subscribers is not None and not subscribers:
                self._subscribers.pop(run_id, None)

    def close(self, run_id: str) -> None:
        """Signal every subscriber of a run to stop iterating."""
        for queue in list(self._subscribers.get(run_id, ())):
            try:
                queue.put_nowait(_CLOSE_SENTINEL)
            except asyncio.QueueFull:
                _evict_oldest_progress(queue)
                try:
                    queue.put_nowait(_CLOSE_SENTINEL)
                except asyncio.QueueFull:
                    pass


bus = EventBus()
