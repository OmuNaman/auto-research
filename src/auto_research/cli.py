"""CLI: `research serve` and `research run <problem.yaml>`."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import typer
import uvicorn

from auto_research.api.main import create_app
from auto_research.logging import configure_logging, get_logger
from auto_research.settings import get_settings

app = typer.Typer(help="auto-research — autonomous AI research agent")
_log = get_logger(__name__)


@app.command()
def serve(
    host: str = typer.Option(None, help="Override settings.api_host"),
    port: int = typer.Option(None, help="Override settings.api_port"),
    reload: bool = typer.Option(False, help="Auto-reload on code changes"),
) -> None:
    """Start the FastAPI server with the agent runtime."""
    settings = get_settings()
    configure_logging(settings.log_level)
    h = host or settings.api_host
    p = port or settings.api_port
    fast = create_app(settings=settings)
    uvicorn.run(fast, host=h, port=p, reload=reload, log_level="info")


@app.command()
def run(
    problem_yaml: Path = typer.Argument(..., exists=True, readable=True),
    server: str = typer.Option(
        None,
        help="If set, POST to this server's /runs endpoint instead of running inline.",
    ),
) -> None:
    """Kick off a research run, either inline or via a running server."""
    body = problem_yaml.read_text()
    if server:
        import httpx
        url = server.rstrip("/") + "/runs"
        r = httpx.post(url, json={"problem_yaml": body}, timeout=30.0)
        r.raise_for_status()
        typer.echo(json.dumps(r.json(), indent=2))
        return

    asyncio.run(_run_inline(body))


async def _run_inline(problem_yaml: str) -> None:
    """Run the planner inline in this process (no HTTP server)."""
    settings = get_settings()
    configure_logging(settings.log_level)
    from auto_research.agents.planner import Planner, PlannerConfig
    from auto_research.citations.manifest import CitationManifest
    from auto_research.compute.runpod import RunPodProvider
    from auto_research.events.bus import SQLiteEventBus
    from auto_research.llm.anthropic import AnthropicProvider
    from auto_research.state.store import StateStore
    import httpx

    store = StateStore(settings.db_path)
    await store.open()
    bus = SQLiteEventBus(store)
    run_id = await store.create_run(problem_yaml)
    ws = settings.workspace_for(run_id)
    http = httpx.AsyncClient(follow_redirects=True)
    manifest = CitationManifest(store, bus, run_id, http_client=http, pdf_dir=ws / "pdfs")
    compute = RunPodProvider(
        api_key=settings.runpod_api_key.get_secret_value(),
        ssh_private_key_path=settings.runpod_ssh_private_key_path,
        datacenter_id=settings.runpod_pod_datacenter,
        network_volume_id=settings.runpod_network_volume_id,
    )
    llm = AnthropicProvider()
    planner = Planner(
        run_id=run_id, store=store, bus=bus, llm=llm, compute=compute,
        manifest=manifest, workspace=ws, config=PlannerConfig(),
    )
    typer.echo(f"run_id={run_id}")
    try:
        await planner.run(problem_yaml)
    finally:
        await manifest.close()
        await compute.close()
        await store.close()


if __name__ == "__main__":
    app()
