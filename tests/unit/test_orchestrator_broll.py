"""B-roll wiring in the orchestrator (FR-010, T012): ``_broll_step``.

With the job's ``broll`` option on and ``settings.broll_provider`` other than
``none``, the planner's windows over the clip's words are fetched through
the provider (a fake here, installed as what ``get_broll_provider`` returns),
passed to ``render_clip(broll=...)`` and persisted on the clip as
``broll_assets``. Any failure renders the reel without B-roll and emits
``StageSkipped(broll, reason)``; cancellation propagates.
"""

from __future__ import annotations

import asyncio
import csv
import json
import shutil
import threading
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.bus.event_bus import AsyncEventBus
from app.bus.job_store import InMemoryJobStore, SqlJobStore
from app.db import models as _models  # noqa: F401 — registers tables on Base
from app.db.base import Base
from app.domain.events import Event, EventType
from app.domain.models import JobState, PipelineOptions
from app.services.broll_service import BrollAsset
from app.services.platforms.base import Chapter, DownloadResult
from app.services.render_service import BrollInsert
from app.services.transcription_service import WordTiming
from app.workers import orchestrator as orch

JOB_ID = "job-broll"
CLIP_ID = "c1-0000-broll"
SAMPLE = Path(__file__).resolve().parents[1] / "fixtures" / "sample.mp4"
_OPTS = PipelineOptions(audio_enhance=False, thumbnail=False)
# Chapter 100-130 s of the source; the words below land at clip 5 s and 15 s.
CHAPTER = {"index": 0, "title": "Talk", "start": 100.0, "end": 130.0}
SOURCE_WORDS = [
    WordTiming("we", 104.0, 104.2),
    WordTiming("saw", 104.5, 104.9),
    WordTiming("mountains", 105.0, 105.6),
    WordTiming("and", 110.0, 110.2),
    WordTiming("rivers", 115.0, 115.5),
]


@pytest.fixture(params=["memory", "sql"])
async def store(request: pytest.FixtureRequest) -> AsyncIterator[Any]:
    if request.param == "memory":
        yield InMemoryJobStore()
        return
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sql_store = SqlJobStore()
    sql_store._factory = async_sessionmaker(engine, expire_on_commit=False)
    yield sql_store
    await engine.dispose()


@pytest.fixture
def memory_store() -> InMemoryJobStore:
    return InMemoryJobStore()


@pytest.fixture
def render_calls(monkeypatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def _render(video_path, output_path, start, end, *args, **kwargs):
        calls.append({"args": (video_path, start, end, *args), **kwargs})
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"reel")
        return output_path

    monkeypatch.setattr(orch.render_service, "render_clip", _render)
    return calls


@pytest.fixture
def assets(tmp_path) -> dict[str, BrollAsset]:
    lib = tmp_path / "lib"
    lib.mkdir()
    out = {}
    for n, query in enumerate(("mountains", "rivers", "ocean")):
        path = lib / f"{query}.mp4"
        shutil.copy(SAMPLE, path)
        out[query] = BrollAsset(
            str(path), "fake", f"id-{n}", f"Author {n}", f"https://example.test/{n}"
        )
    return out


class _Provider:
    name = "fake"

    def __init__(
        self, results: dict[str, Any], *, barrier: asyncio.Barrier | None = None
    ):
        self.results = results
        self.barrier = barrier
        self.calls: list[str] = []
        self.threads: set[int] = set()
        self.started = asyncio.Event()
        self.hang = asyncio.Event()

    async def fetch(self, query: str) -> BrollAsset | None:
        self.calls.append(query)
        self.threads.add(threading.get_ident())
        self.started.set()
        if self.barrier is not None:
            await asyncio.wait_for(self.barrier.wait(), timeout=2)
        result = self.results.get(query)
        if result == "hang":
            await self.hang.wait()
        if isinstance(result, BaseException):
            raise result
        return result


@pytest.fixture
def provider(monkeypatch):
    """Install a fake as what ``get_broll_provider`` returns for "local"."""
    box: dict[str, Any] = {"names": []}

    def install(fake: _Provider) -> _Provider:
        def _get(name: str):
            box["names"].append(name)
            return fake

        monkeypatch.setattr(orch.broll_service, "get_broll_provider", _get)
        return fake

    box["install"] = install
    monkeypatch.setattr(orch.settings, "broll_provider", "local")
    return box


