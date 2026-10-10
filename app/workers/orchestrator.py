from __future__ import annotations

import asyncio
import logging
import re
import shutil
import time
import uuid
from collections.abc import Callable, Coroutine, Sequence
from pathlib import Path
from typing import Any

import app.logging_config  # noqa: F401
from app.bus.event_bus import AsyncEventBus
from app.bus.job_store import JobStore
from app.domain.events import Event, EventType
from app.domain.models import ChapterArtifacts, JobState, PipelineOptions
from app.services import (
    ai_hook_service,
    audio_enhance_service,
    broll_planner,
    broll_service,
    caption_service,
    clip_service,
    export_service,
    ffmpeg_tools,
    filler_removal_service,
    folder_service,
    manifest_service,
    ollama_service,
    reframe_service,
    render_service,
    segment_discovery,
    segment_proposer,
    thumbnail_service,
    transcription_service,
)
from app.services.platforms import resolve as resolve_adapter
from app.settings import settings

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
    if trigger.payload.get("reprompt"):
        await _reprompt_job(trigger, bus, store)
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
        # Full-source word timings, set when discovery transcribed the whole
        # source; each chapter then reuses them instead of re-transcribing.
        source_words: list[transcription_service.WordTiming] | None = None

        if not chapters:
            if not opts.segment_proposer:
                # segment_proposer off → single full-video pseudo-chapter
                chapters = [_full_video_chapter(safe_end)]
                await _emit(
                    bus,
                    EventType.STAGE_SKIPPED,
                    job_id,
                    stage_id="segment_proposer",
                )
            elif _discovery_enabled(opts, payload.get("segment_mode", "auto")):
                chapters, source_words = await _discover_segments(
                    job_id=job_id,
                    video_path=video_path,
                    safe_end=safe_end,
                    cleanup_root=cleanup_root,
                    opts=opts,
                    language=language,
                    prompt=payload.get("prompt"),
                    bus=bus,
                )
            else:
                # Provider "chapter", transcription off or segment_mode
                # "chapter": the whole source is one pseudo-chapter.
                chapters = [_full_video_chapter(safe_end)]
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
                    source_words=source_words,
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


def _full_video_chapter(safe_end: float) -> dict[str, Any]:
    return {"index": 0, "title": "Full Video", "start": 0.0, "end": safe_end}


def _discovery_enabled(opts: PipelineOptions, segment_mode: str) -> bool:
    """Whether a source without chapters gets clips discovered (FR-009).

    Needs the job's ``segment_proposer`` and ``transcription`` options,
    ``segment_mode == "auto"``, and a ``segment_provider`` other than
    ``"chapter"``; otherwise the source stays one "Full Video" chapter.
    """
    return (
        opts.segment_proposer
        and opts.transcription
        and segment_mode == "auto"
        and settings.segment_provider != "chapter"
    )


