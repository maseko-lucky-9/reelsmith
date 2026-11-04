"""``range_response`` (spec 017, T139): the one place the app answers a
``Range`` request for a file: 200 whole, 206 one range, 416 unsatisfiable."""

from __future__ import annotations

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