class _Bus(AsyncEventBus):
    def __init__(self) -> None:
        super().__init__()
        self.events: list[Event] = []

    async def publish(self, event: Event) -> None:  # type: ignore[override]
        self.events.append(event)
        await super().publish(event)

    def broll_skips(self) -> list[dict[str, Any]]:
        return [
            e.payload
            for e in self.events
            if e.type is EventType.STAGE_SKIPPED
            and e.payload.get("stage_id") == "broll"
        ]

    def applied(self) -> list[dict[str, Any]]:
        return [e.payload for e in self.events if e.type is EventType.BROLL_APPLIED]


async def _seeded(store) -> Any:
    await store.create(JobState(job_id=JOB_ID, url="https://x/v", download_path="/d"))
    return store


async def _chapter(
    tmp_path, bus, store, *, opts=_OPTS, clip_id=None, words=SOURCE_WORDS
):
    cleanup = tmp_path / "tmp"
    cleanup.mkdir(exist_ok=True)
    return await orch._process_chapter(
        chapter=dict(CHAPTER),
        job_id=JOB_ID,
        video_path=str(tmp_path / "source.mp4"),
        clips_folder=str(tmp_path / "clips"),
        cleanup_root=cleanup,
        caption_format="srt",
        target_aspect_ratio=9 / 16,
        bus=bus,
        store=store,
        pipeline_options=opts,
        clip_id=clip_id,
        source_words=words,
    )


def _record(asset: BrollAsset, query: str, start: float) -> dict[str, Any]:
    return {
        "query": query,
        "start": start,
        "duration": 3.0,
        "provider": asset.provider,
        "asset_id": asset.asset_id,
        "author": asset.author,
        "source_url": asset.source_url,
        "path": asset.path,
    }


# ── inserts reach the render and the clip ─────────────────────────────────────


async def test_planned_windows_are_passed_to_the_render(
    tmp_path, render_calls, provider, assets, memory_store
):
    fake = provider["install"](_Provider(dict(assets)))
    bus = _Bus()

    out = await _chapter(tmp_path, bus, await _seeded(memory_store))

    assert out is not None
    [call] = render_calls
    assert call["broll"] == [
        BrollInsert(assets["mountains"].path, 5.0, 3.0),
        BrollInsert(assets["rivers"].path, 15.0, 3.0),
    ]
    assert sorted(fake.calls) == ["mountains", "rivers"]
    assert provider["names"] == ["local"]
    assert bus.broll_skips() == []
    [applied] = bus.applied()
    assert applied["chapter_index"] == 0
    assert applied["provider"] == "local"
    assert [a["query"] for a in applied["assets"]] == ["mountains", "rivers"]
    types = [e.type for e in bus.events]
    assert types.index(EventType.BROLL_APPLIED) < types.index(EventType.CLIP_RENDERED)


async def test_broll_assets_are_persisted_on_the_clip(
    tmp_path, render_calls, provider, assets, store
):
    provider["install"](_Provider(dict(assets)))
    await _seeded(store)

    await _chapter(tmp_path, _Bus(), store)

    [clip] = await store.list_clips(job_id=JOB_ID)
    assert clip["broll_assets"] == [
        _record(assets["mountains"], "mountains", 5.0),
        _record(assets["rivers"], "rivers", 15.0),
    ]


async def test_a_query_without_an_asset_drops_only_its_insert(
    tmp_path, render_calls, provider, assets, memory_store
):
    provider["install"](_Provider({"mountains": None, "rivers": assets["rivers"]}))
    bus = _Bus()

    await _chapter(tmp_path, bus, await _seeded(memory_store))

    assert render_calls[0]["broll"] == [BrollInsert(assets["rivers"].path, 15.0, 3.0)]
    [clip] = await memory_store.list_clips(job_id=JOB_ID)
    assert [a["query"] for a in clip["broll_assets"]] == ["rivers"]
    assert bus.broll_skips() == []


# ── nothing runs unless asked ─────────────────────────────────────────────────


