"""AI Voice-over service (W2.3).

Provider-pluggable. Coqui XTTS v2 is the default live path (CPML
non-commercial — fine for the portfolio scope of this repo per
ADR-003 §A.13). Stub provider writes a deterministic WAV header so
CI and dev hosts without the model still exercise the pipeline.

Behind ``YTVIDEO_VOICEOVER_PROVIDER``:
    coqui      | piper | stub  (default ``stub``)

The Coqui path is opt-in via the ``voiceover`` compose profile and
expects the model already pulled to ``YTVIDEO_COQUI_MODEL``.
"""
from __future__ import annotations

import logging
import struct
from pathlib import Path
from typing import Callable, Sequence

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


def synthesize(
    text: str,
    out_path: str,
    *,
    provider: str = "stub",
    model: str = "tts_models/multilingual/multi-dataset/xtts_v2",
    voice: str | None = None,
    invoker: Callable[[Sequence[str]], None] | None = None,
) -> str:
    """Render ``text`` to a WAV at ``out_path``. Returns the path."""
    if not text.strip():
        raise VoiceoverError("empty text")

    if provider == "stub":
        return _stub_synth(text, out_path)

    if provider in ("coqui", "piper"):
        if provider == "piper":
            argv = ("piper", "--text", text, "--output_file", out_path)
        else:
            argv = _coqui_argv(text, out_path, model=model, voice=voice)
        run = invoker or _invoke
        run(argv)
        if not Path(out_path).is_file():
            raise VoiceoverError(f"{provider}: no output produced")
        return out_path

    raise VoiceoverError(f"unknown voiceover provider: {provider!r}")


def _invoke(argv: Sequence[str]) -> None:
    import subprocess
    log.info("voiceover: %s", " ".join(argv))
    proc = subprocess.run(argv, capture_output=True)
    if proc.returncode != 0:
        raise VoiceoverError(
            f"voiceover failed (rc={proc.returncode}): "
            f"{proc.stderr.decode('utf-8', errors='replace')[-500:]}"
        )
