"""scripts/start.sh: the one-command local launcher (Postgres + API + UI).

The script is copied into a throwaway fake repo and run against stub
``docker``/``open``/``curl``/``uname``/``pkill``/``pnpm`` executables that log
every call, plus stub ``python`` and ``vite`` programs that just stay alive. No
real Docker, network or server is involved.
"""

from __future__ import annotations

import os
import shutil
import signal
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
START_SH = REPO_ROOT / "scripts" / "start.sh"
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"
INITDB_SQL = REPO_ROOT / "scripts" / "initdb" / "10-create-test-db.sql"

DEFAULT_DB_URL = "postgresql+asyncpg://reelsmith:reelsmith@127.0.0.1:5432/reelsmith"
SQL_HEALTH = '{"status":"ok","job_store":"sql"}'
MEMORY_HEALTH = '{"status":"ok","job_store":"memory"}'

# Every stub appends "<name> <argv>" to $STUB_CALLS. ``kill`` is not an
# executable but a bash builtin, so BASH_ENV shadows it with a logging function.
_STUBS = {
    "docker": """#!/bin/sh
if [ "$1" = "info" ]; then
  echo "docker $*" >> "$STUB_CALLS"
  [ "${STUB_DOCKER_INFO_FAIL:-0}" = "1" ] && exit 1
  n="${STUB_DOCKER_INFO_FAIL_COUNT:-0}"
  seen="$(cat "$STUB_DIR/info.count" 2>/dev/null || echo 0)"
  echo $((seen + 1)) > "$STUB_DIR/info.count"
  [ "$seen" -lt "$n" ] && exit 1
  exit 0
fi
# `compose up --wait` takes a while; logging on completion makes the call order observable against
# the API process, which the script starts in the background and which logs asynchronously.
sleep "${STUB_COMPOSE_DELAY:-0}"
echo "docker $*" >> "$STUB_CALLS"
[ "${STUB_COMPOSE_FAIL:-0}" = "1" ] && exit 1
exit 0
""",
    "open": '#!/bin/sh\necho "open $*" >> "$STUB_CALLS"\nexit 0\n',
    "uname": '#!/bin/sh\necho "${STUB_UNAME:-Darwin}"\n',
    "pkill": '#!/bin/sh\necho "pkill $*" >> "$STUB_CALLS"\nexit 0\n',
    "curl": """#!/bin/sh
# A server can only answer once the API process exists (the script starts it in the background).
i=0
while [ "${STUB_PYTHON_EXIT:-0}" != "1" ] && [ ! -e "$STUB_DIR/python.pid" ] && [ "$i" -lt 100 ]; do
  sleep 0.05
  i=$((i + 1))
done
echo "curl $*" >> "$STUB_CALLS"
[ "${STUB_CURL_FAIL:-0}" = "1" ] && exit 7
echo "${STUB_HEALTH_BODY}"
""",
    "pnpm": """#!/bin/sh
echo "pnpm $*" >> "$STUB_CALLS"
mkdir -p "$STUB_REPO/web/node_modules/.bin"
cp "$STUB_DIR/vite-stub" "$STUB_REPO/web/node_modules/.bin/vite"
chmod +x "$STUB_REPO/web/node_modules/.bin/vite"
exit 0
""",
}

_PYTHON_STUB = """#!/bin/sh
echo "python $*" >> "$STUB_CALLS"
[ "${STUB_PYTHON_EXIT:-0}" = "1" ] && exit 3
env > "$STUB_DIR/python.env"
echo $$ > "$STUB_DIR/python.pid"
exec sleep 300
"""

_VITE_STUB = """#!/bin/sh
echo "vite $*" >> "$STUB_CALLS"
env > "$STUB_DIR/vite.env"
echo $$ > "$STUB_DIR/vite.pid"
exec sleep 300
"""

