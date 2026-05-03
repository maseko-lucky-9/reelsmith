from __future__ import annotations

import json
import logging

import httpx

log = logging.getLogger(__name__)

_PROMPT = (
    "You are a social media copywriter. Given a video title and transcript excerpt, "
    "return ONLY valid JSON in this exact format (no markdown, no extra text):\n"
    '{{"description": "<one or two sentence video description>", "hashtags": ["hashtag1", "hashtag2", ...]}}\n\n'
    "Title: {title}\n"
    "Transcript: {transcript}"
)


def generate_social_content(
    title: str,
    transcript: str,
    base_url: str,
    model: str,
    timeout: int,
) -> tuple[str, list[str]]:
    try:
        resp = httpx.post(
            f"{base_url}/api/generate",
            json={
                "model": model,
                "prompt": _PROMPT.format(title=title, transcript=transcript[:2000]),
                "stream": False,
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        raw = resp.json().get("response", "")
        data = json.loads(raw)
        return data.get("description", ""), data.get("hashtags", [])
    except Exception:
        log.warning("Ollama social content failed for %r", title, exc_info=True)
        return "", []
