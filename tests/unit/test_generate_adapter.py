"""Unit tests for the generate:// adapter (Stage 1, stub producers)."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from app.services.platforms import (
    GenerateAdapter,
    detect_platform_id,
    resolve,
)
from app.services.platforms.base import DownloadResult
import app.services.platforms.generate as gen_mod


# ── matches() ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url",
    [
        "generate://abc123",
        "generate://deadbeefdeadbeef",
        "generate://A_b-C",
    ],
)
def test_matches_true(url):
    assert GenerateAdapter.matches(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=abc",
        "upload:///tmp/yt/uploads/x.mp4",
        "",
        "not-a-url",
        None,
    ],
)
def test_matches_false(url):
    assert GenerateAdapter.matches(url) is False


# ── registry resolution ───────────────────────────────────────────────────────


def test_registry_resolves_generate_scheme():
    assert isinstance(resolve("generate://x"), GenerateAdapter)
    assert detect_platform_id("generate://x") == "generate"


def test_extract_chapters_always_empty():
    assert GenerateAdapter().extract_chapters({"chapters": [{"title": "x"}]}) == []
    assert GenerateAdapter().extract_chapters({}) == []


# ── helpers ───────────────────────────────────────────────────────────────────


def _write_brief(brief_dir: Path, brief_id: str, **overrides) -> None:
    brief_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "title": "Test Reel",
        "script": "A short script for a generated reel about focus.",
        "shots": [
            {"prompt": "sunrise over a city", "seconds": 1.0},
            {"prompt": "a person writing notes", "seconds": 1.0},
        ],
        "voice_profile": "",
        "music_url": "",
    }
    payload.update(overrides)
    (brief_dir / f"{brief_id}.json").write_text(json.dumps(payload))


def _has_audio_stream(path: str) -> bool:
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", path],
        capture_output=True, text=True,
    )
    streams = json.loads(result.stdout).get("streams", [])
    return any(s.get("codec_type") == "audio" for s in streams)


# ── download() — happy path with stub producers ───────────────────────────────


def test_download_stub_producers_returns_decodable_mp4(tmp_path, monkeypatch):
    brief_dir = tmp_path / "briefs"
    dest = tmp_path / "dest"
    _write_brief(brief_dir, "brief01")

    monkeypatch.setattr(gen_mod.settings, "generate_enabled", True)
    monkeypatch.setattr(gen_mod.settings, "generate_brief_dir", str(brief_dir))
    monkeypatch.setattr(gen_mod.settings, "ltx_provider", "stub")
    monkeypatch.setattr(gen_mod.settings, "generate_tts_provider", "stub")

    result = GenerateAdapter().download("generate://brief01", str(dest))

    assert isinstance(result, DownloadResult)
    assert Path(result.video_path).is_file()
    assert result.source == "generate"
    assert result.title == "Test Reel"
    assert result.info["chapters"] == []
    assert result.duration > 0
    assert _has_audio_stream(result.video_path)


def test_download_disabled_raises(tmp_path, monkeypatch):
    brief_dir = tmp_path / "briefs"
    _write_brief(brief_dir, "brief01")
    monkeypatch.setattr(gen_mod.settings, "generate_enabled", False)
    monkeypatch.setattr(gen_mod.settings, "generate_brief_dir", str(brief_dir))

    with pytest.raises(RuntimeError, match="generate mode disabled"):
        GenerateAdapter().download("generate://brief01", str(tmp_path / "dest"))


def test_download_path_traversal_brief_id_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(gen_mod.settings, "generate_enabled", True)
    monkeypatch.setattr(gen_mod.settings, "generate_brief_dir", str(tmp_path / "briefs"))

    with pytest.raises((PermissionError, ValueError)):
        GenerateAdapter().download(
            "generate://../../etc/passwd", str(tmp_path / "dest")
        )


def test_download_unknown_brief_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(gen_mod.settings, "generate_enabled", True)
    monkeypatch.setattr(gen_mod.settings, "generate_brief_dir", str(tmp_path / "briefs"))

    with pytest.raises(FileNotFoundError):
        GenerateAdapter().download("generate://missing", str(tmp_path / "dest"))


# ── _assemble — VO longer than b-roll must not get truncated ──────────────────


def test_assemble_audio_longer_than_broll_keeps_headroom(tmp_path):
    """When the VO outlasts the concatenated b-roll, the assembled video must
    extend strictly past the audio by ≥ AUDIO_TAIL_EPSILON_SECONDS so the
    downstream probe_safe_end clamp can never truncate spoken content.
    """
    from app.services import ltx_producer, tts_service
    from app.services.clip_service import AUDIO_TAIL_EPSILON_SECONDS

    dest = tmp_path / "dest"
    dest.mkdir(parents=True, exist_ok=True)

    # 1.0s b-roll shot (stub) vs a much longer VO so audio is the longer track.
    shot_path = str(dest / "shot.mp4")
    ltx_producer.generate_shot("a city street", 1.0, shot_path, provider="stub")

    vo_wav = str(dest / "vo.wav")
    # ~120 chars → stub duration ≈ max(2.0, 120/15) = 8.0s of audio.
    long_script = "Stay focused while you work. " * 5
    tts_service.synthesize(long_script, vo_wav, provider="stub")

    audio_dur = gen_mod._probe_duration(vo_wav)
    assert audio_dur > 1.0  # sanity: audio really is the longer track

    out_path = str(dest / "generated.mp4")
    video_dur = GenerateAdapter()._assemble([shot_path], vo_wav, out_path)

    assert Path(out_path).is_file()
    # The returned (probed) clip duration must clear the audio by at least the
    # epsilon, so min(video, audio) - epsilon ≥ audio - epsilon never drops words.
    assert video_dur >= audio_dur + AUDIO_TAIL_EPSILON_SECONDS
