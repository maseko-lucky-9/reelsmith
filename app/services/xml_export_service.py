"""Render NLE timeline XML for a single clip (W1.6).

Two formats supported:

* ``premiere_fcp7`` — Adobe Premiere Pro xmeml v5 (Final Cut 7 XML).
* ``davinci_fcpxml`` — DaVinci Resolve FCPXML 1.10.

Both are single-clip placeholders that point at the rendered MP4.
Multi-track / overlay export ships in W2 once the inline editor
multi-track surface lands.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.db.models import ClipRecord

ExportFormat = Literal["premiere_fcp7", "davinci_fcpxml"]

_TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"

_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATES_DIR)),
    autoescape=select_autoescape(enabled_extensions=("xml", "j2")),
    trim_blocks=True,
    lstrip_blocks=True,
)


@dataclass(frozen=True)
class XmlExport:
    filename: str
    content_type: str
    body: str


def render(clip: ClipRecord, fmt: ExportFormat, *, fps: int = 30) -> XmlExport:
    if not clip.output_path:
        raise ValueError(f"clip {clip.id!r} has no output_path; render first")
    duration_seconds = max(0.0, float((clip.end or 0) - (clip.start or 0)))
    duration_frames = int(round(duration_seconds * fps))
    pathurl = str(Path(clip.output_path).resolve())
    filename = Path(clip.output_path).name

    if fmt == "premiere_fcp7":
        body = _env.get_template("premiere_fcp7.xml.j2").render(
            clip=clip,
            fps=fps,
            duration_frames=duration_frames,
            pathurl=pathurl,
            filename=filename,
        )
        return XmlExport(
            filename=f"{clip.id}.xml",
            content_type="application/xml",
            body=body,
        )
    if fmt == "davinci_fcpxml":
        body = _env.get_template("davinci_fcpxml.xml.j2").render(
            clip=clip,
            fps=fps,
            duration_seconds=duration_seconds,
            pathurl=pathurl,
            filename=filename,
        )
        return XmlExport(
            filename=f"{clip.id}.fcpxml",
            content_type="application/xml",
            body=body,
        )
    raise ValueError(f"unsupported xml export format: {fmt!r}")