async def _discover_segments(
    *,
    job_id: str,
    video_path: str,
    safe_end: float,
    cleanup_root: Path,
    opts: PipelineOptions,
    language: str | None,
    prompt: str | None,
    bus: AsyncEventBus,
) -> tuple[list[dict[str, Any]], list[transcription_service.WordTiming] | None]:
    """Chapters for the best-scoring windows of a source without chapters.

    Transcribes the whole source once (its 16 kHz wav also feeds the
    proposer's loudness features and is deleted afterwards), saves the words
    as the ``<source stem>.words.json`` sidecar, scores candidate windows with
    ``get_segment_proposer()`` and keeps the best (``select_discovered``).
    Emits ``SegmentsProposed`` and one ``SegmentScored`` per kept segment.

    Returns the chapters and the full-source words (``None`` when the source
    was not transcribed). A source shorter than the minimum clip length, no
    kept segment, or any failure yields the single "Full Video" chapter, so
    discovery never fails the job; cancellation propagates.
    """
    full_video = [_full_video_chapter(safe_end)]
    min_secs, max_secs = segment_discovery.clip_length_range(opts)
    if safe_end < min_secs:
        log.info(
            "[%s] Source (%.1fs) shorter than the minimum clip (%ds); one Full Video clip",
            job_id, safe_end, min_secs,
        )
        await _emit(
            bus, EventType.STAGE_SKIPPED, job_id,
            stage_id="segment_proposer", reason="source shorter than the minimum clip",
        )
        return full_video, None

    log.info(
        "[%s] Step: discover clips  provider=%s  length=%d-%ds",
        job_id, settings.segment_provider, min_secs, max_secs,
    )
    step_t0 = time.perf_counter()
    words: list[transcription_service.WordTiming] | None = None
    wav_path = cleanup_root / "discover_source.wav"
    try:
        audio_path = await ffmpeg_tools.to_thread_cancellable(
            clip_service.extract_audio, video_path, 0.0, safe_end, str(wav_path)
        )
        if audio_path is None:
            raise RuntimeError("source has no audio stream")
        words = await transcription_service.transcribe_words_async(
            audio_path, language=language, audio_duration_s=safe_end
        )
        try:
            await asyncio.to_thread(
                segment_discovery.write_words_sidecar, video_path, words
            )
        except OSError as e:
            # Only re-renders lose out: they transcribe their window again.
            log.warning("[%s] Could not save the words sidecar: %s", job_id, e)
        proposer = segment_proposer.get_segment_proposer(
            min_secs=min_secs, max_secs=max_secs
        )
        candidates = await asyncio.to_thread(
            proposer.propose, words, audio_path, [], safe_end, prompt=prompt
        )
        kept = segment_discovery.select_discovered(candidates, safe_end)
        chapters = segment_discovery.segments_to_chapters(kept, safe_end)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001 — discovery must never fail the job
        log.exception("[%s] Clip discovery failed; one Full Video clip", job_id)
        await _emit(
            bus, EventType.STAGE_SKIPPED, job_id,
            stage_id="segment_proposer", reason=f"discovery failed: {e}",
        )
        return full_video, words
    finally:
        wav_path.unlink(missing_ok=True)

    log.info(
        "[%s] Discovery done (%.2fs)  words=%d  candidates=%d  kept=%s",
        job_id, time.perf_counter() - step_t0, len(words), len(candidates),
        [(c["start"], c["end"], c["virality_score"]) for c in chapters],
    )
    await _emit(
        bus, EventType.SEGMENTS_PROPOSED, job_id,
        count=len(chapters), candidates=len(candidates),
    )
    for c in chapters:
        await _emit(
            bus, EventType.SEGMENT_SCORED, job_id,
            index=c["index"], start=c["start"], end=c["end"],
            score=c["virality_score"], breakdown=c["score_breakdown"],
        )
    if not chapters:
        log.warning("[%s] No segment kept; one Full Video clip", job_id)
        return full_video, words
    return chapters, words


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
        # Words saved by clip discovery: reused instead of re-transcribing.
        source_words = await asyncio.to_thread(
            segment_discovery.read_words_sidecar, job.video_path
        )
        if source_words is not None:
            log.info(
                "[%s] Re-render of clip %s reuses the source words sidecar (%d words)",
                job_id, clip_id, len(source_words),
            )
        # The payload's reframe_provider is ignored: the provider is the
        # server's settings.reframe_provider (see _reframe_step).
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
            regenerate_copy=bool(payload.get("regenerate_copy", True)),
            source_words=source_words,
            render_to=output_path,
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


# ── Reprompt (FR-016) ─────────────────────────────────────────────────────────

# Jobs whose reprompt was accepted and has not finished. In-process only: a
# reprompt never changes the job's status (it stays "completed"), so a restart,
# which loses the queue and this set, leaves nothing to recover.
_reprompts_in_flight: set[str] = set()


def claim_reprompt(job_id: str) -> bool:
    """Mark a reprompt of ``job_id`` in flight; False if one already is.

    ``POST /jobs/{id}/reprompt`` claims before it queues the reprompt;
    ``_reprompt_job`` releases the claim when it ends, however it ends.
    """
    if job_id in _reprompts_in_flight:
        return False
    _reprompts_in_flight.add(job_id)
    return True


def release_reprompt(job_id: str) -> None:
    _reprompts_in_flight.discard(job_id)


