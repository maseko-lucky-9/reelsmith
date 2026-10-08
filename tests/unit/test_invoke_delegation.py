"""audio_enhance / animated_caption ``_invoke`` hooks run through ``ffmpeg_tools.run``.

The services keep building test-visible argv with a bare ``"ffmpeg"``;
``ffmpeg_tools.run`` resolves it to the bundled imageio-ffmpeg binary, so the
hooks work on hosts without a system ffmpeg (CI).
"""

from __future__ import annotations

import pytest

from app.services import (
    animated_caption_service,
    audio_enhance_service,
    ffmpeg_tools,
)


@pytest.mark.parametrize(
    "invoke", [audio_enhance_service._invoke, animated_caption_service._invoke]
)
def test_invoke_delegates_unresolved_argv_to_run(invoke, monkeypatch):
    calls = []
    monkeypatch.setattr(
        ffmpeg_tools, "run", lambda argv, **kw: calls.append(list(argv))
    )
    invoke(("ffmpeg", "-y", "-i", "in.wav", "out.wav"))
    assert calls == [["ffmpeg", "-y", "-i", "in.wav", "out.wav"]]


@pytest.mark.parametrize(
    ("invoke", "error"),
    [
        (audio_enhance_service._invoke, audio_enhance_service.AudioEnhanceError),
        (
            animated_caption_service._invoke,
            animated_caption_service.AnimatedCaptionBurnError,
        ),
    ],
)
def test_invoke_wraps_ffmpeg_failure_with_stderr(invoke, error, tmp_path):
    missing = str(tmp_path / "missing.wav")
    with pytest.raises(error, match=r"ffmpeg failed \(rc=\d+\).*No such file"):
        invoke(("ffmpeg", "-hide_banner", "-i", missing, "-f", "null", "-"))


def test_loudnorm_enhance_runs_with_bundled_ffmpeg(tmp_path):
    src = tmp_path / "in.wav"
    ffmpeg_tools.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=1", str(src),
        ]
    )  # fmt: skip
    out = tmp_path / "out.wav"
    assert audio_enhance_service.enhance(
        str(src), str(out), provider="loudnorm"
    ) == str(out)
    assert ffmpeg_tools.duration(out, "audio") == pytest.approx(1.0, abs=0.05)
