"""Unit tests for ltx_producer (generate:// b-roll, Stage 2).

Covers the stub provider (decodable 1080x1920 mp4 of the requested duration),
the import-time torch isolation contract (importing the module must NOT pull in
torch — the real ``ltx`` path is a subprocess against the fork's own venv, not
an in-process import), the dim-rounding / num_frames helpers, and the ``ltx``
subprocess branch (success via a monkeypatched ``subprocess.run``, non-zero
return code, and NOT_CONFIGURED).
"""
from __future__ import annotations

import subprocess
import sys

import pytest

from app.services import ltx_producer as svc


# ── stub provider ─────────────────────────────────────────────────────────────


def test_stub_shot_produces_decodable_1080x1920_mp4(tmp_path):
    """Stub renders a decodable 1080x1920 mp4 of the requested length.

    Reads back via MoviePy (imageio-ffmpeg's bundled ffmpeg) so the check works
    in CI where a system ``ffprobe`` is absent.
    """
    from moviepy.editor import VideoFileClip

    out = str(tmp_path / "shot.mp4")
    result = svc.generate_shot("a quiet city street", 2.0, out, provider="stub")
    assert result == out

    clip = VideoFileClip(out)
    try:
        width, height = clip.size  # MoviePy returns [w, h]
        assert int(width) == 1080
        assert int(height) == 1920
        assert clip.duration == pytest.approx(2.0, abs=0.25)
    finally:
        clip.close()


def test_stub_unknown_provider_raises(tmp_path):
    with pytest.raises(svc.LtxError):
        svc.generate_shot("p", 1.0, str(tmp_path / "x.mp4"), provider="bogus")


# ── dimension / frame-count helpers (pure, unit-testable) ─────────────────────


@pytest.mark.parametrize(
    "value,expected",
    [
        (704, 704),  # already a multiple of 32
        (1216, 1216),
        (1080, 1088),  # 1080/32 = 33.75 → 34*32 = 1088
        (1920, 1920),  # 1920/32 = 60 exactly
        (700, 704),  # nearest is 704 (700/32=21.875 → 22)
        (16, 32),  # floored at 32
        (0, 32),
        (-5, 32),
    ],
)
def test_round_to_multiple_of_32(value, expected):
    assert svc.round_to_multiple_of_32(value) == expected
    assert svc.round_to_multiple_of_32(value) % 32 == 0


@pytest.mark.parametrize(
    "target,expected",
    [
        (1, 9),  # clamped to the minimum valid frame count
        (9, 9),
        (10, 9),  # round((10-1)/8)=round(1.125)=1 → 8*1+1 = 9
        (14, 17),  # round((14-1)/8)=round(1.625)=2 → 8*2+1 = 17
        (17, 17),
        (72, 73),  # 24fps * 3s = 72 → round((72-1)/8)=round(8.875)=9 → 73
        (121, 121),  # the fork's default (8*15+1)
        (120, 121),  # round((120-1)/8)=round(14.875)=15 → 121
    ],
)
def test_nearest_valid_num_frames(target, expected):
    result = svc.nearest_valid_num_frames(target)
    # Always a valid LTX frame count.
    assert result % 8 == 1
    assert result >= 9
    assert result == expected


def test_nearest_valid_num_frames_three_seconds_at_24fps():
    """3s * 24fps = 72 frames → must round to a valid 8n+1 value."""
    nf = svc.nearest_valid_num_frames(round(3.0 * 24))
    assert nf % 8 == 1
    assert nf >= 9


# ── import isolation: importing ltx_producer must NOT import torch ────────────


