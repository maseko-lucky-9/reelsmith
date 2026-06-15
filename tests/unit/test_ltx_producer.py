"""Unit tests for ltx_producer (generate:// b-roll, Stage 1).

Covers the stub provider (decodable 1080x1920 mp4 of the requested duration),
the import-time torch isolation contract (importing the module must NOT pull in
torch), and the no-silent-fallback contract on the real ``ltx`` provider.
"""
from __future__ import annotations

import json
import subprocess
import sys

import pytest

from app.services import ltx_producer as svc


def _ffprobe(path: str) -> dict:
    result = subprocess.run(
        [
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_format", "-show_streams", path,
        ],
        capture_output=True, text=True,
    )
    return json.loads(result.stdout)


# ── stub provider ─────────────────────────────────────────────────────────────


def test_stub_shot_produces_decodable_1080x1920_mp4(tmp_path):
    out = str(tmp_path / "shot.mp4")
    result = svc.generate_shot("a quiet city street", 2.0, out, provider="stub")
    assert result == out

    info = _ffprobe(out)
    video_streams = [s for s in info["streams"] if s["codec_type"] == "video"]
    assert video_streams, "no video stream in stub mp4"
    stream = video_streams[0]
    assert int(stream["width"]) == 1080
    assert int(stream["height"]) == 1920

    duration = float(info["format"]["duration"])
    assert duration == pytest.approx(2.0, abs=0.25)


def test_stub_unknown_provider_raises(tmp_path):
    with pytest.raises(svc.LtxError):
        svc.generate_shot("p", 1.0, str(tmp_path / "x.mp4"), provider="bogus")


# ── import isolation: importing ltx_producer must NOT import torch ────────────


def test_importing_ltx_producer_does_not_import_torch():
    """A fresh interpreter importing the module must leave torch unimported.

    Run in a subprocess so torch cannot be contaminated into sys.modules by
    another test that imported it earlier in this session.
    """
    code = (
        "import sys; import app.services.ltx_producer; "
        "assert 'torch' not in sys.modules, 'torch was imported at module load'; "
        "print('OK')"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, (
        f"subprocess failed:\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )
    assert "OK" in proc.stdout


# ── no-silent-fallback contract on the real ltx provider ──────────────────────


def test_ltx_provider_never_silently_falls_back(tmp_path, monkeypatch):
    """The ``ltx`` provider must FAIL LOUDLY rather than silently degrade.

    Two shapes of the same contract depending on whether torch is installed:
      * torch importable → monkeypatch mps unavailable + use_mps=True must raise
        (RuntimeError / LtxError), never silently render on CPU.
      * torch NOT importable → the lazy import-guard must raise LtxError, never
        return a stub clip.
    """
    out = str(tmp_path / "shot.mp4")
    try:
        import torch  # noqa: PLC0415
    except ImportError:
        # No torch: the import-guard must raise LtxError (non-silent).
        with pytest.raises(svc.LtxError):
            svc.generate_shot(
                "p", 2.0, out, provider="ltx", model_path="/tmp/model", use_mps=True
            )
        return

    # torch present: force MPS unavailable and assert a loud raise.
    monkeypatch.setattr(torch.backends.mps, "is_available", lambda: False)
    with pytest.raises((RuntimeError, svc.LtxError)):
        svc.generate_shot(
            "p", 2.0, out, provider="ltx", model_path="/tmp/model", use_mps=True
        )
