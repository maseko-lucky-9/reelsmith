#!/usr/bin/env python
"""Gate B — Voicebox/Kokoro TTS sidecar smoke test for generate:// mode.

Probes the Voicebox sidecar's health endpoint, synthesizes a sample sentence
through ``tts_service`` (provider=voicebox), and validates the returned WAV.
Operator tooling only — never run by CI (which has no sidecar).

Usage
-----
    python -m scripts.voicebox_smoke [--endpoint URL] [--api-key KEY]
                                     [--voice-profile ID] [--text TEXT]
                                     [--out PATH] [--timeout SECONDS]

Exit codes (shared legend with Gate A / the preflight orchestrator):
    0  PASS            — health ok, WAV synthesized, nframes>0, duration>0
    1  FAIL            — health probe failed, synth raised, or invalid WAV
    2  NOT_CONFIGURED  — no endpoint configured; nothing was run

httpx and settings imports are deferred so importing this module is cheap and
torch-free.
"""
from __future__ import annotations

import argparse
import sys
import wave
from pathlib import Path
from typing import Callable, Sequence
from urllib.parse import urlsplit, urlunsplit

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_NOT_CONFIGURED = 2

DEFAULT_TEXT = "This is a Voicebox brand-voice smoke test for ReelSmith."
DEFAULT_TIMEOUT = 10.0


def _redact_url(url: str) -> str:
    """Strip any ``user:pass@`` userinfo from a URL's netloc for safe logging.

    Leaves clean URLs unchanged. Only the host[:port] survives in the netloc,
    so a credential embedded in the endpoint never reaches stdout/logs. The
    actual request still uses the real (unredacted) endpoint.
    """
    parts = urlsplit(url)
    if "@" not in parts.netloc:
        return url
    host = parts.netloc.rsplit("@", 1)[1]
    return urlunsplit(parts._replace(netloc=host))


def _bootstrap_path() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))


def health_url_for(endpoint: str) -> str:
    """Derive a health-check URL from a synthesize ``endpoint``.

    Replaces a trailing ``/synthesize`` with ``/health``; otherwise appends
    ``/health`` to the endpoint (trimming a trailing slash). Pure + testable.
    """
    ep = endpoint.rstrip("/")
    if ep.endswith("/synthesize"):
        return ep[: -len("/synthesize")] + "/health"
    return ep + "/health"


def _default_health_probe(url: str, api_key: str | None, timeout: float) -> int:
    """GET ``url`` and return the HTTP status code (raises on transport error)."""
    import httpx

    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    resp = httpx.get(url, headers=headers, timeout=timeout)
    return resp.status_code


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="voicebox_smoke",
        description="Gate B — Voicebox TTS sidecar smoke test.",
    )
    p.add_argument("--endpoint", default=None, help="Override YTVIDEO_VOICEBOX_ENDPOINT.")
    p.add_argument("--api-key", default=None, help="Override YTVIDEO_VOICEBOX_API_KEY.")
    p.add_argument(
        "--voice-profile", default=None,
        help="Override YTVIDEO_GENERATE_VOICE_PROFILE (the brand-voice profile_id).",
    )
    p.add_argument(
        "--engine", default=None,
        help="Override YTVIDEO_VOICEBOX_ENGINE (TTS engine, e.g. kokoro).",
    )
    p.add_argument("--text", default=DEFAULT_TEXT, help="Sample sentence to synthesize.")
    p.add_argument("--out", default=None, help="Output WAV path (default: temp file).")
    p.add_argument(
        "--timeout", type=float, default=DEFAULT_TIMEOUT,
        help=f"Health-probe timeout in seconds (default {DEFAULT_TIMEOUT}).",
    )
    return p


def _resolve_config(args: argparse.Namespace):
    from app.settings import settings

    endpoint = args.endpoint if args.endpoint is not None else settings.voicebox_endpoint
    api_key = (
        args.api_key if args.api_key is not None
        else getattr(settings, "voicebox_api_key", None)
    )
    voice_profile = (
        args.voice_profile if args.voice_profile is not None
        else settings.generate_voice_profile
    )
    engine = (
        args.engine if args.engine is not None
        else getattr(settings, "voicebox_engine", "kokoro")
    )
    return endpoint, api_key, voice_profile, engine


