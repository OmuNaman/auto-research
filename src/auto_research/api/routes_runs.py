"""Routes: POST /runs, GET /runs, GET /runs/{id}, POST /runs/{id}/cancel."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from auto_research.agents.planner import Planner, PlannerConfig
from auto_research.citations.manifest import CitationManifest
from auto_research.llm.base import LLMProvider


class CreateRunIn(BaseModel):
    problem_yaml: str


class RunOut(BaseModel):
    id: str
    status: str
    current_phase: str
    refinement_round: int
    total_cost_usd: float
    created_at: float
    updated_at: float


def _row_to_out(row) -> RunOut:
    return RunOut(
        id=row.id, status=row.status, current_phase=row.current_phase,
        refinement_round=row.refinement_round,
        total_cost_usd=row.total_cost_usd,
        created_at=row.created_at, updated_at=row.updated_at,
    )


def build_runs_router() -> APIRouter:
    router = APIRouter(prefix="/runs", tags=["runs"])

    @router.post("", response_model=RunOut)
    async def create_run(payload: CreateRunIn, request: Request) -> RunOut:
        deps = request.app.state.deps
        run_id = await deps.store.create_run(payload.problem_yaml)
        # Spawn planner as an asyncio task in the same event loop
        task = asyncio.create_task(_run_planner(deps, run_id, payload.problem_yaml))
        deps.registry.register(run_id, task)
        row = await deps.store.get_run(run_id)
        assert row is not None
        return _row_to_out(row)

    @router.get("", response_model=list[RunOut])
    async def list_runs(request: Request, limit: int = 50) -> list[RunOut]:
        deps = request.app.state.deps
        rows = await deps.store.list_runs(limit=limit)
        return [_row_to_out(r) for r in rows]

    @router.get("/{run_id}", response_model=RunOut)
    async def get_run(run_id: str, request: Request) -> RunOut:
        deps = request.app.state.deps
        row = await deps.store.get_run(run_id)
        if not row:
            raise HTTPException(404, "run not found")
        return _row_to_out(row)

    @router.post("/{run_id}/cancel")
    async def cancel_run(run_id: str, request: Request) -> dict[str, Any]:
        deps = request.app.state.deps
        ok = deps.registry.cancel(run_id)
        await deps.store.set_status(run_id, "cancelled")
        return {"cancelled": ok}

    return router


async def _run_planner(deps, run_id: str, problem_yaml: str) -> None:
    """Background task body. Constructs per-run dependencies then runs the planner."""
    if deps.llm is None:
        # Lazy import so test envs without `claude` CLI still load the module
        from auto_research.llm.anthropic import AnthropicProvider
        llm: LLMProvider = AnthropicProvider()
    else:
        llm = deps.llm
    compute = deps.compute_factory()
    workspace = deps.settings.workspace_for(run_id)
    http = httpx.AsyncClient(follow_redirects=True)
    manifest = CitationManifest(
        deps.store, deps.bus, run_id,
        http_client=http, pdf_dir=workspace / "pdfs",
    )
    try:
        planner = Planner(
            run_id=run_id, store=deps.store, bus=deps.bus, llm=llm,
            compute=compute, manifest=manifest, workspace=workspace,
            config=PlannerConfig(),
        )
        await planner.run(problem_yaml)
    finally:
        await manifest.close()
        if hasattr(compute, "close"):
            try:
                await compute.close()
            except Exception:  # noqa: BLE001
                pass
