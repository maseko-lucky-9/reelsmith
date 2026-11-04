"""cuBLAS 12 preload for CTranslate2's GPU path on Windows (app/services/cuda_runtime.py)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app.services import cuda_runtime as cr
from app.services import transcription_service as ts
from tests.unit.fake_whisper import install


def _make_dir(root: Path, name: str, libs: tuple[str, ...] = cr.CUBLAS_LIBS) -> Path:
    folder = root / name
    folder.mkdir()
    for lib in libs:
        (folder / lib).write_bytes(b"")
    return folder


class RecordingLoader:
    def __init__(self, fail_on: str | None = None) -> None:
        self.loaded: list[str] = []
        self.fail_on = fail_on

    def __call__(self, path: str) -> object:
        if self.fail_on and path.endswith(self.fail_on):
            raise OSError("cannot load")
        self.loaded.append(path)
        return object()


# ── Finding the DLLs ─────────────────────────────────────────────────────────


def test_find_cublas_returns_the_first_directory_with_every_dll(tmp_path: Path) -> None:
    partial = _make_dir(tmp_path, "partial", ("cublas64_12.dll",))
    first = _make_dir(tmp_path, "first")
    second = _make_dir(tmp_path, "second")

    assert cr.find_cublas([partial, first, second]) == first


def test_find_cublas_is_none_when_no_directory_has_them(tmp_path: Path) -> None:
    assert cr.find_cublas([_make_dir(tmp_path, "partial", ("cublasLt64_12.dll",)), tmp_path / "missing"]) is None


def test_candidates_start_with_the_pip_wheel_then_cuda_path_then_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CUDA_PATH", str(tmp_path / "cuda"))
    monkeypatch.setenv("PATH", str(tmp_path / "onpath"))

    dirs = cr.candidate_dirs()

    assert dirs[0].parts[-3:] == ("nvidia", "cublas", "bin")
    assert dirs.index(tmp_path / "cuda" / "bin") < dirs.index(tmp_path / "cuda" / "bin" / "x64")
    assert dirs.index(tmp_path / "cuda" / "bin" / "x64") < dirs.index(tmp_path / "onpath")
    assert len(dirs) == len(set(dirs))


# ── Preloading ───────────────────────────────────────────────────────────────


def test_preload_loads_cublaslt_before_cublas_by_full_path(tmp_path: Path) -> None:
    folder = _make_dir(tmp_path, "wheel")
    loader = RecordingLoader()

    result = cr.preload_cublas(platform="win32", load=loader, dirs=[folder])

    assert result == folder
    assert loader.loaded == [str(folder / "cublasLt64_12.dll"), str(folder / "cublas64_12.dll")]


def test_preload_does_nothing_off_windows(tmp_path: Path) -> None:
    loader = RecordingLoader()

    assert cr.preload_cublas(platform="linux", load=loader, dirs=[_make_dir(tmp_path, "wheel")]) is None
    assert loader.loaded == []


def test_preload_without_the_dlls_loads_nothing(tmp_path: Path) -> None:
    loader = RecordingLoader()

    assert cr.preload_cublas(platform="win32", load=loader, dirs=[tmp_path]) is None
    assert loader.loaded == []


def test_a_failing_load_is_reported_as_not_preloaded_not_raised(tmp_path: Path) -> None:
    result = cr.preload_cublas(
        platform="win32", load=RecordingLoader(fail_on="cublas64_12.dll"), dirs=[_make_dir(tmp_path, "wheel")]
    )

    assert result is None


def test_ensure_cublas_attempts_once_per_process(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[int] = []
    monkeypatch.setattr(cr, "_attempted", False)
    monkeypatch.setattr(cr, "_loaded_from", None)
    monkeypatch.setattr(cr, "preload_cublas", lambda: calls.append(1) or tmp_path)

    assert cr.ensure_cublas() == tmp_path
    assert cr.ensure_cublas() == tmp_path
    assert calls == [1]


# ── Wired into the model load ────────────────────────────────────────────────


@pytest.fixture
def ensure_calls(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    install(monkeypatch)
    calls: list[int] = []
    monkeypatch.setattr(cr, "ensure_cublas", lambda: calls.append(1))
    return calls


@pytest.mark.parametrize("device", ["auto", "cuda"])
def test_a_gpu_capable_device_preloads_cublas_before_the_model_loads(
    ensure_calls: list[int], monkeypatch: pytest.MonkeyPatch, device: str
) -> None:
    monkeypatch.setattr(ts.settings, "whisper_device", device)

    ts._get_model()

    assert ensure_calls == [1]


def test_the_cpu_device_does_not_touch_cuda(ensure_calls: list[int], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ts.settings, "whisper_device", "cpu")

    ts._get_model()

    assert ensure_calls == []


def test_after_the_cpu_fallback_cuda_is_left_alone(ensure_calls: list[int], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ts, "_cpu_fallback", True)

    ts._get_model()

    assert ensure_calls == []


# ── The real wheel (Windows installs get it from requirements.txt) ───────────


@pytest.mark.skipif(sys.platform != "win32", reason="cuBLAS wheel is Windows-only")
def test_the_installed_wheel_is_found_and_loads() -> None:
    folder = cr.find_cublas()

    assert folder is not None, "pip install nvidia-cublas-cu12 (requirements.txt)"
    assert cr.preload_cublas() == folder
