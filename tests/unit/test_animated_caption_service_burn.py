"""Unit + integration tests for animated_caption_service burn-in (T-06).

The unit tests patch the ffmpeg subprocess via the ``invoker`` hook so
they never touch the filesystem-level binary. The integration test runs
real ffmpeg against a 3-second fixture clip (created on the fly) and is
gated behind ``@pytest.mark.integration``.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from app.services import animated_caption_service as svc


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _write_srt(path: Path, cues: list[tuple[str, str, str]]) -> Path:
    """Write a minimal SRT with the given (start, end, text) triples."""
    blocks: list[str] = []
    for i, (start, end, text) in enumerate(cues, start=1):
        blocks.append(f"{i}\n{start} --> {end}\n{text}\n")
    path.write_text("\n".join(blocks), encoding="utf-8")
    return path


@pytest.fixture()
def sample_srt(tmp_path: Path) -> Path:
    """Two cues, multi-word, used by both styles."""
    return _write_srt(
        tmp_path / "captions.srt",
        cues=[
            ("00:00:00,000", "00:00:01,500", "hello world friend"),
            ("00:00:01,500", "00:00:03,000", "second cue here"),
        ],
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_ass_path(argv: tuple[str, ...]) -> str:
    """Pull the .ass path out of the ``-vf ass=<path>`` argument."""
    assert "-vf" in argv, f"argv missing -vf: {argv}"
    vf = argv[argv.index("-vf") + 1]
    assert vf.startswith("ass="), f"-vf value not ass=...: {vf!r}"
    return vf[len("ass=") :]


def _count_dialogue_lines(ass_text: str) -> int:
    return sum(1 for ln in ass_text.splitlines() if ln.startswith("Dialogue:"))


# ---------------------------------------------------------------------------
# SRT parsing
# ---------------------------------------------------------------------------


def test_srt_parser_handles_comma_and_dot_timestamps(tmp_path: Path):
    srt = _write_srt(
        tmp_path / "mixed.srt",
        cues=[
            ("00:00:00,000", "00:00:01,500", "hello world"),
            ("00:00:01.500", "00:00:03.000", "next line"),  # ``.`` variant
        ],
    )
    cues = svc._parse_srt(str(srt))
    assert len(cues) == 2
    assert cues[0].start == 0.0 and cues[0].end == 1.5
    assert cues[0].text == "hello world"
    assert cues[1].end == 3.0


# ---------------------------------------------------------------------------
# Unit: hormozi style — one Dialogue per WORD
# ---------------------------------------------------------------------------


def test_hormozi_burn_emits_one_dialogue_per_word(tmp_path: Path, sample_srt: Path):
    out = tmp_path / "out.mp4"
    captured_argv: list[tuple[str, ...]] = []
    captured_ass: list[str] = []

    def fake(argv):
        captured_argv.append(tuple(argv))
        # Capture .ass content while the temp file still exists.
        ass_path = _extract_ass_path(tuple(argv))
        captured_ass.append(Path(ass_path).read_text(encoding="utf-8"))
        # Simulate ffmpeg writing the output file.
        out.write_bytes(b"\x00\x00\x00\x18ftypisom")

    result = svc.burn_animated_captions(
        clip_path="/tmp/in.mp4",
        srt_path=str(sample_srt),
        output_path=str(out),
        style="hormozi",
        invoker=fake,
    )

    # Return value and basic invocation
    assert result == str(out)
    assert len(captured_argv) == 1
    argv = captured_argv[0]
    assert argv[0] == "ffmpeg"

    # The -vf argument must look like ``ass=<temp.ass>`` (acceptance bar).
    vf_idx = argv.index("-vf")
    assert argv[vf_idx + 1].startswith("ass=")
    assert argv[vf_idx + 1].endswith(".ass")

    # One Dialogue per WORD: "hello world friend" + "second cue here" = 6 words.
    ass_text = captured_ass[0]
    assert _count_dialogue_lines(ass_text) == 6

    # Every original word must appear as its own Dialogue line.
    for word in ["hello", "world", "friend", "second", "cue", "here"]:
        assert any(
            ln.startswith("Dialogue:") and ln.endswith(word)
            for ln in ass_text.splitlines()
        ), f"missing per-word dialogue for {word!r}"

    # Temp .ass cleaned up after the call returns.
    ass_path = _extract_ass_path(argv)
    assert not Path(ass_path).exists(), "temp .ass file leaked after burn"


# ---------------------------------------------------------------------------
# Unit: static style — one Dialogue per CUE
# ---------------------------------------------------------------------------


def test_static_burn_emits_one_dialogue_per_cue(tmp_path: Path, sample_srt: Path):
    out = tmp_path / "out.mp4"
    captured_ass: list[str] = []

    def fake(argv):
        captured_ass.append(Path(_extract_ass_path(tuple(argv))).read_text(encoding="utf-8"))
        out.write_bytes(b"\x00")

    svc.burn_animated_captions(
        clip_path="/tmp/in.mp4",
        srt_path=str(sample_srt),
        output_path=str(out),
        style="static",
        invoker=fake,
    )

    ass_text = captured_ass[0]
    # 2 cues -> 2 Dialogue lines (preserves the existing static behaviour).
    assert _count_dialogue_lines(ass_text) == 2

    # Each cue's full text should appear on its own dialogue line.
    assert any(ln.endswith("hello world friend") for ln in ass_text.splitlines()
               if ln.startswith("Dialogue:"))
    assert any(ln.endswith("second cue here") for ln in ass_text.splitlines()
               if ln.startswith("Dialogue:"))


# ---------------------------------------------------------------------------
# Unit: error paths
# ---------------------------------------------------------------------------


def test_burn_unknown_style_raises(tmp_path: Path, sample_srt: Path):
    with pytest.raises(ValueError):
        svc.burn_animated_captions(
            clip_path="/tmp/in.mp4",
            srt_path=str(sample_srt),
            output_path=str(tmp_path / "out.mp4"),
            style="comicsans",
            invoker=lambda argv: None,
        )


def test_burn_no_output_raises(tmp_path: Path, sample_srt: Path):
    def fake(argv):
        # Pretend ffmpeg ran but produced nothing.
        pass

    with pytest.raises(svc.AnimatedCaptionBurnError):
        svc.burn_animated_captions(
            clip_path="/tmp/in.mp4",
            srt_path=str(sample_srt),
            output_path=str(tmp_path / "missing.mp4"),
            style="hormozi",
            invoker=fake,
        )


def test_burn_subprocess_failure_propagates(tmp_path: Path, sample_srt: Path):
    def fake(argv):
        raise svc.AnimatedCaptionBurnError("ffmpeg failed (rc=1): bogus")

    with pytest.raises(svc.AnimatedCaptionBurnError):
        svc.burn_animated_captions(
            clip_path="/tmp/in.mp4",
            srt_path=str(sample_srt),
            output_path=str(tmp_path / "out.mp4"),
            style="hormozi",
            invoker=fake,
        )


def test_burn_argv_includes_input_and_output_paths(tmp_path: Path, sample_srt: Path):
    out = tmp_path / "out.mp4"
    captured: list[tuple[str, ...]] = []

    def fake(argv):
        captured.append(tuple(argv))
        out.write_bytes(b"x")

    svc.burn_animated_captions(
        clip_path="/tmp/source.mp4",
        srt_path=str(sample_srt),
        output_path=str(out),
        style="hormozi",
        invoker=fake,
    )
    argv = captured[0]
    assert "/tmp/source.mp4" in argv
    assert str(out) in argv
    assert "-y" in argv  # overwrite flag


# ---------------------------------------------------------------------------
# Integration: actual ffmpeg run
# ---------------------------------------------------------------------------


def _ffmpeg_has_ass_filter() -> bool:
    """Return True iff the local ffmpeg was built with libass support.

    Homebrew's stock ffmpeg ships without ``--enable-libass`` on many
    macOS hosts, which makes the ``ass=`` filter unavailable. We probe
    once so the integration test can skip cleanly when the binary
    can't actually composite subtitles.
    """
    if shutil.which("ffmpeg") is None:
        return False
    try:
        out = subprocess.run(
            ["ffmpeg", "-hide_banner", "-filters"],
            capture_output=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    text = out.stdout.decode("utf-8", errors="replace")
    # The filters table lists each filter on a line; ``ass`` is the
    # libass-driven Advanced SubStation Alpha renderer.
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "ass":
            return True
    return False


@pytest.mark.integration
def test_burn_animated_captions_real_ffmpeg(tmp_path: Path):
    """End-to-end ffmpeg burn against a 3s synthetic fixture clip.

    NOTE: we generate the fixture clip on the fly via ffmpeg's
    ``testsrc`` source (3s, 320x240, 30fps) — no checked-in binary
    fixture is required. The test skips when ffmpeg was built without
    libass (e.g. the stock Homebrew ``ffmpeg`` 8.x on macOS).
    """
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not available")
    if not _ffmpeg_has_ass_filter():
        pytest.skip(
            "local ffmpeg was built without libass — `ass=` filter "
            "unavailable; install via `brew install ffmpeg --with-libass` "
            "or a build that bundles libass to exercise this path"
        )

    # Build the 3s synthetic input clip.
    src = tmp_path / "src.mp4"
    gen = subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=duration=3:size=320x240:rate=30",
            "-pix_fmt", "yuv420p",
            str(src),
        ],
        capture_output=True,
    )
    assert gen.returncode == 0, gen.stderr.decode("utf-8", errors="replace")[-500:]
    assert src.is_file() and src.stat().st_size > 0

    # Minimal SRT covering the 3s window.
    srt = _write_srt(
        tmp_path / "captions.srt",
        cues=[
            ("00:00:00,000", "00:00:01,500", "hello world"),
            ("00:00:01,500", "00:00:03,000", "burn caption"),
        ],
    )

    out = tmp_path / "burned.mp4"
    result = svc.burn_animated_captions(
        clip_path=str(src),
        srt_path=str(srt),
        output_path=str(out),
        style="hormozi",
    )

    assert result == str(out)
    assert out.is_file(), "expected ffmpeg to produce the burned MP4"
    assert out.stat().st_size > 0, "burned MP4 is empty"