def test_importing_ltx_producer_does_not_import_torch():
    """A fresh interpreter importing the module must leave torch unimported.

    Run in a subprocess so torch cannot be contaminated into sys.modules by
    another test that imported it earlier in this session. The real ``ltx``
    path shells out to the fork's own venv; it never imports torch in-process.
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


# ── ltx subprocess provider ───────────────────────────────────────────────────


def _make_fork(tmp_path):
    """Create dummy fork files (python, inference.py, pipeline yaml) on disk so
    the NOT_CONFIGURED guard passes. Returns the three paths."""
    py = tmp_path / "ltx_python"
    py.write_text("#!/usr/bin/env python\n")
    script = tmp_path / "inference.py"
    script.write_text("# fork inference\n")
    config = tmp_path / "pipeline.yaml"
    config.write_text("checkpoint_path: x\n")
    return str(py), str(script), str(config)


def test_ltx_not_configured_raises_when_paths_unset(tmp_path):
    """Empty fork settings → LtxError(NOT_CONFIGURED), no subprocess attempted."""
    with pytest.raises(svc.LtxError) as exc:
        svc.generate_shot(
            "p", 2.0, str(tmp_path / "shot.mp4"),
            provider="ltx",
            ltx_python="",
            ltx_inference_script="",
            ltx_pipeline_config="",
        )
    assert "not configured" in str(exc.value)


def test_ltx_not_configured_raises_when_files_missing(tmp_path):
    """Paths set but files don't exist → LtxError(NOT_CONFIGURED)."""
    with pytest.raises(svc.LtxError) as exc:
        svc.generate_shot(
            "p", 2.0, str(tmp_path / "shot.mp4"),
            provider="ltx",
            ltx_python=str(tmp_path / "no-python"),
            ltx_inference_script=str(tmp_path / "no-script.py"),
            ltx_pipeline_config=str(tmp_path / "no-config.yaml"),
        )
    assert "not configured" in str(exc.value)


def test_ltx_subprocess_success_copies_newest_mp4(tmp_path, monkeypatch):
    """A rc=0 subprocess that writes an mp4 into --output_path → copied to out.

    ``subprocess.run`` is monkeypatched to emulate the fork: it parses the
    --output_path flag, drops a dummy mp4 there, and returns rc 0. The provider
    must find that mp4 and copy it to ``out_path``.
    """
    py, script, config = _make_fork(tmp_path)
    out_path = str(tmp_path / "dest" / "shot.mp4")
    captured: dict = {}

    def fake_run(cmd, capture_output=True, text=True, timeout=None):
        captured["cmd"] = cmd
        # Locate the --output_path folder and drop a dummy mp4 in it.
        idx = cmd.index("--output_path")
        out_dir = cmd[idx + 1]
        from pathlib import Path as _P

        (_P(out_dir) / "video_output_0_abc_704x704x73_0.mp4").write_bytes(
            b"\x00\x00FAKEMP4BYTES"
        )

        class _R:
            returncode = 0
            stdout = "Output saved to ...\n"
            stderr = ""

        return _R()

    monkeypatch.setattr(svc.subprocess, "run", fake_run)

    result = svc.generate_shot(
        "a calm sunrise",
        3.0,
        out_path,
        provider="ltx",
        ltx_python=py,
        ltx_inference_script=script,
        ltx_pipeline_config=config,
        ltx_height=1216,
        ltx_width=704,
        ltx_frame_rate=24,
        seed=7,
    )

    assert result == out_path
    from pathlib import Path

    assert Path(out_path).is_file()
    assert Path(out_path).read_bytes() == b"\x00\x00FAKEMP4BYTES"

    # Flags map to the fork's HfArgumentParser dataclass fields.
    cmd = captured["cmd"]
    assert cmd[0] == py
    assert cmd[1] == script
    assert "--prompt" in cmd and cmd[cmd.index("--prompt") + 1] == "a calm sunrise"
    assert "--pipeline_config" in cmd
    assert cmd[cmd.index("--pipeline_config") + 1] == config
    assert "--output_path" in cmd
    # 1216 and 704 are already ÷32; assert they pass through as the LTX dims.
    assert cmd[cmd.index("--height") + 1] == "1216"
    assert cmd[cmd.index("--width") + 1] == "704"
    # 3s * 24fps = 72 → nearest 8n+1.
    nf = int(cmd[cmd.index("--num_frames") + 1])
    assert nf % 8 == 1 and nf >= 9
    assert cmd[cmd.index("--frame_rate") + 1] == "24"
    assert cmd[cmd.index("--seed") + 1] == "7"


