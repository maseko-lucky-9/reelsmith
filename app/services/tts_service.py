"""Text-to-speech service for generate:// mode (Stage 1).

Provider-pluggable, mirroring ``voiceover_service``. The ``stub`` provider
writes a deterministic WAV of silence sized to the script length so the
generate pipeline and ffprobe work on hosts without a real TTS backend.

Behind ``YTVIDEO_GENERATE_TTS_PROVIDER``:
    stub | voicebox  (default ``stub``)

The ``voicebox`` path POSTs the script to an HTTP endpoint and writes the
returned WAV bytes. The network call goes through an injectable ``invoker``
so tests can exercise the branch without a live server.

The WAV-header writer is copied (not imported) from ``voiceover_service`` to
keep this module decoupled from the legacy voice-over path.
"""
from __future__ import annotations

import logging
import struct
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)


class TtsError(RuntimeError):
    pass


# Copied from voiceover_service to avoid coupling the two modules.
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


def _stub_duration_seconds(text: str) -> float:
    """Estimate spoken duration: ~15 chars/sec, floored at 2 seconds."""
    return max(2.0, len(text) / 15.0)


def _stub_synth(text: str, out_path: str) -> str:
    """Write a deterministic WAV of silence sized to the script length."""
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    sample_rate = 24000
    samples = int(_stub_duration_seconds(text) * sample_rate)
    body = b"\x00\x00" * samples
    Path(out_path).write_bytes(_wav_header(samples, sample_rate) + body)
    return out_path


def _default_invoker(
    endpoint: str,
    api_key: str | None,
    payload: dict,
) -> bytes:
    """POST ``payload`` to ``endpoint`` with bearer auth; return WAV bytes."""
    import httpx

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    resp = httpx.post(endpoint, json=payload, headers=headers, timeout=120.0)
    if resp.status_code != 200:
        raise TtsError(
            f"voicebox request failed (status={resp.status_code}): "
            f"{resp.text[:500]}"
        )
    return resp.content


def synthesize(
    text: str,
    out_path: str,
    *,
    provider: str = "stub",
    endpoint: str | None = None,
    api_key: str | None = None,
    voice_profile: str | None = None,
    invoker: Callable[[str, str | None, dict], bytes] | None = None,
) -> str:
    """Render ``text`` to a WAV at ``out_path``. Returns the path.

    ``stub``    — writes silence of duration ``max(2.0, len(text)/15)`` seconds.
    ``voicebox`` — POSTs ``{text, voice_profile}`` to ``endpoint`` with a
                   bearer ``api_key`` and writes the returned WAV bytes. The
                   ``invoker`` is injectable for testability.

    Raises ``TtsError`` on empty text or a non-200 voicebox response.
    """
    if not text.strip():
        raise TtsError("empty text")

    if provider == "stub":
        return _stub_synth(text, out_path)

    if provider == "voicebox":
        if not endpoint:
            raise TtsError("voicebox: endpoint is required")
        payload = {"text": text, "voice_profile": voice_profile or ""}
        run = invoker or _default_invoker
        wav_bytes = run(endpoint, api_key, payload)
        if not wav_bytes:
            raise TtsError("voicebox: empty response body")
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(wav_bytes)
        return out_path

    raise TtsError(f"unknown tts provider: {provider!r}")
