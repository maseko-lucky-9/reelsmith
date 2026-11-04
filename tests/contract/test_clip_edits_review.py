"""``GET/PUT /api/clips/{clip_id}/edit`` review fixes (spec 016 Phase 4a).

* An edited reel's recorded length (``clip_words.write_reel_duration``) is
  the default timeline's length, not the clip's ``end - start``.
* No transaction is held while the first open transcribes the clip.
* Voice-over items are refused (409) beside an export copy, as
  ``POST /voiceover`` refuses them.
* Cached words are read in time order and clipped to the reel.

Hermetic: in-memory SQLite and a fake transcriber.
"""

from __future__ import annotations

import json

from app.db.session import get_session
from app.services import clip_words, transcription_service
from app.services.transcription_service import WordTiming

# Shared fixtures of the words contract suite.
from tests.contract.test_clip_edits_words import (  # noqa: F401
    CLIP_WORDS,
    _clip_file,
    downloads,
    make_client,
    transcriber,
)


def _video_items(body):
    (track,) = [t for t in body["timeline"]["tracks"] if t["kind"] == "video"]
    return track["items"]


async def test_the_default_timeline_uses_the_recorded_reel_length(downloads, make_client, transcriber):
    clip = _clip_file(downloads / "Talk-1234" / "clips")
    clip_words.write_clip_words(str(clip), [WordTiming(**w) for w in CLIP_WORDS])
    clip_words.write_reel_duration(str(clip), 7.25)
    transcriber(RuntimeError("must not transcribe"))
    client, clip_id, _ = await make_client(str(clip))

    r = await client.get(f"/api/clips/{clip_id}/edit")

    assert r.status_code == 200
    assert _video_items(r.json()) == [{"start": 0.0, "end": 7.25, "src": "main", "trim_start": 0.0}]


async def test_without_a_recorded_length_the_window_holds(downloads, make_client, transcriber):
    clip = _clip_file(downloads / "Talk-1234" / "clips")
    clip_words.write_clip_words(str(clip), [WordTiming(**w) for w in CLIP_WORDS])
    transcriber(RuntimeError("must not transcribe"))
    client, clip_id, _ = await make_client(str(clip))

    r = await client.get(f"/api/clips/{clip_id}/edit")

    assert _video_items(r.json())[0]["end"] == 10.0


async def test_the_backfill_is_bounded_by_the_recorded_reel_length(downloads, make_client, transcriber):
    clip = _clip_file(downloads / "Talk-1234" / "clips")
    clip_words.write_reel_duration(str(clip), 12.5)
    fake = transcriber([WordTiming("late", 11.0, 11.5)])
    client, clip_id, _ = await make_client(str(clip))

    r = await client.get(f"/api/clips/{clip_id}/edit")

    assert fake.calls[0]["duration"] == 12.5
    # Kept: 11 s is inside the 12.5 s edited reel, past the 10 s window.
    assert [w["word"] for w in r.json()["words"]] == ["late"]


async def test_no_transaction_is_open_while_the_clip_is_transcribed(downloads, make_client, monkeypatch):
    clip = _clip_file(downloads / "Talk-1234" / "clips")
    client, clip_id, _ = await make_client(str(clip))
    app_inst = client._transport.app  # type: ignore[attr-defined]
    original = app_inst.dependency_overrides[get_session]
    sessions = []

    async def capturing():
        async for session in original():
            sessions.append(session)
            yield session

    app_inst.dependency_overrides[get_session] = capturing
    seen = []

    async def fake(audio_path, *, language, audio_duration_s, repeatable=False):
        seen.append(sessions[0].in_transaction())
        return [WordTiming("hi", 0.1, 0.4)]

    monkeypatch.setattr(transcription_service, "transcribe_words_async", fake)

    r = await client.get(f"/api/clips/{clip_id}/edit")

    assert r.status_code == 200
    assert seen == [False]
    assert [w["word"] for w in r.json()["words"]] == ["hi"]


_V2_WITH_AUDIO = {
    "schema_version": 2,
    "tracks": [
        {"kind": "video", "items": [{"start": 100.0, "end": 110.0}]},
        {"kind": "audio", "items": [{"start": 0.0, "end": 1.0, "src": "vo.wav", "gain": 1.0}]},
    ],
}


async def test_voiceover_items_beside_an_export_copy_are_409(tmp_path, downloads, make_client):
    share = tmp_path / "share"
    share.mkdir()
    copy = _clip_file(share)
    (share / "vo.wav").write_bytes(b"RIFF")
    client, clip_id, _ = await make_client(str(copy))

    r = await client.put(f"/api/clips/{clip_id}/edit", json={"timeline": _V2_WITH_AUDIO})

    assert r.status_code == 409
    assert "export copy" in r.json()["detail"]
    assert (await client.get(f"/api/clips/{clip_id}/edit")).json()["version"] == 0


async def test_an_export_copy_edit_without_voiceover_still_saves(tmp_path, downloads, make_client):
    share = tmp_path / "share"
    share.mkdir()
    copy = _clip_file(share)
    client, clip_id, _ = await make_client(str(copy))
    timeline = {"schema_version": 2, "tracks": [{"kind": "video", "items": [{"start": 100.0, "end": 110.0}]}]}

    r = await client.put(f"/api/clips/{clip_id}/edit", json={"timeline": timeline})

    assert r.status_code == 200, r.text


async def test_voiceover_items_beside_a_managed_clip_save(downloads, make_client):
    folder = downloads / "Talk-1234" / "clips"
    clip = _clip_file(folder)
    (folder / "vo.wav").write_bytes(b"RIFF")
    client, clip_id, _ = await make_client(str(clip))

    r = await client.put(f"/api/clips/{clip_id}/edit", json={"timeline": _V2_WITH_AUDIO})

    assert r.status_code == 200, r.text


async def test_cached_words_are_sorted_and_clipped_to_the_reel(downloads, make_client, transcriber):
    clip = _clip_file(downloads / "Talk-1234" / "clips")
    path = clip_words.write_clip_words(str(clip), [])
    path.write_text(
        json.dumps(
            [
                {"word": "two", "start": 2.0, "end": 2.5},
                {"word": "one", "start": 1.0, "end": 1.5},
                {"word": "past", "start": 30.0, "end": 31.0},
                {"word": "neg", "start": -1.0, "end": 0.5},
            ]
        )
    )
    transcriber(RuntimeError("must not transcribe"))
    client, clip_id, _ = await make_client(str(clip))

    r = await client.get(f"/api/clips/{clip_id}/edit")

    assert [w["word"] for w in r.json()["words"]] == ["one", "two"]
