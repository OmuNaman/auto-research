import pytest

from auto_research.agents.planner import InvalidTransition, Phase, StateMachine
from auto_research.events.bus import SQLiteEventBus


async def test_valid_transitions(store):
    bus = SQLiteEventBus(store)
    rid = await store.create_run("p")
    sm = StateMachine(store, bus, rid)
    await sm.transition(Phase.INIT, Phase.LITERATURE)
    await sm.transition(Phase.LITERATURE, Phase.DESIGN)
    await sm.transition(Phase.DESIGN, Phase.PROVISION)
    await sm.transition(Phase.PROVISION, Phase.EXECUTE)
    await sm.transition(Phase.EXECUTE, Phase.ANALYZE)
    await sm.transition(Phase.ANALYZE, Phase.WRITE)
    await sm.transition(Phase.WRITE, Phase.DONE)
    row = await store.get_run(rid)
    assert row is not None and row.current_phase == "done"


async def test_analyze_can_loop_back_to_design(store):
    bus = SQLiteEventBus(store)
    rid = await store.create_run("p")
    sm = StateMachine(store, bus, rid)
    await sm.transition(Phase.INIT, Phase.LITERATURE)
    await sm.transition(Phase.LITERATURE, Phase.DESIGN)
    await sm.transition(Phase.DESIGN, Phase.PROVISION)
    await sm.transition(Phase.PROVISION, Phase.EXECUTE)
    await sm.transition(Phase.EXECUTE, Phase.ANALYZE)
    # refine
    await sm.transition(Phase.ANALYZE, Phase.DESIGN, reason="refine round 1",
                        refinement_round=1)
    row = await store.get_run(rid)
    assert row is not None and row.current_phase == "design"


async def test_invalid_transition_raises(store):
    bus = SQLiteEventBus(store)
    rid = await store.create_run("p")
    sm = StateMachine(store, bus, rid)
    with pytest.raises(InvalidTransition):
        await sm.transition(Phase.INIT, Phase.WRITE)  # not allowed
    with pytest.raises(InvalidTransition):
        await sm.transition(Phase.DONE, Phase.LITERATURE)  # terminal


async def test_transitions_emit_events(store):
    bus = SQLiteEventBus(store)
    rid = await store.create_run("p")
    sm = StateMachine(store, bus, rid)
    await sm.transition(Phase.INIT, Phase.LITERATURE, reason="start")
    rows = await store.events_since(rid, 0)
    assert len(rows) == 1
    assert rows[0].type == "phase.transition"
    assert rows[0].payload["from_phase"] == "init"
    assert rows[0].payload["to_phase"] == "literature"
    assert rows[0].payload["reason"] == "start"


async def test_any_phase_can_go_to_failed(store):
    bus = SQLiteEventBus(store)
    rid = await store.create_run("p")
    sm = StateMachine(store, bus, rid)
    await sm.transition(Phase.INIT, Phase.LITERATURE)
    await sm.transition(Phase.LITERATURE, Phase.FAILED, reason="boom")
    row = await store.get_run(rid)
    assert row is not None and row.current_phase == "failed"
