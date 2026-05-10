"""Audio enhancement (W1.8).

Provider-pluggable per the existing transcription/reframe pattern.
``YTVIDEO_AUDIO_ENHANCE_PROVIDER`` selects:

* ``loudnorm``  (default) — ffmpeg EBU R128 two-pass loudness normalisation.
* ``rnnoise``  — ffmpeg + ``arnndn`` for spectral noise reduction; chained
  through loudnorm afterwards.
* ``passthrough`` — copies input to output; the deterministic stub used by
  CI / dev when no ffmpeg is available.

Each provider builds an ffmpeg argv as a tuple of strings; tests assert
on the argv shape, never on the rendered audio. The actual subprocess
call happens through ``_invoke`` which is patched out in unit tests.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Iterable, Sequence

log = logging.getLogger(__name__)


class AudioEnhanceError(RuntimeError):
    pass


# ── argv builders (pure functions; testable in isolation) ────────────────────


def loudnorm_argv(in_path: str, out_path: str) -> tuple[str, ...]:
    """ffmpeg EBU R128 single-pass (good enough for short clips)."""
    return (
        "ffmpeg", "-y", "-i", in_path,
        "-af",
        "loudnorm=I=-16:TP=-1.5:LRA=11",
        "-c:v", "copy",
        out_path,
    )


def rnnoise_argv(in_path: str, out_path: str, *, model_path: str | None = None) -> tuple[str, ...]:
    """RNNoise via ffmpeg's arnndn filter, chained into loudnorm."""
    af = (
        f"arnndn=m={model_path}," if model_path
        else "arnndn,"
    ) + "loudnorm=I=-16:TP=-1.5:LRA=11"
    return (
        "ffmpeg", "-y", "-i", in_path,
        "-af", af,
        "-c:v", "copy",
        out_path,
    )


def demucs_argv(
    in_path: str, out_dir: str, *, model: str = "htdemucs", two_stems: str | None = "vocals"
) -> tuple[str, ...]:
    """demucs source-separation argv (W2.4 — opt-in heavy path).

    With ``two_stems='vocals'`` demucs outputs vocals.wav + no_vocals.wav
    under ``out_dir/<model>/<basename>/``.
    """
    argv: list[str] = ["demucs", "-n", model, "-o", out_dir]
    if two_stems:
        argv.extend(["--two-stems", two_stems])
    argv.append(in_path)
    return tuple(argv)


# ── Public surface ───────────────────────────────────────────────────────────


def enhance(
    in_path: str,
    out_path: str,
    *,
    provider: str = "loudnorm",
    model_path: str | None = None,
    invoker: callable = None,  # type: ignore[assignment]
) -> str:
    """Apply ``provider`` to ``in_path`` -> ``out_path``. Returns ``out_path``.

    ``invoker`` is the callable that actually runs the argv; production
    uses ``_invoke``, tests inject a recorder.
    """
    if not Path(in_path).is_file():
        raise FileNotFoundError(f"audio in not found: {in_path}")

    if provider == "passthrough":
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(in_path, out_path)
        return out_path

    if provider == "loudnorm":
        argv = loudnorm_argv(in_path, out_path)
    elif provider == "rnnoise":
        argv = rnnoise_argv(in_path, out_path, model_path=model_path)
    elif provider == "demucs":
        # demucs writes to a directory; treat out_path as the dir for this provider.
        argv = demucs_argv(in_path, out_path)
    else:
        raise AudioEnhanceError(f"unknown audio enhance provider: {provider!r}")

    invoke = invoker or _invoke
    invoke(argv)
    return out_path


def _invoke(argv: Sequence[str]) -> None:
    log.info("audio_enhance: %s", " ".join(argv))
    proc = subprocess.run(argv, capture_output=True)
    if proc.returncode != 0:
        raise AudioEnhanceError(
            f"ffmpeg failed (rc={proc.returncode}): "
            f"{proc.stderr.decode('utf-8', errors='replace')[-500:]}"
        )