async def test_provider_none_skips_with_a_reason_and_asks_no_provider(
    tmp_path, render_calls, provider, assets, memory_store, monkeypatch
):
    fake = provider["install"](_Provider(dict(assets)))
    monkeypatch.setattr(orch.settings, "broll_provider", "none")
    planned = []
    monkeypatch.setattr(
        orch.broll_planner, "plan_broll", lambda *a, **k: planned.append(1)
    )
    bus = _Bus()

    await _chapter(tmp_path, bus, await _seeded(memory_store))

    assert bus.broll_skips() == [
        {"stage_id": "broll", "chapter_index": 0, "reason": "no provider"}
    ]
    assert provider["names"] == [] and fake.calls == [] and planned == []
    assert render_calls[0]["broll"] is None
    assert bus.applied() == []
    [clip] = await memory_store.list_clips(job_id=JOB_ID)
    assert clip["broll_assets"] is None


@pytest.mark.parametrize(
    "opts",
    [
        _OPTS.model_copy(update={"broll": False}),
        _OPTS.model_copy(update={"render": False, "broll": True}),
    ],
)
async def test_option_off_runs_nothing(
    tmp_path, render_calls, provider, assets, memory_store, monkeypatch, opts
):
    fake = provider["install"](_Provider(dict(assets)))
    planned = []
    monkeypatch.setattr(
        orch.broll_planner, "plan_broll", lambda *a, **k: planned.append(1)
    )
    bus = _Bus()

    await _chapter(tmp_path, bus, await _seeded(memory_store), opts=opts)

    assert provider["names"] == [] and fake.calls == [] and planned == []
    assert bus.applied() == []
    # render=False keeps its single pre-existing StageSkipped(broll) only when
    # broll is also off; broll=False with render on reports nothing, as before.
    assert all(s.get("reason") is None for s in bus.broll_skips())
    for call in render_calls:
        assert call["broll"] is None


async def test_provider_none_renders_exactly_like_the_option_off(
    tmp_path, render_calls, memory_store, monkeypatch
):
    """Default provider (none) and ``broll`` off give the same render call:
    ``broll=None``, which leaves the ffmpeg argv byte-identical
    (``test_no_broll_argv_is_byte_identical_to_the_golden``)."""
    monkeypatch.setattr(orch.settings, "broll_provider", "none")
    await _chapter(tmp_path, _Bus(), await _seeded(memory_store))
    monkeypatch.setattr(orch.settings, "broll_provider", "local")
    off = _OPTS.model_copy(update={"broll": False})
    await _chapter(tmp_path, _Bus(), memory_store, opts=off)

    provider_none, option_off = render_calls
    assert provider_none["broll"] is None
    assert provider_none == option_off


# ── failures never fail the chapter ───────────────────────────────────────────


async def test_every_query_failing_renders_without_broll(
    tmp_path, render_calls, provider, memory_store
):
    provider["install"](
        _Provider(
            {"mountains": RuntimeError("api down"), "rivers": RuntimeError("api down")}
        )
    )
    bus = _Bus()

    out = await _chapter(tmp_path, bus, await _seeded(memory_store))

    assert out is not None
    assert render_calls[0]["broll"] is None
    assert bus.broll_skips() == [
        {
            "stage_id": "broll",
            "chapter_index": 0,
            "reason": "no B-roll found for: mountains, rivers",
        }
    ]
    assert any(e.type is EventType.CLIP_RENDERED for e in bus.events)
    [clip] = await memory_store.list_clips(job_id=JOB_ID)
    assert clip["broll_assets"] is None


async def test_an_error_in_the_step_renders_without_broll(
    tmp_path, render_calls, provider, assets, memory_store, monkeypatch, caplog
):
    provider["install"](_Provider(dict(assets)))

    def _boom(*_a, **_k):
        raise RuntimeError("planner exploded")

    monkeypatch.setattr(orch.broll_planner, "plan_broll", _boom)
    bus = _Bus()

    out = await _chapter(tmp_path, bus, await _seeded(memory_store))

    assert out is not None
    assert render_calls[0]["broll"] is None
    assert [s["reason"] for s in bus.broll_skips()] == [
        "B-roll failed: planner exploded"
    ]
    assert "planner exploded" in caplog.text


