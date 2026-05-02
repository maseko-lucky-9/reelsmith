"""Thin HTTP client for the YouTubeVideo API.

Used by the Streamlit thin client. Server-to-server only — no business logic.
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import httpx


class ApiClient:
    def __init__(self, base_url: str, timeout: float = 30.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def create_job(
        self,
        url: str,
        download_path: str,
        caption_format: str = "srt",
        target_aspect_ratio: float = 9 / 16,
    ) -> str:
        response = httpx.post(
            f"{self._base_url}/jobs",
            json={
                "url": url,
                "download_path": download_path,
                "caption_format": caption_format,
                "target_aspect_ratio": target_aspect_ratio,
            },
            timeout=self._timeout,
        )
        response.raise_for_status()
        return response.json()["job_id"]

    def get_job(self, job_id: str) -> dict[str, Any]:
        response = httpx.get(f"{self._base_url}/jobs/{job_id}", timeout=self._timeout)
        response.raise_for_status()
        return response.json()

    def stream_events(self, job_id: str) -> Iterator[dict[str, Any]]:
        """Yield decoded events from the SSE stream until JobCompleted/JobFailed."""
        with httpx.stream(
            "GET",
            f"{self._base_url}/jobs/{job_id}/events",
            timeout=httpx.Timeout(None, connect=10.0),
        ) as stream:
            current_event: str | None = None
            for raw_line in stream.iter_lines():
                if not raw_line:
                    current_event = None
                    continue
                if raw_line.startswith("event:"):
                    current_event = raw_line.split(":", 1)[1].strip()
                elif raw_line.startswith("data:") and current_event:
                    payload = json.loads(raw_line.split(":", 1)[1].strip())
                    yield {"type": current_event, "payload": payload}
                    if current_event in {"JobCompleted", "JobFailed"}:
                        return
