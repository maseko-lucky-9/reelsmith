"""``GET /sources/youtube/search`` against the real YouTube (spec 015). Opt-in: ``-m live``."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services.source_check import VIDEO_ID_RE

pytestmark = pytest.mark.live


def test_a_real_search_returns_videos_with_the_documented_fields():
    with TestClient(create_app()) as client:
        response = client.get("/sources/youtube/search", params={"q": "ted talk sleep", "limit": 5})

    assert response.status_code == 200
    items = response.json()["items"]
    assert 1 <= len(items) <= 5
    for item in items:
        assert VIDEO_ID_RE.fullmatch(item["id"])
        assert item["title"]
        assert item["url"] == f"https://www.youtube.com/watch?v={item['id']}"
        assert item["thumbnail_url"].startswith("https://i.ytimg.com/vi/")
        assert item["duration_seconds"] is None or item["duration_seconds"] >= 0
