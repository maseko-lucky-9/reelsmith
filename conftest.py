"""Root conftest — applied to every test in the project.

Provides autouse fixtures that clean up background tasks and Docker containers
after every test so nothing leaks between runs.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import tempfile

import pytest

# Override settings that would otherwise be loaded from .env before app.settings
# is imported. env vars take precedence over env_file in pydantic-settings v2,
# so these setdefaults ensure tests run with predictable defaults regardless of
# any local .env file present in the developer's working directory.
os.environ.setdefault("YTVIDEO_OLLAMA_ENABLED", "false")
os.environ.setdefault("YTVIDEO_SEGMENT_PROVIDER", "chapter")
# The LLM re-rank of discovered clips stays off (a developer's .env may turn
# it on); tests that need it set it on settings and stub the model.
os.environ.setdefault("YTVIDEO_SEGMENT_RERANK_PROVIDER", "none")
os.environ.setdefault("YTVIDEO_JOB_STORE", "memory")
# Never load a real Whisper model in the default run: the stub provider for
# every test, and no lifespan warm-up. Real-model coverage lives in
# tests/integration/test_whisper_real.py, which switches the provider itself.
os.environ.setdefault("YTVIDEO_TRANSCRIPTION_PROVIDER", "stub")
os.environ.setdefault("YTVIDEO_WHISPER_WARMUP", "false")
# B-roll stays off and offline whatever a developer's .env says: no provider,
# no Pexels key, and a throwaway library and cache. Tests that need a
# provider set it on settings.
os.environ.setdefault("YTVIDEO_BROLL_PROVIDER", "none")
os.environ.setdefault("YTVIDEO_PEXELS_API_KEY", "")
os.environ.setdefault("YTVIDEO_BROLL_LIBRARY_DIR", tempfile.mkdtemp(prefix="reelsmith-test-broll-"))
os.environ.setdefault("YTVIDEO_BROLL_CACHE_DIR", tempfile.mkdtemp(prefix="reelsmith-test-broll-cache-"))
# Never let the test suite export into a developer's real .env export folder
# (e.g. a Syncthing share); env vars win over .env in pydantic-settings.
os.environ.setdefault("YTVIDEO_EXPORT_BASE_FOLDER", tempfile.mkdtemp(prefix="reelsmith-test-export-"))
# Downloads and uploads go to a throwaway dir too, never the project's
# data/downloads or a developer's .env download path.
os.environ.setdefault("YTVIDEO_DEFAULT_DOWNLOAD_PATH", tempfile.mkdtemp(prefix="reelsmith-test-dl-"))
# Never let a developer's .env switch a platform to a live social adapter
# (e.g. YTVIDEO_SOCIAL_PROVIDER_TIKTOK=cookie) for the test run; tests that
# need a provider set it on settings.
os.environ.setdefault("YTVIDEO_SOCIAL_PROVIDER", "stub")
for _platform in ("YOUTUBE", "TIKTOK", "INSTAGRAM", "LINKEDIN", "X"):
    os.environ.setdefault(f"YTVIDEO_SOCIAL_PROVIDER_{_platform}", "")
# Face-tracked reframe stays off (a developer's .env may turn it on) and its
# model, if a test does download it, never lands in the project's data/models.
os.environ.setdefault("YTVIDEO_REFRAME_PROVIDER", "letterbox")
os.environ.setdefault("YTVIDEO_REFRAME_MODEL_DIR", tempfile.mkdtemp(prefix="reelsmith-test-models-"))


@pytest.fixture(autouse=True)
async def _cancel_lingering_tasks():
    """Cancel asyncio tasks leaked by a test before the event loop is torn down.

    pytest-asyncio (mode=auto) gives each test its own loop, so this only
    affects tasks spawned within the current test that weren't cancelled by
    the test itself (e.g. a LifespanManager that didn't fully drain).
    """
    yield
    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if tasks:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.fixture(autouse=True, scope="session")
def _stop_test_docker_containers():
    """Stop Docker containers labelled ``pytest=reelsmith`` at session end.

    Any container started during the test session should carry the label
    ``pytest=reelsmith`` so this fixture can find and remove it cleanly.
    No-op when Docker is not installed or no matching containers exist.
    """
    yield
    result = subprocess.run(
        ["docker", "ps", "-q", "--filter", "label=pytest=reelsmith"],
        capture_output=True,
        text=True,
    )
    ids = result.stdout.strip().split()
    if ids:
        subprocess.run(["docker", "stop", *ids], capture_output=True)
        subprocess.run(["docker", "rm", *ids], capture_output=True)
