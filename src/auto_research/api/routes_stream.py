"""GET /runs/{id}/events — Server-Sent Events with replay-then-live ordering.

Critical invariant: open the in-memory subscription BEFORE replaying from
SQLite, so any event published between replay and live-stream is buffered in
the subscriber queue. After replay we drain the queue, dropping ids
<= last_replayed_id, then stream live forever. SSE `id:` field is the row id,
so EventSource auto-reconnect's `Last-Event-ID` resumes cleanly.
"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Header, Request
from sse_starlette.sse import EventSourceResponse


def build_stream_router() -> APIRouter:
    router = APIRouter(prefix="/runs", tags=["stream"])

    @router.get("/{run_id}/events")
    async def stream_events(
        run_id: str,
        request: Request,
        after_id: int = 0,
        last_event_id: int | None = Header(None, alias="last-event-id"),
        max_events: int = 0,
        idle_timeout_s: float = 15.0,
    ):
        """SSE replay-then-live. `max_events>0` makes the stream exit after N
        events (useful in tests); 0 means stream forever (production)."""
        deps = request.app.state.deps
        cursor = max(after_id, last_event_id or 0)

        async def gen():
            sent = 0
            async with deps.bus.subscribe(run_id) as q:
                # 1) Replay durable history first
                last_replayed = cursor
                rows = await deps.store.events_since(run_id, cursor, limit=10_000)
                for row in rows:
                    last_replayed = row.id
                    yield {
                        "id": str(row.id),
                        "event": row.type,
                        "data": json.dumps({**row.payload, "id": row.id}),
                    }
                    sent += 1
                    if max_events and sent >= max_events:
                        return

                # 2) Drain queue, dedupe at hand-off
                while not q.empty():
                    ev = q.get_nowait()
                    if (ev.id or 0) <= last_replayed:
                        continue
                    yield {
                        "id": str(ev.id),
                        "event": ev.type,
                        "data": ev.model_dump_json(),
                    }
                    last_replayed = ev.id or last_replayed
                    sent += 1
                    if max_events and sent >= max_events:
                        return

                # 3) Live; exits on disconnect, max_events, or extended idle
                while True:
                    if await request.is_disconnected():
                        return
                    try:
                        ev = await asyncio.wait_for(q.get(), timeout=idle_timeout_s)
                    except asyncio.TimeoutError:
                        yield {"event": "ping", "data": ""}
                        continue
                    if (ev.id or 0) <= last_replayed:
                        continue
                    yield {
                        "id": str(ev.id),
                        "event": ev.type,
                        "data": ev.model_dump_json(),
                    }
                    last_replayed = ev.id or last_replayed
                    sent += 1
                    if max_events and sent >= max_events:
                        return

        return EventSourceResponse(gen())

    return router
