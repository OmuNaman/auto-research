"""EventBus — the single channel between the agent and the UI.

`publish(run_id, event)` is the only mutator. It writes to SQLite (durability)
then fans out to in-memory subscriber queues (realtime). Backpressure: if a
subscriber's queue is full, it is dropped — the client is expected to reconnect
via SSE Last-Event-ID and catch up from SQLite.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from auto_research.events.schemas import Event
from auto_research.logging import get_logger
from auto_research.state.store import StateStore

_log = get_logger(__name__)


class EventBus(ABC):
    @abstractmethod
    async def publish(self, run_id: str, event: Event) -> int: ...

    @abstractmethod
    def subscribe(  # type: ignore[no-untyped-def]
        self, run_id: str
    ) -> "AsyncIterator[asyncio.Queue[Event]]":
        """Context manager returning a per-subscriber queue."""


class SQLiteEventBus(EventBus):
    """SQLite-backed EventBus with in-memory fan-out.

    Invariant: `publish()` first INSERTs into SQLite (assigning a monotonic id),
    then sets `event.id` to the row id, then `put_nowait`s into every subscriber
    queue. This guarantees that any event delivered live is also durable, and
    that the SSE replay-then-live hand-off in routes_stream.py can dedupe by id.
    """

    def __init__(self, store: StateStore, queue_maxsize: int = 1000) -> None:
        self._store = store
        self._subscribers: dict[str, set[asyncio.Queue[Event]]] = {}
        self._queue_maxsize = queue_maxsize

    async def publish(self, run_id: str, event: Event) -> int:
        payload = event.model_dump(mode="json")
        event_id = await self._store.append_event(run_id, event.type, payload)
        event.id = event_id

        dropped: list[asyncio.Queue[Event]] = []
        for q in list(self._subscribers.get(run_id, ())):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                dropped.append(q)
        for q in dropped:
            self._subscribers.get(run_id, set()).discard(q)
            _log.warning("event_bus.subscriber_dropped",
                         run_id=run_id, reason="queue_full",
                         queue_maxsize=self._queue_maxsize)
        return event_id

    @asynccontextmanager  # type: ignore[arg-type]
    async def subscribe(  # type: ignore[override]
        self, run_id: str
    ) -> AsyncIterator[asyncio.Queue[Event]]:
        q: asyncio.Queue[Event] = asyncio.Queue(maxsize=self._queue_maxsize)
        self._subscribers.setdefault(run_id, set()).add(q)
        try:
            yield q
        finally:
            subs = self._subscribers.get(run_id)
            if subs is not None:
                subs.discard(q)
                if not subs:
                    self._subscribers.pop(run_id, None)

    def subscriber_count(self, run_id: str) -> int:
        return len(self._subscribers.get(run_id, ()))
