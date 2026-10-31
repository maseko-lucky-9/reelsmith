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

Clip voice-over (spec 016, ``POST /clips/{id}/voiceover``):
``synthesize_voiceover(text, voice, out_path, provider=...)`` is the hardened
entry point. The text is untrusted: control characters are removed, its
length is bounded (``MAX_TEXT_CHARS``) and it reaches Piper through a text
file passed as ``--input-file`` (never a shell, never argv). The voice name is
checked against ``VOICE_NAME_RE`` and resolves to ``<voice>.onnx`` directly
inside ``YTVIDEO_VOICEOVER_MODEL_DIR``. Piper runs through
``ffmpeg_tools.run`` (killed on timeout or cancel); the wav it writes is
capped in size and must parse as a wav with a positive duration.
``resolve_provider`` turns ``YTVIDEO_VOICEOVER_PROVIDER`` into an engine:
``auto`` is Piper when the ``piper`` command and the voice files exist, else
the stub (logged). Voices are fetched by ``scripts/fetch_piper_voice.py``,
never here.
"""
from __future__ import annotations

import importlib.util
import logging
import os
import re
import shutil
import struct
import sys
import tempfile
import unicodedata
import wave
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Sequence

from app.domain.events import EventType, emit_from_sync
from app.settings import settings

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
        piper_model = settings.piper_model.strip()
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


# ── Clip voice-over (spec 016) ────────────────────────────────────────────────

MAX_TEXT_CHARS = 1000
"""Most characters a voice-over script may hold (after cleaning)."""

VOICE_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
"""A voice name: a plain file stem, no separator."""

MAX_WAV_BYTES = 50 * 1024 * 1024
"""Largest wav a synthesis may leave (about 9 minutes of 22 kHz mono)."""

SYNTH_TIMEOUT_SECONDS = 120.0
"""Piper is killed after this long."""

PROVIDERS = ("auto", "piper", "stub", "off")

MAX_OUTPUT_INDEX = 999

Runner = Callable[[Sequence[str], float], None]
"""Runs an argv to completion within a timeout (seconds); raises on failure."""


class VoiceoverInputError(VoiceoverError):
    """The request's text or voice name is not acceptable."""


class VoiceoverDisabled(VoiceoverError):
    """``YTVIDEO_VOICEOVER_PROVIDER=off``."""


def clean_text(text: object) -> str:
    """The script with control and format characters replaced by spaces and
    whitespace collapsed; ``VoiceoverInputError`` when empty or too long."""
    if not isinstance(text, str):
        raise VoiceoverInputError("text must be a string")
    if len(text) > 4 * MAX_TEXT_CHARS:
        raise VoiceoverInputError(f"text is longer than {MAX_TEXT_CHARS} characters")
    kept = "".join(" " if unicodedata.category(ch).startswith("C") else ch for ch in text)
    cleaned = " ".join(kept.split())
    if not cleaned:
        raise VoiceoverInputError("text is empty")
    if len(cleaned) > MAX_TEXT_CHARS:
        raise VoiceoverInputError(f"text is longer than {MAX_TEXT_CHARS} characters")
    return cleaned


def voice_model_path(voice: object, model_dir: str | Path | None = None) -> Path:
    """``<model dir>/<voice>.onnx`` (it may not exist); ``VoiceoverInputError``
    for a bad name or one that resolves anywhere but directly inside the
    model folder (a symlinked file included)."""
    if not isinstance(voice, str) or not VOICE_NAME_RE.fullmatch(voice) or voice.strip(".") == "":
        raise VoiceoverInputError(f"invalid voice name {str(voice)!r:.80}")
    folder = Path(model_dir if model_dir is not None else settings.voiceover_model_dir).resolve()
    candidate = folder / f"{voice}.onnx"
    if candidate.resolve().parent != folder:
        raise VoiceoverInputError(f"voice {voice!r} resolves outside the voice folder")
    return candidate


def _config_path(model: Path) -> Path:
    return model.with_name(model.name + ".json")


def piper_command() -> list[str] | None:
    """How to run Piper: the ``piper`` command on PATH, else this Python with
    ``-m piper`` when the ``piper-tts`` package is installed, else None."""
    found = shutil.which("piper")
    if found:
        return [found]
    if importlib.util.find_spec("piper") is not None:
        return [sys.executable, "-m", "piper"]
    return None


