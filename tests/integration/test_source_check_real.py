"""The clean-footage check against a real Ollama and real YouTube (spec 015). Opt-in: ``-m integration``.

Skipped when Ollama or ``YTVIDEO_SOURCE_CHECK_MODEL`` is not available, and the
YouTube cases are skipped when YouTube refuses the stream request from this
address (its "confirm you are not a bot" answer): that is the network's
verdict, not the check's.

The two videos were picked from real search results and looked at frame by
frame (see specs/015-youtube-source-search/spec.md, "Measured"):

* ``tbnzAVRZ9Xc`` (Denzel Washington motivational speech): a white subtitle on
  every sampled frame.
* ``5MuIMqhT8DM`` (TED, Matt Walker, "Sleep Is Your Superpower"): a stage talk
  without any text on screen.
"""

from __future__ import annotations

import io

import httpx
import pytest
from PIL import Image, ImageDraw, ImageFont

from app.services import source_check
from app.settings import settings

pytestmark = pytest.mark.integration

CAPTIONED_VIDEO = "tbnzAVRZ9Xc"
CLEAN_VIDEO = "5MuIMqhT8DM"


def _ollama_has_the_model() -> bool:
    try:
        tags = httpx.get(f"{settings.ollama_base_url.rstrip('/')}/api/tags", timeout=3).json()
    except httpx.HTTPError, ValueError:
        return False
    return any(m.get("name") == settings.source_check_model for m in tags.get("models", []))


requires_model = pytest.mark.skipif(not _ollama_has_the_model(), reason="Ollama or the vision model is not available")


def _frame(text: str | None) -> bytes:
    """A flat "talking head" picture, with a subtitle in a large white font when ``text`` is given."""
    image = Image.new("RGB", (854, 480), (70, 100, 130))
    draw = ImageDraw.Draw(image)
    draw.ellipse((357, 70, 497, 230), fill=(225, 185, 155))  # a head
    draw.rectangle((317, 230, 537, 480), fill=(30, 30, 40))  # a torso
    if text:
        font = ImageFont.load_default(size=34)
        width = draw.textlength(text, font=font)
        position = ((854 - width) / 2, 395)
        for dx, dy in ((-2, -2), (2, -2), (-2, 2), (2, 2)):  # a dark outline, as subtitles have
            draw.text((position[0] + dx, position[1] + dy), text, font=font, fill=(0, 0, 0))
        draw.text(position, text, font=font, fill=(255, 255, 255))
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=90)
    return buffer.getvalue()


@requires_model
async def test_the_real_model_tells_a_burned_in_subtitle_from_a_plain_picture():
    async with httpx.AsyncClient(timeout=120) as client:
        plain = await source_check.judge_frame(client, _frame(None))
        subtitled = await source_check.judge_frame(client, _frame("and they are standing around your bed"))

    assert plain is not None and plain.captions is False
    assert subtitled is not None and subtitled.captions is True


async def _stream_or_skip(video_id: str) -> None:
    try:
        await source_check.resolve_stream(video_id)
    except source_check.CheckUnavailableError as error:
        if "confirm you" in str(error) or "429" in str(error):
            pytest.skip(f"YouTube refused the stream request from this address: {str(error)[:120]}")
        raise


@requires_model
async def test_a_real_video_with_burned_in_captions_is_not_clean():
    await _stream_or_skip(CAPTIONED_VIDEO)
    source_check.clear_cache()

    result = await source_check.check_clean(CAPTIONED_VIDEO)

    assert result.clean is False, result
    assert result.frames_checked >= 2


@requires_model
async def test_a_real_talk_without_captions_is_clean():
    await _stream_or_skip(CLEAN_VIDEO)
    source_check.clear_cache()

    result = await source_check.check_clean(CLEAN_VIDEO)

    assert result.clean is True, result
    assert result.frames_checked >= 2
