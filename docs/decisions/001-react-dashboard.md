# ADR 001 — Replace Streamlit with React Dashboard

## Problem

Reelsmith's Streamlit UI was a single form that submitted one job at a time with no clip library, no live progress, and no preview. This blocked the Opus-Clip-style product experience.

## Options Considered

| Option | Pros | Cons |
|---|---|---|
| Streamlit v2 | No new stack, fast to iterate | Still limited to form-per-job, no clip grid, no real-time SSE |
| Gradio | Good for demos, built-in media | Not designed for multi-page dashboard apps |
| React + Vite | Full control over UX, standard ecosystem | More setup, separate build step |

## Decision

**React 19 + Vite 8 + TypeScript + Tailwind v4 + TanStack Router + TanStack Query + shadcn/ui**

- TanStack Router — file-based, type-safe, supports nested layouts
- TanStack Query — server state caching, SSE invalidation hooks
- shadcn/ui — accessible, composable, Tailwind-native components
- Served as static files from FastAPI in production (`YTVIDEO_SERVE_FRONTEND=true`)
- API contract typed via openapi-typescript codegen from FastAPI's /openapi.json

## Consequences

- Build step required (`pnpm build`) before serving frontend from FastAPI
- Dev workflow: two servers (uvicorn + vite dev), proxied via Vite's `/api` → `localhost:8000`
- Streamlit archived to `ui/_legacy/` and kept runnable as a rollback path through Phase 7
- SSE from `EventSource` cannot send auth headers — token passed via `?token=` query param if auth is enabled