_KILL_LOGGER = """kill() {
  case "$1" in
    -0) ;;
    *) echo "kill $*" >> "$STUB_CALLS" ;;
  esac
  builtin kill "$@"
}
"""


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@dataclass
class Sandbox:
    repo: Path
    stubs: Path
    calls_file: Path
    api_port: int
    ui_port: int
    env: dict[str, str]

    @property
    def calls(self) -> list[str]:
        if not self.calls_file.exists():
            return []
        return self.calls_file.read_text().splitlines()

    def first_call(self, prefix: str) -> int:
        for i, line in enumerate(self.calls):
            if line.startswith(prefix):
                return i
        raise AssertionError(f"no call starting with {prefix!r} in {self.calls}")

    def called(self, prefix: str) -> bool:
        return any(line.startswith(prefix) for line in self.calls)

    def pid(self, name: str) -> int:
        return int((self.stubs / f"{name}.pid").read_text())

    def stub_env(self, name: str) -> dict[str, str]:
        text = (self.stubs / f"{name}.env").read_text()
        return dict(line.split("=", 1) for line in text.splitlines() if "=" in line)


@pytest.fixture
def sandbox(tmp_path: Path) -> Sandbox:
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy(START_SH, repo / "scripts" / "start.sh")
    (repo / "scripts" / "start.sh").chmod(0o755)

    stubs = tmp_path / "stubs"
    stubs.mkdir()
    for name, body in _STUBS.items():
        (stubs / name).write_text(body)
        (stubs / name).chmod(0o755)
    (stubs / "vite-stub").write_text(_VITE_STUB)
    (stubs / "kill-logger.sh").write_text(_KILL_LOGGER)

    vite = repo / "web" / "node_modules" / ".bin" / "vite"
    vite.parent.mkdir(parents=True)
    shutil.copy(stubs / "vite-stub", vite)
    vite.chmod(0o755)

    python = repo / ".venv-314" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text(_PYTHON_STUB)
    python.chmod(0o755)

    calls_file = tmp_path / "calls.log"
    api_port, ui_port = _free_port(), _free_port()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("YTVIDEO_", "STUB_"))}
    env.pop("PYTHON", None)
    env.update(
        PATH=f"{stubs}{os.pathsep}{os.environ['PATH']}",
        BASH_ENV=str(stubs / "kill-logger.sh"),
        STUB_CALLS=str(calls_file),
        STUB_DIR=str(stubs),
        STUB_REPO=str(repo),
        STUB_HEALTH_BODY=SQL_HEALTH,
        STUB_UNAME="Darwin",
        DOCKER_WAIT="1",
        API_WAIT="2",
        API_PORT=str(api_port),
        UI_PORT=str(ui_port),
    )
    return Sandbox(repo, stubs, calls_file, api_port, ui_port, env)


def _default_sigint() -> None:
    """Run in the child before exec.

    A shell ignores (and cannot trap) a signal that was ignored on entry. Background jobs of a
    non-interactive shell (``pytest &``, ``nohup``) start with SIGINT ignored, so restore the default.
    """
    signal.signal(signal.SIGINT, signal.SIG_DFL)


def _start(sb: Sandbox, tmp_path: Path, **extra_env: str) -> subprocess.Popen:
    env = {**sb.env, **extra_env}
    out = open(tmp_path / "out.log", "wb")  # noqa: SIM115 - closed by _stop
    proc = subprocess.Popen(
        ["bash", str(sb.repo / "scripts" / "start.sh")],
        env=env,
        stdout=out,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        preexec_fn=_default_sigint,
        cwd=tmp_path,
    )
    proc._out = out  # type: ignore[attr-defined]
    return proc


