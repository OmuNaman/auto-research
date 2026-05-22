"""End-to-end planner tests with mocked LLM + mocked compute.

These drive a fake `LLMProvider` that returns canned final messages per phase
and a fake `ComputeProvider` that pretends to run an experiment. We assert the
full event sequence reaches the bus in the right shape.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from auto_research.agents.planner import Planner, PlannerConfig
from auto_research.agents.pod_runner import ExperimentSpec
from auto_research.citations.manifest import Citation, CitationManifest
from auto_research.compute.base import (
    ComputeProvider,
    ExecResult,
    Pod,
    PodSpec,
    PodStatus,
)
from auto_research.events.bus import SQLiteEventBus
from auto_research.llm.base import (
    LLMProvider,
    LLMTextDelta,
    LLMTurnComplete,
    PhaseResult,
)


# --------------------------------------------------------------------- fakes

class FakeCompute(ComputeProvider):
    def __init__(self) -> None:
        self.provisioned: list[Pod] = []
        self.terminated: list[str] = []
        self.next_id = 0

    async def provision(self, spec: PodSpec) -> Pod:
        self.next_id += 1
        pod = Pod(
            id=f"pod-{self.next_id}", spec=spec, usd_per_hour=0.30,
            ssh_host="127.0.0.1", ssh_port=22000 + self.next_id,
        )
        self.provisioned.append(pod)
        return pod

    async def get_status(self, pod_id: str) -> PodStatus:
        return "ready"

    async def wait_ready(self, pod_id: str, timeout_s: int = 600) -> Pod:
        for p in self.provisioned:
            if p.id == pod_id:
                return p
        raise RuntimeError(f"no pod {pod_id}")

    async def exec(self, pod: Pod, cmd: str, *, cwd: str = "/workspace",
                   timeout_s: int = 600) -> ExecResult:
        return ExecResult(exit_code=0, stdout="ok", stderr="", duration_ms=10)

    async def stream_logs(self, pod: Pod, remote_path: str) -> AsyncIterator[list[str]]:
        # Yield one synthetic batch then stop
        if remote_path.endswith(".jsonl"):
            yield [json.dumps({"step": 0, "score": 0.42})]
            yield [json.dumps({"step": 1, "score": 0.55})]
        else:
            yield ["[log] starting"]
            yield ["[log] done"]

    async def upload(self, pod: Pod, local: Path, remote: str) -> None:
        return None

    async def download(self, pod: Pod, remote: str, local: Path) -> None:
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_text("dummy")

    async def terminate(self, pod_id: str) -> None:
        self.terminated.append(pod_id)

    async def list_active(self) -> list[Pod]:
        return [p for p in self.provisioned if p.id not in self.terminated]


class CannedLLM(LLMProvider):
    """Returns a pre-scripted PhaseResult per agent_name in order."""

    def __init__(self, scripts: dict[str, list[str]]):
        # scripts: agent_name -> list of final_text values, consumed in order
        self.scripts = {k: list(v) for k, v in scripts.items()}
        self.calls: list[tuple[str, str]] = []

    async def stream_phase(self, *, system_prompt, user_prompt, tools, subagents=None,
                          on_event, agent_name="planner", max_turns=None,
                          builtin_tools=None) -> PhaseResult:
        self.calls.append((agent_name, user_prompt))
        # Optionally invoke tools the script declares it wants to call
        script_text = (self.scripts.get(agent_name) or [""]).pop(0)
        # Emit a token delta + turn_complete so the bus sees something
        await on_event(LLMTextDelta(agent=agent_name, message_id="m1",
                                    text=script_text))
        await on_event(LLMTurnComplete(agent=agent_name, final_text=script_text,
                                       input_tokens=10, output_tokens=10,
                                       total_cost_usd=0.001))
        return PhaseResult(final_text=script_text, input_tokens=10, output_tokens=10,
                           cache_read_tokens=0, cache_write_tokens=0,
                           total_cost_usd=0.001)


@pytest.fixture
async def planner_setup(tmp_path, store):
    bus = SQLiteEventBus(store)
    rid = await store.create_run("p")
    http = httpx.AsyncClient(follow_redirects=True)
    manifest = CitationManifest(store, bus, rid, http_client=http)
    compute = FakeCompute()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    yield rid, store, bus, manifest, compute, workspace, http
    await manifest.close()


# ---------------------------------------------------------------------- tests


async def test_planner_full_happy_path(planner_setup):
    rid, store, bus, manifest, compute, workspace, http = planner_setup

    # Pre-seed one citation so the writer has something to cite
    with respx.mock(assert_all_called=False) as mock:
        mock.head("https://arxiv.org/abs/2005.11401").respond(200)
        mock.get("https://export.arxiv.org/api/query?id_list=2005.11401").respond(
            text="<feed><entry><id>http://arxiv.org/abs/2005.11401</id>"
                 "<title>Retrieval-Augmented Generation</title>"
                 "<published>2020-01-01</published><author><name>Lewis</name></author>"
                 "</entry></feed>",
        )
        await manifest.add(Citation(
            key="lewis2020rag", title="Retrieval-Augmented Generation",
            authors=("Lewis",), url="https://arxiv.org/abs/2005.11401",
            arxiv_id="2005.11401",
        ))

    llm = CannedLLM({
        "literature": [json.dumps({
            "added_citation_keys": ["lewis2020rag"],
            "rationale": "Foundational RAG paper.",
        })],
        "designer": [json.dumps({"experiments": [{
            "experiment_id": "exp1",
            "rationale": "smoke",
            "gpu_type": "NVIDIA RTX A5000",
            "requirements": [],
            "script": "print('hello')",
            "timeout_s": 60,
        }]})],
        "analyst": [json.dumps({
            "verdict": "conclusive",
            "rationale": "One experiment completed successfully.",
            "next_actions": [],
        })],
        "writer": [json.dumps({
            "title": "Smoke Test Report",
            "abstract": "abstract",
            "markdown": ("## Introduction\nx [cite:lewis2020rag]\n\n## Related Work\nx\n\n"
                         "## Method\nx\n\n## Results\nx\n\n## Discussion\nx\n\n"
                         "## What We Debunked\n\n\n## Conclusion\nDone."),
            "what_we_debunked": "",
        })],
    })

    p = Planner(
        run_id=rid, store=store, bus=bus, llm=llm, compute=compute,
        manifest=manifest, workspace=workspace,
        config=PlannerConfig(max_refinement_rounds=1),
    )
    await p.run("problem_statement: smoke")

    # Check final phase is done
    row = await store.get_run(rid)
    assert row is not None
    assert row.current_phase == "done"
    assert row.status == "done"

    # Phase transitions in order
    rows = await store.events_since(rid, 0)
    transitions = [(r.payload["from_phase"], r.payload["to_phase"])
                   for r in rows if r.type == "phase.transition"]
    assert transitions == [
        ("init", "literature"),
        ("literature", "design"),
        ("design", "provision"),
        ("provision", "execute"),
        ("execute", "analyze"),
        ("analyze", "write"),
        ("write", "done"),
    ]

    # Pod was provisioned and left running (pods persist after completion)
    assert len(compute.provisioned) == 1
    assert compute.terminated == []

    # Report files written
    md = workspace / "report" / "report.md"
    tex = workspace / "report" / "report.tex"
    bib = workspace / "report" / "references.bib"
    assert md.exists() and tex.exists() and bib.exists()
    assert "Smoke Test Report" in md.read_text()
    assert "(Lewis, 2020)" in md.read_text()  # [cite:KEY] resolved
    assert "@misc{lewis2020rag" in bib.read_text() or "@article{lewis2020rag" in bib.read_text()

    # Analyst verdict was published
    assert any(r.type == "analyst.verdict" and r.payload["verdict"] == "conclusive"
               for r in rows)

    # Metrics from FakeCompute streamed through
    metric_events = [r for r in rows if r.type == "experiment.metric"]
    assert len(metric_events) >= 1
    assert metric_events[0].payload["metrics"]["score"] in (0.42, 0.55)


async def test_planner_refines_then_concludes(planner_setup):
    rid, store, bus, manifest, compute, workspace, http = planner_setup

    # Seed one citation
    with respx.mock(assert_all_called=False) as mock:
        mock.head("https://arxiv.org/abs/2005.11401").respond(200)
        mock.get("https://export.arxiv.org/api/query?id_list=2005.11401").respond(
            text="<feed><entry><id>http://arxiv.org/abs/2005.11401</id>"
                 "<title>RAG</title><published>2020</published>"
                 "<author><name>L</name></author></entry></feed>",
        )
        await manifest.add(Citation(
            key="rag", title="RAG", authors=("L",),
            url="https://arxiv.org/abs/2005.11401", arxiv_id="2005.11401",
        ))

    exp_json = json.dumps({"experiments": [{
        "experiment_id": "exp", "gpu_type": "A5000", "requirements": [],
        "script": "print('x')", "timeout_s": 30,
    }]})
    llm = CannedLLM({
        "literature": [json.dumps({"added_citation_keys": ["rag"], "rationale": "r"})],
        "designer": [exp_json, exp_json],  # called twice (refine)
        "analyst": [
            json.dumps({"verdict": "refine", "rationale": "needs more"}),
            json.dumps({"verdict": "conclusive", "rationale": "ok"}),
        ],
        "writer": [json.dumps({
            "title": "T", "abstract": "a",
            "markdown": "## Introduction\nx [cite:rag]\n## Related Work\nx\n"
                        "## Method\nx\n## Results\nx\n## Discussion\nx\n"
                        "## What We Debunked\n\n## Conclusion\nx",
            "what_we_debunked": "",
        })],
    })

    p = Planner(
        run_id=rid, store=store, bus=bus, llm=llm, compute=compute,
        manifest=manifest, workspace=workspace,
        config=PlannerConfig(max_refinement_rounds=2),
    )
    await p.run("problem_statement: x")
    row = await store.get_run(rid)
    assert row is not None and row.current_phase == "done"
    assert row.refinement_round == 1
    # Two experiments provisioned (one per round)
    assert len(compute.provisioned) == 2


async def test_planner_unknown_cite_stripped_and_logged(planner_setup):
    rid, store, bus, manifest, compute, workspace, http = planner_setup
    # No citations seeded
    llm = CannedLLM({
        "literature": [json.dumps({"added_citation_keys": [], "rationale": "none"})],
        "designer": [json.dumps({"experiments": [{
            "experiment_id": "e", "gpu_type": "A5000", "requirements": [],
            "script": "print('x')", "timeout_s": 30,
        }]})],
        "analyst": [json.dumps({"verdict": "conclusive", "rationale": "ok"})],
        "writer": [json.dumps({
            "title": "T", "abstract": "a",
            "markdown": "## Introduction\nx [cite:ghost] [cite:phantom]\n## Related Work\nx\n"
                        "## Method\nx\n## Results\nx\n## Discussion\nx\n"
                        "## What We Debunked\n\n## Conclusion\nx",
            "what_we_debunked": "",
        })],
    })
    p = Planner(
        run_id=rid, store=store, bus=bus, llm=llm, compute=compute,
        manifest=manifest, workspace=workspace,
    )
    await p.run("problem_statement: x")
    rows = await store.events_since(rid, 0)
    errors = [r for r in rows if r.type == "error"]
    msgs = [e.payload["message"] for e in errors]
    assert any("ghost" in m for m in msgs)
    assert any("phantom" in m for m in msgs)
    # Markdown does NOT contain the unknown keys verbatim
    md = (workspace / "report" / "report.md").read_text()
    assert "[cite:ghost]" not in md
    assert "[cite:phantom]" not in md
