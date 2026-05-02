from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

import app.compat  # noqa: F401 — must run before any MoviePy import

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

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
    loop = asyncio.get_running_loop()
    executor = ThreadPoolExecutor(
        max_workers=settings.max_thread_workers,
        thread_name_prefix="ytvideo",
    )
    loop.set_default_executor(executor)

    # Run DB migrations on startup when using the SQL store.
    if settings.job_store == "sql":
        from alembic import command as alembic_command
        from alembic.config import Config as AlembicConfig
        import asyncio as _asyncio

        cfg_path = Path(__file__).parents[1] / "alembic.ini"
        if cfg_path.exists():
            alembic_cfg = AlembicConfig(str(cfg_path))
            await _asyncio.to_thread(alembic_command.upgrade, alembic_cfg, "head")

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
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        await app.state.event_bus.aclose()

        if settings.job_store == "sql":
            from app.db.engine import dispose_engine
            await dispose_engine()

        executor.shutdown(wait=False)


def create_app() -> FastAPI:
    app = FastAPI(title="Reelsmith API", version="0.1.0", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list(),
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Health probe used by the React dev-server to confirm the API is up.
    @app.get("/api/health", tags=["meta"])
    async def health() -> JSONResponse:
        return JSONResponse({"status": "ok", "job_store": settings.job_store})

    app.include_router(jobs.router)
    app.include_router(folders.router)
    app.include_router(downloads.router)
    app.include_router(transcriptions.router)
    app.include_router(captions.router)
    app.include_router(subtitle_images.router)
    app.include_router(renders.router)

    # Serve the built React app in production (YTVIDEO_SERVE_FRONTEND=true).
    if settings.serve_frontend:
        frontend_dir = Path(__file__).parents[1] / "web" / "dist"
        if frontend_dir.is_dir():
            app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")

    return app


app = create_app()
