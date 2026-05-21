# auto-research web UI

Next.js 14 (app router) live monitor for the auto-research agent.

## Run locally

```bash
# In one terminal: backend
cd ../..
uv run research serve

# In this directory: frontend
pnpm install   # or npm install
pnpm dev       # http://localhost:3000
```

The frontend rewrites `/api/*` → `http://127.0.0.1:8000/*` (see `next.config.js`).
Override with `NEXT_PUBLIC_API_BASE=http://other-host:8000`.

## Pages

- `/` — runs list, auto-refreshes every 3s.
- `/runs/new` — paste a problem YAML, pick a preset, launch.
- `/runs/[id]` — live monitor with 6 tabs: stream / thinking / citations / pods / metrics / artifacts.

State comes from a single SSE connection (`/api/runs/[id]/events`) reduced into derived state in `lib/eventStream.ts`. On refresh, `sessionStorage` holds the last seen event id so the server replays only what was missed.
