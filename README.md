# auto-research

Autonomous AI research agent. Give it a research problem statement and it produces a publication-ready package end-to-end: literature review with real citations → dataset selection → experiment design → GPU experiments on RunPod → iterative refinement → Markdown + LaTeX report.

Local v1: everything runs on your laptop. No Docker, no cloud hosting.

## Quickstart

```bash
# 1. Install deps
uv sync

# 2. Configure secrets
cp .env.example .env
# edit .env with your ANTHROPIC_API_KEY and RUNPOD_API_KEY

# 3. Run the backend (FastAPI + agent loop in one process)
uv run research serve

# 4. In another terminal, run the UI
cd apps/web && pnpm install && pnpm dev

# 5. Open http://localhost:3000 and launch a run.
```

For headless use:

```bash
uv run research run configs/problems/smoke.yaml
```

## Architecture

See `/root/.claude/plans/okay-now-plan-about-iridescent-brooks.md` (the approved plan) for the full design, or `CLAUDE.md` for project conventions.

## Tests

```bash
uv run pytest                    # offline; skips runpod_live
uv run pytest -m runpod_live     # provisions a real pod (cents)
```

## Deploy later

The architecture isolates cloud-specific pieces behind ABCs (`EventBus`, `ComputeProvider`, `LLMProvider`). Porting to a hosted setup (Firestore for events, Railway for the API, Firebase Auth) is an additive change, not a rewrite.