def _stop(proc: subprocess.Popen) -> None:
    """Kill the script's whole session (stubs included) so nothing leaks."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    proc.wait(timeout=5)
    proc._out.close()  # type: ignore[attr-defined]


def run_to_exit(sb: Sandbox, tmp_path: Path, timeout: float = 15, **extra_env: str) -> tuple[int, str]:
    """Run the script until it exits on its own; fail the test if it keeps running."""
    proc = _start(sb, tmp_path, **extra_env)
    try:
        try:
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            pytest.fail(f"start.sh did not exit within {timeout}s; calls={sb.calls}")
        return code, (tmp_path / "out.log").read_text()
    finally:
        _stop(proc)


def _wait_for(predicate, timeout: float = 10, what: str = "condition") -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    pytest.fail(f"timed out waiting for {what}")


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


# --------------------------------------------------------------------------- happy path


def test_happy_path_runs_steps_in_order_exports_env_and_stops_cleanly(sandbox, tmp_path):
    proc = _start(sandbox, tmp_path, STUB_COMPOSE_DELAY="0.3")
    try:
        ui_url = f"http://127.0.0.1:{sandbox.ui_port}"
        _wait_for(lambda: ui_url in (tmp_path / "out.log").read_text(), what="UI URL banner")
        _wait_for(lambda: (sandbox.stubs / "vite.pid").exists(), what="vite stub to start")

        order = [
            sandbox.first_call("docker info"),
            sandbox.first_call("docker compose up -d --wait postgres"),
            sandbox.first_call("python -m uvicorn app.main:app"),
            sandbox.first_call("curl "),
            sandbox.first_call("vite "),
        ]
        assert order == sorted(order), f"steps out of order: {sandbox.calls}"
        assert not sandbox.called("open "), "Docker was already up; must not be launched"

        uvicorn_call = next(c for c in sandbox.calls if c.startswith("python -m uvicorn"))
        assert f"--host 127.0.0.1 --port {sandbox.api_port}" in uvicorn_call
        vite_call = next(c for c in sandbox.calls if c.startswith("vite "))
        assert f"--host 127.0.0.1 --port {sandbox.ui_port}" in vite_call
        assert "/health" in next(c for c in sandbox.calls if c.startswith("curl "))

        api_env = sandbox.stub_env("python")
        assert api_env["YTVIDEO_JOB_STORE"] == "sql"
        assert api_env["YTVIDEO_DB_URL"] == DEFAULT_DB_URL
        assert sandbox.stub_env("vite")["YTVIDEO_API_URL"] == f"http://127.0.0.1:{sandbox.api_port}"

        api_pid, ui_pid = sandbox.pid("python"), sandbox.pid("vite")
        assert _pid_alive(api_pid) and _pid_alive(ui_pid)

        proc.send_signal(signal.SIGINT)
        assert proc.wait(timeout=10) == 130

        # Only the two explicit PIDs (and their children) are signalled: never a group kill.
        calls = sandbox.calls
        assert f"pkill -P {api_pid}" in calls
        assert f"pkill -P {ui_pid}" in calls
        kills = [c.split()[1:] for c in calls if c.startswith("kill ")]
        assert kills, "the explicit PIDs were never killed"
        targets = {a for args in kills for a in args if not a.startswith("-")}
        assert targets == {str(api_pid), str(ui_pid)}, f"unexpected kill targets: {kills}"
        assert f"kill {api_pid}" in calls and f"kill {ui_pid}" in calls, f"no plain SIGTERM: {kills}"
        assert not any(c.startswith("kill -9") for c in calls), "processes should stop on SIGTERM, not need SIGKILL"

        # Postgres is left running.
        assert not any(c.startswith("docker compose") and (" stop" in c or " down" in c) for c in calls)
        assert "docker compose stop postgres" in (tmp_path / "out.log").read_text()

        _wait_for(lambda: not _pid_alive(api_pid) and not _pid_alive(ui_pid), what="children to be gone")
        assert _pid_alive(os.getpid())
    finally:
        _stop(proc)


def test_respects_an_already_set_db_url(sandbox, tmp_path):
    custom = "postgresql+asyncpg://me:pw@127.0.0.1:6543/elsewhere"
    proc = _start(sandbox, tmp_path, YTVIDEO_DB_URL=custom)
    try:
        _wait_for(lambda: (sandbox.stubs / "python.pid").exists(), what="API stub start")
        assert sandbox.stub_env("python")["YTVIDEO_DB_URL"] == custom
        assert sandbox.stub_env("python")["YTVIDEO_JOB_STORE"] == "sql"
    finally:
        _stop(proc)


# --------------------------------------------------------------------------- docker


def test_exits_when_docker_is_down_on_linux_without_touching_compose(sandbox, tmp_path):
    code, out = run_to_exit(sandbox, tmp_path, STUB_UNAME="Linux", STUB_DOCKER_INFO_FAIL="1")
    assert code != 0
    assert "docker" in out.lower()
    assert not sandbox.called("docker compose up")
    assert not sandbox.called("open ")
    assert not sandbox.called("python")


def test_darwin_launches_docker_desktop_then_waits_until_it_answers(sandbox, tmp_path):
    proc = _start(sandbox, tmp_path, STUB_DOCKER_INFO_FAIL_COUNT="1", DOCKER_WAIT="10")
    try:
        _wait_for(lambda: sandbox.called("docker compose up"), what="compose up after Docker came up")
        assert sandbox.called("open -ga Docker")
        assert sandbox.first_call("open -ga Docker") < sandbox.first_call("docker compose up")
    finally:
        _stop(proc)


def test_darwin_gives_up_when_docker_never_answers(sandbox, tmp_path):
    code, out = run_to_exit(sandbox, tmp_path, STUB_DOCKER_INFO_FAIL="1")
    assert code != 0
    assert sandbox.called("open -ga Docker")
    assert not sandbox.called("docker compose up")
    assert not sandbox.called("python")


def test_exits_when_compose_up_fails(sandbox, tmp_path):
    code, _ = run_to_exit(sandbox, tmp_path, STUB_COMPOSE_FAIL="1")
    assert code != 0
    assert not sandbox.called("python")


# --------------------------------------------------------------------------- prerequisites


def test_missing_python_fails_with_a_hint(sandbox, tmp_path):
    code, out = run_to_exit(sandbox, tmp_path, PYTHON=str(tmp_path / "no" / "python"))
    assert code != 0
    assert "PYTHON" in out or ".venv-314" in out
    assert not sandbox.called("python")
    assert not sandbox.called("vite")


def test_installs_web_dependencies_when_node_modules_is_missing(sandbox, tmp_path):
    shutil.rmtree(sandbox.repo / "web" / "node_modules")
    proc = _start(sandbox, tmp_path)
    try:
        _wait_for(lambda: sandbox.called("vite "), what="vite start after install")
        assert sandbox.called("pnpm --dir web install")
        assert sandbox.first_call("pnpm --dir web install") < sandbox.first_call("vite ")
    finally:
        _stop(proc)


# --------------------------------------------------------------------------- ports


def test_refuses_a_busy_api_port(sandbox, tmp_path):
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", sandbox.api_port))
        busy.listen()
        code, out = run_to_exit(sandbox, tmp_path)
    assert code != 0
    assert str(sandbox.api_port) in out
    assert not sandbox.called("python")
    assert not sandbox.called("vite")


def test_refuses_a_busy_ui_port_without_starting_the_api(sandbox, tmp_path):
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", sandbox.ui_port))
        busy.listen()
        code, out = run_to_exit(sandbox, tmp_path)
    assert code != 0
    assert str(sandbox.ui_port) in out
    assert not sandbox.called("python")
    assert not sandbox.called("vite")


# --------------------------------------------------------------------------- health


def test_fails_when_the_api_reports_the_memory_store(sandbox, tmp_path):
    code, out = run_to_exit(sandbox, tmp_path, STUB_HEALTH_BODY=MEMORY_HEALTH)
    assert code != 0
    assert "sql" in out
    assert not sandbox.called("vite")
    # The half-started API must not be left running: the script itself stops it
    # (run_to_exit's cleanup kills the whole session, so check the call log).
    api_pid = sandbox.pid("python")
    assert f"kill {api_pid}" in sandbox.calls, f"the script never stopped the API: {sandbox.calls}"
    _wait_for(lambda: not _pid_alive(api_pid), what="API stub to be stopped")


def test_fails_when_the_api_never_becomes_healthy(sandbox, tmp_path):
    code, _ = run_to_exit(sandbox, tmp_path, STUB_CURL_FAIL="1")
    assert code != 0
    assert not sandbox.called("vite")


def test_fails_early_when_the_api_process_dies(sandbox, tmp_path):
    code, out = run_to_exit(sandbox, tmp_path, STUB_PYTHON_EXIT="1", STUB_CURL_FAIL="1", API_WAIT="30", timeout=10)
    assert code != 0
    assert "exited" in out, "must fail on the dead process, not by running out the health timeout"
    assert not sandbox.called("vite")


# --------------------------------------------------------------------------- compose file


def test_compose_pins_project_name_and_binds_postgres_to_loopback():
    text = COMPOSE_FILE.read_text()
    assert "\nname: reelsmith\n" in "\n" + text
    assert '"127.0.0.1:5432:5432"' in text
    assert '"5432:5432"' not in text


def test_compose_mounts_the_test_database_init_script_read_only():
    text = COMPOSE_FILE.read_text()
    assert "./scripts/initdb/10-create-test-db.sql:/docker-entrypoint-initdb.d/10-create-test-db.sql:ro" in text
    assert INITDB_SQL.read_text().strip() == "CREATE DATABASE reelsmith_test;"
