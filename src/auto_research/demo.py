"""Fake LLM + Fake compute that exercise the full pipeline without real keys.

This lets the UI demo work end-to-end on a fresh machine: every event type
fires, the timeline advances, pods appear with rolling logs, metrics chart
populates, citations cards render, and a report.md / report.tex / references.bib
land in workspaces/<run_id>/report/.
"""

from __future__ import annotations

import asyncio
import json
import random
from collections.abc import AsyncIterator
from pathlib import Path

from auto_research.compute.base import (
    ComputeProvider,
    ExecResult,
    Pod,
    PodSpec,
    PodStatus,
)
from auto_research.llm.base import (
    LLMProvider,
    LLMTextDelta,
    LLMTurnComplete,
    PhaseResult,
)


_DEMO_CITATIONS = [
    {
        "key": "lewis2020rag",
        "title": "Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks",
        "authors": ["Patrick Lewis", "Ethan Perez", "Aleksandra Piktus"],
        "url": "https://arxiv.org/abs/2005.11401",
        "arxiv_id": "2005.11401",
        "year": 2020,
        "venue": "NeurIPS",
    },
    {
        "key": "karpukhin2020dpr",
        "title": "Dense Passage Retrieval for Open-Domain Question Answering",
        "authors": ["Vladimir Karpukhin", "Barlas Oguz", "Sewon Min"],
        "url": "https://arxiv.org/abs/2004.04906",
        "arxiv_id": "2004.04906",
        "year": 2020,
        "venue": "EMNLP",
    },
    {
        "key": "izacard2021fid",
        "title": "Leveraging Passage Retrieval with Generative Models",
        "authors": ["Gautier Izacard", "Edouard Grave"],
        "url": "https://arxiv.org/abs/2007.01282",
        "arxiv_id": "2007.01282",
        "year": 2021,
        "venue": "EACL",
    },
]


class FakeDemoLLM(LLMProvider):
    """Streams canned responses with realistic timing for demo runs."""

    def __init__(self) -> None:
        self._add_cite_calls = 0

    async def stream_phase(self, *, system_prompt, user_prompt, tools,
                          subagents=None, on_event, agent_name="planner",
                          max_turns=None, builtin_tools=None) -> PhaseResult:
        # Stream a short "thinking" message
        thought = {
            "literature": "Searching arXiv for foundation papers on retrieval-augmented generation...",
            "designer": "Designing a tiny embedding benchmark on the smallest GPU available...",
            "analyst": "Inspecting the per-step metrics; results look stable across runs.",
            "writer": "Drafting the report — intro, method, results, debunked section, conclusion.",
        }.get(agent_name, "Thinking...")
        chunks = thought.split(" ")
        for i, c in enumerate(chunks):
            await on_event(LLMTextDelta(
                agent=agent_name, message_id="m1", text=c + (" " if i < len(chunks) - 1 else ""),
            ))
            await asyncio.sleep(0.04)

        # For literature, simulate add_citation tool calls
        if agent_name == "literature":
            tool_handlers = {t.name: t.handler for t in tools}
            for cite in _DEMO_CITATIONS:
                handler = tool_handlers.get("add_citation")
                if handler is None:
                    break
                # Bypass the real network manifest by using a relaxed candidate
                # — the manifest's no-fabrication check requires HEAD + resolver,
                # which won't fire in demo without network. We swallow failures.
                try:
                    await handler(cite)
                except Exception:
                    pass
                await asyncio.sleep(0.15)

        final = _final_text(agent_name)
        await on_event(LLMTurnComplete(
            agent=agent_name, final_text=final,
            input_tokens=200, output_tokens=80, total_cost_usd=0.0008,
        ))
        return PhaseResult(
            final_text=final, input_tokens=200, output_tokens=80,
            cache_read_tokens=0, cache_write_tokens=0, total_cost_usd=0.0008,
        )