def reprompt_in_flight(job_id: str) -> bool:
    return job_id in _reprompts_in_flight


def reprompt_unavailable_reason() -> str | None:
    """Why a reprompt cannot propose clips (provider ``chapter`` has no
    proposer), or None when it can. A reprompt of an explicit time range
    needs no proposer."""
    if settings.segment_provider == "chapter":
        return (
            "clip discovery is off (segment_provider=chapter); set "
            "YTVIDEO_SEGMENT_PROVIDER to local_heuristic to reprompt"
        )
    return None


def _clock(seconds: float) -> str:
    whole = int(seconds)
    return f"{whole // 60}:{whole % 60:02d}"


def _span_chapter(start: float, end: float, safe_end: float) -> dict[str, Any]:
    """The one chapter of an explicit reprompt range, clamped to the source."""
    cstart = max(0.0, float(start))
    cend = min(float(end), safe_end)
    if cend - cstart < segment_discovery.MIN_CHAPTER_SECONDS:
        raise RuntimeError(
            f"range {start:g}-{end:g}s is outside the source (it ends at {safe_end:.1f}s)"
        )
    return {
        "index": 0,
        "title": f"Clip {_clock(cstart)}-{_clock(cend)}",
        "start": cstart,
        "end": cend,
    }


def _reprompt_clips_folder(job: JobState, live_clips: list[dict[str, Any]]) -> str:
    """Where the job's clips live: the job's clips folder (memory store), else
    the folder of a live clip (the SQL store keeps no folder), else
    ``clips`` next to the source."""
    if job.clips_folder:
        return job.clips_folder
    for clip in live_clips:
        if clip.get("output_path"):
            return str(Path(clip["output_path"]).parent)
    return str(Path(job.video_path or ".").parent / "clips")


def _next_clip_index(
    job: JobState, live_clips: list[dict[str, Any]], clips_folder: str
) -> int:
    """One past the highest chapter index the job has used: its chapters, its
    live clips' file names and every ``NN_*`` file in the clips folder (the
    files of retired clips stay on disk), so a new clip never overwrites one."""
    used = {int(i) for i in job.chapters}
    names = [Path(c["output_path"]).name for c in live_clips if c.get("output_path")]
    folder = Path(clips_folder)
    if folder.is_dir():
        names.extend(f.name for f in folder.iterdir())
    for name in names:
        match = _CLIP_INDEX_RE.match(name)
        if match:
            used.add(int(match.group(1)))
    return max(used, default=-1) + 1


async def _propose_reprompt_chapters(
    *,
    job_id: str,
    video_path: str,
    safe_end: float,
    cleanup_root: Path,
    opts: PipelineOptions,
    language: str | None,
    prompt: str | None,
    words: list[transcription_service.WordTiming] | None,
    first_index: int,
    bus: AsyncEventBus,
) -> tuple[list[dict[str, Any]], list[transcription_service.WordTiming]]:
    """Chapters for the proposer's picks over the whole source.

    The source's 16 kHz wav feeds the proposer's loudness features (as in
    discovery); the words come from the sidecar, else the wav is transcribed
    once and the sidecar written. Selection is discovery's
    (``select_discovered``). Chapter indexes start at ``first_index``. Unlike
    discovery, a failure or an empty pick raises: the reprompt then fails and
    the job keeps its clips.
    """
    min_secs, max_secs = segment_discovery.clip_length_range(opts)
    wav_path = cleanup_root / "reprompt_source.wav"
    try:
        audio_path = await ffmpeg_tools.to_thread_cancellable(
            clip_service.extract_audio, video_path, 0.0, safe_end, str(wav_path)
        )
        if audio_path is None:
            raise RuntimeError("source has no audio stream")
        if words is None:
            words = await transcription_service.transcribe_words_async(
                audio_path, language=language, audio_duration_s=safe_end
            )
            try:
                await asyncio.to_thread(
                    segment_discovery.write_words_sidecar, video_path, words
                )
            except OSError as e:
                log.warning("[%s] Could not save the words sidecar: %s", job_id, e)
        proposer = segment_proposer.get_segment_proposer(
            min_secs=min_secs, max_secs=max_secs
        )
        candidates = await asyncio.to_thread(
            proposer.propose, words, audio_path, [], safe_end, prompt=prompt
        )
    finally:
        wav_path.unlink(missing_ok=True)
    kept = segment_discovery.select_discovered(candidates, safe_end)
    chapters = [
        {**c, "index": first_index + c["index"]}
        for c in segment_discovery.segments_to_chapters(kept, safe_end)
    ]
    log.info(
        "[%s] Reprompt proposal  words=%d  candidates=%d  kept=%s",
        job_id, len(words), len(candidates),
        [(c["start"], c["end"], c["virality_score"]) for c in chapters],
    )
    await _emit(
        bus, EventType.SEGMENTS_PROPOSED, job_id,
        count=len(chapters), candidates=len(candidates),
    )
    for c in chapters:
        await _emit(
            bus, EventType.SEGMENT_SCORED, job_id,
            index=c["index"], start=c["start"], end=c["end"],
            score=c["virality_score"], breakdown=c["score_breakdown"],
        )
    if not chapters:
        raise RuntimeError("the proposer kept no segment for this prompt")
    return chapters, words


