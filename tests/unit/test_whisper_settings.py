"""Whisper settings: env overrides, and a test environment that never loads a model."""

from __future__ import annotations

import pytest

from app.settings import Settings, settings


def test_whisper_decode_settings_read_ytvideo_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("YTVIDEO_WHISPER_BEAM_SIZE", "4")
    monkeypatch.setenv("YTVIDEO_WHISPER_VAD_FILTER", "false")
    monkeypatch.setenv("YTVIDEO_WHISPER_CPU_THREADS", "2")
    monkeypatch.setenv("YTVIDEO_WHISPER_WARMUP", "false")

    loaded = Settings(_env_file=None)

    assert loaded.whisper_beam_size == 4
    assert loaded.whisper_vad_filter is False
    assert loaded.whisper_cpu_threads == 2
    assert loaded.whisper_warmup is False


def test_whisper_decode_defaults_come_from_the_bench(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("BEAM_SIZE", "VAD_FILTER", "CPU_THREADS", "WARMUP"):
        monkeypatch.delenv(f"YTVIDEO_WHISPER_{name}", raising=False)

    loaded = Settings(_env_file=None)

    assert loaded.whisper_beam_size == 1
    assert loaded.whisper_vad_filter is True
    assert loaded.whisper_cpu_threads == 8
    assert loaded.whisper_warmup is True


def test_default_test_run_never_loads_a_real_model() -> None:
    """Root conftest pins the stub provider and no warm-up, whatever .env says."""
    assert settings.transcription_provider == "stub"
    assert settings.whisper_warmup is False
