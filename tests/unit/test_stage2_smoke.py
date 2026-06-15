"""Unit tests for the Stage 2 gate tooling (CI-safe).

These cover the operator smoke scripts without torch, LTX weights, or a live
Voicebox sidecar. The contract under test:

  * importing either script must NOT import torch,
  * the NOT_CONFIGURED path returns exit code 2 (no model / no endpoint),
  * a down health endpoint returns exit code 1 (Gate B),
  * the pure black-frame helper distinguishes all-black from bright frames.

No real model is ever run.
"""
from __future__ import annotations

import subprocess
import sys

import numpy as np

from scripts import ltx_smoke, voicebox_smoke


# ── Gate A: NOT_CONFIGURED path ───────────────────────────────────────────────


def test_ltx_smoke_empty_model_path_returns_not_configured():
    """An explicit empty --model-path short-circuits to exit 2."""
    assert ltx_smoke.main(["--model-path", ""]) == ltx_smoke.EXIT_NOT_CONFIGURED


def test_ltx_smoke_unconfigured_settings_returns_not_configured(monkeypatch):
    """With settings' ltx_model_path empty, the gate exits 2 (no overrides)."""
    from app.settings import settings as app_settings

    monkeypatch.setattr(app_settings, "ltx_model_path", "", raising=False)
    assert ltx_smoke.main([]) == ltx_smoke.EXIT_NOT_CONFIGURED


def test_ltx_smoke_nonexistent_weights_returns_not_configured(tmp_path):
    """A model path that does not exist on disk is NOT_CONFIGURED, not a crash."""
    missing = str(tmp_path / "no-such-weights")
    assert ltx_smoke.main(["--model-path", missing]) == ltx_smoke.EXIT_NOT_CONFIGURED


# ── Gate A: black-frame helper (pure, unit-testable) ──────────────────────────


def test_is_mostly_black_all_black_frames_true():
    frames = [np.zeros((8, 8, 3), dtype="uint8") for _ in range(5)]
    assert ltx_smoke.is_mostly_black(frames) is True


def test_is_mostly_black_one_bright_frame_false():
    frames = [np.zeros((8, 8, 3), dtype="uint8") for _ in range(4)]
    frames.append(np.full((8, 8, 3), 255, dtype="uint8"))  # one bright frame
    assert ltx_smoke.is_mostly_black(frames) is False


def test_is_mostly_black_empty_sequence_is_black():
    """No frames sampled is treated as black (fail-closed)."""
    assert ltx_smoke.is_mostly_black([]) is True


def test_is_mostly_black_threshold_boundary():
    """A frame whose mean equals the threshold is NOT considered black."""
    frame = np.full((4, 4, 3), 8, dtype="uint8")  # mean == 8.0 == threshold
    assert ltx_smoke.is_mostly_black([frame], threshold=8.0) is False
    # Just below the threshold → black.
    dark = np.full((4, 4, 3), 7, dtype="uint8")
    assert ltx_smoke.is_mostly_black([dark], threshold=8.0) is True


# ── Gate B: NOT_CONFIGURED + health-fail paths ────────────────────────────────


def test_voicebox_smoke_empty_endpoint_returns_not_configured():
    assert (
        voicebox_smoke.main(["--endpoint", ""])
        == voicebox_smoke.EXIT_NOT_CONFIGURED
    )


def test_voicebox_smoke_unconfigured_settings_returns_not_configured(monkeypatch):
    from app.settings import settings as app_settings

    monkeypatch.setattr(app_settings, "voicebox_endpoint", "", raising=False)
    assert voicebox_smoke.main([]) == voicebox_smoke.EXIT_NOT_CONFIGURED


def test_voicebox_smoke_health_non_200_returns_fail():
    """An endpoint set but health probe returning non-200 → exit 1.

    The health probe is injected so no real network call happens.
    """
    def failing_probe(url, api_key, timeout):
        return 503

    rc = voicebox_smoke.main(
        ["--endpoint", "http://example.test/synthesize"],
        health_probe=failing_probe,
    )
    assert rc == voicebox_smoke.EXIT_FAIL


def test_voicebox_smoke_health_probe_transport_error_returns_fail():
    """A health probe that raises (connection refused) → exit 1, not a crash."""
    def boom_probe(url, api_key, timeout):
        raise ConnectionError("connection refused")

    rc = voicebox_smoke.main(
        ["--endpoint", "http://example.test/synthesize"],
        health_probe=boom_probe,
    )
    assert rc == voicebox_smoke.EXIT_FAIL


def test_voicebox_smoke_happy_path_with_injected_seams(tmp_path):
    """Health ok + a valid synthesized WAV via injected invoker → exit 0."""
    import struct

    def ok_probe(url, api_key, timeout):
        return 200

    def fake_invoker(endpoint, api_key, payload):
        # Minimal 16-bit mono WAV: header + a few non-zero samples.
        sample_rate = 24000
        samples = sample_rate // 10  # 0.1s
        data = b"\x01\x00" * samples
        data_size = len(data)
        header = (
            b"RIFF"
            + struct.pack("<I", 36 + data_size)
            + b"WAVEfmt "
            + struct.pack("<IHHIIHH", 16, 1, 1, sample_rate, sample_rate * 2, 2, 16)
            + b"data"
            + struct.pack("<I", data_size)
        )
        return header + data

    out = str(tmp_path / "smoke.wav")
    rc = voicebox_smoke.main(
        ["--endpoint", "http://example.test/synthesize", "--out", out],
        health_probe=ok_probe,
        invoker=fake_invoker,
    )
    assert rc == voicebox_smoke.EXIT_PASS


# ── Gate B: pure health-URL derivation ────────────────────────────────────────


def test_health_url_for_replaces_synthesize_suffix():
    assert (
        voicebox_smoke.health_url_for("http://h:8080/synthesize")
        == "http://h:8080/health"
    )


def test_health_url_for_appends_health_when_no_suffix():
    assert voicebox_smoke.health_url_for("http://h:8080") == "http://h:8080/health"
    assert voicebox_smoke.health_url_for("http://h:8080/") == "http://h:8080/health"


# ── Import isolation: neither script may import torch at module load ──────────


def test_importing_smoke_scripts_does_not_import_torch():
    """A fresh interpreter importing both scripts must leave torch unimported."""
    code = (
        "import sys; import scripts.ltx_smoke, scripts.voicebox_smoke; "
        "assert 'torch' not in sys.modules, 'torch imported at module load'; "
        "print('OK')"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, (
        f"subprocess failed:\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )
    assert "OK" in proc.stdout


def test_torch_not_in_sys_modules_after_import():
    """In-process guard: importing the modules here didn't pull in torch."""
    assert "torch" not in sys.modules
