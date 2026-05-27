"""Unit tests for xml_export_service event-bus emits (W1.6)."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.bus.event_bus import AsyncEventBus
from app.domain.events import EventType
from app.services import xml_export_service as svc


def _fake_clip(clip_id: str = "clip-1", *, output_path: str | None = "/tmp/clip.mp4"):
    """Minimal duck-typed ClipRecord for the renderer."""
    return SimpleNamespace(
        id=clip_id,
        start=0.0,
        end=12.0,
        title="title",
        summary="sum",
        output_path=output_path,
    )


async def test_render_emits_xml_exported_premiere():
    bus = AsyncEventBus()
    received = []

    async def collect():
        async for ev in bus.subscribe(types=[EventType.XML_EXPORTED]):
            received.append(ev)
            return

    consumer = asyncio.create_task(collect())
    await asyncio.sleep(0)

    out = svc.render(_fake_clip(), "premiere_fcp7", bus=bus, job_id="job-x")
    await asyncio.wait_for(consumer, timeout=1.0)

    assert out.filename == "clip-1.xml"
    assert received[0].type is EventType.XML_EXPORTED
    assert received[0].job_id == "job-x"
    assert received[0].payload["clip_id"] == "clip-1"
    assert received[0].payload["format"] == "premiere_fcp7"
    assert received[0].payload["bytes"] > 0


async def test_render_emits_xml_exported_davinci():
    bus = AsyncEventBus()
    received = []

    async def collect():
        async for ev in bus.subscribe(types=[EventType.XML_EXPORTED]):
            received.append(ev)
            return

    consumer = asyncio.create_task(collect())
    await asyncio.sleep(0)

    out = svc.render(_fake_clip(), "davinci_fcpxml", bus=bus, job_id="job-x")
    await asyncio.wait_for(consumer, timeout=1.0)

    assert out.filename.endswith(".fcpxml")
    assert received[0].payload["format"] == "davinci_fcpxml"


def test_render_without_bus_works():
    """Legacy callers (no bus) still get a result, no emit fired."""
    out = svc.render(_fake_clip(), "premiere_fcp7")
    assert out.filename == "clip-1.xml"
    assert out.content_type == "application/xml"


def test_render_no_output_path_raises():
    with pytest.raises(ValueError):
        svc.render(_fake_clip(output_path=None), "premiere_fcp7")
