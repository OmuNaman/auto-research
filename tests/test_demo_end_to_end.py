"""End-to-end demo: FakeDemoLLM + FakeDemoCompute drive the planner from
INIT to DONE, with the citation manifest in skip_verification mode (no network).

This is the offline equivalent of the smoke run — every event type fires,
report files render, the planner reaches DONE.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

from auto_research.agents.planner import Planner, PlannerConfig
from auto_research.citations.manifest import CitationManifest
from auto_research.demo import FakeDemoCompute, FakeDemoLLM
from auto_research.events.bus import SQLiteEventBus


async def test_demo_run_reaches_done_with_all_event_types(tmp_path, store):
    bus = SQLiteEventBus(store)
    rid = await store.create_run("p")
    http = httpx.AsyncClient(follow_redirects=True)
    manifest = CitationManifest(
        store, bus, rid, http_client=http, skip_verification=True,
    )
    try:
        planner = Planner(
            run_id=rid, store=store, bus=bus,
            llm=FakeDemoLLM(), compute=FakeDemoCompute(),
            manifest=manifest, workspace=tmp_path,
            config=PlannerConfig(max_refinement_rounds=1),
        )
        await asyncio.wait_for(
            planner.run("problem_statement: demo smoke"),
            timeout=60,
        )
    finally:
        await manifest.close()
        await http.aclose()

    row = await store.get_run(rid)
    assert row is not None
    assert row.current_phase == "done"
    assert row.status == "done"

    rows = await store.events_since(rid, 0)
    types_seen = {r.type for r in rows}
    # Every "interesting" event type should have fired at least once
    must_have = {
        "phase.transition",
        "llm.message_delta",
        "citation.added",
        "pod.provisioned",
        "pod.status",
        "pod.log",
        "experiment.metric",
        "file.written",
        "cost.tick",
        "analyst.verdict",
    }
    missing = must_have - types_seen
    assert not missing, f"missing event types: {missing}"

    # All three demo citations registered
    cites = await manifest.all()
    keys = {c.key for c in cites}
    assert {"lewis2020rag", "karpukhin2020dpr", "izacard2021fid"} <= keys

    # Report files rendered
    md = tmp_path / "report" / "report.md"
    tex = tmp_path / "report" / "report.tex"
    bib = tmp_path / "report" / "references.bib"
    assert md.exists() and tex.exists() and bib.exists()
    assert "(Lewis, 2020)" in md.read_text()
    assert "@" in bib.read_text()

    # Verdict was conclusive (per the demo script)
    verdicts = [r for r in rows if r.type == "analyst.verdict"]
    assert verdicts and verdicts[-1].payload["verdict"] == "conclusive"
