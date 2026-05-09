from __future__ import annotations

import asyncio
import logging
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from app.bus.event_bus import AsyncEventBus
from app.bus.job_store import JobStore
from app.domain.events import Event, EventType
from app.domain.models import ChapterArtifacts, JobState, PipelineOptions
from app.services import (
    caption_service,
    clip_service,
    download_service,
    export_service,
    folder_service,
    manifest_service,
    ollama_service,
    platforms,
    render_service,
    subtitle_image_service,
    thumbnail_service,
    transcription_service,
)
from app.services.platforms import resolve as resolve_adapter
from app.settings import settings

import app.logging_config  # noqa: F401

log = logging.getLogger(__name__)


_SAFE_CHAR_RE = re.compile(r"[^\w\-_. ]")


def _sanitize(name: str) -> str:
    return _SAFE_CHAR_RE.sub("_", name).strip() or "chapter"


async def run_orchestrator(bus: AsyncEventBus, store: JobStore) -> None:
    """Subscribes to VIDEO_REQUESTED events and spawns per-job pipelines."""
    log.info("Orchestrator subscribed; awaiting VideoRequested events")
    pipeline_tasks: set[asyncio.Task[Any]] = set()
    try:
        async for event in bus.subscribe(types=[EventType.VIDEO_REQUESTED]):
            task = asyncio.create_task(_run_job(event, bus, store))
            pipeline_tasks.add(task)
            task.add_done_callback(pipeline_tasks.discard)
    except asyncio.CancelledError:
        for task in pipeline_tasks:
            task.cancel()
        for task in pipeline_tasks:
            try:
                await task
            except Exception:  # noqa: BLE001
                pass
        raise


async def _emit(bus: AsyncEventBus, type_: EventType, job_id: str, **payload: Any) -> None:
    await bus.publish(Event(type=type_, job_id=job_id, payload=payload))