def _set_retired(retired: bool) -> Callable[[dict[str, Any]], None]:
    def _mutate(c: dict[str, Any]) -> None:
        c["retired"] = retired

    return _mutate


async def _undo_reprompt(
    store: JobStore,
    job_id: str,
    new_clip_ids: list[str],
    new_indexes: set[int],
    retired_old_ids: list[str],
) -> None:
    """Put the job back as it was before a reprompt that did not finish.

    The old clips this reprompt retired go live again, the new clips are
    retired, their chapters leave the job and their files are deleted (none
    is live; a file a live clip still uses is kept). Best effort: errors are
    logged, the reprompt has already failed.
    """
    try:
        for clip_id in retired_old_ids:
            await store.upsert_clip(job_id, clip_id, _set_retired(False))
        await store.retire_clips(job_id, new_clip_ids)

        def _drop_new_chapters(s: JobState) -> None:
            s.chapters = {i: c for i, c in s.chapters.items() if i not in new_indexes}

        await store.update(job_id, _drop_new_chapters)
        live_paths = {
            path
            for clip in await store.list_clips(job_id=job_id)
            for path in (clip.get("output_path"), clip.get("thumbnail_path"))
            if path
        }
        for clip_id in new_clip_ids:
            clip = await store.get_clip(clip_id, include_retired=True) or {}
            for path in (clip.get("output_path"), clip.get("thumbnail_path")):
                if path and path not in live_paths:
                    Path(path).unlink(missing_ok=True)
    except Exception:  # noqa: BLE001 — best effort; the reprompt already failed
        log.exception("[%s] Could not fully undo a failed reprompt", job_id)


