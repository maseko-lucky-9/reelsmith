"""Make the CUDA 12 cuBLAS libraries loadable before CTranslate2 needs them (Windows).

The CTranslate2 Windows wheel bundles cuDNN 9 but not cuBLAS. It loads
``cublas64_12.dll`` by plain name at the first GPU decode, and Python 3.8+ no
longer searches ``PATH`` for DLLs. ``pip install nvidia-cublas-cu12`` puts them
under ``site-packages/nvidia/cublas/bin``, which is on no search path either.
Without them the first decode raises ``RuntimeError: Library cublas64_12.dll is
not found or cannot be loaded`` and Whisper runs on the CPU
(``transcription_service`` falls back to it).

Loading the DLLs by full path first registers them in the process, so the later
by-name load finds them. Nothing here touches ``PATH`` or any DLL directory.
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys
import sysconfig
import threading
from collections.abc import Callable, Iterable
from pathlib import Path

log = logging.getLogger(__name__)

# cuBLAS depends on cuBLASLt, so Lt loads first.
CUBLAS_LIBS = ("cublasLt64_12.dll", "cublas64_12.dll")

_lock = threading.Lock()
_attempted = False
_loaded_from: Path | None = None


def candidate_dirs() -> list[Path]:
    """Where cuBLAS 12 may live, best first: the pip wheel, a CUDA toolkit, ``PATH``."""
    dirs = [Path(sysconfig.get_paths()[key]) / "nvidia" / "cublas" / "bin" for key in ("purelib", "platlib")]
    cuda_path = os.environ.get("CUDA_PATH")
    if cuda_path:
        dirs += [Path(cuda_path) / "bin", Path(cuda_path) / "bin" / "x64"]
    dirs += [Path(entry) for entry in os.environ.get("PATH", "").split(os.pathsep) if entry]
    return list(dict.fromkeys(dirs))


def find_cublas(dirs: Iterable[Path] | None = None) -> Path | None:
    """The first directory in ``dirs`` (default ``candidate_dirs()``) holding every cuBLAS 12 DLL."""
    for folder in candidate_dirs() if dirs is None else dirs:
        if all((folder / lib).is_file() for lib in CUBLAS_LIBS):
            return folder
    return None


def preload_cublas(
    *,
    platform: str = sys.platform,
    load: Callable[[str], object] | None = None,
    dirs: Iterable[Path] | None = None,
) -> Path | None:
    """Load cuBLAS 12 by full path; the directory it came from, else ``None``.

    ``None`` off Windows, when no copy is found, or when the load fails; the
    caller then simply has no GPU decoding (it falls back to the CPU).
    ``load`` and ``dirs`` exist for tests.
    """
    if platform != "win32":
        return None
    folder = find_cublas(dirs)
    if folder is None:
        return None
    loader = load or ctypes.CDLL
    try:
        for lib in CUBLAS_LIBS:
            loader(str(folder / lib))
    except OSError as exc:
        log.warning("Found cuBLAS 12 in %s but could not load it: %s", folder, exc)
        return None
    return folder


def ensure_cublas() -> Path | None:
    """``preload_cublas`` once per process; the directory it loaded from, else ``None``."""
    global _attempted, _loaded_from
    with _lock:
        if not _attempted:
            _attempted = True
            _loaded_from = preload_cublas()
            if _loaded_from is not None:
                log.info("cuBLAS 12 preloaded from %s", _loaded_from)
            elif sys.platform == "win32":
                log.info("cuBLAS 12 not found (pip install nvidia-cublas-cu12); GPU decoding will fall back to the CPU")
    return _loaded_from