async def _run_job(trigger: Event, bus: AsyncEventBus, store: JobStore) -> None:
    job_id = trigger.job_id
    payload = trigger.payload
    url: str = payload["url"]
    download_path: str = payload["download_path"]
    caption_format: str = payload.get("caption_format", settings.default_caption_format)
    target_aspect_ratio: float = payload.get(
        "target_aspect_ratio", settings.default_target_aspect_ratio
    )

    # ── Pipeline options with server-side safety net (G2) ────────────────────
    raw_opts = payload.get("pipeline_options")
    if raw_opts and isinstance(raw_opts, dict):
        opts = PipelineOptions(**raw_opts)
    else:
        opts = PipelineOptions()

    # Enforce dependency rules server-side regardless of what UI sent
    if not opts.transcription:
        opts.captions = False
    if not opts.render:
        opts.reframe = False
        opts.broll = False
        opts.thumbnail = False

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
            folder_service.create_video_subfolder, download_path, url, adapter.platform_id
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
                )

        outputs = await asyncio.gather(*(_bound(c) for c in chapters), return_exceptions=False)
        output_paths = [p for p in outputs if p]

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

        def _fail(s: JobState) -> None:
            s.status = "failed"
            s.error = str(e)

        await store.update(job_id, _fail)
        await _emit(
            bus,
            EventType.JOB_FAILED,
            job_id,
            failed_step=(await store.get(job_id)).current_step,
            error=str(e),
        )
    finally:
        if cleanup_root is not None and cleanup_root.exists():
            shutil.rmtree(cleanup_root, ignore_errors=True)


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
) -> str | None:
    index = int(chapter["index"])
    title = chapter["title"]
    start = float(chapter["start"])
    end = float(chapter["end"])
    chapter_duration = end - start

    log.info("[%s] Chapter %d/%d start  title=%r  %.1f–%.1fs (%.1fs)",
             job_id, index, index, title, start, end, chapter_duration)
    chapter_t0 = time.perf_counter()

    opts = pipeline_options or PipelineOptions()
    tmp_dir = cleanup_root
    clip_path = str(tmp_dir / f"chapter_{index}.mp4")
    audio_path = str(tmp_dir / f"chapter_{index}.wav")

    def _set_status(state: ChapterArtifacts, status: str) -> None:
        state.status = status  # type: ignore[assignment]

    # ── Extract clip (gated on render — G19) ─────────────────────────────────
    if opts.render:
        log.info("[%s] Chapter %d  extracting clip and audio", job_id, index)
        step_t0 = time.perf_counter()
        await store.upsert_chapter(job_id, lambda c: _set_status(c, "extracting"), index)
        await asyncio.to_thread(
            clip_service.extract_chapter_to_disk,
            video_path,
            start,
            end,
            clip_path,
            audio_path,
        )
        log.info("[%s] Chapter %d  clip extracted (%.2fs)  clip=%s  audio=%s",
                 job_id, index, time.perf_counter() - step_t0, clip_path, audio_path)
        await store.upsert_chapter(
            job_id,
            lambda c: (setattr(c, "clip_path", clip_path), setattr(c, "audio_path", audio_path)),
            index,
        )
        await _emit(
            bus,
            EventType.CHAPTER_CLIP_EXTRACTED,
            job_id,
            chapter_index=index,
            clip_path=clip_path,
            audio_path=audio_path,
        )
    else:
        # render=False → source video stays untouched, no per-chapter clip extraction
        log.info("[%s] Chapter %d  skipping clip extraction (render=False)", job_id, index)
        # We still need audio for transcription if enabled
        if opts.transcription:
            await asyncio.to_thread(
                clip_service.extract_chapter_to_disk,
                video_path,
                start,
                end,
                clip_path,
                audio_path,
            )

    # ── Transcribe ────────────────────────────────────────────────────────────
    words = []
    text = ""
    if opts.transcription:
        log.info("[%s] Chapter %d  transcribing audio  provider=%s",
                 job_id, index, settings.transcription_provider)
        step_t0 = time.perf_counter()
        await store.upsert_chapter(job_id, lambda c: _set_status(c, "transcribing"), index)
        words = await asyncio.wait_for(
            asyncio.to_thread(transcription_service.transcribe_to_words, audio_path),
            timeout=settings.transcription_timeout_seconds,
        )
        text = " ".join(w.word for w in words)
        log.info("[%s] Chapter %d  transcription done (%.2fs)  words=%d",
                 job_id, index, time.perf_counter() - step_t0, len(words))
        await store.upsert_chapter(job_id, lambda c: setattr(c, "transcript", text), index)
        await _emit(bus, EventType.CHAPTER_TRANSCRIBED, job_id, chapter_index=index, text=text)
    else:
        log.info("[%s] Chapter %d  skipping transcription", job_id, index)
        await _emit(bus, EventType.STAGE_SKIPPED, job_id, stage_id="transcribe", chapter_index=index)

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
        await asyncio.to_thread(
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

    # ── Subtitle images (gated on captions) ──────────────────────────────────
    image_paths: list[str] = []
    if captions_obj is not None and opts.captions:
        caption_count = len(captions_obj)
        log.info("[%s] Chapter %d  rendering %d subtitle image(s)", job_id, index, caption_count)
        step_t0 = time.perf_counter()
        videosize = (1280, 720)  # default; overridden by render service per real video size
        for idx, caption in enumerate(captions_obj):
            caption_text = caption.text if hasattr(caption, "text") else str(caption)
            image_path = str(tmp_dir / f"chapter_{index}_caption_{idx}.png")
            await asyncio.to_thread(
                subtitle_image_service.render_to_path,
                caption_text,
                videosize,
                image_path,
            )
            image_paths.append(image_path)
        log.info("[%s] Chapter %d  subtitle images done (%.2fs)", job_id, index,
                 time.perf_counter() - step_t0)
    await store.upsert_chapter(
        job_id, lambda c: setattr(c, "image_paths", image_paths), index
    )
    if image_paths:
        await _emit(
            bus,
            EventType.SUBTITLE_IMAGE_RENDERED,
            job_id,
            chapter_index=index,
            image_paths=image_paths,
        )

    # ── Render final clip (gated on render) ──────────────────────────────────
    output_path: str | None = None
    safe_title = _sanitize(title)
    if opts.render:
        log.info("[%s] Chapter %d  rendering final clip  aspect=%.4f", job_id, index, target_aspect_ratio)
        step_t0 = time.perf_counter()
        await store.upsert_chapter(job_id, lambda c: _set_status(c, "rendering"), index)
        output_path = str(Path(clips_folder) / f"{index:02d}_{safe_title}.mp4")
        await asyncio.wait_for(
            asyncio.to_thread(
                render_service.render_clip,
                clip_path,
                output_path,
                0.0,
                chapter_duration,
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
    clip_id = str(uuid.uuid4())
    thumbnail_path: str | None = None
    if opts.render and opts.thumbnail and output_path:
        try:
            thumbnail_out = str(Path(clips_folder) / f"{index:02d}_{safe_title}_thumb.jpg")
            thumbnail_path = await asyncio.to_thread(
                thumbnail_service.generate_thumbnail, output_path, thumbnail_out
            )
            log.info("[%s] Chapter %d  thumbnail generated  path=%s", job_id, index, thumbnail_path)
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
