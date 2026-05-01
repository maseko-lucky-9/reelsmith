from __future__ import annotations

import asyncio
import logging
import re
import shutil
from pathlib import Path
from typing import Any

from app.bus.event_bus import AsyncEventBus
from app.bus.job_store import JobStore
from app.domain.events import Event, EventType
from app.domain.models import ChapterArtifacts, JobState
from app.services import (
    caption_service,
    clip_service,
    download_service,
    folder_service,
    render_service,
    subtitle_image_service,
    transcription_service,
)
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

    cleanup_root: Path | None = None
    try:
        await store.update(job_id, lambda s: setattr(s, "status", "running"))

        # Folder
        await store.update(job_id, lambda s: setattr(s, "current_step", "folder"))
        destination, clips_folder = await asyncio.to_thread(
            folder_service.create_video_subfolder, download_path, url
        )
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

        # Download
        await store.update(job_id, lambda s: setattr(s, "current_step", "download"))
        video_path, info = await asyncio.wait_for(
            asyncio.to_thread(download_service.download_video, url, destination),
            timeout=settings.download_timeout_seconds,
        )
        if not video_path or info is None:
            raise RuntimeError("download failed")

        title = info.get("title", "")
        duration = float(info.get("duration") or 0.0)

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

        # Chapters
        await store.update(job_id, lambda s: setattr(s, "current_step", "chapters"))
        chapters = await asyncio.to_thread(download_service.extract_chapters, info)
        if not chapters:
            chapters = [
                {"index": 0, "title": "Full Video", "start": 0.0, "end": duration}
            ]
        await _emit(bus, EventType.CHAPTERS_DETECTED, job_id, chapters=chapters)

        # Per-chapter fan-out with semaphore
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
                )

        outputs = await asyncio.gather(*(_bound(c) for c in chapters), return_exceptions=False)
        output_paths = [p for p in outputs if p]

        def _complete(s: JobState) -> None:
            s.status = "completed"
            s.current_step = "completed"
            s.output_paths = output_paths

        await store.update(job_id, _complete)
        await _emit(bus, EventType.JOB_COMPLETED, job_id, output_paths=output_paths)

    except asyncio.CancelledError:
        log.warning("Job %s cancelled", job_id)
        raise
    except Exception as e:  # noqa: BLE001
        log.exception("Job %s failed", job_id)

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
) -> str | None:
    index = int(chapter["index"])
    title = chapter["title"]
    start = float(chapter["start"])
    end = float(chapter["end"])

    tmp_dir = cleanup_root
    clip_path = str(tmp_dir / f"chapter_{index}.mp4")
    audio_path = str(tmp_dir / f"chapter_{index}.wav")

    def _set_status(state: ChapterArtifacts, status: str) -> None:
        state.status = status  # type: ignore[assignment]

    await store.upsert_chapter(job_id, lambda c: _set_status(c, "extracting"), index)
    await asyncio.to_thread(
        clip_service.extract_chapter_to_disk,
        video_path,
        start,
        end,
        clip_path,
        audio_path,
    )
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

    # Transcribe
    await store.upsert_chapter(job_id, lambda c: _set_status(c, "transcribing"), index)
    text = await asyncio.wait_for(
        asyncio.to_thread(transcription_service.speech_to_text, audio_path),
        timeout=settings.transcription_timeout_seconds,
    )
    await store.upsert_chapter(job_id, lambda c: setattr(c, "transcript", text), index)
    await _emit(bus, EventType.CHAPTER_TRANSCRIBED, job_id, chapter_index=index, text=text)

    # Captions
    await store.upsert_chapter(job_id, lambda c: _set_status(c, "captioning"), index)
    captions_obj = await asyncio.to_thread(
        caption_service.generate_captions, text, 0.0, end - start, caption_format
    )
    captions_path = str(tmp_dir / f"chapter_{index}.{caption_format}")
    await asyncio.to_thread(
        caption_service.write_captions, captions_obj, caption_format, captions_path
    )
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

    # Subtitle images (one per caption)
    image_paths = []
    if captions_obj is not None:
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
    await store.upsert_chapter(
        job_id, lambda c: setattr(c, "image_paths", image_paths), index
    )
    await _emit(
        bus,
        EventType.SUBTITLE_IMAGE_RENDERED,
        job_id,
        chapter_index=index,
        image_paths=image_paths,
    )

    # Render
    await store.upsert_chapter(job_id, lambda c: _set_status(c, "rendering"), index)
    safe_title = _sanitize(title)
    output_path = str(Path(clips_folder) / f"{index:02d}_{safe_title}.mp4")
    await asyncio.wait_for(
        asyncio.to_thread(
            render_service.render_clip,
            clip_path,
            output_path,
            0.0,
            end - start,
            captions_path,
            target_aspect_ratio,
        ),
        timeout=settings.render_timeout_seconds,
    )
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
    return output_path
