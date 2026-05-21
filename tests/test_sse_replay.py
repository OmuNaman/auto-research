"""SSE end-to-end: subscribe-before-replay-then-live, reconnect with
Last-Event-ID, no dupes at the hand-off.

Spins the FastAPI app in-process via httpx ASGI transport. Uses a dummy
LLM/compute since we never actually run the planner here.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator

import httpx
import pytest

from auto_research.api.main import create_app
from auto_research.events.bus import SQLiteEventBus
from auto_research.events.schemas import PhaseTransitionEvent
from auto_research.settings import Settings
from auto_research.state.store import StateStore


def _mk(run_id: str, i: int) -> PhaseTransitionEvent:
    return PhaseTransitionEvent(
        run_id=run_id, from_phase="init", to_phase="literature",
        reason=f"r{i}",
    )


SSE_LINE = re.compile(r"^(id|event|data): ?(.*)$")


def _parse_sse(text: str) -> list[dict]:
    """Parse a chunk of SSE text into list of {id, event, data} dicts."""
    out: list[dict] = []
    current: dict = {}
    for line in text.split("\n"):
        if not line.strip():
            if current:
                out.append(current)
                current = {}
            continue
        m = SSE_LINE.match(line)
        if m:
            current[m.group(1)] = m.group(2)
    if current:
        out.append(current)
    return out


async def _read_n_events_from_response(resp, n: int) -> list[dict]:
    """Read SSE lines from an httpx response until we have n events with id."""
    events: list[dict] = []
    current: dict = {}
    async for line in resp.aiter_lines():
        if not line.strip():
            if current and "id" in current:
                events.append(current)
                if len(events) >= n:
                    return events
            current = {}
            continue
        if line.startswith(":"):
            continue
        m = SSE_LINE.match(line)
        if m:
            current[m.group(1)] = m.group(2)
    if current and "id" in current:
        events.append(current)
    return events


@pytest.fixture
async def app_with_seeded_run(tmp_path):
    settings = Settings(
        anthropic_api_key="x", runpod_api_key="x",
        runpod_ssh_private_key_path=tmp_path / "key",
        workspace_root=tmp_path / "ws",
        db_path=tmp_path / "state.db",
    )
    (tmp_path / "key").write_text("k")
    store = StateStore(settings.db_path)
    await store.open()
    bus = SQLiteEventBus(store)

    app = create_app(
        settings=settings, store=store, bus=bus, llm=None,
        compute_factory=lambda: None,
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Trigger startup
        await client.get("/health")
        rid = await store.create_run("p")
        yield client, store, bus, rid
    await store.close()


async def test_sse_replays_then_streams_live_no_dupes(app_with_seeded_run):
    client, store, bus, rid = app_with_seeded_run

    # Pre-populate 5 events durably
    for i in range(5):
        await bus.publish(rid, _mk(rid, i))

    # Open SSE with after_id=0; concurrently publish 5 more
    async def publisher():
        await asyncio.sleep(0.05)
        for i in range(5, 10):
            await bus.publish(rid, _mk(rid, i))

    pub_task = asyncio.create_task(publisher())

    async with client.stream(
        "GET", f"/runs/{rid}/events?after_id=0&max_events=10&idle_timeout_s=1",
    ) as resp:
        assert resp.status_code == 200
        events = await _read_n_events_from_response(resp, 10)
    await pub_task

    ids = [int(e["id"]) for e in events]
    assert ids == sorted(ids)
    assert len(ids) == len(set(ids)), f"duplicates in {ids}"
    assert len(ids) == 10


async def test_sse_replay_after_id_skips_history(app_with_seeded_run):
    client, store, bus, rid = app_with_seeded_run
    for i in range(10):
        await bus.publish(rid, _mk(rid, i))
    async with client.stream(
        "GET", f"/runs/{rid}/events?after_id=5&max_events=5&idle_timeout_s=1",
    ) as resp:
        events = await _read_n_events_from_response(resp, 5)
    ids = [int(e["id"]) for e in events]
    assert all(i > 5 for i in ids)
    assert len(ids) == 5


async def test_sse_reconnect_via_last_event_id_header(app_with_seeded_run):
    client, store, bus, rid = app_with_seeded_run
    for i in range(5):
        await bus.publish(rid, _mk(rid, i))

    async with client.stream(
        "GET", f"/runs/{rid}/events?after_id=0&max_events=5&idle_timeout_s=1",
    ) as resp:
        events = await _read_n_events_from_response(resp, 5)
    last = int(events[-1]["id"])

    for i in range(5, 8):
        await bus.publish(rid, _mk(rid, i))

    async with client.stream(
        "GET", f"/runs/{rid}/events?max_events=3&idle_timeout_s=1",
        headers={"last-event-id": str(last)},
    ) as resp:
        events2 = await _read_n_events_from_response(resp, 3)
    ids2 = [int(e["id"]) for e in events2]
    assert all(i > last for i in ids2), f"got {ids2} with cursor={last}"
    assert len(ids2) == 3


async def test_runs_routes_basic(app_with_seeded_run):
    client, store, bus, rid = app_with_seeded_run
    r = await client.get("/runs")
    assert r.status_code == 200
    data = r.json()
    assert any(row["id"] == rid for row in data)

    r2 = await client.get(f"/runs/{rid}")
    assert r2.status_code == 200
    assert r2.json()["id"] == rid

    r3 = await client.get(f"/runs/{rid}/files")
    assert r3.status_code == 200


async def test_files_route_blocks_traversal(app_with_seeded_run, tmp_path):
    client, store, bus, rid = app_with_seeded_run
    # workspace_for is reachable via the app's deps; write a benign file
    app = client._transport.app
    ws = app.state.deps.settings.workspace_for(rid)
    (ws / "a.txt").write_text("hello")
    r = await client.get(f"/runs/{rid}/files/a.txt")
    assert r.status_code == 200
    assert r.text == "hello"
    r2 = await client.get(f"/runs/{rid}/files/../../etc/passwd")
    assert r2.status_code in {403, 404}
