from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

import app.compat  # noqa: F401 — must run before any MoviePy import

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.bus.event_bus import AsyncEventBus
from app.bus.job_store import JobStore
from app.routers import (
    captions,
    downloads,
    folders,
    jobs,
    renders,
    subtitle_images,
    transcriptions,
)
from app.settings import settings
from app.workers.orchestrator import run_orchestrator

import app.logging_config  # noqa: F401


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Cap the thread pool used by every asyncio.to_thread call.
    # MoviePy/yt-dlp/SpeechRecognition are all CPU/IO heavy; an unbounded pool
    # (Python default: min(32, cpu_count+4)) causes thermal throttling and
    # memory pressure on a laptop. max_thread_workers controls this via env var
    # YTVIDEO_MAX_THREAD_WORKERS (default 4).
    loop = asyncio.get_running_loop()
    executor = ThreadPoolExecutor(
        max_workers=settings.max_thread_workers,
        thread_name_prefix="ytvideo",
    )
    loop.set_default_executor(executor)

    app.state.event_bus = AsyncEventBus()
    app.state.job_store = JobStore()
    app.state.orchestrator_task = asyncio.create_task(
        run_orchestrator(app.state.event_bus, app.state.job_store)
    )
    try:
        yield
    finally:
        task = app.state.orchestrator_task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 - shutdown best-effort
                pass
        await app.state.event_bus.aclose()
        executor.shutdown(wait=False)


def create_app() -> FastAPI:
    app = FastAPI(title="YouTubeVideo API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(jobs.router)
    app.include_router(folders.router)
    app.include_router(downloads.router)
    app.include_router(transcriptions.router)
    app.include_router(captions.router)
    app.include_router(subtitle_images.router)
    app.include_router(renders.router)
    return app


app = create_app()
