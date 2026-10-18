"""Whisper device selection and the CPU fallback for an unusable CUDA runtime.

faster-whisper loads the model fine on a host whose GPU lacks the CUDA 12
libraries (``cublas64_12.dll``) and only fails on the first decode, as a
``RuntimeError`` from the lazy segment iterator. So the fallback lives in the
decode path: with ``whisper_device="auto"`` the model is reloaded on the CPU
and the decode retried once.
"""

from __future__ import annotations

from typing import Any

import faster_whisper
import pytest

from app.services import transcription_service as ts
from app.settings import Settings
from tests.unit.fake_whisper import FakeWhisperModel, install

CUBLAS_ERROR = "Library cublas64_12.dll is not found or cannot be loaded"


class DeviceFactory:
    """``WhisperModel`` stand-in: the CPU gets a working model, anything else ``gpu``."""

    def __init__(self, gpu: FakeWhisperModel, cpu: FakeWhisperModel) -> None:
        self.gpu = gpu
        self.cpu = cpu
        self.devices: list[str] = []

    def __call__(self, *args: Any, **kwargs: Any) -> FakeWhisperModel:
        device = kwargs.get("device", "auto")
        self.devices.append(device)
        return self.cpu if device == "cpu" else self.gpu


@pytest.fixture
def devices(monkeypatch: pytest.MonkeyPatch) -> DeviceFactory:
    install(monkeypatch)
    factory = DeviceFactory(
        gpu=FakeWhisperModel(fail_with=RuntimeError(CUBLAS_ERROR)),
        cpu=FakeWhisperModel(),
    )
    monkeypatch.setattr(faster_whisper, "WhisperModel", factory)
    return factory


# ── Setting ──────────────────────────────────────────────────────────────────


def test_device_defaults_to_auto(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("YTVIDEO_WHISPER_DEVICE", raising=False)

    assert Settings(_env_file=None).whisper_device == "auto"


def test_device_reads_ytvideo_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YTVIDEO_WHISPER_DEVICE", "cpu")

    assert Settings(_env_file=None).whisper_device == "cpu"


def test_the_configured_device_is_passed_to_the_model(devices: DeviceFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ts.settings, "whisper_device", "cpu")

    ts.transcribe_to_words("a.wav")

    assert devices.devices == ["cpu"]


# ── Fallback ─────────────────────────────────────────────────────────────────


def test_unusable_cuda_falls_back_to_cpu_and_returns_the_transcript(
    devices: DeviceFactory,
) -> None:
    words = ts.transcribe_to_words("a.wav")

    assert [w.word for w in words] == ["w0", "w1"]
    assert devices.devices == ["auto", "cpu"]


def test_later_calls_stay_on_cpu_without_retrying_cuda(devices: DeviceFactory) -> None:
    ts.transcribe_to_words("a.wav")
    ts.transcribe_to_words("b.wav")

    assert devices.devices == ["auto", "cpu"]
    assert len(devices.gpu.transcribe_calls) == 1
    assert len(devices.cpu.transcribe_calls) == 2


def test_repeatable_decode_reuses_the_cpu_model(devices: DeviceFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    # A decode loop makes a repeatable call decode a second time.
    devices.cpu.outputs = [["loop"] * 60, ["fine"]]
    monkeypatch.setattr(ts, "has_decode_loop", lambda words: len(words) > 1)

    words = ts.transcribe_to_words("a.wav", repeatable=True)

    assert [w.word for w in words] == ["fine"]
    assert devices.devices == ["auto", "cpu"]
    assert len(devices.gpu.transcribe_calls) == 1


def test_explicit_cuda_does_not_fall_back(devices: DeviceFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ts.settings, "whisper_device", "cuda")

    with pytest.raises(RuntimeError, match="cublas64_12"):
        ts.transcribe_to_words("a.wav")

    assert devices.devices == ["cuda"]


def test_a_cpu_failure_is_not_retried(devices: DeviceFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ts.settings, "whisper_device", "cpu")
    devices.cpu.fail_with = RuntimeError(CUBLAS_ERROR)

    with pytest.raises(RuntimeError, match="cublas64_12"):
        ts.transcribe_to_words("a.wav")

    assert devices.devices == ["cpu"]


def test_an_unrelated_runtime_error_is_not_swallowed(devices: DeviceFactory) -> None:
    devices.gpu.fail_with = RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        ts.transcribe_to_words("a.wav")

    assert devices.devices == ["auto"]


@pytest.mark.parametrize(
    "message",
    [
        CUBLAS_ERROR,
        "Library cudnn_ops64_9.dll is not found or cannot be loaded",
        "CUDA driver version is insufficient for CUDA runtime version",
    ],
)
def test_cuda_runtime_errors_are_recognised(message: str) -> None:
    assert ts._is_cuda_runtime_error(RuntimeError(message))


@pytest.mark.parametrize("error", [RuntimeError("boom"), ValueError("cublas"), ts.TranscriptionCancelled("cancelled")])
def test_other_errors_are_not_cuda_runtime_errors(error: Exception) -> None:
    assert not ts._is_cuda_runtime_error(error)
