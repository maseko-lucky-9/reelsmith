from __future__ import annotations

import asyncio
import logging
import re
import shutil
import time
import uuid
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

from app.bus.event_bus import AsyncEventBus
from app.bus.job_store import JobStore
from app.domain.events import Event, EventType
from app.domain.models import ChapterArtifacts, JobState, PipelineOptions
from app.services import (
    ai_hook_service,
    audio_enhance_service,
    caption_service,
    clip_service,
    download_service,
    export_service,
    ffmpeg_tools,
    filler_removal_service,
    folder_service,
    manifest_service,
    ollama_service,
    platforms,
    render_service,
    thumbnail_service,
    transcription_service,
)
from app.services.platforms import resolve as resolve_adapter
from app.settings import settings

import app.logging_config  # noqa: F401

log = logging.getLogger(__name__)


_SAFE_CHAR_RE = re.compile(r"[^\w\-_. ]")
# Leading chapter index of a rendered clip's file name ({index:02d}_{title}.mp4).
_CLIP_INDEX_RE = re.compile(r"^(\d+)_")


def _sanitize(name: str) -> str:
    return _SAFE_CHAR_RE.sub("_", name).strip() or "chapter"


async def run_orchestrator(bus: AsyncEventBus, store: JobStore) -> None:
    """Subscribes to VIDEO_REQUESTED events and runs per-job pipelines.

    At most ``settings.max_concurrent_jobs`` pipelines run at once; a job past
    the cap waits for a slot with its status still ``pending``. Cancelling the
    orchestrator cancels running and waiting jobs alike.
    """
    log.info("Orchestrator subscribed; awaiting VideoRequested events")
    max_jobs = settings.max_concurrent_jobs
    if max_jobs < 1:
        log.warning(
            "max_concurrent_jobs=%d is below 1; running 1 job at a time", max_jobs
        )
        max_jobs = 1
    job_slots = asyncio.Semaphore(max_jobs)
    pipeline_tasks: set[asyncio.Task[Any]] = set()

    async def _run_when_slot_free(event: Event) -> None:
        async with job_slots:
            await _run_job(event, bus, store)

    try:
        async for event in bus.subscribe(types=[EventType.VIDEO_REQUESTED]):
            task = asyncio.create_task(_run_when_slot_free(event))
            pipeline_tasks.add(task)
            task.add_done_callback(pipeline_tasks.discard)
    except asyncio.CancelledError:
        for task in pipeline_tasks:
            task.cancel()
        # Wait for every job to unwind — cancellation is where their ffmpeg is
        # killed. return_exceptions: one job's CancelledError must not end
        # the wait for the others.
        await asyncio.gather(*pipeline_tasks, return_exceptions=True)
        raise


async def _emit(bus: AsyncEventBus, type_: EventType, job_id: str, **payload: Any) -> None:
    await bus.publish(Event(type=type_, job_id=job_id, payload=payload))