def test_ltx_subprocess_rounds_dims_to_multiple_of_32(tmp_path, monkeypatch):
    """A non-÷32 request is rounded before being passed to the fork CLI."""
    py, script, config = _make_fork(tmp_path)
    out_path = str(tmp_path / "shot.mp4")
    captured: dict = {}

    def fake_run(cmd, capture_output=True, text=True, timeout=None):
        captured["cmd"] = cmd
        idx = cmd.index("--output_path")
        from pathlib import Path as _P

        (_P(cmd[idx + 1]) / "video_output_0.mp4").write_bytes(b"X")

        class _R:
            returncode = 0
            stdout = ""
            stderr = ""

        return _R()

    monkeypatch.setattr(svc.subprocess, "run", fake_run)

    svc.generate_shot(
        "p", 2.0, out_path,
        provider="ltx",
        ltx_python=py,
        ltx_inference_script=script,
        ltx_pipeline_config=config,
        ltx_height=1920,  # 1920 % 32 == 0 → unchanged
        ltx_width=1080,  # 1080 → 1088
    )

    cmd = captured["cmd"]
    assert cmd[cmd.index("--height") + 1] == "1920"
    assert cmd[cmd.index("--width") + 1] == "1088"


def test_ltx_subprocess_nonzero_returncode_raises_with_stderr(tmp_path, monkeypatch):
    """A non-zero return code surfaces as LtxError carrying the stderr tail."""
    py, script, config = _make_fork(tmp_path)

    def fake_run(cmd, capture_output=True, text=True, timeout=None):
        class _R:
            returncode = 1
            stdout = ""
            stderr = "CUDA out of memory: tried to allocate the world"

        return _R()

    monkeypatch.setattr(svc.subprocess, "run", fake_run)

    with pytest.raises(svc.LtxError) as exc:
        svc.generate_shot(
            "p", 2.0, str(tmp_path / "shot.mp4"),
            provider="ltx",
            ltx_python=py,
            ltx_inference_script=script,
            ltx_pipeline_config=config,
        )
    assert "CUDA out of memory" in str(exc.value)


def test_ltx_subprocess_no_mp4_produced_raises(tmp_path, monkeypatch):
    """rc=0 but no mp4 in the output folder → LtxError, never a silent pass."""
    py, script, config = _make_fork(tmp_path)

    def fake_run(cmd, capture_output=True, text=True, timeout=None):
        class _R:
            returncode = 0
            stdout = "nothing written"
            stderr = ""

        return _R()

    monkeypatch.setattr(svc.subprocess, "run", fake_run)

    with pytest.raises(svc.LtxError) as exc:
        svc.generate_shot(
            "p", 2.0, str(tmp_path / "shot.mp4"),
            provider="ltx",
            ltx_python=py,
            ltx_inference_script=script,
            ltx_pipeline_config=config,
        )
    assert "no mp4" in str(exc.value)


def test_ltx_subprocess_timeout_raises(tmp_path, monkeypatch):
    """A subprocess timeout surfaces as LtxError, not an uncaught exception."""
    py, script, config = _make_fork(tmp_path)

    def fake_run(cmd, capture_output=True, text=True, timeout=None):
        raise subprocess.TimeoutExpired(cmd, timeout or 1)

    monkeypatch.setattr(svc.subprocess, "run", fake_run)

    with pytest.raises(svc.LtxError) as exc:
        svc.generate_shot(
            "p", 2.0, str(tmp_path / "shot.mp4"),
            provider="ltx",
            ltx_python=py,
            ltx_inference_script=script,
            ltx_pipeline_config=config,
        )
    assert "timed out" in str(exc.value)
