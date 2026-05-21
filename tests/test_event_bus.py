import asyncio

import pytest

from auto_research.events.bus import SQLiteEventBus
from auto_research.events.schemas import PhaseTransitionEvent, parse_event


def _mk(run_id: str, i: int) -> PhaseTransitionEvent:
    return PhaseTransitionEvent(
        run_id=run_id, from_phase="init", to_phase="literature", reason=f"step-{i}"
    )


async def test_publish_assigns_monotonic_id(store):
    bus = SQLiteEventBus(store)
    rid = await store.create_run("p")
    ids = [await bus.publish(rid, _mk(rid, i)) for i in range(5)]
    assert ids == sorted(ids)
    rows = await store.events_since(rid, 0)
    assert [r.id for r in rows] == ids


async def test_subscribe_receives_live_events(store):
    bus = SQLiteEventBus(store)
    rid = await store.create_run("p")
    received: list[int] = []

    async with bus.subscribe(rid) as q:
        async def consume():
            for _ in range(3):
                ev = await asyncio.wait_for(q.get(), timeout=1.0)
                received.append(ev.id or 0)

        consumer = asyncio.create_task(consume())
        await asyncio.sleep(0)  # let subscriber register
        for i in range(3):
            await bus.publish(rid, _mk(rid, i))
        await consumer
    assert len(received) == 3
    assert received == sorted(received)
    assert all(i > 0 for i in received)


async def test_subscribe_before_replay_then_live_no_dupes_no_gaps(store):
    """The integration invariant: open subscription FIRST, replay from SQLite,
    then drain queue dropping ids <= last_replayed. End-to-end, no dupes, no gaps."""
    bus = SQLiteEventBus(store)
    rid = await store.create_run("p")

    # Pre-populate 10 events
    pre_ids = [await bus.publish(rid, _mk(rid, i)) for i in range(10)]

    seen: list[int] = []
    interleave_done = asyncio.Event()

    async with bus.subscribe(rid) as q:
        # While subscription is open, interleave more publishes
        async def publisher():
            for i in range(10, 15):
                await bus.publish(rid, _mk(rid, i))
                await asyncio.sleep(0)
            interleave_done.set()

        pub_task = asyncio.create_task(publisher())

        # Replay first
        replayed = await store.events_since(rid, after_id=3)
        last_replayed = max((r.id for r in replayed), default=3)
        seen.extend(r.id for r in replayed)

        await interleave_done.wait()
        await pub_task

        # Drain queue, dedupe
        while not q.empty():
            ev = q.get_nowait()
            if (ev.id or 0) > last_replayed:
                seen.append(ev.id or 0)

    # We should have seen ids 4..15 exactly once, in order, no duplicates.
    expected = pre_ids[3:] + list(range(pre_ids[-1] + 1, pre_ids[-1] + 6))
    assert seen == expected, f"expected {expected}, got {seen}"
    assert len(set(seen)) == len(seen)


async def test_full_subscriber_dropped_publisher_not_blocked(store):
    bus = SQLiteEventBus(store, queue_maxsize=3)
    rid = await store.create_run("p")

    async with bus.subscribe(rid) as q:
        for i in range(10):
            await bus.publish(rid, _mk(rid, i))
        # Subscriber filled at 3, then dropped on the 4th; bus must not block.
        assert q.qsize() <= 3
        assert bus.subscriber_count(rid) == 0
    # All 10 still durable
    rows = await store.events_since(rid, 0)
    assert len(rows) == 10


async def test_multiple_subscribers_each_get_all(store):
    bus = SQLiteEventBus(store)
    rid = await store.create_run("p")
    counts = [0, 0]

    async def reader(idx: int) -> None:
        async with bus.subscribe(rid) as q:
            for _ in range(5):
                await asyncio.wait_for(q.get(), timeout=1.0)
                counts[idx] += 1

    readers = [asyncio.create_task(reader(0)), asyncio.create_task(reader(1))]
    await asyncio.sleep(0.05)  # let both subscribe
    for i in range(5):
        await bus.publish(rid, _mk(rid, i))
    await asyncio.gather(*readers)
    assert counts == [5, 5]


async def test_event_roundtrip_through_sqlite_parse(store):
    bus = SQLiteEventBus(store)
    rid = await store.create_run("p")
    eid = await bus.publish(rid, _mk(rid, 0))
    rows = await store.events_since(rid, 0)
    assert len(rows) == 1
    # Reconstruct via parse_event from the stored payload
    parsed = parse_event({**rows[0].payload, "id": rows[0].id})
    assert isinstance(parsed, PhaseTransitionEvent)
    assert parsed.id == eid
    assert parsed.to_phase == "literature"


@pytest.mark.parametrize("n_subs", [1, 3, 5])
async def test_concurrent_subscribers_isolation(store, n_subs):
    bus = SQLiteEventBus(store)
    rid = await store.create_run("p")
    received: list[list[int]] = [[] for _ in range(n_subs)]

    async def reader(idx: int) -> None:
        async with bus.subscribe(rid) as q:
            for _ in range(4):
                ev = await asyncio.wait_for(q.get(), timeout=1.0)
                received[idx].append(ev.id or 0)

    tasks = [asyncio.create_task(reader(i)) for i in range(n_subs)]
    await asyncio.sleep(0.05)
    for i in range(4):
        await bus.publish(rid, _mk(rid, i))
    await asyncio.gather(*tasks)
    # Every subscriber saw the same 4 ids, in order
    for lst in received:
        assert len(lst) == 4
        assert lst == sorted(lst)