async def _run_job(trigger: Event, bus: AsyncEventBus, store: JobStore) -> None:
    if trigger.payload.get("rerender_clip_id"):
        await _rerender_clip(trigger, bus, store)
        return

    job_id = trigger.job_id
    payload = trigger.payload
    url: str = payload["url"]
    download_path: str = payload["download_path"]
    caption_format: str = payload.get("caption_format", settings.default_caption_format)
    target_aspect_ratio: float = payload.get(
        "target_aspect_ratio", settings.default_target_aspect_ratio
    )
    # Job language tag (e.g. "en-US"); None lets the transcription service
    # apply settings.default_transcription_language.
    language: str | None = payload.get("language")

    opts = _effective_options(payload.get("pipeline_options"))

    log.info("[%s] Job started  url=%s  format=%s", job_id, url, caption_format)
    job_t0 = time.perf_counter()

    cleanup_root: Path | None = None
    try:
        await store.update(job_id, lambda s: setattr(s, "status", "running"))

        # ── Resolve platform adapter ─────────────────────────────────────────
        adapter = resolve_adapter(url)
        log.info("[%s] Platform=%s", job_id, adapter.platform_id)

        # ── Folder ────────────────────────────────────────────────────────────
        log.info("[%s] Step: create folder  path=%s", job_id, download_path)
        step_t0 = time.perf_counter()
        await store.update(job_id, lambda s: setattr(s, "current_step", "folder"))
        destination, clips_folder = await asyncio.to_thread(
            folder_service.create_video_subfolder,
            download_path,
            url,
            adapter.platform_id,
            job_id=job_id,
        )
        log.info("[%s] Folder ready (%.2fs)  dest=%s", job_id, time.perf_counter() - step_t0, destination)
        cleanup_root = Path(clips_folder) / "_tmp" / job_id
        cleanup_root.mkdir(parents=True, exist_ok=True)

        def _set_folders(s: JobState) -> None:
            s.destination_folder = destination
            s.clips_folder = clips_folder

        await store.update(job_id, _set_folders)
        await _emit(
            bus,
            EventType.FOLDER_CREATED,
            job_id,
            destination_folder=destination,
            clips_folder=clips_folder,
        )

        # ── Download ──────────────────────────────────────────────────────────
        log.info("[%s] Step: download video", job_id)
        step_t0 = time.perf_counter()
        await store.update(job_id, lambda s: setattr(s, "current_step", "download"))
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(adapter.download, url, destination),
                timeout=settings.download_timeout_seconds,
            )
        except asyncio.TimeoutError:
            raise
        except Exception as e:
            log.error("[%s] download failed  url=%s  error=%s", job_id, url, e)
            raise RuntimeError(f"download failed: {e}") from e
        video_path = result.video_path
        info = result.info
        if not video_path or info is None:
            raise RuntimeError("download failed")

        title = info.get("title", "")
        duration = float(info.get("duration") or 0.0)
        log.info(
            "[%s] Download complete (%.2fs)  title=%r  duration=%.1fs  path=%s",
            job_id, time.perf_counter() - step_t0, title, duration, video_path,
        )

        def _set_download(s: JobState) -> None:
            s.video_path = video_path
            s.title = title
            s.duration = duration

        await store.update(job_id, _set_download)
        await _emit(
            bus,
            EventType.VIDEO_DOWNLOADED,
            job_id,
            video_path=video_path,
            title=title,
            duration=duration,
        )

        # ── Chapters ──────────────────────────────────────────────────────────
        log.info("[%s] Step: extract chapters", job_id)
        await store.update(job_id, lambda s: setattr(s, "current_step", "chapters"))
        raw_chapters = adapter.extract_chapters(info)
        chapters = [
            {"index": c.index, "title": c.title, "start": c.start, "end": c.end}
            for c in raw_chapters
        ]
        safe_end = await asyncio.to_thread(clip_service.probe_safe_end, video_path)

        if not chapters:
            if opts.segment_proposer:
                # Heuristic segment proposer would run here (future)
                chapters = [
                    {"index": 0, "title": "Full Video", "start": 0.0, "end": safe_end}
                ]
            else:
                # segment_proposer off → single full-video pseudo-chapter
                chapters = [
                    {"index": 0, "title": "Full Video", "start": 0.0, "end": safe_end}
                ]
                await _emit(
                    bus,
                    EventType.STAGE_SKIPPED,
                    job_id,
                    stage_id="segment_proposer",
                )
        else:
            clamped: list[dict[str, Any]] = []
            for c in chapters:
                cstart = max(0.0, float(c["start"]))
                cend = min(float(c["end"]), safe_end)
                if cend - cstart < 0.5:
                    log.warning(
                        "[%s] Dropping chapter %r post-clamp (duration=%.3fs)",
                        job_id, c.get("title"), cend - cstart,
                    )
                    continue
                clamped.append({**c, "start": cstart, "end": cend})
            chapters = clamped

        if not chapters:
            raise RuntimeError(
                f"all chapters out-of-bounds vs safe_end {safe_end:.3f}s "
                f"(yt-dlp duration {duration:.3f}s)"
            )

        if duration - safe_end > clip_service.AUDIO_TAIL_EPSILON_SECONDS + 0.5:
            log.info(
                "[%s] Audio EOF gap: yt-dlp=%.3fs safe_end=%.3fs (audio shorter than video?)",
                job_id, duration, safe_end,
            )

        log.info("[%s] %d chapter(s) detected: %s", job_id, len(chapters),
                 [c["title"] for c in chapters])
        await _emit(bus, EventType.CHAPTERS_DETECTED, job_id, chapters=chapters)

        # ── Per-chapter fan-out ───────────────────────────────────────────────
        semaphore = asyncio.Semaphore(settings.max_parallel_chapters)

        async def _bound(chapter: dict[str, Any]) -> str | None:
            async with semaphore:
                return await _process_chapter(
                    chapter=chapter,
                    job_id=job_id,
                    video_path=video_path,
                    clips_folder=clips_folder,
                    cleanup_root=cleanup_root,
                    caption_format=caption_format,
                    target_aspect_ratio=target_aspect_ratio,
                    bus=bus,
                    store=store,
                    pipeline_options=opts,
                    language=language,
                )

        # Structured fan-out: the first failing chapter cancels its siblings
        # and the group waits for them to unwind before the job is marked
        # failed, so no chapter emits events after JobFailed. Their ffmpeg
        # runs, transcription and caption-file writes go through
        # to_thread_cancellable: ffmpeg is killed, and the worker threads are
        # awaited, so nothing still writes into cleanup_root when it is
        # removed. The remaining plain asyncio.to_thread calls (caption
        # timing, thumbnail, ai_hook, ollama) cannot be interrupted and may
        # finish in the background; they write nothing into cleanup_root.
        # The job fails with the first chapter's own error, as with gather().
        try:
            async with asyncio.TaskGroup() as chapter_group:
                chapter_tasks = [chapter_group.create_task(_bound(c)) for c in chapters]
        except ExceptionGroup as group:
            first, *others = group.exceptions
            for other in others:
                log.warning(
                    "[%s] Another chapter also failed: %s", job_id, other,
                    exc_info=other,
                )
            raise first from None
        output_paths = [p for p in (t.result() for t in chapter_tasks) if p]

        # ── Export ────────────────────────────────────────────────────────────
        if settings.export_base_folder:
            export_dir = str(Path(settings.export_base_folder) / job_id)
        else:
            export_dir = str(Path(clips_folder).parent / "exports")
        exported_paths = await asyncio.to_thread(
            export_service.export_clips, output_paths, export_dir
        )
        await _emit(bus, EventType.EXPORT_COMPLETED, job_id,
                    export_dir=export_dir, count=len(exported_paths))

        # ── Manifest ──────────────────────────────────────────────────────────
        clips_data = await store.list_clips(job_id=job_id)
        path_map = {Path(p).stem: p for p in exported_paths}
        for clip in clips_data:
            stem = Path(clip.get("output_path") or "").stem
            clip["export_path"] = path_map.get(stem, "")

        manifest_path = await asyncio.to_thread(
            manifest_service.write_manifest, clips_data, export_dir
        )
        await _emit(bus, EventType.MANIFEST_CREATED, job_id, manifest_path=manifest_path)

        total_elapsed = time.perf_counter() - job_t0
        log.info(
            "[%s] Job completed in %.2fs  outputs=%d  paths=%s",
            job_id, total_elapsed, len(output_paths), output_paths,
        )

        def _complete(s: JobState) -> None:
            s.status = "completed"
            s.current_step = "completed"
            s.output_paths = output_paths

        await store.update(job_id, _complete)
        await _emit(bus, EventType.JOB_COMPLETED, job_id, output_paths=output_paths)

    except asyncio.CancelledError:
        log.warning("[%s] Job cancelled after %.2fs", job_id, time.perf_counter() - job_t0)
        raise
    except Exception as e:  # noqa: BLE001
        log.exception("[%s] Job failed after %.2fs", job_id, time.perf_counter() - job_t0)
        # A cancel (e.g. shutdown) may already be pending — TaskGroup re-arms
        # an outer cancel that hit while a chapter was failing — or arrive
        # now; record the failure regardless, then let the cancel through.
        await _finish_then_honour_cancel(_record_failure(bus, store, job_id, str(e)))
    finally:
        if cleanup_root is not None and cleanup_root.exists():
            shutil.rmtree(cleanup_root, ignore_errors=True)


