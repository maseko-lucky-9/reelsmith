from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

import app.logging_config  # noqa: F401
from app.api_prefix import ApiPrefixMiddleware
from app.bus.event_bus import AsyncEventBus
from app.bus.job_store import InMemoryJobStore, SqlJobStore
from app.domain.events import Event, EventType
from app.routers import (
    ai_hook,
    brand_templates,
    bulk_export,
    captions,
    clip_edits,
    clips,
    downloads,
    enhance_speech,
    folders,
    generate,
    jobs,
    media,
    renders,
    reprompt,
    social_publish,
    subtitle_images,
    transcriptions,
    uploads,
    xml_export,
)
from app.services import transcription_service
from app.services.retention import run_retention_sweeps
from app.settings import settings
from app.spa_fallback import SpaFallbackMiddleware
from app.workers.orchestrator import run_orchestrator

log = logging.getLogger(__name__)

# The built React app, served at "/" when YTVIDEO_SERVE_FRONTEND=true.
FRONTEND_DIST = Path(__file__).parents[1] / "web" / "dist"


async def _warm_up_whisper() -> None:
    """Load the Whisper model off the event loop; failure only costs the first job."""
    try:
        await asyncio.to_thread(transcription_service.warm_up)
    except Exception:  # noqa: BLE001 — non-fatal: the first job loads it instead
        log.warning("Whisper warm-up failed; the first job will load the model",
                    exc_info=True)


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
    if settings.job_store == "sql" and not settings.skip_alembic:
        from alembic import command as alembic_command
        from alembic.config import Config as AlembicConfig

        cfg_path = Path(__file__).parents[1] / "alembic.ini"
        if cfg_path.exists():
            alembic_cfg = AlembicConfig(str(cfg_path))
            await asyncio.to_thread(alembic_command.upgrade, alembic_cfg, "head")

    app.state.event_bus = AsyncEventBus()
    app.state.job_store = _make_store()

    # Jobs a previous process left pending/running have no pipeline any more
    # (the queue and the tasks died with it); fail them so they don't block
    # their URL in the duplicate check. Before the queue worker starts, so no
    # new job can be caught by it. No events: nobody is subscribed yet.
    if settings.job_store == "sql":
        interrupted = await app.state.job_store.fail_interrupted_jobs()
        if interrupted:
            log.warning(
                "Marked %d job(s) interrupted by restart as failed: %s",
                len(interrupted), interrupted,
            )

    # Job queue: the routers (jobs, uploads, generate, clips rerender) enqueue
    # (job_id, payload); this worker turns each into a VIDEO_REQUESTED event.
    # The max_concurrent_jobs cap is enforced by run_orchestrator around each
    # pipeline, not here (publish() returns immediately).
    job_queue: asyncio.Queue[tuple[str, dict]] = asyncio.Queue()
    app.state.job_queue = job_queue

    async def _queue_worker():
        while True:
            job_id, payload = await job_queue.get()
            try:
                await app.state.event_bus.publish(
                    Event(
                        type=EventType.VIDEO_REQUESTED,
                        job_id=job_id,
                        payload=payload,
                    )
                )
            except Exception:  # noqa: BLE001 — keep the worker alive
                log.exception("[%s] failed to publish VideoRequested", job_id)
            finally:
                job_queue.task_done()

    worker_task = asyncio.create_task(_queue_worker())
    # Background model load so the first job doesn't pay for it. The reference
    # is held on app.state; the thread can't be interrupted, so shutdown only
    # cancels the waiting task.
    app.state.whisper_warmup_task = None
    if settings.transcription_provider == "whisper" and settings.whisper_warmup:
        app.state.whisper_warmup_task = asyncio.create_task(_warm_up_whisper())
    app.state.orchestrator_task = asyncio.create_task(
        run_orchestrator(app.state.event_bus, app.state.job_store)
    )

    # Retention janitor — only active in sql mode.
    retention_task: asyncio.Task | None = None
    if settings.job_store == "sql":
        async def _janitor():
            from app.db.session import get_session_factory

            while True:
                await asyncio.sleep(settings.retention_sweep_minutes * 60)
                try:
                    # Expired clips, unused sources, retired clip files (T033).
                    await run_retention_sweeps(
                        get_session_factory(), now=datetime.now(UTC)
                    )
                except Exception:  # noqa: BLE001 — keep the janitor alive
                    log.exception("Retention sweep failed")

        retention_task = asyncio.create_task(_janitor())

    try:
        yield
    finally:
        warmup_task = app.state.whisper_warmup_task
        for t in [worker_task, retention_task, app.state.orchestrator_task, warmup_task]:
            if t is None:
                continue
            t.cancel()
        task = app.state.orchestrator_task
        for t in [worker_task, retention_task, task, warmup_task]:
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
    # Every route answers at /x and /api/x: the Vite dev proxy strips /api,
    # the UI served by serve_frontend does not (T034). Routers stay unprefixed.
    app.add_middleware(ApiPrefixMiddleware)

    # Health probe: /health, and /api/health through ApiPrefixMiddleware.
    @app.get("/health", tags=["meta"])
    async def health() -> JSONResponse:
        return JSONResponse({"status": "ok", "job_store": settings.job_store})

    from app.services.platforms import UnsupportedPlatformError

    @app.exception_handler(UnsupportedPlatformError)
    async def _unsupported_platform_handler(request, exc: UnsupportedPlatformError):
        return JSONResponse(status_code=400, content={"detail": str(exc), "url": exc.url})

    app.include_router(jobs.router)
    app.include_router(generate.router)
    app.include_router(clips.router)
    app.include_router(clip_edits.router)
    app.include_router(media.router)
    app.include_router(uploads.router)
    app.include_router(brand_templates.router)
    app.include_router(folders.router)
    app.include_router(downloads.router)
    app.include_router(transcriptions.router)
    app.include_router(captions.router)
    app.include_router(subtitle_images.router)
    app.include_router(renders.router)
    app.include_router(social_publish.router)
    app.include_router(xml_export.router)
    app.include_router(ai_hook.router)
    app.include_router(enhance_speech.router)
    app.include_router(reprompt.router)
    app.include_router(bulk_export.router)

    # Serve the built React app in production (YTVIDEO_SERVE_FRONTEND=true).
    if settings.serve_frontend:
        frontend_dir = FRONTEND_DIST
        if frontend_dir.is_dir():
            # Reloading a client route (/uploads/new, /jobs/<id>) gets index.html
            # (T036). Added after ApiPrefixMiddleware, so it runs before it and
            # still sees the /api prefix.
            app.add_middleware(SpaFallbackMiddleware, dist=frontend_dir)
            app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")

    return app


app = create_app()
