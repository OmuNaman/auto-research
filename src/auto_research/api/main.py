"""FastAPI app + agent task registry. Agent runs in the same event loop."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from auto_research.api.routes_files import build_files_router
from auto_research.api.routes_runs import build_runs_router
from auto_research.api.routes_stream import build_stream_router
from auto_research.compute.base import ComputeProvider
from auto_research.compute.runpod import RunPodProvider
from auto_research.events.bus import SQLiteEventBus
from auto_research.llm.base import LLMProvider
from auto_research.logging import configure_logging, get_logger
from auto_research.settings import Settings, get_settings
from auto_research.state.store import StateStore

_log = get_logger(__name__)


@dataclass
class PlannerRegistry:
    """Tracks the asyncio.Task running each active run's planner."""

    tasks: dict[str, asyncio.Task] = field(default_factory=dict)

    def register(self, run_id: str, task: asyncio.Task) -> None:
        self.tasks[run_id] = task

    def cancel(self, run_id: str) -> bool:
        t = self.tasks.get(run_id)
        if t and not t.done():
            t.cancel()
            return True
        return False

    def is_active(self, run_id: str) -> bool:
        t = self.tasks.get(run_id)
        return t is not None and not t.done()


@dataclass
class AppDeps:
    settings: Settings
    store: StateStore
    bus: SQLiteEventBus
    llm: LLMProvider | None
    compute_factory: object   # callable -> ComputeProvider
    registry: PlannerRegistry
    demo_mode: bool = False


def create_app(
    *,
    settings: Settings | None = None,
    store: StateStore | None = None,
    bus: SQLiteEventBus | None = None,
    llm: LLMProvider | None = None,
    compute_factory=None,
    demo_mode: bool = False,
) -> FastAPI:
    settings_final = settings or get_settings()
    configure_logging(settings_final.log_level)

    # Build deps eagerly so tests with httpx.ASGITransport (no lifespan)
    # still have app.state.deps populated. SQLiteEventBus has no async init;
    # StateStore.open() is async, so when no store is injected, we use the
    # lifespan to call .open() (which production CLI does).
    s = store or StateStore(settings_final.db_path)
    b = bus or SQLiteEventBus(s)
    cf = compute_factory or _default_compute_factory(settings_final)
    deps = AppDeps(
        settings=settings_final, store=s, bus=b, llm=llm,
        compute_factory=cf, registry=PlannerRegistry(),
        demo_mode=demo_mode,
    )
    _opened_here = store is None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if _opened_here:
            await s.open()
        _log.info("api.startup", host=settings_final.api_host,
                  port=settings_final.api_port,
                  workspace=str(settings_final.workspace_root))
        try:
            yield
        finally:
            for t in deps.registry.tasks.values():
                if not t.done():
                    t.cancel()
            if _opened_here:
                await s.close()

    app = FastAPI(title="auto-research", version="0.1.0", lifespan=lifespan)
    app.state.deps = deps
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
        allow_methods=["*"], allow_headers=["*"], allow_credentials=False,
    )
    app.include_router(build_runs_router())
    app.include_router(build_stream_router())
    app.include_router(build_files_router())

    @app.get("/health")
    async def health() -> dict:
        return {"ok": True}

    return app


def _default_compute_factory(settings: Settings):
    def make() -> ComputeProvider:
        key = settings.runpod_api_key.get_secret_value()
        if not key:
            raise RuntimeError("RUNPOD_API_KEY is empty; cannot create compute provider")
        return RunPodProvider(
            api_key=key,
            ssh_private_key_path=settings.runpod_ssh_private_key_path,
            datacenter_id=settings.runpod_pod_datacenter,
            network_volume_id=settings.runpod_network_volume_id,
        )
    return make