async def test_an_unknown_provider_renders_without_broll(
    tmp_path, render_calls, memory_store, monkeypatch
):
    monkeypatch.setattr(orch.settings, "broll_provider", "pexel")
    bus = _Bus()

    await _chapter(tmp_path, bus, await _seeded(memory_store))

    assert render_calls[0]["broll"] is None
    [skip] = bus.broll_skips()
    assert skip["reason"].startswith("B-roll failed: no B-roll provider 'pexel'")


async def test_words_without_a_keyword_skip_the_stage(
    tmp_path, render_calls, provider, assets, memory_store
):
    fake = provider["install"](_Provider(dict(assets)))
    bus = _Bus()
    words = [WordTiming("the", 105.0, 105.2), WordTiming("cat", 106.0, 106.2)]

    await _chapter(tmp_path, bus, await _seeded(memory_store), words=words)

    assert fake.calls == []
    assert [s["reason"] for s in bus.broll_skips()] == ["no keyword to illustrate"]
    assert render_calls[0]["broll"] is None


async def test_cancellation_propagates(
    tmp_path, render_calls, provider, assets, memory_store
):
    fake = provider["install"](_Provider({**assets, "mountains": "hang"}))
    bus = _Bus()
    task = asyncio.create_task(_chapter(tmp_path, bus, await _seeded(memory_store)))
    await asyncio.wait_for(fake.started.wait(), timeout=2)

    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert render_calls == []
    assert bus.broll_skips() == []


async def test_fetches_run_concurrently_on_the_event_loop(
    tmp_path, render_calls, provider, assets, memory_store
):
    """Both fetches are in flight at once (the barrier needs two parties) and
    each is awaited on the loop's own thread, so a slow network fetch blocks
    neither the loop nor the other fetch."""
    fake = provider["install"](_Provider(dict(assets), barrier=asyncio.Barrier(2)))

    await _chapter(tmp_path, _Bus(), await _seeded(memory_store))

    assert len(render_calls[0]["broll"]) == 2
    assert fake.threads == {threading.get_ident()}


# ── re-render and the whole job ───────────────────────────────────────────────


async def test_rerender_goes_through_the_same_step(
    tmp_path, render_calls, provider, assets, store, monkeypatch
):
    provider["install"](_Provider(dict(assets)))
    await _seeded(store)
    clips = tmp_path / "clips"
    clips.mkdir()
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    output = clips / "00_Talk.mp4"
    output.write_bytes(b"old")

    def _complete(s: JobState) -> None:
        s.status = "completed"
        s.video_path = str(source)
        s.pipeline_options = _OPTS

    await store.update(JOB_ID, _complete)
    await store.upsert_clip(
        JOB_ID,
        CLIP_ID,
        lambda c: c.update({"start": 100.0, "end": 130.0, "output_path": str(output),
                            "title": "Talk", "broll_assets": [{"query": "stale"}]}),
    )  # fmt: skip
    monkeypatch.setattr(
        orch.segment_discovery, "read_words_sidecar", lambda video_path: SOURCE_WORDS
    )
    trigger = Event(
        type=EventType.VIDEO_REQUESTED,
        job_id=JOB_ID,
        payload={"rerender_clip_id": CLIP_ID, "regenerate_copy": False},
    )

    await orch._rerender_clip(trigger, _Bus(), store)

    assert render_calls[0]["broll"] == [
        BrollInsert(assets["mountains"].path, 5.0, 3.0),
        BrollInsert(assets["rivers"].path, 15.0, 3.0),
    ]
    clip = await store.get_clip(CLIP_ID)
    assert [a["query"] for a in clip["broll_assets"]] == ["mountains", "rivers"]

    # Re-rendered again with B-roll off: the stale credits go with it.
    monkeypatch.setattr(orch.settings, "broll_provider", "none")
    await orch._rerender_clip(trigger, _Bus(), store)

    assert render_calls[1]["broll"] is None
    assert (await store.get_clip(CLIP_ID))["broll_assets"] is None


