"""``PUT/GET /api/clips/{clip_id}/edit`` with timeline schema v2 (spec 016, T127).

PUT validates v1 and v2 timelines (422 with the reason) against bounds the
server supplies: the source window (the job's recorded duration, else the
clip's own window) and the clip's folder for voice-over files. v1 timelines
are stored and planned unchanged. Hermetic: in-memory SQLite, tmp folders.
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import ClipEdit, ClipRecord, JobRecord
from app.db.session import get_session
from app.main import create_app


@pytest.fixture
async def make_client(tmp_path):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    clients: list[httpx.AsyncClient] = []
    folder = tmp_path / "Talk-1234" / "clips"
    folder.mkdir(parents=True)
    clip_file = folder / "00_Talk.mp4"
    clip_file.write_bytes(b"reel")
    (folder / "voice.wav").write_bytes(b"RIFF")

    async def _make(
        *,
        job_state: dict[str, Any] | None = None,
        output_path: str | None = str(clip_file),
        timeline: dict[str, Any] | None = None,
    ) -> tuple[httpx.AsyncClient, str]:
        async with factory() as session:
            job = JobRecord(youtube_url="https://example.com/v", state=job_state)
            session.add(job)
            await session.flush()
            clip = ClipRecord(job_id=job.id, start=100.0, end=110.0, output_path=output_path)
            session.add(clip)
            await session.flush()
            if timeline is not None:
                session.add(ClipEdit(clip_id=clip.id, timeline=timeline))
            await session.commit()
            clip_id = clip.id

        async def _override():
            async with factory() as session:
                yield session

        app_inst = create_app()
        app_inst.dependency_overrides[get_session] = _override
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app_inst), base_url="http://test")
        clients.append(client)
        return client, clip_id

    _make.folder = folder  # type: ignore[attr-defined]
    yield _make
    for client in clients:
        await client.aclose()
    await engine.dispose()


V1 = {
    "tracks": [
        {"kind": "video", "items": [{"start": 0.0, "end": 10.0, "src": "main", "trim_start": 0.0}]},
        {
            "kind": "caption",
            "items": [
                {
                    "start": 0.5,
                    "end": 1.3,
                    "text": "we saw",
                    "style": "default",
                    "words": [{"word": "we", "start": 0.5, "end": 0.8}, {"word": "saw", "start": 1.0, "end": 1.3}],
                }
            ],
        },
        {"kind": "text-overlay", "items": [{"start": 1.0, "end": 4.0, "text": "hook", "x": 0.5, "y": 0.1}]},
    ]
}


def _v2(**top: Any) -> dict[str, Any]:
    timeline: dict[str, Any] = {
        "schema_version": 2,
        "caption_style": "hormozi",
        "mute_source": True,
        "tracks": [
            {
                "kind": "video",
                "items": [{"start": 100.0, "end": 104.0, "src": "main"}, {"start": 106.0, "end": 112.0, "src": "main"}],
            },
            {
                "kind": "caption",
                "items": [
                    {
                        "start": 0.5,
                        "end": 1.5,
                        "text": "we saw the sea",
                        "words": [{"word": "we", "start": 0.5, "end": 0.8}, {"word": "saw", "start": 1.0, "end": 1.3}],
                    }
                ],
            },
            {"kind": "text-overlay", "items": [{"start": 1.0, "end": 4.0, "text": "hook", "x": 0.5, "y": 0.1}]},
            {"kind": "audio", "items": [{"start": 0.0, "end": 3.0, "src": "voice.wav", "gain": 1.5}]},
        ],
    }
    timeline.update(top)
    return timeline


def _with_item(kind: str, item: dict[str, Any]) -> dict[str, Any]:
    timeline = _v2()
    (track,) = [t for t in timeline["tracks"] if t["kind"] == kind]
    track["items"] = [item]
    return timeline


async def _put(client: httpx.AsyncClient, clip_id: str, timeline: dict[str, Any]) -> httpx.Response:
    return await client.put(f"/api/clips/{clip_id}/edit", json={"timeline": timeline})


# ── v1 compatibility ─────────────────────────────────────────────────────────


async def test_v1_put_get_round_trip_is_unchanged(make_client):
    client, clip_id = await make_client()

    r = await _put(client, clip_id, V1)

    assert r.status_code == 200
    assert r.json()["timeline"] == V1
    assert (await client.get(f"/api/clips/{clip_id}/edit")).json()["timeline"] == V1


async def test_stored_v1_edit_loads_and_plans_unchanged(make_client):
    client, clip_id = await make_client(timeline=V1)

    got = await client.get(f"/api/clips/{clip_id}/edit")
    plan = await client.get(f"/api/clips/{clip_id}/edit/plan")

    assert got.json()["timeline"] == V1
    assert plan.status_code == 200
    body = plan.json()
    assert body["schema_version"] == 1
    assert body["duration"] == 10.0
    assert body["video"] == [
        {"start": 0.0, "end": 10.0, "src": str(make_client.folder / "00_Talk.mp4"), "trim_start": 0.0}
    ]
    assert body["segments"] == [] and body["audio"] == []
    assert [w["word"] for w in body["captions"][0]["words"]] == ["we", "saw"]


async def test_v1_web_placeholder_video_item_may_be_saved(make_client):
    client, clip_id = await make_client(output_path=None)
    timeline = {
        "tracks": [
            {"kind": "video", "items": [{"start": 0, "end": 0, "src": "main"}]},
            {"kind": "caption", "items": []},
            {"kind": "text-overlay", "items": []},
        ]
    }

    r = await _put(client, clip_id, timeline)

    assert r.status_code == 200
    assert r.json()["timeline"] == timeline


async def test_v1_audio_track_rejected(make_client):
    client, clip_id = await make_client()

    r = await _put(client, clip_id, {"tracks": [{"kind": "audio", "items": []}]})

    assert r.status_code == 422
    assert "unknown track kind" in r.json()["detail"]


# ── v2 round trip and plan ───────────────────────────────────────────────────


async def test_v2_put_get_round_trip_and_plan(make_client):
    client, clip_id = await make_client(job_state={"duration": 600.0})
    timeline = _v2()

    r = await _put(client, clip_id, timeline)

    assert r.status_code == 200, r.text
    assert r.json()["timeline"] == timeline
    assert r.json()["version"] == 1
    got = (await client.get(f"/api/clips/{clip_id}/edit")).json()
    assert got["timeline"] == timeline
    plan = (await client.get(f"/api/clips/{clip_id}/edit/plan")).json()
    assert plan["schema_version"] == 2
    assert plan["segments"] == [{"start": 100.0, "end": 104.0}, {"start": 106.0, "end": 112.0}]
    assert [(v["start"], v["end"], v["trim_start"]) for v in plan["video"]] == [
        (0.0, 4.0, 100.0),
        (4.0, 10.0, 106.0),
    ]
    assert plan["duration"] == 10.0
    assert plan["caption_style"] == "hormozi" and plan["mute_source"] is True
    assert plan["audio"] == [{"start": 0.0, "end": 3.0, "src": "voice.wav", "gain": 1.5}]
    words = plan["captions"][0]["words"]
    assert [w["word"] for w in words] == ["we", "saw", "the", "sea"]
    assert [w["start"] for w in words] == pytest.approx([0.5, 0.75, 1.0, 1.25])


async def test_v2_update_bumps_version(make_client):
    client, clip_id = await make_client(job_state={"duration": 600.0})
    await _put(client, clip_id, _v2())

    r = await client.put(f"/api/clips/{clip_id}/edit", json={"timeline": _v2(mute_source=False), "version": 1})

    assert r.status_code == 200
    assert r.json()["version"] == 2
    assert r.json()["timeline"]["mute_source"] is False


async def test_v2_extend_inside_the_recorded_source_duration(make_client):
    client, clip_id = await make_client(job_state={"duration": 600.0})
    timeline = _v2()
    timeline["tracks"][0]["items"] = [{"start": 95.0, "end": 115.0, "src": "main"}]

    assert (await _put(client, clip_id, timeline)).status_code == 200


async def test_v2_past_the_recorded_source_duration_rejected(make_client):
    client, clip_id = await make_client(job_state={"duration": 120.0})
    timeline = _v2()
    timeline["tracks"][0]["items"] = [{"start": 110.0, "end": 125.0, "src": "main"}]

    r = await _put(client, clip_id, timeline)

    assert r.status_code == 422
    assert "outside the source" in r.json()["detail"]


async def test_v2_without_a_source_duration_is_bounded_by_the_clip_window(make_client):
    client, clip_id = await make_client(job_state=None)
    timeline = _v2()
    timeline["tracks"][0]["items"] = [{"start": 95.0, "end": 105.0, "src": "main"}]

    r = await _put(client, clip_id, timeline)

    assert r.status_code == 422
    assert "[100.000, 110.000]" in r.json()["detail"]


# ── 422s ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("timeline", "message"),
    [
        (_with_item("video", {"start": -1.0, "end": 104.0}), "negative"),
        (_with_item("video", {"start": 104.0, "end": 101.0}), "must be > start"),
        (_with_item("caption", {"start": 3.0, "end": 2.0, "text": "x"}), "must be > start"),
        (_with_item("caption", {"start": 9.0, "end": 11.0, "text": "x"}), "past the edited reel's end"),
        (_with_item("caption", {"start": 0.0, "end": 1.0, "text": "x" * 501}), "longer than"),
        (_with_item("text-overlay", {"start": 0.0, "end": 1.0, "text": "y" * 501}), "longer than"),
        (_with_item("audio", {"start": 0.0, "end": 1.0, "src": "voice.wav", "gain": 5}), "gain"),
        (_with_item("audio", {"start": 0.0, "end": 1.0, "src": "../voice.wav"}), "plain file name"),
        (_with_item("audio", {"start": 0.0, "end": 1.0, "src": "/etc/hosts.wav"}), "plain file name"),
        (_with_item("audio", {"start": 0.0, "end": 1.0, "src": "missing.wav"}), "no such file"),
        (_v2(caption_style="neon"), "caption_style"),
        (
            {**_v2(), "tracks": _v2()["tracks"] + [{"kind": "caption", "items": [{"start": 0, "end": 1}] * 2001}]},
            "too many caption items",
        ),
    ],
)
async def test_put_rejects_bad_v2_input(make_client, timeline, message):
    client, clip_id = await make_client(job_state={"duration": 600.0})

    r = await _put(client, clip_id, timeline)

    assert r.status_code == 422, r.text
    assert message in r.json()["detail"]
    assert (await client.get(f"/api/clips/{clip_id}/edit")).json().get("is_default") is True


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity", "1e400"])
async def test_put_rejects_non_finite_times(make_client, literal):
    client, clip_id = await make_client(job_state={"duration": 600.0})
    raw = json.dumps({"timeline": _v2()}).replace('"end": 104.0', f'"end": {literal}', 1)
    assert literal in raw

    r = await client.put(f"/api/clips/{clip_id}/edit", content=raw, headers={"content-type": "application/json"})

    assert r.status_code == 422


async def test_put_rejects_unknown_schema_version(make_client):
    client, clip_id = await make_client()

    r = await _put(client, clip_id, _v2(schema_version=3))

    assert r.status_code == 422
    # Refused by the request model (a list of field errors), before the plan check.
    assert r.json()["detail"][0]["loc"][-1] == "schema_version"


async def test_put_rejects_non_bool_mute_source(make_client):
    client, clip_id = await make_client(job_state={"duration": 600.0})

    r = await _put(client, clip_id, _v2(mute_source="yes"))

    assert r.status_code == 422


async def test_put_rejects_audio_symlink_escaping_the_clip_folder(make_client, tmp_path):
    client, clip_id = await make_client(job_state={"duration": 600.0})
    outside = tmp_path / "secret.wav"
    outside.write_bytes(b"RIFF")
    os.symlink(outside, make_client.folder / "escape.wav")

    r = await _put(client, clip_id, _with_item("audio", {"start": 0.0, "end": 1.0, "src": "escape.wav"}))

    assert r.status_code == 422
    assert "outside the clip's folder" in r.json()["detail"]


async def test_put_rejects_audio_for_a_clip_without_a_file(make_client):
    client, clip_id = await make_client(job_state={"duration": 600.0}, output_path=None)

    r = await _put(client, clip_id, _v2())

    assert r.status_code == 422
    assert "no folder" in r.json()["detail"]