def _effective_options(raw_opts: Any) -> PipelineOptions:
    """Pipeline options from a payload, with the server-side safety net (G2)."""
    if raw_opts and isinstance(raw_opts, dict):
        opts = PipelineOptions(**raw_opts)
    else:
        opts = PipelineOptions()

    # Enforce dependency rules server-side regardless of what UI sent
    if not opts.transcription:
        opts.captions = False
        opts.filler_removal = False
        opts.ai_hook = False
    if not opts.render:
        opts.reframe = False
        opts.broll = False
        opts.thumbnail = False
    return opts


def _chapter_for_clip(clip: dict[str, Any]) -> dict[str, Any]:
    """Rebuild the chapter a clip was rendered from.

    Chapter bounds are not persisted per chapter, but the clip keeps its own
    start/end/title, and its file name keeps the chapter index, so the
    re-render writes the same ``{index:02d}_{title}.mp4`` it replaces.
    """
    match = _CLIP_INDEX_RE.match(Path(clip["output_path"]).name)
    return {
        "index": int(match.group(1)) if match else 0,
        "title": clip.get("title") or "",
        "start": float(clip.get("start") or 0.0),
        "end": float(clip.get("end") or 0.0),
    }


async def _rerender_clip(trigger: Event, bus: AsyncEventBus, store: JobStore) -> None:
    """Re-render one clip of a completed job in place, from its saved source.

    The job's status, step, error and outputs are left alone and neither
    JobCompleted nor JobFailed is emitted. A failure is logged and swallowed:
    the clip keeps its fields, render_clip's atomic replace keeps its
    previous file, and the chapter gets back the status it had before the
    attempt (``completed`` if it had none). Cancellation propagates.
    """
    job_id = trigger.job_id
    payload = trigger.payload
    clip_id: str = payload["rerender_clip_id"]
    log.info("[%s] Re-render of clip %s started", job_id, clip_id)
    t0 = time.perf_counter()
    cleanup_root: Path | None = None
    chapter: dict[str, Any] | None = None
    prior_chapter_status = "completed"
    try:
        job = await store.get(job_id)
        clip = await store.get_clip(clip_id)
        if clip is None or clip.get("job_id") != job_id:
            log.error("[%s] Re-render skipped: clip %s not found", job_id, clip_id)
            return
        output_path = clip.get("output_path")
        if not output_path:
            log.error("[%s] Re-render skipped: clip %s was never rendered", job_id, clip_id)
            return
        if not job.video_path or not Path(job.video_path).is_file():
            log.error(
                "[%s] Re-render skipped: source video not retained (%s)",
                job_id, job.video_path,
            )
            return

        chapter = _chapter_for_clip(clip)
        prior_chapter = job.chapters.get(chapter["index"])
        if prior_chapter is not None:
            prior_chapter_status = prior_chapter.status
        clips_folder = str(Path(output_path).parent)
        cleanup_root = Path(clips_folder) / "_tmp" / f"{job_id}-rerender-{clip_id[:8]}"
        cleanup_root.mkdir(parents=True, exist_ok=True)
        # A re-render always renders, whatever the job's options said.
        raw_opts = payload.get("pipeline_options") or job.pipeline_options.model_dump()
        # reframe_provider is accepted but unused: reframe is unwired (task T012).
        await _process_chapter(
            chapter=chapter,
            job_id=job_id,
            video_path=job.video_path,
            clips_folder=clips_folder,
            cleanup_root=cleanup_root,
            caption_format=payload.get("caption_format", job.caption_format),
            target_aspect_ratio=payload.get("target_aspect_ratio", job.target_aspect_ratio),
            bus=bus,
            store=store,
            pipeline_options=_effective_options({**raw_opts, "render": True}),
            language=payload.get("language", job.language),
            clip_id=clip_id,
        )
        log.info(
            "[%s] Re-render of clip %s done in %.2fs",
            job_id, clip_id, time.perf_counter() - t0,
        )
    except asyncio.CancelledError:
        log.warning("[%s] Re-render of clip %s cancelled", job_id, clip_id)
        raise
    except Exception:  # noqa: BLE001 — a failed re-render must not fail the job
        log.exception(
            "[%s] Re-render of clip %s failed after %.2fs; clip left unchanged",
            job_id, clip_id, time.perf_counter() - t0,
        )
        if chapter is not None:
            await _restore_chapter_status(
                store, job_id, int(chapter["index"]), prior_chapter_status
            )
    finally:
        if cleanup_root is not None:
            shutil.rmtree(cleanup_root, ignore_errors=True)


