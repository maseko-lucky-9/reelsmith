#!/usr/bin/env python
"""Pipeline benchmark: run one job through ``orchestrator._run_job`` and record cost.

Runs the real per-chapter pipeline (extract → audio enhance → transcribe →
captions → render → thumbnail → export) in-process against a local file, with
no API server, DB or LLM. Records:

* per-stage wall time, parsed from the orchestrator's ``(N.NNs)`` log lines;
* total wall clock;
* peak RSS of this process and of its largest reaped child (ffmpeg);
* the rendered outputs (copied next to the JSON) and their stream format.

Usage
-----
    .venv-mac/bin/python -m scripts.bench --input tests/fixtures/sample.mp4 \
        --label baseline-moviepy [--chapters chapters.json] [--real-whisper]

    .venv-mac/bin/python -m scripts.bench --compare artifacts/bench/a.json artifacts/bench/b.json

Writes ``artifacts/bench/<label>.json``, ``<label>.log`` and ``<label>/`` (outputs).

Chapters
--------
Local files arrive as ``upload://`` jobs, whose adapter returns no chapters
(``app/services/platforms/upload.py``), so the orchestrator falls back to one
full-length chapter. ``--chapters`` injects spans instead: a JSON list (or
``{"chapters": [...]}``) of ``{"title", "start", "end"}`` objects in seconds.
They are fed through the orchestrator's existing ``resolve_adapter`` seam (the
same one ``tests/unit/test_orchestrator.py`` uses), so ``_run_job`` still
clamps them against ``probe_safe_end`` exactly as it would for YouTube chapters.

Environment
-----------
Set *before* any ``app`` import: in-memory job store, Ollama off, stub
transcription unless ``--real-whisper``, and an export folder inside a
throwaway work dir. Everything else (audio enhance, thumbnail, render settings)
uses the app defaults so the numbers reflect a default job.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import platform
import re
import resource
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

DEFAULT_OUT_DIR = _REPO_ROOT / "artifacts" / "bench"

# Pipeline order for the comparison table; unknown stages sort after these.
_STAGE_ORDER = (
    "Folder ready",
    "Download complete",
    "clip extracted",
    "audio extracted",
    "audio enhanced",
    "transcription done",
    "filler removal done",
    "captions written",
    "subtitle images done",
    "render done",
    "thumbnail generated",
    "ai_hook done",
    "chapter total",
    "job total",
)

# "[job] Chapter 3  render done (41.07s)  output=..." / "[job] Folder ready (0.01s) ..."
# Two decimals only: the chapter header's "(5.0s)" is a duration, not a timing.
_STEP_RE = re.compile(
    r"^\[[^\]]+\]\s+(?:Chapter (?P<chapter>\d+)\s+)?(?P<stage>[A-Za-z_][\w ]*?) \((?P<secs>\d+\.\d\d)s\)"
)
_CHAPTER_TOTAL_RE = re.compile(
    r"^\[[^\]]+\]\s+Chapter (?P<chapter>\d+)\s+finished in (?P<secs>\d+\.\d\d)s"
)
_JOB_TOTAL_RE = re.compile(r"^\[[^\]]+\]\s+Job completed in (?P<secs>\d+\.\d\d)s")


@dataclass(frozen=True)
class StageTiming:
    """One timed pipeline step parsed from an orchestrator log line."""

    chapter: int | None
    stage: str
    seconds: float


def parse_stage_timing(message: str) -> StageTiming | None:
    """Parse an orchestrator log message into a ``StageTiming``, or ``None``."""
    if match := _JOB_TOTAL_RE.match(message):
        return StageTiming(None, "job total", float(match["secs"]))
    if match := _CHAPTER_TOTAL_RE.match(message):
        return StageTiming(int(match["chapter"]), "chapter total", float(match["secs"]))
    if match := _STEP_RE.match(message):
        chapter = int(match["chapter"]) if match["chapter"] is not None else None
        return StageTiming(chapter, match["stage"].strip(), float(match["secs"]))
    return None


def aggregate_stage_timings(timings: list[StageTiming]) -> dict[str, dict[str, Any]]:
    """Sum timings per stage; keep the per-chapter breakdown (keys are str for JSON)."""
    stages: dict[str, dict[str, Any]] = {}
    for t in timings:
        entry = stages.setdefault(
            t.stage, {"total_s": 0.0, "count": 0, "per_chapter": {}}
        )
        entry["total_s"] = round(entry["total_s"] + t.seconds, 3)
        entry["count"] += 1
        if t.chapter is not None:
            key = str(t.chapter)
            entry["per_chapter"][key] = round(
                entry["per_chapter"].get(key, 0.0) + t.seconds, 3
            )
    return dict(sorted(stages.items(), key=lambda kv: _stage_sort_key(kv[0])))


def _stage_sort_key(stage: str) -> tuple[int, str]:
    try:
        return (_STAGE_ORDER.index(stage), stage)
    except ValueError:
        return (len(_STAGE_ORDER), stage)


def normalize_maxrss_mib(raw: int, platform_name: str = sys.platform) -> float:
    """Convert ``ru_maxrss`` to MiB: bytes on macOS, kilobytes on Linux."""
    divisor = 1024 * 1024 if platform_name == "darwin" else 1024
    return raw / divisor


class _StageTimingHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.timings: list[StageTiming] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            parsed = parse_stage_timing(record.getMessage())
        except Exception:  # noqa: BLE001 — a bad record must not break the run
            return
        if parsed is not None:
            self.timings.append(parsed)


def load_chapters(path: Path) -> list[Any]:
    """Load ``--chapters`` JSON into ``platforms.base.Chapter`` objects."""
    from app.services.platforms.base import Chapter

    raw = json.loads(Path(path).read_text())
    spans = raw["chapters"] if isinstance(raw, dict) else raw
    chapters = []
    for i, span in enumerate(spans):
        start, end = float(span["start"]), float(span["end"])
        if end <= start:
            raise ValueError(f"chapter {i} end ({end}) must be after start ({start})")
        chapters.append(
            Chapter(
                index=i,
                title=str(span.get("title") or f"Chapter {i}"),
                start=start,
                end=end,
            )
        )
    return chapters


# ── Comparison table ──────────────────────────────────────────────────────────


def _pct(a: float | None, b: float | None) -> str:
    if a is None or b is None or a == 0:
        return ""
    return f"{(b - a) / a * 100:+.1f}%"


def _fmt(value: float | None, spec: str) -> str:
    return "—" if value is None else format(value, spec)


def compare_table(a: dict[str, Any], b: dict[str, Any]) -> str:
    """Markdown table comparing two bench JSON results (b relative to a)."""
    rows = [
        f"| metric | {a['label']} | {b['label']} | Δ |",
        "|---|---:|---:|---:|",
    ]

    def numeric(name: str, va: float | None, vb: float | None, spec: str) -> None:
        rows.append(
            f"| {name} | {_fmt(va, spec)} | {_fmt(vb, spec)} | {_pct(va, vb)} |"
        )

    numeric("wall clock (s)", a.get("wall_clock_s"), b.get("wall_clock_s"), ".2f")
    stage_names = sorted(
        set(a.get("stages", {})) | set(b.get("stages", {})), key=_stage_sort_key
    )
    for stage in stage_names:
        va = a.get("stages", {}).get(stage, {}).get("total_s")
        vb = b.get("stages", {}).get(stage, {}).get("total_s")
        numeric(f"stage: {stage} (s)", va, vb, ".2f")
    for kind in ("self", "children"):
        numeric(
            f"peak RSS {kind} (MiB)",
            a.get("peak_rss_mib", {}).get(kind),
            b.get("peak_rss_mib", {}).get(kind),
            ".1f",
        )
    outs_a = {o["name"]: o for o in a.get("outputs", [])}
    outs_b = {o["name"]: o for o in b.get("outputs", [])}
    for name in sorted(set(outs_a) | set(outs_b)):
        oa, ob = outs_a.get(name, {}), outs_b.get(name, {})
        size_a = oa["size_bytes"] / 1e6 if "size_bytes" in oa else None
        size_b = ob["size_bytes"] / 1e6 if "size_bytes" in ob else None
        numeric(f"output {name} size (MB)", size_a, size_b, ".2f")
        for field in ("pix_fmt",):
            rows.append(
                f"| output {name} {field} | {oa.get(field, '—')} | {ob.get(field, '—')} |  |"
            )
        dims_a = f"{oa['width']}x{oa['height']}" if "width" in oa else "—"
        dims_b = f"{ob['width']}x{ob['height']}" if "width" in ob else "—"
        rows.append(f"| output {name} size (px) | {dims_a} | {dims_b} |  |")
    return "\n".join(rows)


# ── Running a job ─────────────────────────────────────────────────────────────


def _configure_environment(work_dir: Path, real_whisper: bool) -> None:
    import os

    os.environ["YTVIDEO_JOB_STORE"] = "memory"
    os.environ["YTVIDEO_OLLAMA_ENABLED"] = "false"
    os.environ["YTVIDEO_TRANSCRIPTION_PROVIDER"] = "whisper" if real_whisper else "stub"
    os.environ["YTVIDEO_EXPORT_BASE_FOLDER"] = str(work_dir / "export")
    os.environ["YTVIDEO_DEFAULT_DOWNLOAD_PATH"] = str(work_dir / "download")
    os.environ.setdefault("YTVIDEO_LOG_LEVEL", "INFO")


def _probe_media(path: Path) -> dict[str, Any]:
    import av

    info: dict[str, Any] = {"name": path.name, "size_bytes": path.stat().st_size}
    with av.open(str(path)) as container:
        info["duration_s"] = (
            round(container.duration / 1_000_000, 3) if container.duration else None
        )
        if container.streams.video:
            v = container.streams.video[0]
            info.update(
                width=v.codec_context.width,
                height=v.codec_context.height,
                pix_fmt=v.codec_context.pix_fmt,
                codec=v.codec_context.name,
                profile=v.codec_context.profile,
                fps=str(v.average_rate) if v.average_rate else None,
                frames=v.frames,
            )
        info["has_audio"] = bool(container.streams.audio)
    return info


def _git_revision() -> dict[str, Any]:
    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=_REPO_ROOT, capture_output=True, text=True, check=False
        ).stdout.strip()

    return {
        "commit": git("rev-parse", "--short", "HEAD"),
        "dirty": bool(git("status", "--porcelain")),
    }


def _make_adapter(input_path: Path, chapters: list[Any] | None):
    """UploadAdapter whose download() accepts any local file and whose chapters are injectable."""
    import av

    from app.services.platforms.base import DownloadResult
    from app.services.platforms.upload import UploadAdapter

    class BenchAdapter(UploadAdapter):
        def download(self, url: str, destination_folder: str) -> DownloadResult:
            # Mirrors UploadAdapter.download minus the /tmp/yt/uploads root guard.
            with av.open(str(input_path)) as container:
                duration = container.duration / 1_000_000 if container.duration else 0.0
            info = {
                "title": input_path.stem,
                "duration": duration,
                "chapters": [],
                "upload_date": None,
                "description": "",
                "tags": [],
            }
            return DownloadResult(
                video_path=str(input_path),
                info=info,
                title=input_path.stem,
                duration=duration,
                source=self.platform_id,
            )

        def extract_chapters(self, info: dict) -> list[Any]:
            return list(chapters) if chapters else super().extract_chapters(info)

    return BenchAdapter()


async def _run_job(
    input_path: Path, work_dir: Path, chapters: list[Any] | None
) -> dict[str, Any]:
    from app.bus.event_bus import AsyncEventBus
    from app.bus.job_store import JobStore
    from app.domain.events import Event, EventType
    from app.domain.models import JobState
    from app.workers import orchestrator

    adapter = _make_adapter(input_path, chapters)
    orchestrator.resolve_adapter = lambda url: adapter  # the seam tests use too

    job_id = f"bench-{uuid.uuid4().hex[:8]}"
    url = f"upload://{input_path}"
    download_path = str(work_dir / "download")
    store = JobStore()
    await store.create(
        JobState(job_id=job_id, url=url, source="upload", download_path=download_path)
    )
    trigger = Event(
        type=EventType.VIDEO_REQUESTED,
        job_id=job_id,
        payload={
            "url": url,
            "download_path": download_path,
            "caption_format": "srt",
            "target_aspect_ratio": 9 / 16,
        },
    )
    await orchestrator._run_job(trigger, AsyncEventBus(), store)
    state = await store.get(job_id)
    return {
        "job_id": job_id,
        "status": state.status,
        "error": state.error,
        "output_paths": list(state.output_paths or []),
    }


def run_bench(args: argparse.Namespace) -> dict[str, Any]:
    input_path = Path(args.input).resolve()
    if not input_path.is_file():
        raise SystemExit(f"--input not found: {input_path}")
    out_dir = Path(args.out_dir).resolve()
    label = args.label or input_path.stem
    out_dir.mkdir(parents=True, exist_ok=True)
    artifacts = out_dir / label
    if artifacts.exists():
        shutil.rmtree(artifacts)
    artifacts.mkdir(parents=True)

    with tempfile.TemporaryDirectory(prefix="reelsmith-bench-") as tmp:
        work_dir = Path(tmp)
        _configure_environment(work_dir, args.real_whisper)

        import app.logging_config  # noqa: F401 — installs the stdout handler first

        root = logging.getLogger()
        if not args.verbose:
            for handler in root.handlers:
                handler.setLevel(logging.WARNING)
        timing_handler = _StageTimingHandler()
        file_handler = logging.FileHandler(out_dir / f"{label}.log", mode="w")
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")
        )
        root.addHandler(timing_handler)
        root.addHandler(file_handler)

        chapters = load_chapters(Path(args.chapters)) if args.chapters else None
        t0 = time.perf_counter()
        try:
            job = asyncio.run(_run_job(input_path, work_dir, chapters))
        finally:
            root.removeHandler(timing_handler)
            root.removeHandler(file_handler)
            file_handler.close()
        wall = time.perf_counter() - t0

        outputs = []
        for src in job["output_paths"]:
            dst = artifacts / Path(src).name
            shutil.copy2(src, dst)
            outputs.append(
                {**_probe_media(dst), "path": str(dst.relative_to(_REPO_ROOT))}
            )

    from app.settings import settings

    result = {
        "label": label,
        "status": job["status"],
        "error": job["error"],
        "wall_clock_s": round(wall, 3),
        "stages": aggregate_stage_timings(timing_handler.timings),
        "peak_rss_mib": {
            "self": round(
                normalize_maxrss_mib(
                    resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                ),
                1,
            ),
            "children": round(
                normalize_maxrss_mib(
                    resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
                ),
                1,
            ),
        },
        "input": {**_probe_media(input_path), "path": str(input_path)},
        "chapters": [vars(c) for c in chapters] if chapters else None,
        "outputs": outputs,
        "settings": {
            "transcription_provider": settings.transcription_provider,
            "whisper_model": settings.whisper_model,
            "audio_enhance_provider": settings.audio_enhance_provider,
            "max_parallel_chapters": settings.max_parallel_chapters,
        },
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "machine": platform.machine(),
            **_git_revision(),
        },
    }
    json_path = out_dir / f"{label}.json"
    json_path.write_text(json.dumps(result, indent=2) + "\n")
    print(
        f"wrote {json_path.relative_to(_REPO_ROOT)}  status={result['status']}  wall={result['wall_clock_s']}s"
    )
    return result


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--input", help="local video file to process")
    parser.add_argument("--label", help="result name (default: input stem)")
    parser.add_argument(
        "--chapters", help="JSON list of {title,start,end} chapter spans"
    )
    parser.add_argument(
        "--real-whisper",
        action="store_true",
        help="use faster-whisper instead of the stub",
    )
    parser.add_argument(
        "--out-dir", default=str(DEFAULT_OUT_DIR), help="where JSON/log/outputs go"
    )
    parser.add_argument(
        "--verbose", action="store_true", help="echo pipeline INFO logs to stdout"
    )
    parser.add_argument(
        "--compare",
        nargs=2,
        metavar=("A_JSON", "B_JSON"),
        help="print a markdown table",
    )
    args = parser.parse_args(argv)
    if not args.compare and not args.input:
        parser.error("--input is required unless --compare is given")
    return args


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    if args.compare:
        a, b = (json.loads(Path(p).read_text()) for p in args.compare)
        print(compare_table(a, b))
        return 0
    result = run_bench(args)
    return 0 if result["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
