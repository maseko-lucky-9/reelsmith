"""Overall download percent from yt-dlp progress hooks (T052).

yt-dlp downloads ``bestvideo+bestaudio`` as two files one after the other and
reports each on its own, so ``DownloadTracker`` combines them, weighted by
the requested formats' sizes when every size is known.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.services.platforms import _yt_dlp_base
from app.services.platforms._yt_dlp_base import DownloadTracker, download_progress


def _tracker() -> tuple[DownloadTracker, list[int]]:
    calls: list[int] = []
    return DownloadTracker(calls.append), calls


def _downloading(filename: str, done: int, total: int | None, **extra: Any) -> dict:
    return {
        "status": "downloading",
        "filename": filename,
        "downloaded_bytes": done,
        "total_bytes": total,
        **extra,
    }


def _finished(filename: str, size: int) -> dict:
    return {
        "status": "finished",
        "filename": filename,
        "downloaded_bytes": size,
        "total_bytes": size,
    }


def test_parts_with_known_sizes_are_weighted_by_size():
    tracker, calls = _tracker()
    tracker.set_part_sizes([900, 100])

    tracker.progress_hook(_finished("v.f137.mp4", 900))
    assert calls[-1] == 90
    tracker.progress_hook(_downloading("a.f140.m4a", 50, 100))
    assert calls[-1] == 95
    tracker.progress_hook(_finished("a.f140.m4a", 100))
    assert calls[-1] == 100


def test_parts_are_weighted_equally_when_a_size_is_unknown():
    tracker, calls = _tracker()
    tracker.set_part_sizes([900, None])

    tracker.progress_hook(_finished("v.f137.mp4", 900))
    assert calls[-1] == 50
    tracker.progress_hook(_downloading("a.f140.m4a", 50, None, total_bytes_estimate=100))
    assert calls[-1] == 75


def test_single_file_without_recorded_sizes_is_one_part():
    tracker, calls = _tracker()

    tracker.progress_hook(_downloading("v.mp4", 42, 100))

    assert calls == [42]


def test_percent_never_decreases():
    tracker, calls = _tracker()

    tracker.progress_hook(_downloading("v.mp4", 60, 100))
    # A retry restarts the part from zero.
    tracker.progress_hook(_downloading("v.mp4", 10, 100))
    tracker.progress_hook(_downloading("v.mp4", 70, 100))

    assert calls == [60, 70]


def test_callback_fires_only_when_the_integer_percent_rises():
    tracker, calls = _tracker()

    for done in (102, 107, 110):  # 10.2 %, 10.7 %, 11.0 %
        tracker.progress_hook(_downloading("v.mp4", done, 1000))

    assert calls == [10, 11]


def test_percent_is_clamped_at_100():
    tracker, calls = _tracker()

    tracker.progress_hook(_downloading("v.mp4", 150, 100))

    assert calls == [100]


def test_a_raising_callback_does_not_propagate():
    def _boom(percent: int) -> None:
        raise RuntimeError("loop is closed")

    tracker = DownloadTracker(_boom)

    tracker.progress_hook(_downloading("v.mp4", 50, 100))


def test_finish_completes_a_download_whose_parts_were_never_all_reported():
    tracker, calls = _tracker()
    tracker.set_part_sizes([900, 100])
    tracker.progress_hook(_finished("v.mp4", 900))
    assert calls == [90]

    tracker.finish()
    tracker.finish()

    assert calls == [90, 100]
    assert tracker.percent == 100


def test_a_raising_callback_does_not_propagate_from_finish():
    def boom(_: int) -> None:
        raise RuntimeError("bus gone")

    tracker = DownloadTracker(boom)
    tracker.finish()


def test_formats_recorder_reads_requested_format_sizes():
    tracker, _ = _tracker()
    recorder = _yt_dlp_base._FormatSizesPP(tracker)
    info = {
        "requested_formats": [
            {"format_id": "137", "filesize": 900},
            {"format_id": "140", "filesize": None, "filesize_approx": 100},
        ]
    }

    assert recorder.run(info) == ([], info)
    tracker.progress_hook(_finished("v.f137.mp4", 900))
    assert tracker.percent == 90


def test_formats_recorder_reads_a_single_format_size():
    tracker, _ = _tracker()
    recorder = _yt_dlp_base._FormatSizesPP(tracker)

    recorder.run({"format_id": "18", "filesize_approx": 1000})
    tracker.progress_hook(_downloading("v.mp4", 500, 1000))

    assert tracker.percent == 50


# ── yt_dlp_download wiring ────────────────────────────────────────────────────


class _StubYoutubeDL:
    instances: list[_StubYoutubeDL] = []

    def __init__(self, opts: dict[str, Any]) -> None:
        self.opts = opts
        self.progress_hooks: list[Any] = []
        self.post_processors: list[tuple[Any, str]] = []
        _StubYoutubeDL.instances.append(self)

    def __enter__(self) -> _StubYoutubeDL:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def add_progress_hook(self, hook: Any) -> None:
        self.progress_hooks.append(hook)

    def add_post_processor(self, pp: Any, when: str = "post_process") -> None:
        self.post_processors.append((pp, when))

    def extract_info(self, url: str, download: bool) -> dict[str, Any]:
        info = {
            "title": "T",
            "duration": 1.0,
            "requested_formats": [{"filesize": 900}, {"filesize": 100}],
        }
        for pp, when in self.post_processors:
            if when == "before_dl":
                pp.run(info)
        for hook in self.progress_hooks:
            hook(_finished("v.f137.mp4", 900))
        return info

    def prepare_filename(self, info: dict[str, Any]) -> str:
        return "/tmp/v.mp4"


@pytest.fixture
def stub_ydl(monkeypatch: pytest.MonkeyPatch) -> type[_StubYoutubeDL]:
    _StubYoutubeDL.instances.clear()
    monkeypatch.setattr(_yt_dlp_base, "YoutubeDL", _StubYoutubeDL)
    return _StubYoutubeDL


def test_download_wires_the_tracker_when_a_callback_is_set(stub_ydl, tmp_path):
    calls: list[int] = []
    token = download_progress.set(calls.append)
    try:
        _yt_dlp_base.yt_dlp_download("https://x.test/v", str(tmp_path), source="t")
    finally:
        download_progress.reset(token)

    (ydl,) = stub_ydl.instances
    assert len(ydl.progress_hooks) == 1
    assert [when for _, when in ydl.post_processors] == ["before_dl"]
    # The stub reports only the first part; the download returning ends it at 100.
    assert calls == [90, 100]


def test_download_adds_no_hook_without_a_callback(stub_ydl, tmp_path):
    _yt_dlp_base.yt_dlp_download("https://x.test/v", str(tmp_path), source="t")

    (ydl,) = stub_ydl.instances
    assert ydl.progress_hooks == []
    assert ydl.post_processors == []