async def _reprompt_job(trigger: Event, bus: AsyncEventBus, store: JobStore) -> None:
    """Re-discover the clips of a completed job from its retained source.

    Forgets the job's replay history first (a new SSE subscriber must not be
    handed the previous run's ``JobCompleted``). Takes the source's words
    from the ``.words.json`` sidecar, or transcribes the source once and
    saves them. The chapters are the payload's ``start_seconds``-
    ``end_seconds`` range, or else the proposer's picks for the prompt and
    length range. Each renders as a new clip whose index follows every
    existing one, so no file is overwritten. The new clips stay hidden
    (retired) until all of them have rendered and been exported; then they go
    live, the old clips are retired, the prompt and length range (only those)
    are recorded on the job, and ``JobReprompted`` + ``JobCompleted`` are
    emitted.

    The job stays ``completed`` throughout, so a restart mid-reprompt
    (``fail_interrupted_jobs``) leaves it and its old clips alone. On any
    failure ``_undo_reprompt`` keeps the old clips and drops the new ones, and
    ``RepromptFailed`` is emitted, never ``JobFailed``. Cancellation undoes
    the same way and propagates. The in-flight claim is always released.
    """
    job_id = trigger.job_id
    payload = trigger.payload
    log.info("[%s] Reprompt started  prompt=%r", job_id, payload.get("prompt"))
    t0 = time.perf_counter()
    cleanup_root: Path | None = None
    new_clip_ids: list[str] = []
    new_indexes: set[int] = set()
    retired_old_ids: list[str] = []
    try:
        await bus.forget(job_id)
        job = await store.get(job_id)
        if job.status != "completed":
            raise RuntimeError(
                f"job is {job.status}; only a completed job can be reprompted"
            )
        video_path = job.video_path
        if not video_path or not Path(video_path).is_file():
            raise RuntimeError("source video not retained")
        prompt = payload["prompt"] if "prompt" in payload else job.prompt
        length_min = payload.get("target_length_min_seconds")
        length_max = payload.get("target_length_max_seconds")
        span_start = payload.get("start_seconds")
        span_end = payload.get("end_seconds")
        has_span = span_start is not None and span_end is not None
        if not has_span and (reason := reprompt_unavailable_reason()):
            raise RuntimeError(reason)
        opts = _effective_options(job.pipeline_options.model_dump())
        if length_min is not None:
            opts.target_length_min_seconds = length_min
        if length_max is not None:
            opts.target_length_max_seconds = length_max
        language = payload.get("language", job.language)

        old_clips = await store.list_clips(job_id=job_id)
        old_ids = [c["clip_id"] for c in old_clips]
        clips_folder = _reprompt_clips_folder(job, old_clips)
        first_index = _next_clip_index(job, old_clips, clips_folder)
        cleanup_root = Path(clips_folder) / "_tmp" / f"{job_id}-reprompt"
        cleanup_root.mkdir(parents=True, exist_ok=True)
        safe_end = await asyncio.to_thread(clip_service.probe_safe_end, video_path)
        words = await asyncio.to_thread(
            segment_discovery.read_words_sidecar, video_path
        )
        log.info(
            "[%s] Reprompt  first_index=%d  live_clips=%d  words=%s",
            job_id, first_index, len(old_ids),
            "none (transcribing)" if words is None else f"{len(words)} from the sidecar",
        )

        # ── Chapters ──────────────────────────────────────────────────────────
        if has_span:
            chapters = [
                {**_span_chapter(span_start, span_end, safe_end), "index": first_index}
            ]
        else:
            chapters, words = await _propose_reprompt_chapters(
                job_id=job_id,
                video_path=video_path,
                safe_end=safe_end,
                cleanup_root=cleanup_root,
                opts=opts,
                language=language,
                prompt=prompt,
                words=words,
                first_index=first_index,
                bus=bus,
            )
        await _emit(bus, EventType.CHAPTERS_DETECTED, job_id, chapters=chapters)

        # ── New clips, hidden until every one has rendered ────────────────────
        for chapter in chapters:
            clip_id = str(uuid.uuid4())
            await store.upsert_clip(job_id, clip_id, _set_retired(True))
            new_clip_ids.append(clip_id)
            new_indexes.add(int(chapter["index"]))

        semaphore = asyncio.Semaphore(settings.max_parallel_chapters)

        async def _bound(chapter: dict[str, Any], clip_id: str) -> str | None:
            async with semaphore:
                return await _process_chapter(
                    chapter=chapter,
                    job_id=job_id,
                    video_path=video_path,
                    clips_folder=clips_folder,
                    cleanup_root=cleanup_root,
                    caption_format=payload.get("caption_format", job.caption_format),
                    target_aspect_ratio=payload.get(
                        "target_aspect_ratio", job.target_aspect_ratio
                    ),
                    bus=bus,
                    store=store,
                    pipeline_options=opts,
                    language=language,
                    clip_id=clip_id,
                    source_words=words,
                )

        try:
            async with asyncio.TaskGroup() as chapter_group:
                chapter_tasks = [
                    chapter_group.create_task(_bound(c, cid))
                    for c, cid in zip(chapters, new_clip_ids)
                ]
        except ExceptionGroup as group:
            raise group.exceptions[0] from None
        output_paths = [p for p in (t.result() for t in chapter_tasks) if p]

        # ── Export + manifest of the new clips ────────────────────────────────
        if settings.export_base_folder:
            export_dir = str(Path(settings.export_base_folder) / job_id)
        else:
            export_dir = str(Path(clips_folder).parent / "exports")
        exported_paths = await asyncio.to_thread(
            export_service.export_clips, output_paths, export_dir
        )
        await _emit(bus, EventType.EXPORT_COMPLETED, job_id,
                    export_dir=export_dir, count=len(exported_paths))
        path_map = {Path(p).stem: p for p in exported_paths}
        manifest_clips: list[dict[str, Any]] = []
        for clip_id in new_clip_ids:
            clip = dict(await store.get_clip(clip_id, include_retired=True) or {})
            clip["export_path"] = path_map.get(Path(clip.get("output_path") or "").stem, "")
            manifest_clips.append(clip)
        manifest_path = await asyncio.to_thread(
            manifest_service.write_manifest, manifest_clips, export_dir
        )
        await _emit(bus, EventType.MANIFEST_CREATED, job_id, manifest_path=manifest_path)

        # ── Swap: new clips live, then old clips retired ──────────────────────
        for clip_id in new_clip_ids:
            await store.upsert_clip(job_id, clip_id, _set_retired(False))
        retired_old_ids = list(old_ids)
        await store.retire_clips(job_id, old_ids)

        def _record(s: JobState) -> None:
            s.prompt = prompt
            if length_min is not None:
                s.pipeline_options.target_length_min_seconds = length_min
            if length_max is not None:
                s.pipeline_options.target_length_max_seconds = length_max
            s.output_paths = output_paths
            s.chapters = {i: c for i, c in s.chapters.items() if i in new_indexes}

        await store.update(job_id, _record)
        log.info(
            "[%s] Reprompt done in %.2fs  new=%d  retired=%d  paths=%s",
            job_id, time.perf_counter() - t0, len(new_clip_ids), len(old_ids),
            output_paths,
        )
        await _emit(
            bus, EventType.JOB_REPROMPTED, job_id,
            prompt=prompt, clip_ids=new_clip_ids, retired_clip_ids=old_ids,
            output_paths=output_paths,
        )
        await _emit(bus, EventType.JOB_COMPLETED, job_id, output_paths=output_paths)
    except asyncio.CancelledError:
        log.warning("[%s] Reprompt cancelled; the job keeps its clips", job_id)
        await _finish_then_honour_cancel(
            _undo_reprompt(store, job_id, new_clip_ids, new_indexes, retired_old_ids)
        )
        raise
    except Exception as e:  # noqa: BLE001 — a failed reprompt must not fail the job
        log.exception(
            "[%s] Reprompt failed after %.2fs; the job keeps its clips",
            job_id, time.perf_counter() - t0,
        )

        async def _fail() -> None:
            await _undo_reprompt(
                store, job_id, new_clip_ids, new_indexes, retired_old_ids
            )
            await _emit(bus, EventType.REPROMPT_FAILED, job_id, error=str(e))

        await _finish_then_honour_cancel(_fail())
    finally:
        release_reprompt(job_id)
        if cleanup_root is not None:
            shutil.rmtree(cleanup_root, ignore_errors=True)


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