async def _restore_chapter_status(
    store: JobStore, job_id: str, index: int, status: str
) -> None:
    """Put a chapter back to ``status`` after a failed re-render (T028a);
    otherwise it stays at the stage that failed, e.g. ``rendering``."""

    def _reset(c: ChapterArtifacts) -> None:
        c.status = status  # type: ignore[assignment]

    try:
        await store.upsert_chapter(job_id, _reset, index)
    except Exception:  # noqa: BLE001 — best effort; the re-render already failed
        log.exception(
            "[%s] Could not reset chapter %d status after a failed re-render",
            job_id, index,
        )


async def _record_failure(
    bus: AsyncEventBus, store: JobStore, job_id: str, error: str
) -> None:
    def _fail(s: JobState) -> None:
        s.status = "failed"
        s.error = error

    await store.update(job_id, _fail)
    await _emit(
        bus,
        EventType.JOB_FAILED,
        job_id,
        failed_step=(await store.get(job_id)).current_step,
        error=error,
    )


async def _finish_then_honour_cancel(coro: Coroutine[Any, Any, None]) -> None:
    """Run ``coro`` to completion even if the current task is (or gets)
    cancelled meanwhile; then re-raise that cancellation.

    Raises:
        asyncio.CancelledError: the current task was cancelled before or
            while ``coro`` ran.
    """
    current = asyncio.current_task()
    cancelled = current is not None and current.cancelling() > 0
    inner = asyncio.create_task(coro)
    while not inner.done():
        try:
            # asyncio.wait never cancels what it waits on.
            await asyncio.wait({inner})
        except asyncio.CancelledError:
            cancelled = True
    inner.result()
    if cancelled:
        raise asyncio.CancelledError


