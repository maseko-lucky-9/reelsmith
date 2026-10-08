"""Architecture invariant: MoviePy is gone (perf P1).

Rendering runs on the bundled imageio-ffmpeg binary + PyAV. Nothing under
app/ or scripts/ may import moviepy (or the deleted ``app.compat`` shim that
existed only for it), requirements.txt must not pin it, and the app must
import with moviepy unavailable.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCANNED = ("app", "scripts")

_MOVIEPY_RE = re.compile(r"^\s*(import\s+moviepy|from\s+moviepy)", re.MULTILINE)
_COMPAT_RE = re.compile(
    r"^\s*(import\s+app\.compat|from\s+app\s+import\s+compat)", re.MULTILINE
)


def _python_files():
    for top in SCANNED:
        yield from (REPO_ROOT / top).rglob("*.py")


def test_app_and_scripts_do_not_import_moviepy():
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in _python_files()
        if _MOVIEPY_RE.search(path.read_text(encoding="utf-8"))
    ]
    assert not offenders, f"moviepy imports found: {offenders}"


def test_compat_shim_is_gone():
    assert not (REPO_ROOT / "app" / "compat.py").exists()
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in [*_python_files(), REPO_ROOT / "conftest.py"]
        if _COMPAT_RE.search(path.read_text(encoding="utf-8"))
    ]
    assert not offenders, f"app.compat imports found: {offenders}"


def test_requirements_do_not_pin_moviepy_and_declare_ffmpeg_stack():
    reqs = (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
    names = {
        re.split(r"[=<>\[ ]", line.strip(), maxsplit=1)[0]
        for line in reqs.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert "moviepy" not in names
    assert {"imageio-ffmpeg", "av"} <= names


def test_app_imports_with_moviepy_blocked():
    """``sys.modules['moviepy'] = None`` makes any moviepy import raise."""
    code = (
        "import sys; sys.modules['moviepy'] = None; sys.modules['moviepy.editor'] = None\n"
        "import app.main, app.workers.orchestrator\n"
        "from app.services import render_service, thumbnail_service, ltx_producer\n"
        "from app.services.platforms import generate, upload\n"
        "import scripts.ltx_smoke, scripts.bench\n"
        "print('ok')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert proc.stdout.strip().endswith("ok")
