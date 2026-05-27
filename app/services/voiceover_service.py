"""AI Voice-over service (W2.3).

Provider-pluggable. Coqui XTTS v2 is the default live path (CPML
non-commercial — fine for the portfolio scope of this repo per
ADR-003 §A.13). Stub provider writes a deterministic WAV header so
CI and dev hosts without the model still exercise the pipeline.

Behind ``YTVIDEO_VOICEOVER_PROVIDER``:
    coqui      | piper | stub  (default ``stub``)

The Coqui path is opt-in via the ``voiceover`` compose profile and
expects the model already pulled to ``YTVIDEO_COQUI_MODEL``.

The Piper path requires:
    - ``piper`` binary on PATH (YTVIDEO_PIPER_MODEL must be set)
    - ``YTVIDEO_PIPER_MODEL`` env var pointing to the .onnx model file
    - Text is fed on stdin; argv is ``["piper", "--model", <path>, "--output_file", <out>]``
"""
from __future__ import annotations

import logging
import os
import shutil
import struct
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Sequence

from app.domain.events import EventType, emit_from_sync

if TYPE_CHECKING:  # pragma: no cover - import only for typing
    from app.bus.event_bus import AsyncEventBus

log = logging.getLogger(__name__)


class VoiceoverError(RuntimeError):
    pass


def _wav_header(num_samples: int, sample_rate: int = 24000) -> bytes:
    """Minimal 16-bit mono WAV header so the file is technically playable."""
    byte_rate = sample_rate * 2
    block_align = 2
    data_size = num_samples * 2
    return (
        b"RIFF"
        + struct.pack("<I", 36 + data_size)
        + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, sample_rate, byte_rate, block_align, 16)
        + b"data"
        + struct.pack("<I", data_size)
    )


def _stub_synth(text: str, out_path: str) -> str:
    """Write a deterministic empty WAV (1 second of silence)."""
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    sample_rate = 24000
    samples = sample_rate
    body = b"\x00\x00" * samples
    Path(out_path).write_bytes(_wav_header(samples, sample_rate) + body)
    return out_path


def _coqui_argv(
    text: str, out_path: str, *, model: str, voice: str | None
) -> tuple[str, ...]:
    """Build the argv for the ``tts`` CLI shipped with Coqui XTTS."""
    argv: list[str] = [
        "tts",
        "--text", text,
        "--model_name", model,
        "--out_path", out_path,
    ]
    if voice:
        argv.extend(["--speaker_idx", voice])
    return tuple(argv)


def _piper_argv(out_path: str, *, model: str) -> tuple[str, ...]:
    """Build the argv for the ``piper`` TTS CLI.

    Text is fed via stdin; the binary reads stdin and writes a WAV to
    ``--output_file``.  Model path comes from ``YTVIDEO_PIPER_MODEL``.
    """
    return ("piper", "--model", model, "--output_file", out_path)


def synthesize(
    text: str,
    out_path: str,
    *,
    provider: str = "stub",
    model: str = "tts_models/multilingual/multi-dataset/xtts_v2",
    voice: str | None = None,
    invoker: Callable[[Sequence[str], str], None] | None = None,
    bus: "AsyncEventBus | None" = None,
    job_id: str | None = None,
) -> str:
    """Render ``text`` to a WAV at ``out_path``. Returns the path.

    For the ``piper`` provider ``invoker`` receives ``(argv, stdin_text)``
    so tests can inspect both dimensions. For all other providers the
    second argument is an empty string and can be ignored.

    ``bus`` + ``job_id`` are optional. When both are provided, a
    ``VOICEOVER_GENERATED`` event is scheduled on success.
    """
    if not text.strip():
        raise VoiceoverError("empty text")

    if provider == "stub":
        result = _stub_synth(text, out_path)
        emit_from_sync(
            bus, job_id, EventType.VOICEOVER_GENERATED,
            {"provider": provider, "output": out_path, "text_len": len(text)},
        )
        return result

    if provider == "coqui":
        argv = _coqui_argv(text, out_path, model=model, voice=voice)
        run = invoker or _invoke
        run(argv, "")
        if not Path(out_path).is_file():
            raise VoiceoverError("coqui: no output produced")
        emit_from_sync(
            bus, job_id, EventType.VOICEOVER_GENERATED,
            {"provider": provider, "output": out_path, "text_len": len(text), "voice": voice},
        )
        return out_path

    if provider == "piper":
        if shutil.which("piper") is None:
            raise VoiceoverError(
                "piper binary not found on PATH — install piper-tts or add it to PATH"
            )
        piper_model = os.environ.get("YTVIDEO_PIPER_MODEL", "").strip()
        if not piper_model:
            raise VoiceoverError(
                "YTVIDEO_PIPER_MODEL is not set — provide a path to the .onnx model file"
            )
        argv = _piper_argv(out_path, model=piper_model)
        run = invoker or _invoke
        run(argv, text)
        if not Path(out_path).is_file():
            raise VoiceoverError("piper: no output produced")
        emit_from_sync(
            bus, job_id, EventType.VOICEOVER_GENERATED,
            {"provider": provider, "output": out_path, "text_len": len(text)},
        )
        return out_path

    raise VoiceoverError(f"unknown voiceover provider: {provider!r}")


def _invoke(argv: Sequence[str], stdin_text: str = "") -> None:
    import subprocess
    log.info("voiceover: %s", " ".join(argv))
    input_bytes = stdin_text.encode() if stdin_text else None
    proc = subprocess.run(argv, input=input_bytes, capture_output=True)
    if proc.returncode != 0:
        raise VoiceoverError(
            f"voiceover failed (rc={proc.returncode}): "
            f"{proc.stderr.decode('utf-8', errors='replace')[-500:]}"
        )
