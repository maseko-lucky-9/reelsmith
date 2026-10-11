"""yt-dlp uses the bundled ffmpeg (constitution III, tasks T003 / former E3).

The default format ``bestvideo+bestaudio`` makes yt-dlp merge the two streams
with ffmpeg. Without ``ffmpeg_location`` it would run whichever ``ffmpeg`` is
on ``PATH`` (or fail when there is none).
"""

from __future__ import annotations

import shutil
from unittest.mock import MagicMock, patch

from yt_dlp import YoutubeDL
from yt_dlp.postprocessor.ffmpeg import FFmpegMergerPP

from app.services import ffmpeg_tools
from app.services.folder_service import fetch_video_title
from app.services.platforms._yt_dlp_base import build_ydl_opts, yt_dlp_download


def _fake_ydl(info: dict) -> MagicMock:
    ydl = MagicMock()
    ydl.__enter__ = MagicMock(return_value=ydl)
    ydl.__exit__ = MagicMock(return_value=False)
    ydl.extract_info.return_value = info
    ydl.prepare_filename.return_value = "/dest/video.mp4"
    return ydl


def test_download_passes_bundled_ffmpeg_to_yt_dlp(tmp_path):
    with patch(
        "app.services.platforms._yt_dlp_base.YoutubeDL",
        return_value=_fake_ydl({"title": "t", "duration": 1.0}),
    ) as ydl_cls:
        yt_dlp_download(
            "https://www.youtube.com/watch?v=x", str(tmp_path), source="youtube"
        )

    opts = ydl_cls.call_args.args[0]
    assert opts["ffmpeg_location"] == ffmpeg_tools.exe()
    assert "bestvideo" in opts["format"]


def test_merge_uses_bundled_ffmpeg_with_no_ffmpeg_on_path(tmp_path, monkeypatch):
    """Offline: yt-dlp's merger joins a video-only and an audio-only file with
    the opts the downloader builds, while ``PATH`` holds no ffmpeg at all."""
    video_only = tmp_path / "v.f137.mp4"
    audio_only = tmp_path / "a.f140.m4a"
    ffmpeg_tools.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=64x48:rate=10:duration=1",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(video_only),
        ],
        timeout=60,
    )
    ffmpeg_tools.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1",
            "-vn",
            "-c:a",
            "aac",
            str(audio_only),
        ],
        timeout=60,
    )
    assert not ffmpeg_tools.has_audio(video_only)

    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir()
    monkeypatch.setenv("PATH", str(empty_bin))
    assert shutil.which("ffmpeg") is None and shutil.which("ffprobe") is None

    merged = tmp_path / "merged.mp4"
    opts = build_ydl_opts(str(tmp_path)) | {"quiet": True, "no_warnings": True}
    info = {
        "filepath": str(merged),
        "ext": "mp4",
        "vcodec": "avc1",
        "acodec": "mp4a.40.2",
        "requested_formats": [
            {
                "vcodec": "avc1",
                "acodec": "none",
                "protocol": "https",
                "filepath": str(video_only),
            },
            {
                "vcodec": "none",
                "acodec": "mp4a.40.2",
                "protocol": "https",
                "filepath": str(audio_only),
            },
        ],
        "__files_to_merge": [str(video_only), str(audio_only)],
    }
    with YoutubeDL(opts) as ydl:
        FFmpegMergerPP(ydl).run(info)

    assert merged.exists()
    assert ffmpeg_tools.video_size(merged) == (64, 48)
    assert ffmpeg_tools.has_audio(merged)


def test_fetch_video_title_sets_a_socket_timeout():
    with patch(
        "app.services.folder_service.YoutubeDL",
        return_value=_fake_ydl({"title": "My Video"}),
    ) as ydl_cls:
        assert fetch_video_title("https://www.youtube.com/watch?v=x") == "My Video"

    opts = ydl_cls.call_args.args[0]
    assert opts["socket_timeout"] == 10
    assert opts["extract_flat"] is True
