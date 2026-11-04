#!/usr/bin/env bash
# One-command local launcher: Postgres (Docker) + API (uvicorn) + UI (Vite).
#
#   scripts/start.sh
#
# What it does:
#   - starts Docker Desktop on macOS if it is not running
#   - docker compose up -d --wait postgres   (job history lives in this volume)
#   - runs the API with YTVIDEO_JOB_STORE=sql against that Postgres
#   - runs the Vite dev server, proxying /api to that API
#   - Ctrl-C stops the API and the UI; Postgres keeps running
#
# Overrides (all optional):
#   PYTHON=...      interpreter for the API   (default: .venv-314/bin/python)
#   API_PORT=8000   UI_PORT=5173
#   DOCKER_WAIT=90  seconds to wait for Docker Desktop to answer
#   API_WAIT=120    seconds to wait for the API to report job_store=sql
#   YTVIDEO_DB_URL  already-set value wins over the compose default
#
# Compatible with macOS /bin/bash 3.2 (no arrays, associative arrays, `wait -n`).
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

API_PORT="${API_PORT:-8000}"
UI_PORT="${UI_PORT:-5173}"
DOCKER_WAIT="${DOCKER_WAIT:-90}"
API_WAIT="${API_WAIT:-120}"
PY="${PYTHON:-$REPO/.venv-314/bin/python}"
VITE="$REPO/web/node_modules/.bin/vite"

API_PID=""
UI_PID=""
POSTGRES_STARTED=0

is_alive() {
  [ -n "$1" ] && kill -0 "$1" 2>/dev/null
}

# Stops exactly the two PIDs this script started, plus their children (e.g. the
# esbuild helper under Vite). Never signals the process group: that would also
# take down whatever launched this script (a shell, pytest, ...).
# shellcheck disable=SC2329  # invoked through the EXIT trap below
cleanup() {
  trap '' INT TERM
  trap - EXIT
  local pid i
  for pid in "$UI_PID" "$API_PID"; do
    if is_alive "$pid"; then
      pkill -P "$pid" 2>/dev/null || true
      kill "$pid" 2>/dev/null || true
    fi
  done
  # Give them a few seconds to exit, then force.
  i=0
  while [ "$i" -lt 25 ]; do
    if ! is_alive "$UI_PID" && ! is_alive "$API_PID"; then
      break
    fi
    sleep 0.2
    i=$((i + 1))
  done
  for pid in "$UI_PID" "$API_PID"; do
    if is_alive "$pid"; then
      kill -9 "$pid" 2>/dev/null || true
    fi
  done
  if [ "$POSTGRES_STARTED" = "1" ]; then
    echo "==> API and UI stopped. Postgres is still running; stop it with:"
    echo "    cd \"$REPO\" && docker compose stop postgres"
  fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# True when something already accepts connections on 127.0.0.1:$1 (bash /dev/tcp,
# so no lsof/nc dependency).
port_busy() {
  (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null
}

die() {
  echo "ERROR: $*" >&2
  exit 1
}

# --- 1. Docker ---------------------------------------------------------------
if ! docker info >/dev/null 2>&1; then
  if [ "$(uname -s)" = "Darwin" ]; then
    echo "==> Docker is not running; starting Docker Desktop (waiting up to ${DOCKER_WAIT}s)"
    open -ga Docker
    waited=0
    until docker info >/dev/null 2>&1; do
      if [ "$waited" -ge "$DOCKER_WAIT" ]; then
        die "Docker did not answer within ${DOCKER_WAIT}s. Start Docker Desktop, then re-run scripts/start.sh."
      fi
      sleep 1
      waited=$((waited + 1))
    done
  else
    die "Docker is not running. Start the Docker daemon (e.g. 'sudo systemctl start docker'), then re-run scripts/start.sh."
  fi
fi

echo "==> docker compose up -d --wait postgres"
docker compose up -d --wait postgres
POSTGRES_STARTED=1

# --- 2. Environment ----------------------------------------------------------
export YTVIDEO_JOB_STORE=sql
export YTVIDEO_DB_URL="${YTVIDEO_DB_URL:-postgresql+asyncpg://reelsmith:reelsmith@127.0.0.1:5432/reelsmith}"

if [ ! -x "$PY" ]; then
  die "Python not found at $PY. Create the venv ('python3.14 -m venv .venv-314 && .venv-314/bin/pip install -r requirements.txt') or set PYTHON=/path/to/python."
fi

if [ ! -d "$REPO/web/node_modules" ]; then
  echo "==> web/node_modules missing; pnpm --dir web install"
  pnpm --dir web install
fi
if [ ! -x "$VITE" ]; then
  die "Vite not found at $VITE. Run 'pnpm --dir web install'."
fi

# --- 3. Ports ----------------------------------------------------------------
if port_busy "$API_PORT"; then
  die "API port $API_PORT is already in use. Stop whatever holds it or set API_PORT=<free port>."
fi
if port_busy "$UI_PORT"; then
  die "UI port $UI_PORT is already in use. Stop whatever holds it or set UI_PORT=<free port>."
fi

# --- 4. API ------------------------------------------------------------------
echo "==> API on http://127.0.0.1:$API_PORT (job store: sql)"
"$PY" -m uvicorn app.main:app --host 127.0.0.1 --port "$API_PORT" &
API_PID=$!

echo "==> waiting for the API to report job_store=sql (up to ${API_WAIT}s; first start runs migrations)"
waited=0
healthy=0
while [ "$waited" -lt "$API_WAIT" ]; do
  if ! kill -0 "$API_PID" 2>/dev/null; then
    die "the API process exited during startup (see its output above)."
  fi
  body="$(curl -fsS --max-time 2 "http://127.0.0.1:$API_PORT/health" 2>/dev/null || true)"
  if printf '%s' "$body" | grep -Eq '"job_store"[[:space:]]*:[[:space:]]*"sql"'; then
    healthy=1
    break
  fi
  if printf '%s' "$body" | grep -q '"job_store"'; then
    die "the API is not using the sql job store (health said: $body). Is something else answering on port $API_PORT?"
  fi
  sleep 1
  waited=$((waited + 1))
done
if [ "$healthy" != "1" ]; then
  die "the API did not report job_store=sql within ${API_WAIT}s."
fi

# --- 5. UI -------------------------------------------------------------------
# Vite is run directly (not through pnpm) so there is no wrapper process to
# orphan it. `exec` keeps $! the Vite PID itself.
(cd "$REPO/web" && YTVIDEO_API_URL="http://127.0.0.1:$API_PORT" exec "$VITE" --host 127.0.0.1 --port "$UI_PORT" --strictPort) &
UI_PID=$!

echo "==> Reelsmith is up:  http://127.0.0.1:$UI_PORT   (Ctrl-C stops the API and the UI)"

# --- 6. Supervise ------------------------------------------------------------
while is_alive "$API_PID" && is_alive "$UI_PID"; do
  sleep 1
done

if ! is_alive "$API_PID"; then
  echo "ERROR: the API process exited unexpectedly." >&2
else
  echo "ERROR: the UI process exited unexpectedly." >&2
fi
exit 1
