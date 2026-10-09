# ADR-005: Serve the API at Both `/x` and `/api/x`

**Status:** Accepted
**Date:** 2026-10-09
**Author:** Thulani Maseko
**Implemented in:** T034 (`fix/api-prefix-routing`)

## Context

The React client calls every endpoint as `/api/...` (`web/src/api/client.ts`). Six routers declared `prefix="/api/..."` (`reprompt`, `clip_edits`, `ai_hook`, `xml_export`, `enhance_speech`, `bulk_export`); every other router had no prefix. The two ways of running the UI disagreed about the prefix:

- **Dev.** The Vite proxy forwards `/api/x` to the backend as `/x` (`web/vite.config.ts`), so the six prefixed routers returned 404.
- **`YTVIDEO_SERVE_FRONTEND=true`.** FastAPI serves the built UI at `/` and nothing strips `/api`, so only the six prefixed routers resolved and every other call (`/api/jobs`, `/api/health`, ...) returned 404. `scripts/deploy.sh` smokes `/api/health`, which also returned 404.

## Decision

Every route answers at both `/x` and `/api/x`, with one scheme in every mode:

1. All routers are unprefixed.
2. `ApiPrefixMiddleware` (`app/api_prefix.py`), added in `create_app`, strips one leading `/api` segment from `path` and `raw_path` before routing. `/api` alone becomes `/`, `/apixyz` is not rewritten, and `root_path` is kept. When the decoded path has the segment but the raw bytes do not (for example `/%61pi/jobs`), the request is left alone.
3. The middleware is pure ASGI, not `BaseHTTPMiddleware`. It replaces the scope and passes `receive` and `send` through untouched, so SSE (`/jobs/{id}/events`), the streamed bulk-export zip, client disconnects and websockets are unaffected.
4. The Vite proxy rewrite stays: it keeps working (`/api/x` becomes `/x`).

## Consequences

- The OpenAPI schema lists each route once, unprefixed. `/api/docs`, `/api/redoc` and `/api/openapi.json` also answer.
- The app-level API-key dependency (`YTVIDEO_REQUIRE_AUTH`) applies the same way at both addresses, because the rewrite happens before routing. The static mount stays outside it, as before.
- With `serve_frontend`, an `/api/...` path that matches no route falls through to the static mount and returns its 404.
- `scripts/deploy.sh`'s `/api/health` smoke test now resolves without a change.
- Starlette's trailing-slash redirect builds its URL from the rewritten path, so `/api/jobs/` redirects to `/jobs`, which also answers.

## Tests

`tests/unit/test_api_prefix_middleware.py` (scope rewriting, streaming pass-through), `tests/contract/test_api_prefix_routing.py` (both addresses for every router family, OpenAPI has no `/api` paths, SSE through the middleware, `serve_frontend` with and without auth) and the `/api/...` cases in `tests/contract/test_auth.py`.
