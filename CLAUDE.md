# auto-research — project conventions

Project: autonomous AI research agent. Plan of record: `/root/.claude/plans/okay-now-plan-about-iridescent-brooks.md`.

## Run commands

- `uv sync` — install Python deps.
- `uv run research serve` — start FastAPI on `127.0.0.1:8000`, agent runs as an asyncio task in the same process.
- `uv run research run <problem.yaml>` — kick off a run via CLI.
- `uv run pytest` — tests (skips `runpod_live` marker by default).
- `cd apps/web && pnpm dev` — start the Next.js UI on `:3000`.

## Conventions

- **Python 3.11**, async-first. No threads except `asyncio.to_thread` for sync I/O.
- **One event loop**: agent runs as `asyncio.create_task` inside the FastAPI process. Never spawn threads or subprocesses for agent work.
- **SQLite (WAL)** is durability; an in-memory `asyncio.Queue` is realtime. Both writes happen inside one `EventBus.publish()`.
- **State**: everything per-run lives under `workspaces/<run_id>/`. SQLite at `workspaces/state.db`.
- **Secrets**: `.env` is gitignored; only `.env.example` is committed. Settings loaded via `pydantic-settings` in `src/auto_research/settings.py`.
- **Logging**: structlog JSON, `run_id` bound via contextvar.
- **Events**: pydantic discriminated union in `src/auto_research/events/schemas.py`. Every UI element is driven by these events.
- **Citations**: no fabrication. `CitationManifest.add()` is the only writer; it requires DOI or arXiv ID + URL HEAD check + title fuzz >= 0.85.
- **Compute**: RunPod via REST (httpx) + SSH (asyncssh). Behind `ComputeProvider` ABC so we can add others later.
- **LLM**: Claude via `claude-agent-sdk`. We drive the state machine; the SDK owns one phase at a time.

## Env vars

See `.env.example`. Required for any real run: `ANTHROPIC_API_KEY`, `RUNPOD_API_KEY`, `RUNPOD_SSH_PRIVATE_KEY_PATH`. Optional but recommended: `SEMANTIC_SCHOLAR_API_KEY`, `HF_TOKEN`, `CONTACT_EMAIL`.

## Layout

```
src/auto_research/        # Python package
  settings.py logging.py
  state/      events/      llm/      agents/      tools/      compute/      citations/      api/      cli.py
apps/web/                  # Next.js 14 UI
configs/                   # default.yaml + problems/*.yaml
workspaces/                # gitignored, per-run artifacts
tests/
```

## Commits

Build order is the plan's commit list. Each commit is one section of the plan; keep them reviewable.
