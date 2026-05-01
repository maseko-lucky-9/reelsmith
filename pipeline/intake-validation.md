# Intake Validation

## Status
- **Parseable**: yes
- **dual_client**: false (Streamlit-only)
- **scope_warning**: false (single bounded context: UI)
- **design_brief_source**: none — design gate required

## Normalized Requirement

**Problem**: Streamlit UI shows only a top-level progress bar driven by SSE; rich per-step log lines emitted by FastAPI backend services are invisible to the user (server-terminal only).

**Goal**: Add a structured live-updating "Pipeline Log" panel to `ui/streamlit_app.py` that renders a human-readable line for each SSE event in real time.

## Acceptance Criteria
1. Collapsible "Pipeline Log" expander appears beneath the progress bar after job submission.
2. Each SSE event appends a new log line in real time.
3. Log lines: timestamp + friendly event label + key payload fields (title, chapter index, output path, error).
4. On `JobCompleted` panel stays open with all lines; on `JobFailed` error is highlighted.
5. Existing pytest suite passes.
6. No new pip dependency unless justified.

## Confirmed Scope (single phase, UI-only)
- Files in scope: `ui/streamlit_app.py`, `ui/api_client.py` (read-only confirmation), possibly a new helper module `ui/log_formatter.py`.
- Out of scope: backend changes, SSE event schema changes, new event types.

## Constraints
- Streamlit built-ins only (no new deps if avoidable).
- Both dark/light themes acceptable (avoid raw HTML color hacks).
- Progress bar behavior must not regress.

## Estimated task count
3-5 tasks — well under the 20-task threshold.
