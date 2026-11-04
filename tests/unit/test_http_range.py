"""``range_response`` (spec 017, T139): the one place the app answers a
``Range`` request for a file: 200 whole, 206 one range, 416 unsatisfiable."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.routers.media import range_response

SIZE = 1000
DATA = bytes(i % 251 for i in range(SIZE))


@pytest.fixture
def client(tmp_path: Path):
    path = tmp_path / "file.bin"
    path.write_bytes(DATA)
    app = FastAPI()

    @app.get("/f")
    def f(request: Request):
        return range_response(
            path,
            request.headers.get("range"),
            media_type="application/x-test",
            disposition="inline",
        )

    return TestClient(app)


def test_no_range_is_200_with_the_whole_file(client):
    r = client.get("/f")
    assert r.status_code == 200
    assert r.content == DATA
    assert r.headers["accept-ranges"] == "bytes"
    assert r.headers["content-length"] == str(SIZE)
    assert r.headers["content-type"] == "application/x-test"
    assert r.headers["content-disposition"] == 'inline; filename="file.bin"'


def test_closed_range_is_206(client):
    r = client.get("/f", headers={"Range": "bytes=0-99"})
    assert r.status_code == 206
    assert r.content == DATA[:100]
    assert r.headers["content-range"] == f"bytes 0-99/{SIZE}"
    assert r.headers["content-length"] == "100"
    assert r.headers["accept-ranges"] == "bytes"


def test_open_ended_range_runs_to_the_end(client):
    r = client.get("/f", headers={"Range": f"bytes={SIZE - 10}-"})
    assert r.status_code == 206
    assert r.content == DATA[-10:]
    assert r.headers["content-range"] == f"bytes {SIZE - 10}-{SIZE - 1}/{SIZE}"


def test_end_past_the_file_is_clamped(client):
    r = client.get("/f", headers={"Range": f"bytes=990-{SIZE * 5}"})
    assert r.status_code == 206
    assert r.content == DATA[990:]
    assert r.headers["content-range"] == f"bytes 990-{SIZE - 1}/{SIZE}"
    assert r.headers["content-length"] == "10"


def test_range_spanning_several_chunks(client):
    r = client.get("/f", headers={"Range": "bytes=3-999"})
    assert r.content == DATA[3:]


@pytest.mark.parametrize("header", [f"bytes={SIZE}-", f"bytes={SIZE + 5}-", "bytes=50-10"])
def test_unsatisfiable_range_is_416(client, header):
    r = client.get("/f", headers={"Range": header})
    assert r.status_code == 416
    assert r.headers["content-range"] == f"bytes */{SIZE}"
    assert r.json() == {"detail": "range not satisfiable"}


@pytest.mark.parametrize(
    "header", ["bytes=-500", "bytes=0-9,20-29", "items=0-9", "garbage", "bytes=a-b"]
)
def test_a_range_that_does_not_match_is_ignored(client, header):
    r = client.get("/f", headers={"Range": header})
    assert r.status_code == 200
    assert r.content == DATA


# ── Content-Disposition and file handles (review findings on PR #97) ─────────


def _serve(tmp_path: Path, name: str, **kw):
    path = tmp_path / name
    path.write_bytes(DATA)
    app = FastAPI()

    @app.get("/f")
    def f(request: Request):
        return range_response(
            path,
            request.headers.get("range"),
            media_type="application/x-test",
            disposition="attachment",
            **kw,
        )

    return TestClient(app)


@pytest.mark.parametrize("name", ["01_日本の旅.mp4", "a｜b：c.mp4", "clip 🎬.mp4"])
def test_non_latin1_name_is_200_with_rfc5987_filename(tmp_path, name):
    from urllib.parse import quote

    r = _serve(tmp_path, name).get("/f")
    assert r.status_code == 200
    assert r.content == DATA
    cd = r.headers["content-disposition"]
    assert cd.startswith("attachment; filename=")
    assert f"filename*=utf-8''{quote(name, safe='')}" in cd
    cd.encode("ascii")  # fully ASCII on the wire


@pytest.mark.parametrize("name", ['a"b.mp4', "a\\b.mp4"])
def test_quote_and_backslash_in_name_cannot_break_the_header(tmp_path, name):
    from app.routers.media import content_disposition

    cd = content_disposition("inline", name)
    fallback = cd.split("filename=", 1)[1].split(";", 1)[0]
    assert fallback.startswith('"') and fallback.endswith('"')
    inner = fallback[1:-1]
    assert '"' not in inner and "\\" not in inner


def test_crlf_in_name_cannot_inject_a_header():
    from app.routers.media import content_disposition

    cd = content_disposition("inline", "a\r\nX-Evil: 1.mp4")
    assert "\r" not in cd and "\n" not in cd
    assert "%0D%0A" in cd


def _open_fds() -> int:
    return len(os.listdir("/dev/fd"))


def test_file_handle_is_closed_when_the_client_aborts_mid_stream(tmp_path):
    import asyncio
    import gc

    path = tmp_path / "big.bin"
    path.write_bytes(b"x" * (65536 * 8))
    gc.collect()
    before = _open_fds()

    held = []  # keep the response alive: closing must not depend on refcount/GC

    async def run() -> None:
        resp = range_response(path, None, media_type="video/mp4", disposition="inline")
        held.append(resp)
        sent = 0

        async def send(message):
            nonlocal sent
            if message["type"] == "http.response.body":
                sent += 1
                if sent == 2:
                    raise OSError("client gone")

        async def receive():
            await asyncio.sleep(3600)

        scope = {"type": "http", "method": "GET", "asgi": {"spec_version": "2.4"}}
        with pytest.raises(Exception):  # noqa: B017, PT011
            await resp(scope, receive, send)

    asyncio.run(run())
    assert _open_fds() == before  # no gc.collect(): closed deterministically


def test_unreadable_response_closes_handle_for_416(tmp_path):
    path = tmp_path / "f.bin"
    path.write_bytes(DATA)
    before = _open_fds()
    opened = []
    real = os.fdopen
    os.fdopen = lambda *a, **k: opened.append(real(*a, **k)) or opened[-1]  # type: ignore[assignment]
    try:
        r = range_response(path, "bytes=5000-", media_type="x/y", disposition="inline")
    finally:
        os.fdopen = real
    assert r.status_code == 416
    assert len(opened) == 1 and opened[0].closed
    assert _open_fds() == before


def test_file_vanishing_before_open_is_404_with_the_given_detail(tmp_path):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as e:
        range_response(
            tmp_path / "gone.bin",
            None,
            media_type="x/y",
            disposition="inline",
            missing_detail="source video not retained",
        )
    assert e.value.status_code == 404
    assert e.value.detail == "source video not retained"
