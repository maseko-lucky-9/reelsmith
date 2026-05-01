"""Playwright UI smoke test against the running Streamlit app.

Requires both services running:
  uvicorn app.main:app --port 8000
  streamlit run ui/streamlit_app.py --server.port 8501

Run with:
  pytest tests/e2e/test_playwright_ui.py -m playwright -s -v
"""
from __future__ import annotations

import os
import re
import tempfile

import pytest
from playwright.sync_api import Page, expect

pytestmark = [pytest.mark.playwright]

STREAMLIT_URL = os.environ.get("YTVIDEO_UI_URL", "http://localhost:8501")
CANONICAL_URL = "https://www.youtube.com/watch?v=8I3_NM-V_w0"


@pytest.fixture(scope="session")
def download_dir(tmp_path_factory):
    return str(tmp_path_factory.mktemp("playwright_downloads"))


def test_ui_loads(page: Page):
    page.goto(STREAMLIT_URL)
    expect(page.get_by_text("YouTube Video Downloader")).to_be_visible(timeout=15_000)


def test_submit_job_and_see_progress(page: Page, download_dir):
    page.goto(STREAMLIT_URL)

    # Wait for Streamlit to fully hydrate
    expect(page.get_by_text("YouTube Video Downloader")).to_be_visible(timeout=15_000)

    # Fill in the YouTube URL
    url_input = page.get_by_label("YouTube Video URL")
    url_input.click()
    url_input.fill(CANONICAL_URL)

    # Fill in the download path directly (bypass the OS folder dialog)
    path_input = page.get_by_label("Folder to save the video")
    path_input.click()
    path_input.fill(download_dir)
    # Commit the value by pressing Tab so session_state picks it up
    path_input.press("Tab")

    # Click Process Video
    page.get_by_role("button", name="Process Video").click()

    # Should see the job-submitted success message within 10 s
    expect(page.get_by_text(re.compile(r"Job submitted", re.IGNORECASE))).to_be_visible(
        timeout=10_000
    )

    # Should see at least one SSE step label — proves the event stream started
    expect(page.get_by_text(re.compile(r"Step:", re.IGNORECASE))).to_be_visible(
        timeout=30_000
    )


def test_invalid_url_shows_error(page: Page, download_dir):
    page.goto(STREAMLIT_URL)
    expect(page.get_by_text("YouTube Video Downloader")).to_be_visible(timeout=15_000)

    url_input = page.get_by_label("YouTube Video URL")
    url_input.click()
    url_input.fill("https://not-youtube.com/watch?v=abc")

    path_input = page.get_by_label("Folder to save the video")
    path_input.click()
    path_input.fill(download_dir)
    path_input.press("Tab")

    page.get_by_role("button", name="Process Video").click()

    expect(page.get_by_text(re.compile(r"Invalid YouTube URL", re.IGNORECASE))).to_be_visible(
        timeout=5_000
    )


def test_missing_folder_shows_error(page: Page):
    page.goto(STREAMLIT_URL)
    expect(page.get_by_text("YouTube Video Downloader")).to_be_visible(timeout=15_000)

    url_input = page.get_by_label("YouTube Video URL")
    url_input.click()
    url_input.fill(CANONICAL_URL)

    path_input = page.get_by_label("Folder to save the video")
    path_input.click()
    path_input.fill("/this/path/does/not/exist")
    path_input.press("Tab")

    page.get_by_role("button", name="Process Video").click()

    expect(page.get_by_text(re.compile(r"does not exist", re.IGNORECASE))).to_be_visible(
        timeout=5_000
    )
