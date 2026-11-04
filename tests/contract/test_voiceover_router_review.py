"""``POST /clips/{id}/voiceover`` review fixes (spec 016 Phase 4a).

No lost update (the edit is re-checked after synthesis, 409 when it moved),
no database transaction held while the engine runs, at most two syntheses at
once (503 "voice-over busy"), a documented ``provider`` field, and the
refusal paths that must leave no wav and the stored edit unchanged.
Hermetic: in-memory SQLite, tmp folders, fake engines.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path

import pytest
from sqlalchemy import update

from app.db.models import ClipEdit, ClipRecord
from app.db.session import get_session
from app.routers import voiceover as voiceover_router
from app.services import voiceover_service
from app.settings import settings

# Shared fixtures of the voice-over contract suite.
from tests.contract.test_voiceover_router import (  # noqa: F401
    _stored,
    _wavs,
    clip_file,
    downloads,
    make_client,
)

V2 = {
    "schema_version": 2,
    "tracks": [
        {"kind": "video", "items": [{"start": 100.0, "end": 110.0, "src": "main"}]},
        {"kind": "audio", "items": []},
    ],
}


_REAL_SYNTHESIZE = voiceover_service.synthesize_voiceover


def _stub(text, voice, out_path, **kw):
    return _REAL_SYNTHESIZE(text, voice, out_path, provider="stub")


def _in_loop(loop, coro):
    """Run ``coro`` on the request's event loop from the synthesis thread."""
    return asyncio.run_coroutine_threadsafe(coro, loop).result(timeout=10)


# ── 1. Lost update ───────────────────────────────────────────────────────────


async def test_a_save_during_synthesis_wins_and_the_voiceover_is_409(make_client, clip_file, monkeypatch):
    client, clip_id, _ = await make_client(str(clip_file), timeline=V2)
    loop = asyncio.get_running_loop()
    saved_meanwhile = {**V2, "caption_style": "hormozi"}
    puts = []

    def synth_with_a_concurrent_save(text, voice, out_path, **kw):
        puts.append(
            _in_loop(loop, client.put(f"/api/clips/{clip_id}/edit", json={"timeline": saved_meanwhile, "version": 3}))
        )
        return _stub(text, voice, out_path)

    monkeypatch.setattr(voiceover_service, "synthesize_voiceover", synth_with_a_concurrent_save)

    r = await client.post(f"/api/clips/{clip_id}/voiceover", json={"text": "hi"})

    assert [p.status_code for p in puts] == [200]
    assert r.status_code == 409, r.text
    assert "changed" in r.json()["detail"]
    assert _wavs(clip_file.parent) == []
    stored = await _stored(make_client, clip_id)
    assert stored.version == 4
    assert stored.timeline == puts[0].json()["timeline"]
    assert stored.timeline["caption_style"] == "hormozi"


async def test_a_first_edit_created_during_synthesis_wins_and_the_voiceover_is_409(make_client, clip_file, monkeypatch):
    client, clip_id, _ = await make_client(str(clip_file))
    loop = asyncio.get_running_loop()
    puts = []

    def synth_with_a_first_save(text, voice, out_path, **kw):
        puts.append(_in_loop(loop, client.put(f"/api/clips/{clip_id}/edit", json={"timeline": V2})))
        return _stub(text, voice, out_path)

    monkeypatch.setattr(voiceover_service, "synthesize_voiceover", synth_with_a_first_save)

    r = await client.post(f"/api/clips/{clip_id}/voiceover", json={"text": "hi"})

    assert [p.status_code for p in puts] == [200]
    assert r.status_code == 409, r.text
    assert _wavs(clip_file.parent) == []
    stored = await _stored(make_client, clip_id)
    assert stored.version == 1
    assert stored.timeline == V2


# ── 2. No transaction across synthesis; two at once ──────────────────────────


async def test_no_transaction_is_open_while_the_engine_runs(make_client, clip_file, monkeypatch):
    client, clip_id, _ = await make_client(str(clip_file), timeline=V2)
    app_inst = client._transport.app  # type: ignore[attr-defined]
    original = app_inst.dependency_overrides[get_session]
    sessions = []

    async def capturing():
        async for session in original():
            sessions.append(session)
            yield session

    app_inst.dependency_overrides[get_session] = capturing
    seen = []

    def synth(text, voice, out_path, **kw):
        seen.append(sessions[0].in_transaction())
        return _stub(text, voice, out_path)

    monkeypatch.setattr(voiceover_service, "synthesize_voiceover", synth)

    r = await client.post(f"/api/clips/{clip_id}/voiceover", json={"text": "hi"})

    assert r.status_code == 200, r.text
    assert seen == [False]
    assert r.json()["edit"]["version"] == 4


async def test_a_third_concurrent_voiceover_is_503_busy(make_client, clip_file, monkeypatch):
    clients = [await make_client(str(clip_file)) for _ in range(3)]
    entered = threading.Semaphore(0)
    release = threading.Event()

    def slow(text, voice, out_path, **kw):
        entered.release()
        release.wait(10)
        return _stub(text, voice, out_path)

    monkeypatch.setattr(voiceover_service, "synthesize_voiceover", slow)
    running = [
        asyncio.create_task(c.post(f"/api/clips/{cid}/voiceover", json={"text": "hi"})) for c, cid, _ in clients[:2]
    ]
    try:
        for _ in range(2):
            assert await asyncio.to_thread(entered.acquire, True, 10)
        c3, cid3, _ = clients[2]
        busy = await c3.post(f"/api/clips/{cid3}/voiceover", json={"text": "hi"})
    finally:
        release.set()
    done = await asyncio.gather(*running)

    assert busy.status_code == 503
    assert "voice-over busy" in busy.json()["detail"]
    assert await _stored(make_client, cid3) is None
    assert [d.status_code for d in done] == [200, 200]
    assert len(_wavs(clip_file.parent)) == 2
    # Free again once they are done.
    again = await c3.post(f"/api/clips/{cid3}/voiceover", json={"text": "hi"})
    assert again.status_code == 200