async def _process_chapter(
    *,
    chapter: dict[str, Any],
    job_id: str,
    video_path: str,
    clips_folder: str,
    cleanup_root: Path,
    caption_format: str,
    target_aspect_ratio: float,
    bus: AsyncEventBus,
    store: JobStore,
    pipeline_options: PipelineOptions | None = None,
    language: str | None = None,
    clip_id: str | None = None,
) -> str | None:
    """Process one chapter into a clip; ``clip_id`` updates an existing clip
    in place (re-render) instead of creating a new one."""
    index = int(chapter["index"])
    title = chapter["title"]
    start = float(chapter["start"])
    end = float(chapter["end"])
    chapter_duration = end - start

    log.info("[%s] Chapter %d/%d start  title=%r  %.1f–%.1fs (%.1fs)",
             job_id, index, index, title, start, end, chapter_duration)
    chapter_t0 = time.perf_counter()

    # TODO(orchestrator-wiring-wave-2): integrate the remaining W1-W3 services.
    # The following services are imported in the codebase but not yet wired
    # into _process_chapter — each needs a deliberate insertion point + UX
    # design before it lands:
    #   * voiceover_service             — TTS over captions; replaces or
    #                                     augments the original audio track
    #                                     after filler_removal.
    #   * animated_caption_service      — burned-in animated subs; either
    #                                     replaces the static subtitle_image
    #                                     pass or feeds an additional render
    #                                     overlay.
    #   * transition_service            — clip-to-clip transitions when more
    #                                     than one chapter exists; orchestrator
    #                                     would need a post-chapter join pass.
    #   * brand_vocabulary_service      — pronunciation / spelling overrides
    #                                     applied to transcript before captions.
    #   * profanity_filter_service      — bleep/redact pass on words/captions.
    #   * active_speaker_service        — informs reframe; would slot in just
    #                                     before render when reframe=True.
    opts = pipeline_options or PipelineOptions()
    tmp_dir = cleanup_root

    def _set_status(state: ChapterArtifacts, status: str) -> None:
        state.status = status  # type: ignore[assignment]

    # ── Extract chapter audio (gated on transcription) ───────────────────────
    # The reel is rendered straight from the source (render_clip trims it),
    # so the only per-chapter artefact is the transcription wav: 16 kHz mono,
    # cut with the render's exact -ss/-t window so word timings line up.
    audio_path: str | None = None
    if opts.transcription:
        wav_path = str(tmp_dir / f"chapter_{index}.wav")
        log.info("[%s] Chapter %d  extracting audio", job_id, index)
        step_t0 = time.perf_counter()
        await store.upsert_chapter(job_id, lambda c: _set_status(c, "extracting"), index)
        audio_path = await ffmpeg_tools.to_thread_cancellable(
            clip_service.extract_audio, video_path, start, chapter_duration, wav_path
        )
        log.info("[%s] Chapter %d  audio extracted (%.2fs)  audio=%s",
                 job_id, index, time.perf_counter() - step_t0, audio_path)
        if audio_path is not None:
            await store.upsert_chapter(
                job_id, lambda c: setattr(c, "audio_path", audio_path), index
            )
    # The web UI's "Extract clips" stage (render-gated) still counts this event;
    # there is no intermediate chapter clip any more, so clip_path is None.
    if opts.render:
        await _emit(
            bus,
            EventType.CHAPTER_CLIP_EXTRACTED,
            job_id,
            chapter_index=index,
            clip_path=None,
            audio_path=audio_path,
        )

    # ── Audio enhancement (W1.8 — gated on audio_enhance) ────────────────────
    # Replace the on-disk audio with the enhanced output so transcription
    # consumes the cleaned track (the reel keeps the source's original audio).
    # Runs only when a chapter wav was extracted above.
    if opts.audio_enhance and audio_path is not None and Path(audio_path).exists():
        log.info(
            "[%s] Chapter %d  enhancing audio  provider=%s",
            job_id, index, settings.audio_enhance_provider,
        )
        step_t0 = time.perf_counter()
        enhanced_audio_path = str(tmp_dir / f"chapter_{index}_enhanced.wav")
        try:
            await ffmpeg_tools.to_thread_cancellable(
                audio_enhance_service.enhance,
                audio_path,
                enhanced_audio_path,
                provider=settings.audio_enhance_provider,
                model_path=settings.audio_enhance_rnnoise_model,
                for_transcription=True,
            )
            # Hand-off: subsequent stages should read the enhanced track.
            audio_path = enhanced_audio_path
            await store.upsert_chapter(
                job_id, lambda c: setattr(c, "audio_path", enhanced_audio_path), index
            )
            log.info(
                "[%s] Chapter %d  audio enhanced (%.2fs)  path=%s",
                job_id, index, time.perf_counter() - step_t0, enhanced_audio_path,
            )
        except Exception as exc:  # noqa: BLE001
            # Non-fatal: fall back to the original audio. Enhancement is
            # quality-of-life and must never block the rest of the pipeline.
            log.warning(
                "[%s] Chapter %d  audio enhancement failed (%s); using original audio",
                job_id, index, exc,
            )
        else:
            # Emitted here, not by the service: it runs in a worker thread,
            # where emit_from_sync has no event loop and does nothing.
            await _emit(
                bus, EventType.AUDIO_ENHANCED, job_id,
                chapter_index=index,
                provider=settings.audio_enhance_provider,
                audio_path=enhanced_audio_path,
            )
    elif not opts.audio_enhance:
        log.info("[%s] Chapter %d  skipping audio_enhance", job_id, index)
        await _emit(
            bus, EventType.STAGE_SKIPPED, job_id,
            stage_id="audio_enhance", chapter_index=index,
        )

    # ── Transcribe ────────────────────────────────────────────────────────────
    words = []
    text = ""
    if opts.transcription:
        log.info("[%s] Chapter %d  transcribing audio  provider=%s  language=%s",
                 job_id, index, settings.transcription_provider, language)
        step_t0 = time.perf_counter()
        await store.upsert_chapter(job_id, lambda c: _set_status(c, "transcribing"), index)
        if audio_path is None:
            log.warning("[%s] Chapter %d  source has no audio; nothing to transcribe",
                        job_id, index)
        else:
            # Budget max(setting, chapter length), charged from decode start;
            # a timeout/cancel stops the worker at its next segment.
            words = await transcription_service.transcribe_words_async(
                audio_path, language=language, audio_duration_s=chapter_duration
            )
        text = " ".join(w.word for w in words)
        log.info("[%s] Chapter %d  transcription done (%.2fs)  words=%d",
                 job_id, index, time.perf_counter() - step_t0, len(words))
        await store.upsert_chapter(job_id, lambda c: setattr(c, "transcript", text), index)
        await _emit(bus, EventType.CHAPTER_TRANSCRIBED, job_id, chapter_index=index, text=text)
    else:
        log.info("[%s] Chapter %d  skipping transcription", job_id, index)
        await _emit(bus, EventType.STAGE_SKIPPED, job_id, stage_id="transcribe", chapter_index=index)

    # ── Filler removal (W2.5 — gated on filler_removal + transcription) ──────
    # Strip filler tokens from the word list so downstream caption/render
    # stages operate on the cleaned transcript. We rebuild ``text`` to keep
    # social-content + clip.transcript in sync with the trimmed words.
    if opts.filler_removal and opts.transcription and words:
        log.info("[%s] Chapter %d  removing fillers  before=%d", job_id, index, len(words))
        step_t0 = time.perf_counter()
        words_before = len(words)
        spans = [
            filler_removal_service.WordSpan(text=w.word, start=w.start, end=w.end)
            for w in words
        ]
        # plan_keep_intervals collapses the kept WordSpans into (start, end)
        # intervals. For caption/render alignment we want the per-word list
        # filtered, which we do here directly with the same allowlist.
        allowlist = {f.lower() for f in filler_removal_service.DEFAULT_FILLERS}
        kept_words = [
            w for w in words
            if not filler_removal_service._is_filler(w.word, allowlist)
        ]
        if kept_words:
            words = kept_words
            text = " ".join(w.word for w in words)
            await store.upsert_chapter(
                job_id, lambda c: setattr(c, "transcript", text), index
            )
        log.info(
            "[%s] Chapter %d  filler removal done (%.2fs)  after=%d",
            job_id, index, time.perf_counter() - step_t0, len(words),
        )
        # words_removed is 0 when nothing matched, or when every word was a
        # filler (the transcript is then kept as is).
        await _emit(
            bus, EventType.FILLERS_REMOVED, job_id,
            chapter_index=index,
            words_removed=words_before - len(words),
            words_kept=len(words),
        )
    elif not opts.filler_removal:
        await _emit(
            bus, EventType.STAGE_SKIPPED, job_id,
            stage_id="filler_removal", chapter_index=index,
        )

    # ── Captions ──────────────────────────────────────────────────────────────
    captions_obj = None
    captions_path: str | None = None
    if opts.captions and opts.transcription:
        log.info("[%s] Chapter %d  generating captions  format=%s  words_per_segment=%d",
                 job_id, index, caption_format, settings.caption_words_per_segment)
        step_t0 = time.perf_counter()
        await store.upsert_chapter(job_id, lambda c: _set_status(c, "captioning"), index)
        captions_obj = await asyncio.to_thread(
            caption_service.generate_captions_from_word_timings,
            words, settings.caption_words_per_segment, caption_format,
        )
        captions_path = str(tmp_dir / f"chapter_{index}.{caption_format}")
        # Cancellable: a cancelled chapter waits for this write into tmp_dir
        # to finish, so the job's tmp cleanup never races it.
        await ffmpeg_tools.to_thread_cancellable(
            caption_service.write_captions, captions_obj, caption_format, captions_path
        )
        caption_count = len(captions_obj) if captions_obj is not None else 0
        log.info("[%s] Chapter %d  captions written (%.2fs)  count=%d  path=%s",
                 job_id, index, time.perf_counter() - step_t0, caption_count, captions_path)
        await store.upsert_chapter(
            job_id, lambda c: setattr(c, "captions_path", captions_path), index
        )
        await _emit(
            bus,
            EventType.CAPTIONS_GENERATED,
            job_id,
            chapter_index=index,
            format=caption_format,
            captions_path=captions_path,
        )
    else:
        log.info("[%s] Chapter %d  skipping captions", job_id, index)
        await _emit(bus, EventType.STAGE_SKIPPED, job_id, stage_id="caption", chapter_index=index)

    # Caption images are drawn and burned in by render_clip (one PNG per unique
    # caption, cropped); ChapterArtifacts.image_paths stays [] for the API/UI.

    # ── Render final clip (gated on render) ──────────────────────────────────
    output_path: str | None = None
    safe_title = _sanitize(title)
    if opts.render:
        log.info("[%s] Chapter %d  rendering final clip  aspect=%.4f", job_id, index, target_aspect_ratio)
        step_t0 = time.perf_counter()
        await store.upsert_chapter(job_id, lambda c: _set_status(c, "rendering"), index)
        output_path = str(Path(clips_folder) / f"{index:02d}_{safe_title}.mp4")
        # Reads the SOURCE and trims [start, end) itself — one ffmpeg pass.
        # Cancellation (e.g. this wait_for timing out) kills that ffmpeg.
        await asyncio.wait_for(
            ffmpeg_tools.to_thread_cancellable(
                render_service.render_clip,
                video_path,
                output_path,
                start,
                end,
                captions_path,
                target_aspect_ratio,
                word_timings=words,
                caption_words_per_segment=settings.caption_words_per_segment,
            ),
            timeout=settings.render_timeout_seconds,
        )
        log.info("[%s] Chapter %d  render done (%.2fs)  output=%s",
                 job_id, index, time.perf_counter() - step_t0, output_path)
        log.info("[%s] Chapter %d  finished in %.2fs", job_id, index, time.perf_counter() - chapter_t0)

        await store.upsert_chapter(
            job_id,
            lambda c: (setattr(c, "output_path", output_path), _set_status(c, "completed")),
            index,
        )
        await _emit(
            bus,
            EventType.CLIP_RENDERED,
            job_id,
            chapter_index=index,
            output_path=output_path,
        )
    else:
        log.info("[%s] Chapter %d  skipping render (render=False)", job_id, index)
        await _emit(bus, EventType.STAGE_SKIPPED, job_id, stage_id="render", chapter_index=index)
        # Also emit skips for dependent stages
        if not opts.reframe:
            await _emit(bus, EventType.STAGE_SKIPPED, job_id, stage_id="reframe", chapter_index=index)
        if not opts.broll:
            await _emit(bus, EventType.STAGE_SKIPPED, job_id, stage_id="broll", chapter_index=index)
        if not opts.thumbnail:
            await _emit(bus, EventType.STAGE_SKIPPED, job_id, stage_id="thumbnail", chapter_index=index)
        await store.upsert_chapter(
            job_id,
            lambda c: _set_status(c, "completed"),
            index,
        )

    # ── Thumbnail (gated on render + thumbnail) ──────────────────────────────
    clip_id = clip_id or str(uuid.uuid4())
    thumbnail_path: str | None = None
    if opts.render and opts.thumbnail and output_path:
        try:
            step_t0 = time.perf_counter()
            thumbnail_out = str(Path(clips_folder) / f"{index:02d}_{safe_title}_thumb.jpg")
            thumbnail_path = await asyncio.to_thread(
                thumbnail_service.generate_thumbnail, output_path, thumbnail_out
            )
            log.info("[%s] Chapter %d  thumbnail generated (%.2fs)  path=%s",
                     job_id, index, time.perf_counter() - step_t0, thumbnail_path)
            await _emit(bus, EventType.THUMBNAIL_GENERATED, job_id, chapter_index=index, thumbnail_path=thumbnail_path)
        except Exception as e:  # noqa: BLE001
            log.warning("[%s] Chapter %d  thumbnail failed: %s", job_id, index, e)
    elif opts.render and not opts.thumbnail:
        log.info("[%s] Chapter %d  skipping thumbnail", job_id, index)
        await _emit(bus, EventType.STAGE_SKIPPED, job_id, stage_id="thumbnail", chapter_index=index)

    # ── Upsert clip record ────────────────────────────────────────────────────
    def _init_clip(c: dict[str, Any]) -> None:
        c.update({
            "start": start,
            "end": end,
            "output_path": output_path,
            "thumbnail_path": thumbnail_path,
            "title": title,
            "transcript": text,
        })

    await store.upsert_clip(job_id, clip_id, _init_clip)

    # ── AI hook (W1.7 — gated on ai_hook + transcript) ───────────────────────
    # Generate a single punchy opening line per clip and stash it on the
    # clip record (DB column ``ai_hook_text``). Needs the transcript to be
    # meaningful, so we skip silently when transcription was off or empty.
    if opts.ai_hook and text.strip():
        log.info("[%s] Chapter %d  generating AI hook  model=%s", job_id, index, settings.ollama_model)
        step_t0 = time.perf_counter()
        try:
            hook = await asyncio.to_thread(
                ai_hook_service.generate_hook,
                text,
                base_url=settings.ollama_base_url,
                model=settings.ollama_model,
                timeout=settings.ollama_timeout_seconds,
                max_chars=getattr(settings, "ai_hook_max_chars", 80),
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("[%s] Chapter %d  ai_hook failed: %s", job_id, index, exc)
            hook = ""

        if hook:
            def _set_hook(c: dict[str, Any]) -> None:
                c["ai_hook_text"] = hook

            await store.upsert_clip(job_id, clip_id, _set_hook)
            log.info(
                "[%s] Chapter %d  ai_hook done (%.2fs)  hook=%r",
                job_id, index, time.perf_counter() - step_t0, hook,
            )
            await _emit(
                bus, EventType.AI_HOOK_GENERATED, job_id,
                chapter_index=index, clip_id=clip_id, hook=hook,
            )
        else:
            log.info("[%s] Chapter %d  ai_hook returned empty; skipping", job_id, index)
    elif not opts.ai_hook:
        await _emit(
            bus, EventType.STAGE_SKIPPED, job_id,
            stage_id="ai_hook", chapter_index=index,
        )

    # ── Social content ────────────────────────────────────────────────────────
    if settings.ollama_enabled:
        description, hashtags = await asyncio.to_thread(
            ollama_service.generate_social_content,
            title,
            text,
            settings.ollama_base_url,
            settings.ollama_model,
            settings.ollama_timeout_seconds,
        )
    else:
        description, hashtags = "", []

    def _update_social(c: dict[str, Any]) -> None:
        c["summary"] = description
        c["hashtags"] = hashtags

    await store.upsert_clip(job_id, clip_id, _update_social)
    await _emit(
        bus, EventType.SOCIAL_CONTENT_GENERATED, job_id,
        chapter_index=index, description=description, hashtag_count=len(hashtags),
    )

    return output_path
