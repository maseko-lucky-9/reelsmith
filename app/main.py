from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

import app.compat  # noqa: F401 — must run before any MoviePy import

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.bus.event_bus import AsyncEventBus
from app.bus.job_store import InMemoryJobStore, SqlJobStore
from app.domain.events import Event, EventType
from app.routers import (
    brand_templates,
    captions,
    clips,
    downloads,
    folders,
    jobs,
    media,
    renders,
    subtitle_images,
    transcriptions,
    uploads,
)
from app.settings import settings
from app.workers.orchestrator import run_orchestrator

import app.logging_config  # noqa: F401


def _make_store():
    if settings.job_store == "sql":
        return SqlJobStore()
    return InMemoryJobStore()


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

        cfg_path = Path(__file__).parents[1] / "alembic.ini"
        if cfg_path.exists():
            alembic_cfg = AlembicConfig(str(cfg_path))
            await asyncio.to_thread(alembic_command.upgrade, alembic_cfg, "head")

    app.state.event_bus = AsyncEventBus()
    app.state.job_store = _make_store()

    # Job queue with concurrency cap.
    # POST /jobs enqueues (job_id, payload); this worker fires VIDEO_REQUESTED
    # respecting max_concurrent_jobs via the semaphore.
    job_queue: asyncio.Queue[tuple[str, dict]] = asyncio.Queue()
    job_semaphore = asyncio.Semaphore(settings.max_concurrent_jobs)
    app.state.job_queue = job_queue

    async def _queue_worker():
        while True:
            job_id, payload = await job_queue.get()
            try:
                async with job_semaphore:
                    await app.state.event_bus.publish(
                        Event(
                            type=EventType.VIDEO_REQUESTED,
                            job_id=job_id,
                            payload=payload,
                        )
                    )
            except Exception:  # noqa: BLE001
                pass
            finally:
                job_queue.task_done()

    worker_task = asyncio.create_task(_queue_worker())
    app.state.orchestrator_task = asyncio.create_task(
        run_orchestrator(app.state.event_bus, app.state.job_store)
    )

    # Retention janitor — only active in sql mode.
    retention_task: asyncio.Task | None = None
    if settings.job_store == "sql":
        async def _janitor():
            import datetime
            from sqlalchemy import update as sa_update
            from app.db.models import ClipRecord
            from app.db.session import get_session_factory

            while True:
                await asyncio.sleep(settings.retention_sweep_minutes * 60)
                cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
                    days=settings.retention_days
                )
                try:
                    factory = get_session_factory()
                    async with factory() as session:
                        result = await session.execute(
                            sa_update(ClipRecord)
                            .where(ClipRecord.created_at < cutoff)
                            .where(ClipRecord.retired == False)  # noqa: E712
                            .values(retired=True)
                            .returning(ClipRecord.output_path, ClipRecord.thumbnail_path)
                        )
                        for row in result.all():
                            for path_field in row:
                                if path_field:
                                    p = Path(path_field)
                                    if p.exists():
                                        p.unlink(missing_ok=True)
                        await session.commit()
                except Exception:  # noqa: BLE001
                    pass

        retention_task = asyncio.create_task(_janitor())

    try:
        yield
    finally:
        for t in [worker_task, retention_task, app.state.orchestrator_task]:
            if t is None:
                continue
            t.cancel()
        task = app.state.orchestrator_task
        for t in [worker_task, retention_task, task]:
            if t is not None and not t.done():
                t.cancel()
                try:
                    await t
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
        await app.state.event_bus.aclose()

        if settings.job_store == "sql":
            from app.db.engine import dispose_engine
            await dispose_engine()

        executor.shutdown(wait=False)


def create_app() -> FastAPI:
    from app.auth import require_api_key

    dependencies = [Depends(require_api_key)] if settings.require_auth else []
    app = FastAPI(
        title="Reelsmith API",
        version="0.1.0",
        lifespan=lifespan,
        dependencies=dependencies,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list(),
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Health probe — Vite proxy strips /api prefix so this must live at /health.
    @app.get("/health", tags=["meta"])
    async def health() -> JSONResponse:
        return JSONResponse({"status": "ok", "job_store": settings.job_store})

    from app.services.platforms import UnsupportedPlatformError

    @app.exception_handler(UnsupportedPlatformError)
    async def _unsupported_platform_handler(request, exc: UnsupportedPlatformError):
        return JSONResponse(status_code=400, content={"detail": str(exc), "url": exc.url})

    app.include_router(jobs.router)
    app.include_router(clips.router)
    app.include_router(media.router)
    app.include_router(uploads.router)
    app.include_router(brand_templates.router)
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
