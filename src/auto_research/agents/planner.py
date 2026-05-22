"""Planner + StateMachine.

The Planner orchestrates the per-run lifecycle:
  INIT → LITERATURE → DESIGN → PROVISION → EXECUTE → ANALYZE → (WRITE → DONE | DESIGN [refine])

The StateMachine is a small validator/persister; the Planner owns the per-phase
implementation. Each phase uses the LLMProvider (one ClaudeSDKClient session)
with phase-specific system prompt + tool whitelist. `pod_runner` is invoked
directly (not as an SDK sub-agent) for cost/reliability reasons.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml

from auto_research.agents.analyst import ANALYST_SYSTEM_PROMPT
from auto_research.agents.designer import DESIGNER_SYSTEM_PROMPT
from auto_research.agents.literature import (
    LITERATURE_SYSTEM_PROMPT,
    make_literature_tools,
)
from auto_research.agents.pod_runner import (
    ExperimentResult,
    ExperimentSpec,
    run_experiment,
)
from auto_research.agents.writer import WRITER_SYSTEM_PROMPT
from auto_research.citations.manifest import Citation, CitationManifest, NoFabricationError
from auto_research.compute.base import ComputeProvider, PodSpec
from auto_research.events.bus import EventBus
from auto_research.events.schemas import (
    AnalystVerdictEvent,
    ErrorEvent,
    LLMMessageDeltaEvent,
    LLMToolCallEvent,
    PhaseTransitionEvent,
    ToolResultEvent,
)
from auto_research.llm.base import (
    LLMProvider,
    LLMStreamEvent,
    LLMTextDelta,
    LLMToolCall,
    LLMToolResult,
    LLMTurnComplete,
)
from auto_research.logging import bind_run_id, get_logger
from auto_research.state.store import StateStore
from auto_research.tools.report_render import render_report
from auto_research.tools.search_arxiv import search_arxiv
from auto_research.tools.search_semantic_scholar import search_semantic_scholar

_log = get_logger(__name__)


class Phase(StrEnum):
    INIT = "init"
    LITERATURE = "literature"
    DESIGN = "design"
    PROVISION = "provision"
    EXECUTE = "execute"
    ANALYZE = "analyze"
    WRITE = "write"
    DONE = "done"
    FAILED = "failed"


_TRANSITIONS: dict[Phase, set[Phase]] = {
    Phase.INIT: {Phase.LITERATURE, Phase.FAILED},
    Phase.LITERATURE: {Phase.DESIGN, Phase.FAILED},
    Phase.DESIGN: {Phase.PROVISION, Phase.FAILED},
    Phase.PROVISION: {Phase.EXECUTE, Phase.FAILED},
    Phase.EXECUTE: {Phase.ANALYZE, Phase.FAILED},
    Phase.ANALYZE: {Phase.WRITE, Phase.DESIGN, Phase.FAILED},
    Phase.WRITE: {Phase.DONE, Phase.FAILED},
    Phase.DONE: set(),
    Phase.FAILED: set(),
}


class InvalidTransition(RuntimeError):
    pass


class StateMachine:
    """Validates + persists phase transitions. One per run."""

    def __init__(self, store: StateStore, bus: EventBus, run_id: str) -> None:
        self.store = store
        self.bus = bus
        self.run_id = run_id

    async def transition(
        self, frm: Phase, to: Phase, *, reason: str = "", refinement_round: int = 0,
    ) -> None:
        if to not in _TRANSITIONS[frm]:
            raise InvalidTransition(f"{frm} → {to} not allowed")
        await self.store.set_phase(self.run_id, to.value)
        await self.bus.publish(self.run_id, PhaseTransitionEvent(
            run_id=self.run_id, from_phase=frm.value, to_phase=to.value,
            reason=reason, refinement_round=refinement_round,
        ))


# ----------------------------------------------------------------- planner


JSON_BLOCK_RE = re.compile(r"\{.*\}", re.S)


def _extract_json(text: str) -> dict[str, Any]:
    """Find the LAST JSON object in a string. Tolerates code fences."""
    cleaned = re.sub(r"```(?:json)?\s*|\s*```", "", text)
    matches = list(JSON_BLOCK_RE.finditer(cleaned))
    if not matches:
        raise ValueError("no JSON object found in model output")
    return json.loads(matches[-1].group(0))


@dataclass
class PlannerConfig:
    max_refinement_rounds: int = 3
    max_experiments_per_run: int = 64
    # Verified RunPod gpuTypeId (see designer.py for the full menu of valid IDs).
    default_gpu_type: str = "NVIDIA RTX A5000"
    default_image: str = "runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04"


@dataclass
class _RunState:
    problem: dict[str, Any]
    literature_summary: str = ""
    citation_keys: list[str] = field(default_factory=list)
    experiments: list[dict[str, Any]] = field(default_factory=list)
    results: list[ExperimentResult] = field(default_factory=list)
    verdict: dict[str, Any] | None = None


class Planner:
    def __init__(
        self,
        *,
        run_id: str,
        store: StateStore,
        bus: EventBus,
        llm: LLMProvider,
        compute: ComputeProvider,
        manifest: CitationManifest,
        workspace: Path,
        config: PlannerConfig | None = None,
    ) -> None:
        self.run_id = run_id
        self.store = store
        self.bus = bus
        self.llm = llm
        self.compute = compute
        self.manifest = manifest
        self.workspace = workspace
        self.config = config or PlannerConfig()
        self.sm = StateMachine(store, bus, run_id)
        self._refinement_round = 0
        self._current_phase = Phase.INIT

    # ------------------------------------------------------------------ run

    async def run(self, problem_yaml: str) -> None:
        bind_run_id(self.run_id)
        problem = yaml.safe_load(problem_yaml) or {}
        state = _RunState(problem=problem)
        try:
            await self._literature(state)
            while True:
                await self._design(state)
                await self._execute(state)
                await self._analyze(state)
                verdict = (state.verdict or {}).get("verdict")
                if verdict in {"conclusive", "debunked"}:
                    break
                if self._refinement_round >= self.config.max_refinement_rounds:
                    _log.warning("planner.refinement_cap_reached",
                                 cap=self.config.max_refinement_rounds)
                    break
                self._refinement_round = await self.store.bump_refinement_round(self.run_id)
                await self.sm.transition(
                    Phase.ANALYZE, Phase.DESIGN,
                    reason=f"refine (round {self._refinement_round})",
                    refinement_round=self._refinement_round,
                )
                self._current_phase = Phase.DESIGN
            await self._write(state)
            await self.store.set_status(self.run_id, "done")
        except Exception as exc:  # noqa: BLE001
            import traceback
            await self.bus.publish(self.run_id, ErrorEvent(
                run_id=self.run_id, where=f"planner.{self._current_phase.value}",
                message=str(exc), traceback=traceback.format_exc(), fatal=True,
            ))
            await self.store.set_status(self.run_id, "failed")
            try:
                await self.sm.transition(self._current_phase, Phase.FAILED, reason=str(exc))
            except InvalidTransition:
                pass
            raise

    # ----------------------------------------------------------- on_event wire

    def _on_llm(self, agent: str):
        async def _handle(ev: LLMStreamEvent) -> None:
            match ev:
                case LLMTextDelta():
                    await self.bus.publish(self.run_id, LLMMessageDeltaEvent(
                        run_id=self.run_id, agent=agent, message_id=ev.message_id,
                        text_delta=ev.text,
                    ))
                case LLMToolCall():
                    await self.bus.publish(self.run_id, LLMToolCallEvent(
                        run_id=self.run_id, agent=agent, tool=ev.tool,
                        tool_call_id=ev.tool_call_id, input_json=ev.input,
                    ))
                case LLMToolResult():
                    await self.bus.publish(self.run_id, ToolResultEvent(
                        run_id=self.run_id, tool_call_id=ev.tool_call_id,
                        tool=ev.tool, ok=ev.ok, output_json=ev.output,
                        error=ev.error, duration_ms=ev.duration_ms,
                    ))
                case LLMTurnComplete():
                    if ev.total_cost_usd > 0:
                        await self.store.add_cost(self.run_id, ev.total_cost_usd)
        return _handle

    # ------------------------------------------------------------- phases

    async def _literature(self, state: _RunState) -> None:
        await self.sm.transition(Phase.INIT, Phase.LITERATURE, reason="start")
        self._current_phase = Phase.LITERATURE

        async def on_search_arxiv(args: dict) -> dict:
            hits = await search_arxiv(args["query"], limit=args.get("limit", 10))
            return {"hits": [h.__dict__ for h in hits]}

        async def on_search_s2(args: dict) -> dict:
            hits = await search_semantic_scholar(args["query"], limit=args.get("limit", 10))
            return {"hits": [h.__dict__ for h in hits]}

        async def on_add_citation(args: dict) -> dict:
            try:
                c = Citation(
                    key=args["key"], title=args["title"],
                    authors=tuple(args.get("authors") or []),
                    url=args["url"], doi=args.get("doi") or None,
                    arxiv_id=args.get("arxiv_id") or None,
                    year=args.get("year"), venue=args.get("venue"),
                )
                added = await self.manifest.add(c)
                state.citation_keys.append(added.key)
                return {"ok": True, "key": added.key, "title": added.title}
            except NoFabricationError as exc:
                return {"ok": False, "error": str(exc)}

        tools = make_literature_tools(
            on_search_arxiv=on_search_arxiv,
            on_search_s2=on_search_s2,
            on_add_citation=on_add_citation,
        )
        result = await self.llm.stream_phase(
            system_prompt=LITERATURE_SYSTEM_PROMPT,
            user_prompt=(
                "Problem statement:\n\n"
                f"{state.problem.get('problem_statement', '')}\n\n"
                f"Domains: {state.problem.get('domains') or []}\n"
                "Find 3-8 relevant papers and register them via add_citation. "
                "End with the JSON summary as instructed."
            ),
            tools=tools,
            on_event=self._on_llm("literature"),
            agent_name="literature",
        )
        try:
            summary = _extract_json(result.final_text)
            state.literature_summary = summary.get("rationale", "")
            # Trust manifest, not the LLM's claim, for citation_keys
            state.citation_keys = [c.key for c in await self.manifest.all()]
        except ValueError:
            state.literature_summary = result.final_text[:2000]
        await self.store.save_checkpoint(self.run_id, Phase.LITERATURE.value, {
            "summary": state.literature_summary,
            "citation_keys": state.citation_keys,
        })

    async def _design(self, state: _RunState) -> None:
        await self.sm.transition(Phase.LITERATURE, Phase.DESIGN, reason="design") \
            if self._current_phase == Phase.LITERATURE else None
        self._current_phase = Phase.DESIGN

        prompt = (
            f"Problem:\n{state.problem.get('problem_statement','')}\n\n"
            f"Literature summary:\n{state.literature_summary}\n\n"
            f"Registered citation keys: {state.citation_keys}\n\n"
            f"Refinement round: {self._refinement_round}\n"
            f"Prior results (compact): {[r.metrics[:3] for r in state.results]}\n\n"
            "Design experiments per the schema and end with the JSON block."
        )
        result = await self.llm.stream_phase(
            system_prompt=DESIGNER_SYSTEM_PROMPT,
            user_prompt=prompt, tools=[],
            on_event=self._on_llm("designer"), agent_name="designer",
        )
        try:
            spec = _extract_json(result.final_text)
        except (ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"designer produced no parseable JSON: {exc}") from exc
        state.experiments = list(spec.get("experiments") or [])
        if not state.experiments:
            raise RuntimeError("designer returned zero experiments")
        if len(state.experiments) > self.config.max_experiments_per_run:
            state.experiments = state.experiments[: self.config.max_experiments_per_run]
        await self.store.save_checkpoint(self.run_id, Phase.DESIGN.value, spec)

    async def _execute(self, state: _RunState) -> None:
        await self.sm.transition(Phase.DESIGN, Phase.PROVISION, reason="provision")
        self._current_phase = Phase.PROVISION
        await self.sm.transition(Phase.PROVISION, Phase.EXECUTE, reason="execute")
        self._current_phase = Phase.EXECUTE

        new_results: list[ExperimentResult] = []
        for i, exp in enumerate(state.experiments):
            exp_id = exp.get("experiment_id") or f"exp{i+1}"
            spec = ExperimentSpec(
                experiment_id=exp_id,
                pod_spec=PodSpec(
                    gpu_type=exp.get("gpu_type") or self.config.default_gpu_type,
                    image=exp.get("image") or self.config.default_image,
                    disk_gb=int(exp.get("disk_gb") or 30),
                    name=f"{self.run_id[:8]}-{exp_id}",
                ),
                script=exp["script"],
                requirements=list(exp.get("requirements") or []),
                timeout_s=int(exp.get("timeout_s") or 1800),
            )
            try:
                res = await run_experiment(
                    spec=spec, provider=self.compute, bus=self.bus,
                    run_id=self.run_id, workspace_dir=self.workspace,
                )
                new_results.append(res)
            except Exception as exc:  # noqa: BLE001
                import traceback
                await self.bus.publish(self.run_id, ErrorEvent(
                    run_id=self.run_id, where=f"planner.execute.{exp_id}",
                    message=str(exc), traceback=traceback.format_exc(), fatal=False,
                ))
        state.results.extend(new_results)
        await self.store.save_checkpoint(self.run_id, Phase.EXECUTE.value, {
            "experiment_ids": [r.experiment_id for r in new_results],
            "exit_codes": [r.exit_code for r in new_results],
        })

    async def _analyze(self, state: _RunState) -> None:
        await self.sm.transition(Phase.EXECUTE, Phase.ANALYZE, reason="analyze")
        self._current_phase = Phase.ANALYZE

        compact = [{
            "experiment_id": r.experiment_id,
            "exit_code": r.exit_code,
            "metrics": r.metrics[-50:],  # truncate
            "duration_s": r.duration_s,
        } for r in state.results]
        prompt = (
            f"Problem:\n{state.problem.get('problem_statement','')}\n\n"
            f"Refinement round: {self._refinement_round}\n"
            f"All experiment results so far:\n{json.dumps(compact, default=str)}\n\n"
            "Emit the verdict JSON."
        )
        result = await self.llm.stream_phase(
            system_prompt=ANALYST_SYSTEM_PROMPT, user_prompt=prompt, tools=[],
            on_event=self._on_llm("analyst"), agent_name="analyst",
        )
        try:
            verdict = _extract_json(result.final_text)
        except (ValueError, json.JSONDecodeError):
            verdict = {"verdict": "inconclusive",
                       "rationale": "Analyst produced no parseable JSON.",
                       "next_actions": []}
        state.verdict = verdict
        await self.bus.publish(self.run_id, AnalystVerdictEvent(
            run_id=self.run_id, refinement_round=self._refinement_round,
            verdict=verdict.get("verdict", "inconclusive"),  # type: ignore[arg-type]
            rationale=verdict.get("rationale", ""),
            next_actions=list(verdict.get("next_actions") or []),
        ))
        await self.store.save_checkpoint(self.run_id, Phase.ANALYZE.value, verdict)

    async def _write(self, state: _RunState) -> None:
        await self.sm.transition(Phase.ANALYZE, Phase.WRITE, reason="write")
        self._current_phase = Phase.WRITE

        compact_results = [{
            "experiment_id": r.experiment_id,
            "exit_code": r.exit_code,
            "metrics_tail": r.metrics[-10:],
        } for r in state.results]
        prompt = (
            f"Problem:\n{state.problem.get('problem_statement','')}\n\n"
            f"Literature summary:\n{state.literature_summary}\n\n"
            f"Registered citation keys (use these verbatim in [cite:KEY]): "
            f"{state.citation_keys}\n\n"
            f"Experiment results:\n{json.dumps(compact_results, default=str)}\n\n"
            f"Analyst verdict:\n{json.dumps(state.verdict, default=str)}\n\n"
            "Write the report. End with the JSON block as instructed."
        )
        result = await self.llm.stream_phase(
            system_prompt=WRITER_SYSTEM_PROMPT, user_prompt=prompt, tools=[],
            on_event=self._on_llm("writer"), agent_name="writer",
        )
        try:
            report = _extract_json(result.final_text)
        except (ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"writer produced no parseable JSON: {exc}") from exc

        rendered = await render_report(
            title=report.get("title") or "Untitled",
            markdown_body=report.get("markdown") or "",
            manifest=self.manifest,
            workspace=self.workspace,
            bus=self.bus, run_id=self.run_id,
        )
        await self.store.save_checkpoint(self.run_id, Phase.WRITE.value, {
            "title": rendered.title,
            "md_path": str(rendered.md_path),
            "tex_path": str(rendered.tex_path),
            "bib_path": str(rendered.bib_path),
            "stripped_keys": rendered.stripped_keys,
        })
        await self.sm.transition(Phase.WRITE, Phase.DONE, reason="done")
        self._current_phase = Phase.DONE