def resolve_provider(
    requested: str | None = None,
    voice: str | None = None,
    *,
    model_dir: str | Path | None = None,
) -> str:
    """The engine to use: "piper" or "stub".

    ``requested`` defaults to ``YTVIDEO_VOICEOVER_PROVIDER``; "off" raises
    ``VoiceoverDisabled``, an unknown value ``VoiceoverError``. "auto" is
    Piper when ``piper_command()`` finds Piper and the voice's ``.onnx`` and
    ``.onnx.json`` exist, else the stub.
    """
    name = (requested if requested is not None else settings.voiceover_provider).strip().lower()
    if name not in PROVIDERS:
        raise VoiceoverError(f"unknown voiceover provider: {name!r}; expected one of {PROVIDERS}")
    if name == "off":
        raise VoiceoverDisabled("voice-over is off (YTVIDEO_VOICEOVER_PROVIDER=off)")
    if name != "auto":
        return name
    model = voice_model_path(voice or settings.voiceover_voice, model_dir)
    if piper_command() is None:
        log.info("voiceover: auto -> stub (piper is not installed)")
        return "stub"
    if not (model.is_file() and _config_path(model).is_file()):
        log.info(
            "voiceover: auto -> stub (voice %s not in %s; run python -m scripts.fetch_piper_voice)",
            model.stem, model.parent,
        )
        return "stub"
    return "piper"


def reserve_output_path(folder: str | Path) -> Path:
    """Create and return a new empty ``voiceover_<n>.wav`` in ``folder`` (the
    first free ``n``); the name is chosen here, never by a client."""
    base = Path(folder)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    for n in range(1, MAX_OUTPUT_INDEX + 1):
        path = base / f"voiceover_{n}.wav"
        try:
            fd = os.open(path, flags, 0o644)
        except FileExistsError:
            continue
        os.close(fd)
        return path
    raise VoiceoverError(f"too many voice-over files in {base}")


def wav_duration(path: str | Path) -> float:
    """Seconds of audio in the wav at ``path``; ``VoiceoverError`` when it is
    missing, empty, over ``MAX_WAV_BYTES``, not a wav or holds no audio."""
    target = Path(path)
    try:
        size = target.stat().st_size
    except OSError as exc:
        raise VoiceoverError("voice-over produced no file") from exc
    if size == 0:
        raise VoiceoverError("voice-over produced an empty file")
    if size > MAX_WAV_BYTES:
        raise VoiceoverError(f"voice-over file is larger than {MAX_WAV_BYTES} bytes")
    try:
        with wave.open(str(target), "rb") as wav:
            frames, rate = wav.getnframes(), wav.getframerate()
    except (wave.Error, EOFError, OSError) as exc:
        raise VoiceoverError(f"voice-over file is not a valid wav: {exc}") from exc
    if rate <= 0 or frames <= 0:
        raise VoiceoverError("voice-over file holds no audio")
    return frames / rate


def _run_killable(argv: Sequence[str], timeout: float) -> None:
    from app.services import ffmpeg_tools

    try:
        ffmpeg_tools.run(argv, timeout=timeout)
    except ffmpeg_tools.FfmpegCancelled:
        raise
    except ffmpeg_tools.FfmpegError as exc:
        raise VoiceoverError(f"piper failed: {exc}") from exc


def synthesize_voiceover(
    text: str,
    voice: str | None,
    out_path: str | Path,
    *,
    provider: str,
    model_dir: str | Path | None = None,
    runner: Runner | None = None,
    timeout: float = SYNTH_TIMEOUT_SECONDS,
) -> float:
    """Speak ``text`` with ``voice`` into the wav at ``out_path``; its duration
    in seconds. ``provider`` is "piper" or "stub" (``resolve_provider``).

    ``out_path`` must come from the server (``reserve_output_path``). The
    text and voice are validated here whatever the provider.
    """
    script = clean_text(text)
    model = voice_model_path(voice or settings.voiceover_voice, model_dir)
    out = Path(out_path)
    if provider == "stub":
        _stub_synth(script, str(out))
    elif provider == "piper":
        command = piper_command()
        if command is None:
            raise VoiceoverError("piper is not installed (pip install piper-tts)")
        config = _config_path(model)
        if not (model.is_file() and config.is_file()):
            raise VoiceoverError(
                f"voice {model.stem!r} is not in {model.parent}; run python -m scripts.fetch_piper_voice"
            )
        with tempfile.TemporaryDirectory(prefix="reelsmith-vo-") as tmp:
            text_file = Path(tmp) / "script.txt"
            text_file.write_text(script + "\n", encoding="utf-8")
            argv = [
                *command,
                "--model", str(model),
                "--config", str(config),
                "--input-file", str(text_file),
                "--output-file", str(out),
            ]
            log.info("voiceover: piper voice=%s chars=%d -> %s", model.stem, len(script), out.name)
            (runner or _run_killable)(argv, timeout)
    else:
        raise VoiceoverError(f"unknown voiceover provider: {provider!r}")
    return wav_duration(out)
