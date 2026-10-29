"""``GET /api/clips/{clip_id}/edit`` words and default timeline (spec 016, T126).

With no saved edit, a clip with a rendered file answers a default timeline
built from its words (``<job folder>/.clip_words/<clip stem>.words.json``). A clip rendered before the
sidecar existed is transcribed once on that first open and the sidecar
cached; a failed or skipped backfill answers 200 with no words. Hermetic:
in-memory SQLite and a fake transcriber (no whisper, no network).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import ClipRecord, JobRecord
from app.db.session import get_session
from app.main import create_app
from app.services import clip_words, transcription_service
from app.services.transcription_service import WordTiming
from app.settings import settings
from app.workers import orchestrator

CLIP_WORDS = [
    {"word": "we", "start": 0.5, "end": 0.8},
    {"word": "saw", "start": 1.0, "end": 1.3},
    {"word": "the", "start": 1.4, "end": 1.5},
    {"word": "sea", "start": 2.0, "end": 2.6},
]


class _Transcriber:
    """Fake ``transcribe_words_async``: records calls, answers ``result``."""

    def __init__(self, result: Any) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, audio_path, *, language, audio_duration_s, repeatable=False):
        self.calls.append({"path": audio_path, "language": language, "duration": audio_duration_s})
        if isinstance(self.result, BaseException):
            raise self.result
        return list(self.result)


@pytest.fixture
def downloads(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "downloads"
    (root / "Talk-1234" / "clips").mkdir(parents=True)
    monkeypatch.setattr(settings, "default_download_path", str(root))
    monkeypatch.setattr(settings, "caption_words_per_segment", 3)
    return root


@pytest.fixture
def transcriber(monkeypatch):
    def install(result: Any) -> _Transcriber:
        fake = _Transcriber(result)
        monkeypatch.setattr(transcription_service, "transcribe_words_async", fake)
        return fake

    return install


@pytest.fixture
async def make_client():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    clients: list[httpx.AsyncClient] = []

    async def _make(output_path: str | None) -> tuple[httpx.AsyncClient, str, str]:
        async with factory() as session:
            job = JobRecord(youtube_url="https://example.com/v", language="en-US")
            session.add(job)
            await session.flush()
            clip = ClipRecord(job_id=job.id, start=100.0, end=110.0, output_path=output_path)
            session.add(clip)
            await session.commit()
            clip_id, job_id = clip.id, job.id

        async def _override():
            async with factory() as session:
                yield session

        app_inst = create_app()
        app_inst.dependency_overrides[get_session] = _override
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app_inst), base_url="http://test")
        clients.append(client)
        return client, clip_id, job_id

    yield _make
    for client in clients:
        await client.aclose()
    await engine.dispose()


def _clip_file(folder: Path) -> Path:
    clip = folder / "00_Talk.mp4"
    clip.write_bytes(b"reel")
    return clip


def _approx(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{**w, "start": pytest.approx(w["start"]), "end": pytest.approx(w["end"])} for w in words]


def _caption_items(body: dict[str, Any]) -> list[dict[str, Any]]:
    (track,) = [t for t in body["timeline"]["tracks"] if t["kind"] == "caption"]
    return track["items"]


async def test_sidecar_words_give_a_default_timeline(downloads, make_client, transcriber):
    clip = _clip_file(downloads / "Talk-1234" / "clips")
    clip_words.write_clip_words(str(clip), [WordTiming(**w) for w in CLIP_WORDS])
    fake = transcriber(RuntimeError("must not transcribe"))
    client, clip_id, _ = await make_client(str(clip))

    r = await client.get(f"/api/clips/{clip_id}/edit")

    assert r.status_code == 200
    body = r.json()
    assert body["clip_id"] == clip_id
    assert body["version"] == 0
    assert body["is_default"] is True
    assert body["words"] == _approx(CLIP_WORDS)
    items = _caption_items(body)
    assert [i["text"] for i in items] == ["we saw the", "sea"]
    assert items[0]["words"] == _approx(CLIP_WORDS[:3])
    assert items[1]["words"] == _approx(CLIP_WORDS[3:])
    (video,) = [t for t in body["timeline"]["tracks"] if t["kind"] == "video"]
    assert video["items"][0]["end"] == pytest.approx(10.0)
    assert fake.calls == []


async def test_first_open_backfills_and_caches_the_sidecar(downloads, make_client, transcriber):
    clip = _clip_file(downloads / "Talk-1234" / "clips")
    # One word runs past the 10 s clip: clamped to the window.
    fake = transcriber([WordTiming("hi", 0.2, 0.5), WordTiming("there", 9.8, 10.4)])
    client, clip_id, _ = await make_client(str(clip))

    r = await client.get(f"/api/clips/{clip_id}/edit")

    assert r.status_code == 200
    expected = [
        {"word": "hi", "start": 0.2, "end": 0.5},
        {"word": "there", "start": 9.8, "end": 10.0},
    ]
    assert r.json()["words"] == _approx(expected)
    assert _caption_items(r.json())[0]["words"] == _approx(expected)
    assert fake.calls == [{"path": str(clip), "language": "en-US", "duration": pytest.approx(10.0)}]
    sidecar = clip_words.clip_words_path(str(clip))
    assert sidecar == downloads / "Talk-1234" / ".clip_words" / "00_Talk.words.json"
    assert json.loads(sidecar.read_text()) == _approx(expected)
    assert [p.name for p in clip.parent.iterdir()] == ["00_Talk.mp4"]

    # The second open reads the cache.
    r2 = await client.get(f"/api/clips/{clip_id}/edit")
    assert r2.json()["words"] == _approx(expected)
    assert len(fake.calls) == 1


async def test_missing_clip_file_is_404_and_never_transcribed(downloads, make_client, transcriber):
    fake = transcriber([WordTiming("x", 0.0, 1.0)])
    gone = downloads / "Talk-1234" / "clips" / "00_Gone.mp4"
    client, clip_id, _ = await make_client(str(gone))

    r = await client.get(f"/api/clips/{clip_id}/edit")

    assert r.status_code == 404
    assert fake.calls == []
    assert not clip_words.clip_words_path(str(gone)).exists()


@pytest.mark.parametrize("error", [RuntimeError("whisper exploded"), TimeoutError("decode budget")])
async def test_failed_transcription_answers_empty_words(downloads, make_client, transcriber, error):
    clip = _clip_file(downloads / "Talk-1234" / "clips")
    fake = transcriber(error)
    client, clip_id, _ = await make_client(str(clip))

    r = await client.get(f"/api/clips/{clip_id}/edit")

    assert r.status_code == 200
    assert r.json()["words"] == []
    assert _caption_items(r.json()) == []
    assert len(fake.calls) == 1
    # Nothing cached: the next open tries again.
    assert not clip_words.clip_words_path(str(clip)).exists()


async def test_backfill_is_bounded_in_time(downloads, make_client, transcriber, monkeypatch):
    import asyncio

    from app.services import clip_words

    clip = _clip_file(downloads / "Talk-1234" / "clips")
    monkeypatch.setattr(clip_words, "BACKFILL_GRACE_SECONDS", 0.05)
    monkeypatch.setattr(transcription_service, "decode_timeout_seconds", lambda s: 0.0)
    released = asyncio.Event()

    async def _hang(audio_path, *, language, audio_duration_s, repeatable=False):
        try:
            await asyncio.sleep(30)
        finally:
            released.set()

    monkeypatch.setattr(transcription_service, "transcribe_words_async", _hang)
    client, clip_id, job_id = await make_client(str(clip))

    r = await asyncio.wait_for(client.get(f"/api/clips/{clip_id}/edit"), timeout=5)

    assert r.status_code == 200
    assert r.json()["words"] == []
    assert released.is_set()
    assert not orchestrator.job_has_work(job_id)


async def test_export_copy_is_not_transcribed(tmp_path, downloads, make_client, transcriber):
    outside = tmp_path / "exports"
    outside.mkdir()
    clip = _clip_file(outside)
    fake = transcriber([WordTiming("x", 0.0, 1.0)])
    client, clip_id, _ = await make_client(str(clip))

    r = await client.get(f"/api/clips/{clip_id}/edit")

    assert r.status_code == 200
    assert r.json()["words"] == []
    assert fake.calls == []
    assert not clip_words.clip_words_path(str(clip)).exists()


async def test_refused_sidecar_path_is_not_transcribed(tmp_path, downloads, make_client, transcriber):
    """A words folder swapped for a symlink: nothing is cached through it, so
    the clip is not transcribed (it would be on every open)."""
    import os

    clip = _clip_file(downloads / "Talk-1234" / "clips")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    os.symlink(elsewhere, downloads / "Talk-1234" / ".clip_words")
    fake = transcriber([WordTiming("x", 0.0, 1.0)])
    client, clip_id, _ = await make_client(str(clip))

    r = await client.get(f"/api/clips/{clip_id}/edit")

    assert r.status_code == 200
    assert r.json()["words"] == []
    assert fake.calls == []
    assert list(elsewhere.iterdir()) == []


async def test_busy_job_is_not_transcribed(downloads, make_client, transcriber):
    clip = _clip_file(downloads / "Talk-1234" / "clips")
    fake = transcriber([WordTiming("x", 0.0, 1.0)])
    client, clip_id, job_id = await make_client(str(clip))
    orchestrator.claim_work(job_id)
    try:
        r = await client.get(f"/api/clips/{clip_id}/edit")
    finally:
        orchestrator.release_work(job_id)

    assert r.status_code == 200
    assert r.json()["words"] == []
    assert fake.calls == []


async def test_backfill_holds_the_job_until_done(downloads, make_client, transcriber, monkeypatch):
    """A job delete during the backfill sees work on the job (409)."""
    clip = _clip_file(downloads / "Talk-1234" / "clips")
    client, clip_id, job_id = await make_client(str(clip))
    seen: list[bool] = []

    async def _record(audio_path, *, language, audio_duration_s, repeatable=False):
        seen.append(orchestrator.job_has_work(job_id))
        return [WordTiming("x", 0.0, 1.0)]

    monkeypatch.setattr(transcription_service, "transcribe_words_async", _record)

    r = await client.get(f"/api/clips/{clip_id}/edit")

    assert r.status_code == 200
    assert seen == [True]
    assert not orchestrator.job_has_work(job_id)


async def test_saved_edit_wins_and_carries_the_words(downloads, make_client, transcriber):
    clip = _clip_file(downloads / "Talk-1234" / "clips")
    clip_words.write_clip_words(str(clip), [WordTiming(**w) for w in CLIP_WORDS])
    transcriber(RuntimeError("must not transcribe"))
    client, clip_id, _ = await make_client(str(clip))
    timeline = {
        "tracks": [
            {"kind": "video", "items": [{"start": 0.0, "end": 10.0, "src": "main"}]},
            {"kind": "caption", "items": []},
            {"kind": "text-overlay", "items": []},
        ]
    }
    assert (await client.put(f"/api/clips/{clip_id}/edit", json={"timeline": timeline})).status_code == 200

    r = await client.get(f"/api/clips/{clip_id}/edit")

    body = r.json()
    assert body["version"] == 1
    assert "is_default" not in body
    assert body["timeline"]["tracks"][1]["items"] == []
    assert body["words"] == _approx(CLIP_WORDS)


async def test_saving_the_default_timeline_round_trips(downloads, make_client, transcriber):
    clip = _clip_file(downloads / "Talk-1234" / "clips")
    clip_words.write_clip_words(str(clip), [WordTiming(**w) for w in CLIP_WORDS])
    client, clip_id, _ = await make_client(str(clip))
    default = (await client.get(f"/api/clips/{clip_id}/edit")).json()

    r = await client.put(f"/api/clips/{clip_id}/edit", json={"timeline": default["timeline"]})

    assert r.status_code == 200
    assert r.json()["timeline"] == default["timeline"]
    plan = await client.get(f"/api/clips/{clip_id}/edit/plan")
    assert plan.status_code == 200
    assert [c["text"] for c in plan.json()["captions"]] == ["we saw the", "sea"]