async def _reframe_step(
    *,
    job_id: str,
    index: int,
    video_path: str,
    start: float,
    end: float,
    target_aspect_ratio: float,
    opts: PipelineOptions,
    bus: AsyncEventBus,
) -> list[tuple[float, float]] | None:
    """Face-tracked crop track for the chapter's reel; ``None`` = letterbox.

    Runs only when the job's ``reframe`` option is on AND
    ``settings.reframe_provider == "face_track"`` (default ``letterbox``:
    then the render is exactly as before). Detection decodes the chapter in a
    worker thread via ``to_thread_cancellable``, so a cancel stops the decode
    and waits for it. Never fails the chapter: an error, a split screen,
    several similar faces or no face emits ``StageSkipped(reframe, reason)``
    and the reel is letterboxed. Cancellation propagates.
    """
    if not opts.reframe or settings.reframe_provider != "face_track":
        return None
    log.info("[%s] Chapter %d  face tracking  window=%.1f-%.1fs", job_id, index, start, end)
    step_t0 = time.perf_counter()
    try:
        plan = await ffmpeg_tools.to_thread_cancellable(
            reframe_service.face_track, video_path, start, end, target_aspect_ratio
        )
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001 — reframe must never fail the chapter
        log.exception("[%s] Chapter %d  face tracking failed; letterbox", job_id, index)
        reason = f"face tracking failed: {e}"
    else:
        if plan.track is not None:
            log.info(
                "[%s] Chapter %d  face track (%.2fs)  keyframes=%d  %s",
                job_id, index, time.perf_counter() - step_t0, len(plan.track), plan.reason,
            )
            return plan.track
        reason = plan.reason
        log.info(
            "[%s] Chapter %d  no face track (%.2fs): %s; letterbox",
            job_id, index, time.perf_counter() - step_t0, reason,
        )
    await _emit(
        bus, EventType.STAGE_SKIPPED, job_id,
        stage_id="reframe", chapter_index=index, reason=reason,
    )
    return None


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
    regenerate_copy: bool = True,
    source_words: Sequence[Any] | None = None,
    render_to: str | None = None,
) -> str | None:
    """Process one chapter into a clip; ``clip_id`` updates an existing clip
    in place (re-render) instead of creating a new one.

    ``regenerate_copy=False`` (re-render only) skips the social-copy block:
    the clip keeps its title, summary, hashtags and AI hook text.

    ``source_words`` are full-source word timings (clip discovery's
    transcript); when given and transcription is on, the chapter's words are
    these rebased onto its window instead of a fresh transcription of it.
    ``render_to`` is the reel's path (re-render: the clip's existing file);
    by default ``{index:02d}_{title}.mp4`` in ``clips_folder``.
    """
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
    reuse_words = opts.transcription and source_words is not None
    if opts.transcription and not reuse_words:
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
    elif reuse_words and opts.audio_enhance:
        # Nothing to enhance: the chapter is not transcribed again.
        log.info("[%s] Chapter %d  audio_enhance not needed: words reused", job_id, index)
        await _emit(
            bus, EventType.STAGE_SKIPPED, job_id,
            stage_id="audio_enhance", chapter_index=index, reason="words reused",
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
        if reuse_words:
            # Full-source timings moved onto this window's clock and clipped
            # to it, so captions never start before 0 or end past the reel.
            words = segment_discovery.rebase_words(source_words, start, end)
            log.info("[%s] Chapter %d  reusing source words", job_id, index)
        elif audio_path is None:
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

    # ── B-roll (gated on render + broll; settings.broll_provider) ────────────
    broll_inserts, broll_assets = await _broll_step(
        job_id=job_id, index=index, words=words, duration=chapter_duration,
        opts=opts, bus=bus,
    )

    # ── Render final clip (gated on render) ──────────────────────────────────
    output_path: str | None = None
    safe_title = _sanitize(title)
    if opts.render:
        log.info("[%s] Chapter %d  rendering final clip  aspect=%.4f", job_id, index, target_aspect_ratio)
        step_t0 = time.perf_counter()
        await store.upsert_chapter(job_id, lambda c: _set_status(c, "rendering"), index)
        output_path = render_to or str(Path(clips_folder) / f"{index:02d}_{safe_title}.mp4")
        crop_track = await _reframe_step(
            job_id=job_id, index=index, video_path=video_path, start=start, end=end,
            target_aspect_ratio=target_aspect_ratio, opts=opts, bus=bus,
        )
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
                broll=broll_inserts,
                caption_words_per_segment=settings.caption_words_per_segment,
                crop_track=crop_track,
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
            # Named after the reel: {index:02d}_{title}_thumb.jpg by default.
            reel = Path(output_path)
            thumbnail_out = str(reel.with_name(f"{reel.stem}_thumb.jpg"))
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
            "transcript": text,
            "broll_assets": broll_assets,
        })
        if regenerate_copy:
            c["title"] = title
        # Clip discovery's score fields (absent on chapter-based clips).
        for key in ("virality_score", "score_breakdown", "summary"):
            if key in chapter:
                c[key] = chapter[key]

    await store.upsert_clip(job_id, clip_id, _init_clip)

    # ── AI hook (W1.7 — gated on ai_hook + transcript) ───────────────────────
    # Generate a single punchy opening line per clip and stash it on the
    # clip record (DB column ``ai_hook_text``). Needs the transcript to be
    # meaningful, so we skip silently when transcription was off or empty.
    if not regenerate_copy:
        log.info(
            "[%s] Chapter %d  keeping title, summary, hashtags and AI hook "
            "(regenerate_copy=False)",
            job_id, index,
        )
        return output_path
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
        # Without a generated description a discovered clip keeps the
        # proposer's summary; chapter-based clips are unchanged.
        c["summary"] = (
            description if description or "summary" not in chapter
            else chapter["summary"]
        )
        c["hashtags"] = hashtags

    await store.upsert_clip(job_id, clip_id, _update_social)
    await _emit(
        bus, EventType.SOCIAL_CONTENT_GENERATED, job_id,
        chapter_index=index, description=description, hashtag_count=len(hashtags),
    )

    return output_path


