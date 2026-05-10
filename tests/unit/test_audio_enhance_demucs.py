"""Unit tests for the W2.4 demucs provider in audio_enhance_service."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services import audio_enhance_service as svc


def test_demucs_argv_default():
    argv = svc.demucs_argv("/in.mp4", "/out_dir")
    assert argv[0] == "demucs"
    assert "-n" in argv and argv[argv.index("-n") + 1] == "htdemucs"
    assert argv[argv.index("-o") + 1] == "/out_dir"
    assert "--two-stems" in argv
    assert argv[argv.index("--two-stems") + 1] == "vocals"
    assert argv[-1] == "/in.mp4"


def test_demucs_argv_no_split():
    argv = svc.demucs_argv("/in.mp4", "/out_dir", two_stems=None)
    assert "--two-stems" not in argv


def test_enhance_demucs_invokes_invoker(tmp_path):
    src = tmp_path / "in.mp4"
    src.write_bytes(b"x")
    captured = []

    def fake(argv):
        captured.append(tuple(argv))

    out = svc.enhance(
        str(src), str(tmp_path / "out_dir"),
        provider="demucs", invoker=fake,
    )
    assert out == str(tmp_path / "out_dir")
    assert captured[0][0] == "demucs"
