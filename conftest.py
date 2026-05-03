"""Root conftest — applied to every test in the project.

Applies PIL.Image.ANTIALIAS compat shim before any test can import MoviePy.
Provides autouse fixtures that clean up background tasks and Docker containers
after every test so nothing leaks between runs.
"""
from __future__ import annotations

import asyncio
import subprocess

import pytest

import app.compat  # noqa: F401 — PIL.Image.ANTIALIAS shim for MoviePy 1.0.3


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