async def _broll_step(
    *,
    job_id: str,
    index: int,
    words: Sequence[Any],
    duration: float,
    opts: PipelineOptions,
    bus: AsyncEventBus,
) -> tuple[list[render_service.BrollInsert] | None, list[dict[str, Any]] | None]:
    """B-roll inserts for the chapter's reel and their ``broll_assets``
    records; ``(None, None)`` renders without B-roll.

    Runs only when the job's ``render`` and ``broll`` options are on. With
    ``settings.broll_provider == "none"`` (the default) it emits
    ``StageSkipped(broll, reason="no provider")`` and does nothing else.
    Otherwise ``broll_planner.plan_broll`` picks up to two 3 s windows over
    the clip-relative ``words``, the provider fetches one clip per query (two
    at a time, awaited on the loop; a failed or empty query drops its
    insert) and ``BRollApplied`` reports what goes into the render. Never
    fails the chapter: an error or nothing to show emits
    ``StageSkipped(broll, reason)`` and the reel renders without B-roll.
    Cancellation propagates.
    """
    if not (opts.render and opts.broll):
        return None, None
    provider_name = settings.broll_provider
    if provider_name == "none":
        reason = "no provider"
    else:
        step_t0 = time.perf_counter()
        try:
            provider = broll_service.get_broll_provider(provider_name)
            plan = broll_planner.plan_broll(words, duration)
            found = await broll_service.fetch_all(provider, [p.query for p in plan])
            picks = [(p, a) for p, a in zip(plan, found) if a is not None]
            inserts = [render_service.BrollInsert(a.path, p.start, p.duration) for p, a in picks]
            render_service.validate_broll(inserts, duration)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — B-roll must never fail the chapter
            log.exception("[%s] Chapter %d  B-roll failed; rendering without it", job_id, index)
            reason = f"B-roll failed: {e}"
        else:
            if picks:
                assets = [
                    {
                        "query": p.query, "start": p.start, "duration": p.duration,
                        "provider": a.provider, "asset_id": a.asset_id, "author": a.author,
                        "source_url": a.source_url, "path": a.path,
                    }
                    for p, a in picks
                ]  # fmt: skip
                log.info(
                    "[%s] Chapter %d  B-roll (%.2fs)  provider=%s  inserts=%s",
                    job_id, index, time.perf_counter() - step_t0, provider_name,
                    [(a["query"], a["start"], a["asset_id"]) for a in assets],
                )
                await _emit(
                    bus, EventType.BROLL_APPLIED, job_id,
                    chapter_index=index, provider=provider_name, assets=assets,
                )
                return inserts, assets
            reason = (
                f"no B-roll found for: {', '.join(p.query for p in plan)}"
                if plan
                else "no keyword to illustrate"
            )
    log.info("[%s] Chapter %d  no B-roll: %s", job_id, index, reason)
    await _emit(
        bus, EventType.STAGE_SKIPPED, job_id,
        stage_id="broll", chapter_index=index, reason=reason,
    )
    return None, None