def _final_text(agent: str) -> str:
    if agent == "literature":
        return json.dumps({
            "added_citation_keys": [c["key"] for c in _DEMO_CITATIONS],
            "rationale": "Foundational works on RAG, dense retrieval, and FiD.",
        })
    if agent == "designer":
        return json.dumps({"experiments": [{
            "experiment_id": "demo_exp1",
            "rationale": "Compare two tiny sentence embedders on a 100-row STS subset.",
            "gpu_type": "NVIDIA RTX A5000",
            "requirements": [],
            "script": "print('demo')",
            "timeout_s": 60,
        }]})
    if agent == "analyst":
        return json.dumps({
            "verdict": "conclusive",
            "rationale": "Both embedders produced sensible Spearman scores; the differences "
                        "are within expected variance. Pipeline OK end-to-end.",
            "next_actions": [],
        })
    if agent == "writer":
        return json.dumps({
            "title": "Sentence Embeddings on STS-B: A Smoke-Test Comparison",
            "abstract": "We compare two small sentence-embedding models on a tiny "
                       "subset of STS-B as a smoke test for the auto-research pipeline.",
            "markdown": (
                "## Introduction\n\nRetrieval-augmented generation depends on dense passage "
                "encoders. We exercise the pipeline using foundational prior work "
                "[cite:lewis2020rag] [cite:karpukhin2020dpr] [cite:izacard2021fid].\n\n"
                "## Related Work\n\nDense Passage Retrieval [cite:karpukhin2020dpr] and "
                "Fusion-in-Decoder [cite:izacard2021fid] underpin modern RAG.\n\n"
                "## Method\n\nWe load 100 STS-B sentence pairs and compute Spearman "
                "correlation between cosine similarity of embeddings and gold scores.\n\n"
                "## Results\n\nBoth models produced sensible Spearman scores in the 0.75-0.85 "
                "range. See the metrics panel for live values.\n\n"
                "## Discussion\n\nA 100-sample subset is too small for strong conclusions, "
                "as expected for a smoke test. The pipeline itself is healthy.\n\n"
                "## What We Debunked\n\nNothing — this is a smoke test, not a real study.\n\n"
                "## Conclusion\n\nPipeline works end-to-end; ready for the real RAG-cross-"
                "domain investigation."
            ),
            "what_we_debunked": "",
        })
    return "{}"


class FakeDemoCompute(ComputeProvider):
    """Pretends to provision pods, streams synthetic logs and metrics."""

    def __init__(self) -> None:
        self.n = 0
        self.terminated: set[str] = set()
        self._pods: dict[str, Pod] = {}

    async def provision(self, spec: PodSpec) -> Pod:
        await asyncio.sleep(0.3)
        self.n += 1
        pod = Pod(
            id=f"demo-pod-{self.n}", spec=spec, usd_per_hour=0.30,
            ssh_host="127.0.0.1", ssh_port=22000 + self.n,
        )
        self._pods[pod.id] = pod
        return pod

    async def get_status(self, pod_id: str) -> PodStatus:
        return "terminated" if pod_id in self.terminated else "ready"

    async def wait_ready(self, pod_id: str, timeout_s: int = 600) -> Pod:
        await asyncio.sleep(0.5)
        return self._pods[pod_id]

    async def exec(self, pod: Pod, cmd: str, *, cwd: str = "/workspace",
                   timeout_s: int = 600) -> ExecResult:
        # Pretend the experiment runs for ~5s
        await asyncio.sleep(5.0)
        return ExecResult(exit_code=0, stdout="demo done", stderr="", duration_ms=5000)

    async def stream_logs(self, pod: Pod, remote_path: str) -> AsyncIterator[list[str]]:
        # Two distinct streams: metrics.jsonl emits JSONL, run.log emits text
        if remote_path.endswith(".jsonl"):
            for step in range(20):
                await asyncio.sleep(0.25)
                row = {
                    "step": step,
                    "spearman_bge": round(0.78 + random.uniform(-0.02, 0.02), 4),
                    "spearman_minilm": round(0.74 + random.uniform(-0.02, 0.02), 4),
                }
                yield [json.dumps(row)]
        else:
            for step in range(20):
                await asyncio.sleep(0.25)
                yield [f"[step {step}] processed batch ok"]

    async def upload(self, pod: Pod, local: Path, remote: str) -> None:
        return None

    async def download(self, pod: Pod, remote: str, local: Path) -> None:
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_text("demo artifact")

    async def terminate(self, pod_id: str) -> None:
        self.terminated.add(pod_id)

    async def list_active(self) -> list[Pod]:
        return [p for k, p in self._pods.items() if k not in self.terminated]
