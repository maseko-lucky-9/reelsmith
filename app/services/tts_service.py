"""Text-to-speech service for generate:// mode (Stage 2).

Provider-pluggable, mirroring ``voiceover_service``. The ``stub`` provider
writes a deterministic WAV of silence sized to the script length so the
generate pipeline and ffprobe work on hosts without a real TTS backend.

Behind ``YTVIDEO_GENERATE_TTS_PROVIDER``:
    stub | voicebox  (default ``stub``)

The ``voicebox`` path drives the Voicebox sidecar's ASYNC REST API:

    1. ``POST {base}/generate`` with ``{profile_id, text, engine}`` returns a
       ``GenerationResponse`` (``{id, status, audio_path}``) with
       ``status == "generating"`` — generation runs on the sidecar's worker.
    2. Poll ``GET {base}/history/{id}`` until ``status == "completed"`` (the
       SSE ``/generate/{id}/status`` stream does NOT carry ``audio_path``;
       ``/history/{id}`` does). ``failed`` or a timeout raises ``TtsError``.
    3. Download the WAV via ``GET {base}/audio/{id}`` — robust against the
       sidecar storing ``audio_path`` as a data-dir-relative path that the
       reelsmith process can't read directly. (If ``audio_path`` is an existing
       absolute file we copy it instead, saving a round-trip.)

The whole sidecar interaction goes through an injectable ``invoker`` so unit
tests exercise the branch with NO live service.

The WAV-header writer is copied (not imported) from ``voiceover_service`` to
keep this module decoupled from the legacy voice-over path.
"""
from __future__ import annotations

import logging
import struct
import time
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

# Total wall-clock budget for a single voicebox generation (queue + synth).
_VOICEBOX_POLL_TIMEOUT_SECONDS = 300.0
_VOICEBOX_POLL_INTERVAL_SECONDS = 1.0


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


def _normalize_base(endpoint: str) -> str:
    """Derive the Voicebox API base URL from a configured endpoint.

    The endpoint may be the sidecar base (``http://127.0.0.1:17493``) or carry
    a trailing ``/generate`` (``http://127.0.0.1:17493/generate``). Strip a
    trailing ``/generate`` and any trailing slash so the caller can build
    ``{base}/generate``, ``{base}/history/{id}``, ``{base}/audio/{id}``.
    """
    base = endpoint.rstrip("/")
    if base.endswith("/generate"):
        base = base[: -len("/generate")]
    return base.rstrip("/")


def _default_invoker(
    endpoint: str,
    api_key: str | None,
    payload: dict,
) -> bytes:
    """Drive the Voicebox async REST flow and return the finished WAV bytes.

    ``payload`` carries ``{profile_id, text, engine}``. Polls
    ``GET {base}/history/{id}`` until completion, then downloads the WAV from
    ``GET {base}/audio/{id}`` (falling back to a local copy if ``audio_path``
    is an existing absolute file). Raises ``TtsError`` on any HTTP error,
    ``failed`` status, or timeout.
    """
    import httpx

    base = _normalize_base(endpoint)
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    with httpx.Client(timeout=120.0) as client:
        # 1. Kick off generation.
        resp = client.post(f"{base}/generate", json=payload, headers=headers)
        if resp.status_code != 200:
            raise TtsError(
                f"voicebox POST /generate failed (status={resp.status_code}): "
                f"{resp.text[:500]}"
            )
        body = resp.json()
        gen_id = body.get("id")
        if not gen_id:
            raise TtsError(f"voicebox /generate returned no id: {body}")
        status = body.get("status") or "completed"
        audio_path = body.get("audio_path") or ""

        # 2. Poll /history/{id} until terminal.
        deadline = time.monotonic() + _VOICEBOX_POLL_TIMEOUT_SECONDS
        while status not in ("completed", "failed"):
            if time.monotonic() > deadline:
                raise TtsError(
                    f"voicebox generation {gen_id} timed out after "
                    f"{_VOICEBOX_POLL_TIMEOUT_SECONDS:.0f}s (last status={status})"
                )
            time.sleep(_VOICEBOX_POLL_INTERVAL_SECONDS)
            poll = client.get(f"{base}/history/{gen_id}", headers=headers)
            if poll.status_code != 200:
                raise TtsError(
                    f"voicebox GET /history/{gen_id} failed "
                    f"(status={poll.status_code}): {poll.text[:500]}"
                )
            pbody = poll.json()
            status = pbody.get("status") or "completed"
            audio_path = pbody.get("audio_path") or audio_path

        if status == "failed":
            raise TtsError(f"voicebox generation {gen_id} failed")

        # 3a. Local absolute path that exists → read it directly.
        if audio_path:
            local = Path(audio_path)
            if local.is_absolute() and local.is_file():
                return local.read_bytes()

        # 3b. Otherwise pull it from the download route (storage-path-safe).
        dl = client.get(f"{base}/audio/{gen_id}", headers=headers)
        if dl.status_code != 200:
            raise TtsError(
                f"voicebox GET /audio/{gen_id} failed "
                f"(status={dl.status_code}): {dl.text[:500]}"
            )
        return dl.content


def synthesize(
    text: str,
    out_path: str,
    *,
    provider: str = "stub",
    endpoint: str | None = None,
    api_key: str | None = None,
    voice_profile: str | None = None,
    engine: str = "kokoro",
    invoker: Callable[[str, str | None, dict], bytes] | None = None,
) -> str:
    """Render ``text`` to a WAV at ``out_path``. Returns the path.

    ``stub``     — writes silence of duration ``max(2.0, len(text)/15)`` seconds.
    ``voicebox`` — drives the Voicebox sidecar's async REST API: POST /generate
                   with ``{profile_id, text, engine}``, poll /history/{id} to
                   completion, then download the WAV. ``voice_profile`` is the
                   required ``profile_id``. The whole flow goes through the
                   injectable ``invoker`` for testability.

    Raises ``TtsError`` on empty text, a missing voice profile, a non-200
    response, a failed generation, a timeout, or empty audio.
    """
    if not text.strip():
        raise TtsError("empty text")

    if provider == "stub":
        return _stub_synth(text, out_path)

    if provider == "voicebox":
        if not endpoint:
            raise TtsError("voicebox: endpoint is required")
        if not voice_profile:
            raise TtsError(
                "voicebox: voice_profile (profile_id) is required — set "
                "YTVIDEO_GENERATE_VOICE_PROFILE"
            )
        payload = {
            "profile_id": voice_profile,
            "text": text,
            "engine": engine or "kokoro",
        }
        run = invoker or _default_invoker
        wav_bytes = run(endpoint, api_key, payload)
        if not wav_bytes:
            raise TtsError("voicebox: empty audio")
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(wav_bytes)
        return out_path

    raise TtsError(f"unknown tts provider: {provider!r}")
