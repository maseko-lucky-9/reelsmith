"""``POST /api/clips/{clip_id}/voiceover`` (spec 016) and the AI hook route's
transcript handling. Hermetic: in-memory SQLite, tmp folders, the stub engine
(or a fake one); Piper never runs and nothing is downloaded."""

from __future__ import annotations

import wave
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import ClipEdit, ClipRecord, JobRecord
from app.db.session import get_session
from app.main import create_app
from app.services import clip_words, voiceover_service
from app.services.transcription_service import WordTiming
from app.settings import settings
from app.workers import orchestrator

WORDS = [
    WordTiming("we", 0.5, 0.8),
    WordTiming("saw", 1.0, 1.3),
    WordTiming("the", 1.4, 1.5),
    WordTiming("sea", 1.6, 2.0),
]


@pytest.fixture
def downloads(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "downloads"
    (root / "Talk-1234" / "clips").mkdir(parents=True)
    monkeypatch.setattr(settings, "default_download_path", str(root))
    monkeypatch.setattr(settings, "caption_words_per_segment", 2)
    monkeypatch.setattr(settings, "voiceover_provider", "stub")
    monkeypatch.setattr(settings, "voiceover_model_dir", str(tmp_path / "models"))
    return root


@pytest.fixture
def clip_file(downloads) -> Path:
    path = downloads / "Talk-1234" / "clips" / "00_Talk.mp4"
    path.write_bytes(b"reel")
    clip_words.write_clip_words(str(path), WORDS)
    return path


@pytest.fixture
async def make_client():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    clients: list[httpx.AsyncClient] = []

    async def _make(
        output_path: str | None,
        *,
        hook: str | None = "Wait for the twist.",
        timeline: dict[str, Any] | None = None,
        transcript: Any = None,
        summary: str | None = None,
    ) -> tuple[httpx.AsyncClient, str, str]:
        async with factory() as session:
            job = JobRecord(youtube_url="https://example.com/v", state={"duration": 600.0})
            session.add(job)
            await session.flush()
            clip = ClipRecord(
                job_id=job.id,
                start=100.0,
                end=110.0,
                output_path=output_path,
                ai_hook_text=hook,
                transcript=transcript,
                summary=summary,
            )
            session.add(clip)
            await session.flush()
            if timeline is not None:
                session.add(ClipEdit(clip_id=clip.id, timeline=timeline, version=3))
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

    _make.factory = factory  # type: ignore[attr-defined]
    yield _make
    for client in clients:
        await client.aclose()
    await engine.dispose()


async def _stored(make_client, clip_id: str) -> ClipEdit | None:
    async with make_client.factory() as session:
        return (await session.execute(select(ClipEdit).where(ClipEdit.clip_id == clip_id))).scalar_one_or_none()


def _wavs(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.glob("voiceover_*.wav"))


# ── Happy paths ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("prefix", ["", "/api"])
async def test_creates_a_wav_in_the_clip_folder_and_a_v2_edit(make_client, clip_file, prefix):
    client, clip_id, _ = await make_client(str(clip_file))

    r = await client.post(f"{prefix}/clips/{clip_id}/voiceover", json={"text": "Hello there"})

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["provider"] == "stub"
    assert body["item"] == {"start": 0.0, "end": 1.0, "src": "voiceover_1.wav", "gain": 1.0}
    wav_path = clip_file.parent / "voiceover_1.wav"
    with wave.open(str(wav_path)) as wav:
        assert wav.getnframes() > 0
    timeline = body["edit"]["timeline"]
    assert timeline["schema_version"] == 2
    kinds = {t["kind"]: t["items"] for t in timeline["tracks"]}
    assert kinds["video"] == [{"start": 100.0, "end": 110.0, "src": "main"}]
    assert [c["text"] for c in kinds["caption"]] == ["we saw", "the sea"]
    assert kinds["audio"] == [body["item"]]
    assert body["edit"]["version"] == 1
    stored = await _stored(make_client, clip_id)
    assert stored is not None and stored.timeline == timeline


async def test_default_script_is_the_ai_hook(make_client, clip_file, monkeypatch):
    spoken: list[str] = []
    real = voiceover_service.synthesize_voiceover

    def spy(text, voice, out_path, **kw):
        spoken.append(text)
        return real(text, voice, out_path, **kw)

    monkeypatch.setattr(voiceover_service, "synthesize_voiceover", spy)
    client, clip_id, _ = await make_client(str(clip_file), hook="Wait for\x00 the twist.")

    r = await client.post(f"/api/clips/{clip_id}/voiceover")

    assert r.status_code == 200, r.text
    assert spoken == ["Wait for the twist."]


async def test_appends_to_a_saved_v2_edit_and_keeps_its_items(make_client, clip_file):
    (clip_file.parent / "old.wav").write_bytes(b"RIFF")
    saved = {
        "schema_version": 2,
        "caption_style": "hormozi",
        "tracks": [
            {
                "kind": "video",
                "items": [{"start": 100.0, "end": 102.0, "src": "main"}, {"start": 105.0, "end": 106.5, "src": "main"}],
            },
            {"kind": "text-overlay", "items": [{"start": 0.0, "end": 1.0, "text": "hook", "x": 0.5, "y": 0.1}]},
            {"kind": "audio", "items": [{"start": 0.0, "end": 1.0, "src": "old.wav", "gain": 0.5}]},
        ],
    }
    client, clip_id, _ = await make_client(str(clip_file), timeline=saved)

    r = await client.post(f"/api/clips/{clip_id}/voiceover", json={"text": "Second take", "start": 3.0})

    assert r.status_code == 200, r.text
    body = r.json()
    # Reel is 3.5 s long: a 1 s voice-over at 3.0 is clamped to 3.5.
    assert body["item"] == {"start": 3.0, "end": 3.5, "src": "voiceover_1.wav", "gain": 1.0}
    assert body["edit"]["version"] == 4
    timeline = body["edit"]["timeline"]
    assert timeline["caption_style"] == "hormozi"
    audio = next(t for t in timeline["tracks"] if t["kind"] == "audio")
    assert audio["items"] == [saved["tracks"][2]["items"][0], body["item"]]
    overlay = next(t for t in timeline["tracks"] if t["kind"] == "text-overlay")
    assert overlay["items"] == saved["tracks"][1]["items"]


async def test_second_voiceover_gets_a_new_server_chosen_name(make_client, clip_file):
    client, clip_id, _ = await make_client(str(clip_file))
    first = await client.post(f"/api/clips/{clip_id}/voiceover", json={"text": "One"})
    second = await client.post(f"/api/clips/{clip_id}/voiceover", json={"text": "Two"})
    assert first.json()["item"]["src"] == "voiceover_1.wav"
    assert second.json()["item"]["src"] == "voiceover_2.wav"
    assert len(next(t for t in second.json()["edit"]["timeline"]["tracks"] if t["kind"] == "audio")["items"]) == 2


async def test_client_cannot_choose_the_output_path(make_client, clip_file, tmp_path):
    client, clip_id, _ = await make_client(str(clip_file))
    r = await client.post(
        f"/api/clips/{clip_id}/voiceover",
        json={"text": "hi", "src": "../../evil.wav", "out_path": str(tmp_path / "evil.wav")},
    )
    assert r.status_code == 200
    assert r.json()["item"]["src"] == "voiceover_1.wav"
    assert not (tmp_path / "evil.wav").exists()


# ── Refusals ─────────────────────────────────────────────────────────────────


async def test_a_v1_edit_is_refused_and_left_alone(make_client, clip_file):
    v1 = {"tracks": [{"kind": "video", "items": [{"start": 0.0, "end": 10.0, "src": "main", "trim_start": 0.0}]}]}
    client, clip_id, _ = await make_client(str(clip_file), timeline=v1)

    r = await client.post(f"/api/clips/{clip_id}/voiceover", json={"text": "hi"})

    assert r.status_code == 409
    assert "v1" in r.json()["detail"]
    assert _wavs(clip_file.parent) == []
    stored = await _stored(make_client, clip_id)
    assert stored.timeline == v1 and stored.version == 3


async def test_unknown_clip_is_404(make_client, clip_file):
    client, _, _ = await make_client(str(clip_file))
    r = await client.post("/api/clips/nope/voiceover", json={"text": "hi"})
    assert r.status_code == 404


async def test_missing_clip_file_is_404(make_client, downloads):
    client, clip_id, _ = await make_client(str(downloads / "Talk-1234" / "clips" / "gone.mp4"))
    r = await client.post(f"/api/clips/{clip_id}/voiceover", json={"text": "hi"})
    assert r.status_code == 404
    assert "file" in r.json()["detail"]


@pytest.mark.parametrize("hook", [None, "   "])
async def test_no_text_and_no_hook_is_404(make_client, clip_file, hook):
    client, clip_id, _ = await make_client(str(clip_file), hook=hook)
    r = await client.post(f"/api/clips/{clip_id}/voiceover", json={})
    assert r.status_code == 404
    assert "hook" in r.json()["detail"]


@pytest.mark.parametrize(
    "payload",
    [
        {"text": ""},
        {"text": "\x00\x01\n"},
        {"text": "a" * 1001},
        {"text": "a" * 5000},
        {"text": "hi", "voice": "../../etc/passwd"},
        {"text": "hi", "voice": "a/b"},
        {"text": "hi", "start": -1},
        {"text": "hi", "start": 10.0},
    ],
)
async def test_bad_input_is_422_and_writes_nothing(make_client, clip_file, payload):
    client, clip_id, _ = await make_client(str(clip_file))
    r = await client.post(f"/api/clips/{clip_id}/voiceover", json=payload)
    assert r.status_code == 422, r.text
    assert _wavs(clip_file.parent) == []
    assert await _stored(make_client, clip_id) is None



async def test_start_past_the_reel_end_is_refused_before_synthesis(make_client, clip_file, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(voiceover_service, "synthesize_voiceover", lambda *a, **k: calls.append("synth") or 1.0)
    client, clip_id, _ = await make_client(str(clip_file))
    r = await client.post(f"/api/clips/{clip_id}/voiceover", json={"text": "hi", "start": 10.0})
    assert r.status_code == 422
    assert "reel's end" in r.json()["detail"]
    assert calls == []

@pytest.mark.parametrize("state", ["deleting", "active"])
async def test_busy_job_is_409(make_client, clip_file, monkeypatch, state):
    client, clip_id, job_id = await make_client(str(clip_file))
    if state == "deleting":
        assert orchestrator.claim_delete(job_id)
    else:
        orchestrator.claim_work(job_id)
    try:
        r = await client.post(f"/api/clips/{clip_id}/voiceover", json={"text": "hi"})
    finally:
        orchestrator.release_delete(job_id)
        orchestrator.release_work(job_id)
    assert r.status_code == 409
    assert _wavs(clip_file.parent) == []
    assert not orchestrator.job_active(job_id)


async def test_synthesis_is_counted_as_work_on_the_job(make_client, clip_file, monkeypatch):
    client, clip_id, job_id = await make_client(str(clip_file))
    seen: list[bool] = []
    real = voiceover_service.synthesize_voiceover

    def spy(*a, **kw):
        seen.append(orchestrator.job_has_work(job_id))
        return real(*a, **kw)

    monkeypatch.setattr(voiceover_service, "synthesize_voiceover", spy)
    r = await client.post(f"/api/clips/{clip_id}/voiceover", json={"text": "hi"})
    assert r.status_code == 200
    assert seen == [True]
    assert not orchestrator.job_has_work(job_id)


async def test_export_copy_is_409(make_client, tmp_path, downloads):
    share = tmp_path / "share"
    share.mkdir()
    copy = share / "00_Talk.mp4"
    copy.write_bytes(b"reel")
    client, clip_id, _ = await make_client(str(copy))
    r = await client.post(f"/api/clips/{clip_id}/voiceover", json={"text": "hi"})
    assert r.status_code == 409
    assert _wavs(share) == []


async def test_engine_failure_is_502_and_leaves_no_wav(make_client, clip_file, monkeypatch):
    def broken(text, voice, out_path, **kw):
        Path(out_path).write_bytes(b"half")
        raise voiceover_service.VoiceoverError("piper failed: rc=1")

    monkeypatch.setattr(voiceover_service, "synthesize_voiceover", broken)
    client, clip_id, job_id = await make_client(str(clip_file))
    r = await client.post(f"/api/clips/{clip_id}/voiceover", json={"text": "hi"})
    assert r.status_code == 502
    assert _wavs(clip_file.parent) == []
    assert await _stored(make_client, clip_id) is None
    assert not orchestrator.job_has_work(job_id)


async def test_provider_off_is_503(make_client, clip_file, monkeypatch):
    monkeypatch.setattr(settings, "voiceover_provider", "off")
    client, clip_id, _ = await make_client(str(clip_file))
    r = await client.post(f"/api/clips/{clip_id}/voiceover", json={"text": "hi"})
    assert r.status_code == 503
    assert _wavs(clip_file.parent) == []


async def test_auto_without_piper_uses_the_stub(make_client, clip_file, monkeypatch):
    monkeypatch.setattr(settings, "voiceover_provider", "auto")
    monkeypatch.setattr(voiceover_service, "piper_command", lambda: None)
    client, clip_id, _ = await make_client(str(clip_file))
    r = await client.post(f"/api/clips/{clip_id}/voiceover", json={"text": "hi"})
    assert r.status_code == 200
    assert r.json()["provider"] == "stub"


# ── AI hook route: transcript regression ─────────────────────────────────────


@pytest.mark.parametrize(
    "transcript",
    [
        "the pipeline stores plain text",
        {"text": "the pipeline stores plain text"},
        {"segments": [{"text": "the pipeline"}, {"text": "stores plain text"}]},
        [{"text": "the pipeline"}, {"text": "stores plain text"}],
    ],
)
async def test_ai_hook_reads_the_clip_transcript_in_every_shape(make_client, clip_file, monkeypatch, transcript):
    from app.routers import ai_hook as ai_hook_router

    seen: list[str] = []

    def fake_hook(text, **kwargs):
        seen.append(text)
        return "Stored hook."

    monkeypatch.setattr(ai_hook_router, "generate_hook", fake_hook)
    client, clip_id, _ = await make_client(str(clip_file), hook=None, transcript=transcript, summary="summary fallback")

    r = await client.post(f"/api/clips/{clip_id}/ai-hook")

    assert r.status_code == 200
    assert seen == ["the pipeline stores plain text"]
    async with make_client.factory() as session:
        clip = await session.get(ClipRecord, clip_id)
        assert clip.ai_hook_text == "Stored hook."


async def test_generated_hook_becomes_the_default_voiceover_script(make_client, clip_file, monkeypatch):
    from app.routers import ai_hook as ai_hook_router

    monkeypatch.setattr(ai_hook_router, "generate_hook", lambda text, **kw: "Fresh hook.")
    spoken: list[str] = []
    real = voiceover_service.synthesize_voiceover

    def spy(text, voice, out_path, **kw):
        spoken.append(text)
        return real(text, voice, out_path, **kw)

    monkeypatch.setattr(voiceover_service, "synthesize_voiceover", spy)
    client, clip_id, _ = await make_client(str(clip_file), hook=None, transcript="some words")

    assert (await client.post(f"/api/clips/{clip_id}/ai-hook")).status_code == 200
    assert (await client.post(f"/api/clips/{clip_id}/voiceover")).status_code == 200
    assert spoken == ["Fresh hook."]