class _LongAdapter:
    platform_id = "youtube"

    def download(self, url: str, destination_folder: str) -> DownloadResult:
        video_path = str(Path(destination_folder) / "video.mp4")
        Path(video_path).write_bytes(b"\x00")
        info = {"title": "Long", "duration": 30.0}
        return DownloadResult(
            video_path=video_path,
            info=info,
            title="Long",
            duration=30.0,
            source="youtube",
        )

    def extract_chapters(self, info: dict) -> list[Chapter]:
        return [Chapter(index=0, title="Long", start=0.0, end=30.0)]


async def _run_job(tmp_path, monkeypatch, store) -> list[Event]:
    bus = AsyncEventBus()
    await store.create(
        JobState(
            job_id=JOB_ID,
            url="https://www.youtube.com/watch?v=b",
            download_path=str(tmp_path),
        )
    )

    def _subfolder(download_path, url, platform_id="video", job_id=None):
        clips = Path(download_path) / "vid" / "clips"
        clips.mkdir(parents=True, exist_ok=True)
        return str(clips.parent), str(clips)

    def _extract_audio(src, start, duration, wav_path):
        Path(wav_path).write_bytes(b"\x00")
        return wav_path

    monkeypatch.setattr(orch.folder_service, "create_video_subfolder", _subfolder)
    monkeypatch.setattr(orch, "resolve_adapter", lambda url: _LongAdapter())
    monkeypatch.setattr(orch.clip_service, "probe_safe_end", lambda path: 30.0)
    monkeypatch.setattr(orch.clip_service, "extract_audio", _extract_audio)
    monkeypatch.setattr(
        orch.transcription_service,
        "transcribe_to_words",
        lambda audio_path, **_: [
            WordTiming("mountains", 5.0, 5.5),
            WordTiming("rivers", 15.0, 15.4),
        ],
    )
    monkeypatch.setattr(orch.settings, "export_base_folder", str(tmp_path / "exports"))

    received: list[Event] = []

    async def collect() -> None:
        async for event in bus.subscribe(job_id=JOB_ID):
            received.append(event)
            if event.type in (EventType.JOB_COMPLETED, EventType.JOB_FAILED):
                return

    collector = asyncio.create_task(collect())
    worker = asyncio.create_task(orch.run_orchestrator(bus, store))
    for _ in range(5):
        await asyncio.sleep(0.01)
    await bus.publish(
        Event(
            type=EventType.VIDEO_REQUESTED,
            job_id=JOB_ID,
            payload={
                "url": "https://www.youtube.com/watch?v=b",
                "download_path": str(tmp_path),
                "pipeline_options": _OPTS.model_dump(),
            },
        )
    )
    await asyncio.wait_for(collector, timeout=10)
    worker.cancel()
    try:
        await worker
    except asyncio.CancelledError:
        pass
    return received


async def test_a_job_whose_provider_fails_completes_without_broll(
    tmp_path, render_calls, provider, memory_store, monkeypatch
):
    provider["install"](
        _Provider({"mountains": RuntimeError("down"), "rivers": RuntimeError("down")})
    )

    events = await _run_job(tmp_path, monkeypatch, memory_store)

    assert events[-1].type is EventType.JOB_COMPLETED
    assert (await memory_store.get(JOB_ID)).status == "completed"
    assert render_calls[0]["broll"] is None
    skips = [
        e.payload["reason"]
        for e in events
        if e.type is EventType.STAGE_SKIPPED and e.payload.get("stage_id") == "broll"
    ]
    assert skips == ["no B-roll found for: mountains, rivers"]


async def test_a_job_with_broll_credits_it_in_the_manifest(
    tmp_path, render_calls, provider, assets, memory_store, monkeypatch
):
    provider["install"](_Provider(dict(assets)))

    events = await _run_job(tmp_path, monkeypatch, memory_store)

    assert events[-1].type is EventType.JOB_COMPLETED
    [manifest] = (tmp_path / "exports").rglob("manifest.csv")
    with manifest.open(encoding="utf-8") as fh:
        [row] = list(csv.DictReader(fh))
    assert json.loads(row["broll_credits"]) == [
        {
            "provider": "fake",
            "author": "Author 0",
            "source_url": "https://example.test/0",
        },
        {
            "provider": "fake",
            "author": "Author 1",
            "source_url": "https://example.test/1",
        },
    ]
