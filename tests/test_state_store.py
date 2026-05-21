import pytest

from auto_research.state.store import StateStore


async def test_schema_init_idempotent(tmp_path):
    s = StateStore(tmp_path / "state.db")
    await s.open()
    await s.close()
    s2 = StateStore(tmp_path / "state.db")
    await s2.open()  # must not raise
    await s2.close()


async def test_create_and_get_run(store):
    rid = await store.create_run("problem: smoke")
    row = await store.get_run(rid)
    assert row is not None
    assert row.id == rid
    assert row.status == "running"
    assert row.current_phase == "init"
    assert row.problem_yaml == "problem: smoke"


async def test_event_id_monotonic_per_run(store):
    r1 = await store.create_run("p1")
    r2 = await store.create_run("p2")
    ids_r1 = [await store.append_event(r1, "phase.transition", {"k": i}) for i in range(5)]
    ids_r2 = [await store.append_event(r2, "phase.transition", {"k": i}) for i in range(3)]
    assert ids_r1 == sorted(ids_r1)
    assert ids_r2 == sorted(ids_r2)
    # IDs are global-monotonic (single AUTOINCREMENT), which is fine because
    # we always query with WHERE run_id = ?
    all_ids = ids_r1 + ids_r2
    assert len(set(all_ids)) == len(all_ids)


async def test_events_since(store):
    rid = await store.create_run("p")
    for i in range(10):
        await store.append_event(rid, "phase.transition", {"i": i})
    rows = await store.events_since(rid, after_id=0)
    assert len(rows) == 10
    rows = await store.events_since(rid, after_id=5)
    assert [r.payload["i"] for r in rows] == [5, 6, 7, 8, 9]


async def test_set_phase_and_status(store):
    rid = await store.create_run("p")
    await store.set_phase(rid, "literature")
    await store.set_status(rid, "done")
    row = await store.get_run(rid)
    assert row is not None
    assert row.current_phase == "literature"
    assert row.status == "done"


async def test_refinement_round_bumps(store):
    rid = await store.create_run("p")
    assert await store.bump_refinement_round(rid) == 1
    assert await store.bump_refinement_round(rid) == 2


async def test_checkpoint_roundtrip(store):
    rid = await store.create_run("p")
    await store.save_checkpoint(rid, "design", {"matrix": [1, 2, 3]})
    out = await store.load_checkpoint(rid, "design")
    assert out == {"matrix": [1, 2, 3]}
    # Overwrite
    await store.save_checkpoint(rid, "design", {"matrix": [9]})
    assert (await store.load_checkpoint(rid, "design")) == {"matrix": [9]}


async def test_pods_for_run(store):
    rid = await store.create_run("p")
    await store.upsert_pod(
        id="pod1", run_id=rid, runpod_id="rp-1", status="ready",
        gpu="A5000", usd_per_hour=0.30, started_at=1.0, ssh_host="x", ssh_port=22,
    )
    pods = await store.pods_for_run(rid)
    assert len(pods) == 1
    assert pods[0].status == "ready"
    # Update status
    await store.upsert_pod(
        id="pod1", run_id=rid, runpod_id="rp-1", status="terminated",
        gpu="A5000", usd_per_hour=0.30, started_at=1.0, terminated_at=2.0,
    )
    pods = await store.pods_for_run(rid)
    assert pods[0].status == "terminated"
    assert pods[0].terminated_at == 2.0


@pytest.mark.parametrize("delta", [0.5, 1.25, 0.01])
async def test_cost_accumulates(store, delta):
    rid = await store.create_run("p")
    await store.add_cost(rid, delta)
    await store.add_cost(rid, delta)
    row = await store.get_run(rid)
    assert row is not None
    assert abs(row.total_cost_usd - delta * 2) < 1e-9