# ── 8. provider ──────────────────────────────────────────────────────────────


async def test_provider_is_documented_in_the_openapi_model(make_client, clip_file):
    client, _, _ = await make_client(str(clip_file))
    spec = (await client.get("/openapi.json")).json()
    post = spec["paths"]["/clips/{clip_id}/voiceover"]["post"]
    ref = post["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    model = spec["components"]["schemas"][ref.rsplit("/", 1)[-1]]
    assert model["properties"]["provider"]["enum"] == ["piper", "stub"]
    assert "warn" in model["properties"]["provider"]["description"]
    assert set(model["required"]) >= {"edit", "item", "provider"}


@pytest.mark.parametrize("cause", ["no-piper", "no-voice"])
def test_auto_falling_back_to_the_stub_logs_one_warning(tmp_path, monkeypatch, caplog, cause):
    models = tmp_path / "models"
    models.mkdir()
    if cause == "no-piper":
        monkeypatch.setattr(voiceover_service, "piper_command", lambda: None)
    else:
        monkeypatch.setattr(voiceover_service, "piper_command", lambda: ["piper"])
    with caplog.at_level(logging.INFO, logger=voiceover_service.log.name):
        assert voiceover_service.resolve_provider("auto", "en_US-x", model_dir=models) == "stub"
    warnings = [r for r in caplog.records if r.name == voiceover_service.log.name and r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "auto -> stub" in warnings[0].getMessage()


# ── 10. Refusals that leave nothing behind ───────────────────────────────────


async def test_a_timeline_that_fails_validation_after_synthesis_is_422_and_keeps_the_edit(make_client, clip_file):
    broken = {
        "schema_version": 2,
        "tracks": [
            {"kind": "video", "items": [{"start": 100.0, "end": 110.0, "src": "main"}]},
            # Its wav was deleted since: the timeline no longer validates.
            {"kind": "audio", "items": [{"start": 0.0, "end": 1.0, "src": "gone.wav", "gain": 1.0}]},
        ],
    }
    client, clip_id, _ = await make_client(str(clip_file), timeline=broken)

    r = await client.post(f"/api/clips/{clip_id}/voiceover", json={"text": "hi"})

    assert r.status_code == 422, r.text
    assert "gone.wav" in r.json()["detail"]
    assert _wavs(clip_file.parent) == []
    stored = await _stored(make_client, clip_id)
    assert (stored.timeline, stored.version) == (broken, 3)


async def test_a_start_that_rounds_onto_the_reel_end_does_not_fit(make_client, clip_file):
    client, clip_id, _ = await make_client(str(clip_file))

    r = await client.post(f"/api/clips/{clip_id}/voiceover", json={"text": "hi", "start": 9.9996})

    assert r.status_code == 422, r.text
    assert "does not fit" in r.json()["detail"]
    assert _wavs(clip_file.parent) == []
    assert await _stored(make_client, clip_id) is None


async def test_engine_input_error_is_422_and_discards_the_wav(make_client, clip_file, monkeypatch):
    def refuses(text, voice, out_path, **kw):
        Path(out_path).write_bytes(b"partial")
        raise voiceover_service.VoiceoverInputError("text is empty")

    monkeypatch.setattr(voiceover_service, "synthesize_voiceover", refuses)
    client, clip_id, _ = await make_client(str(clip_file))

    r = await client.post(f"/api/clips/{clip_id}/voiceover", json={"text": "hi"})

    assert r.status_code == 422
    assert _wavs(clip_file.parent) == []
    assert await _stored(make_client, clip_id) is None


@pytest.mark.parametrize("where", ["provider-setting", "voice-path"])
async def test_a_server_side_voiceover_misconfiguration_is_500(make_client, clip_file, monkeypatch, where):
    if where == "provider-setting":
        monkeypatch.setattr(settings, "voiceover_provider", "bogus")
    else:

        def broken(*a, **k):
            raise voiceover_service.VoiceoverError("voice folder unreadable")

        monkeypatch.setattr(voiceover_service, "voice_model_path", broken)
    client, clip_id, _ = await make_client(str(clip_file))

    r = await client.post(f"/api/clips/{clip_id}/voiceover", json={"text": "hi"})

    assert r.status_code == 500
    assert _wavs(clip_file.parent) == []
    assert await _stored(make_client, clip_id) is None


@pytest.mark.parametrize("window", [(100.0, 100.0), (0.0, 0.0), (110.0, 100.0)])
async def test_unknown_source_window_without_an_edit_is_409(make_client, clip_file, window):
    client, clip_id, _ = await make_client(str(clip_file))
    async with make_client.factory() as session:
        await session.execute(update(ClipRecord).where(ClipRecord.id == clip_id).values(start=window[0], end=window[1]))
        await session.commit()

    r = await client.post(f"/api/clips/{clip_id}/voiceover", json={"text": "hi"})

    assert r.status_code == 409
    assert "source window is unknown" in r.json()["detail"]
    assert _wavs(clip_file.parent) == []
    assert await _stored(make_client, clip_id) is None


def test_the_router_exports_its_concurrency_limit():
    assert voiceover_router.MAX_CONCURRENT_SYNTHESES == 2
    assert isinstance(voiceover_router._synth_slots, asyncio.Semaphore)


_ = ClipEdit  # imported for the model registry
