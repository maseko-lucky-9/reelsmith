"""AI Hook generator (W1.7).

Wraps ``ollama_service`` with a hook-specific prompt. Returns a single
short, attention-grabbing sentence to overlay or speak at the start
of a clip. Truncated to ``YTVIDEO_AI_HOOK_MAX_CHARS`` (default 80).
"""
from __future__ import annotations

import json
import logging

import httpx

from app.settings import settings

log = logging.getLogger(__name__)

_PROMPT = (
    "You are a viral-clip copywriter. Given a short transcript, return ONLY "
    "valid JSON with a single field 'hook' containing a punchy one-sentence "
    'opening hook (max {max_chars} characters). No markdown, no extra text.\n\n'
    'Format: {{"hook": "<one sentence>"}}\n\n'
    "Transcript: {transcript}"
)


def generate_hook(
    transcript: str,
    *,
    base_url: str | None = None,
    model: str | None = None,
    timeout: int | None = None,
    max_chars: int | None = None,
) -> str:
    """Return a hook string. Empty string on failure.

    Pure function for unit-testing — accepts overrides; tests inject a
    mock httpx layer.
    """
    if not getattr(settings, "ai_hook_enabled", True):
        return ""
    if not transcript or not transcript.strip():
        return ""

    base_url = base_url or settings.ollama_base_url
    model = model or settings.ollama_model
    timeout = timeout or settings.ollama_timeout_seconds
    max_chars = max_chars or getattr(settings, "ai_hook_max_chars", 80)

    prompt = _PROMPT.format(max_chars=max_chars, transcript=transcript[:1500])
    try:
        resp = httpx.post(
            f"{base_url}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False},
            timeout=timeout,
        )
        resp.raise_for_status()
        raw = resp.json().get("response", "")
        data = json.loads(raw)
        hook = (data.get("hook") or "").strip()
    except Exception as exc:  # noqa: BLE001
        log.warning("ai_hook_service: ollama call failed: %s", exc)
        return ""

    if not hook:
        return ""
    if len(hook) > max_chars:
        hook = hook[: max_chars - 1].rstrip() + "…"
    return hook