def _validate_wav(path: str) -> tuple[bool, str, int, float]:
    """Return (ok, message, nframes, duration_seconds) for a WAV file."""
    try:
        with wave.open(path, "rb") as wf:
            nframes = wf.getnframes()
            rate = wf.getframerate() or 0
            duration = nframes / rate if rate else 0.0
    except wave.Error as e:
        return False, f"not a valid WAV: {e}", 0, 0.0
    except Exception as e:  # noqa: BLE001
        return False, f"could not open WAV: {e}", 0, 0.0
    if nframes <= 0:
        return False, "WAV has zero frames", nframes, duration
    if duration <= 0:
        return False, "WAV has zero duration", nframes, duration
    return True, "ok", nframes, duration


def main(
    argv: Sequence[str] | None = None,
    *,
    health_probe: Callable[[str, str | None, float], int] | None = None,
    invoker: Callable[[str, str | None, dict], bytes] | None = None,
) -> int:
    """Run Gate B.

    ``health_probe`` and ``invoker`` are injectable seams so tests can exercise
    the health-fail and synth paths without real network I/O.
    """
    _bootstrap_path()
    args = _build_parser().parse_args(argv)

    endpoint, api_key, voice_profile, engine = _resolve_config(args)

    # ── Pre-check: configured? ────────────────────────────────────────────────
    if not endpoint:
        print("Gate B: NOT_CONFIGURED — Voicebox endpoint not set.")
        print(
            "  Fix: start the Voicebox/Kokoro sidecar and set "
            "YTVIDEO_VOICEBOX_ENDPOINT (and YTVIDEO_VOICEBOX_API_KEY if used)."
        )
        return EXIT_NOT_CONFIGURED

    # The voicebox provider requires a non-empty profile_id. When the operator
    # hasn't configured one, fall back to a placeholder so the gate can still
    # exercise the synth path (the injected invoker / real sidecar decides what
    # to do with it).
    effective_profile = voice_profile or "smoke-profile"

    probe = health_probe or _default_health_probe
    hurl = health_url_for(endpoint)
    print("Gate B: running Voicebox smoke...")
    print(f"  endpoint      : {_redact_url(endpoint)}")
    print(f"  health url    : {_redact_url(hurl)}")
    print(f"  voice profile : {voice_profile or '(default: smoke-profile)'}")
    print(f"  engine        : {engine}")

    # ── Health probe ──────────────────────────────────────────────────────────
    try:
        status = probe(hurl, api_key, args.timeout)
    except Exception as e:  # noqa: BLE001
        print(f"Gate B: FAIL — health probe error: {e}")
        return EXIT_FAIL
    if status != 200:
        print(f"Gate B: FAIL — health probe returned status {status} (expected 200).")
        return EXIT_FAIL
    print("  health        : ok (200)")

    # ── Synthesize + validate the WAV ─────────────────────────────────────────
    from app.services import tts_service

    if args.out:
        out_path = args.out
    else:
        import tempfile

        out_path = str(Path(tempfile.gettempdir()) / "voicebox_smoke.wav")
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)

    try:
        tts_service.synthesize(
            args.text,
            out_path,
            provider="voicebox",
            endpoint=endpoint,
            api_key=api_key,
            voice_profile=effective_profile,
            engine=engine,
            invoker=invoker,
        )
    except Exception as e:  # noqa: BLE001
        print(f"Gate B: FAIL — synthesize raised: {e}")
        return EXIT_FAIL

    ok, msg, nframes, duration = _validate_wav(out_path)

    verdict = "PASS" if ok else "FAIL"
    print("── Gate B report ──────────────────────────────")
    print(f"  endpoint     : {_redact_url(endpoint)}")
    print("  health       : 200")
    print(f"  wav frames   : {nframes}")
    print(f"  wav duration : {duration:.2f}s")
    print(f"  output       : {out_path}")
    print(f"  verdict      : {verdict}")
    if not ok:
        print(f"    ! {msg}")
    print("───────────────────────────────────────────────")

    return EXIT_PASS if ok else EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
