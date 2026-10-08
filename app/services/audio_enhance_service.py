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
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, Sequence

from app.domain.events import EventType, emit_from_sync
from app.services import ffmpeg_tools

if TYPE_CHECKING:  # pragma: no cover - import only for typing
    from app.bus.event_bus import AsyncEventBus

log = logging.getLogger(__name__)


class AudioEnhanceError(RuntimeError):
    pass


# ── argv builders (pure functions; testable in isolation) ────────────────────


# Whisper's native input. loudnorm resamples to 192 kHz internally and writes
# that rate unless told otherwise; transcription only needs 16 kHz mono.
TRANSCRIPTION_OUTPUT_ARGS = ("-ac", "1", "-ar", "16000")


def loudnorm_argv(
    in_path: str, out_path: str, *, for_transcription: bool = False
) -> tuple[str, ...]:
    """ffmpeg EBU R128 single-pass (good enough for short clips).

    ``for_transcription`` writes 16 kHz mono (Whisper input) instead of
    keeping the source's channels and loudnorm's 192 kHz output rate.
    """
    return (
        "ffmpeg", "-y", "-i", in_path,
        "-af",
        "loudnorm=I=-16:TP=-1.5:LRA=11",
        "-c:v", "copy",
        *(TRANSCRIPTION_OUTPUT_ARGS if for_transcription else ()),
        out_path,
    )


def rnnoise_argv(
    in_path: str,
    out_path: str,
    *,
    model_path: str | None = None,
    for_transcription: bool = False,
) -> tuple[str, ...]:
    """RNNoise via ffmpeg's arnndn filter, chained into loudnorm."""
    af = (
        f"arnndn=m={model_path}," if model_path
        else "arnndn,"
    ) + "loudnorm=I=-16:TP=-1.5:LRA=11"
    return (
        "ffmpeg", "-y", "-i", in_path,
        "-af", af,
        "-c:v", "copy",
        *(TRANSCRIPTION_OUTPUT_ARGS if for_transcription else ()),
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
    bus: "AsyncEventBus | None" = None,
    job_id: str | None = None,
    for_transcription: bool = False,
) -> str:
    """Apply ``provider`` to ``in_path`` -> ``out_path``. Returns ``out_path``.

    ``for_transcription`` (loudnorm / rnnoise) writes 16 kHz mono for Whisper;
    the orchestrator sets it because the enhanced track only feeds
    transcription — the reel keeps the source's original audio.

    ``invoker`` is the callable that actually runs the argv; production
    uses ``_invoke``, tests inject a recorder.

    ``bus`` + ``job_id`` are optional. When both are provided AND the
    caller is on the asyncio event-loop thread, an ``AUDIO_ENHANCED``
    event is scheduled. Sync callers invoked via ``asyncio.to_thread``
    (e.g. the orchestrator) won't see the emit — that caller emits
    separately. Legacy callers (no bus) are unaffected.
    """
    if not Path(in_path).is_file():
        raise FileNotFoundError(f"audio in not found: {in_path}")

    if provider == "passthrough":
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(in_path, out_path)
        emit_from_sync(
            bus, job_id, EventType.AUDIO_ENHANCED,
            {"provider": provider, "input": in_path, "output": out_path},
        )
        return out_path

    if provider == "loudnorm":
        argv = loudnorm_argv(in_path, out_path, for_transcription=for_transcription)
    elif provider == "rnnoise":
        argv = rnnoise_argv(
            in_path, out_path, model_path=model_path,
            for_transcription=for_transcription,
        )
    elif provider == "demucs":
        # demucs writes to a directory; treat out_path as the dir for this provider.
        argv = demucs_argv(in_path, out_path)
    else:
        raise AudioEnhanceError(f"unknown audio enhance provider: {provider!r}")

    invoke = invoker or _invoke
    invoke(argv)
    emit_from_sync(
        bus, job_id, EventType.AUDIO_ENHANCED,
        {"provider": provider, "input": in_path, "output": out_path},
    )
    return out_path


def _invoke(argv: Sequence[str]) -> None:
    """Run ``argv`` via ``ffmpeg_tools.run`` (bare ``ffmpeg`` → bundled binary)."""
    log.info("audio_enhance: %s", " ".join(argv))
    try:
        ffmpeg_tools.run(argv)
    except ffmpeg_tools.FfmpegError as e:
        raise AudioEnhanceError(
            f"ffmpeg failed (rc={e.returncode}): {e.stderr_tail[-500:]}"
        ) from e
