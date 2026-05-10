"""Tier capability flag map (W3.9).

Single-tenant default: every flag is True. The flag map mirrors the
OpusClip pricing matrix so feature gating can flip from "always on"
to per-tier checks without surgery on the routers.

Future: read from a workspace's plan / billing record. For now,
``capabilities_for(workspace_id)`` always returns the BUSINESS tier
because we don't bill anything.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Mapping


@dataclass(frozen=True)
class Capabilities:
    # Editing & quality
    animated_captions: bool = True
    multi_aspect_reframe: bool = True
    voiceover: bool = True
    filler_removal: bool = True
    transitions: bool = True
    brand_templates: bool = True
    brand_vocabulary: bool = True
    profanity_filter: bool = True
    custom_clip_length: bool = True
    reprompt: bool = True

    # Distribution
    publish_youtube: bool = True
    publish_tiktok: bool = True
    publish_instagram: bool = True
    publish_linkedin: bool = True
    publish_x: bool = True
    multi_profile: bool = True
    scheduler: bool = True
    bulk_schedule: bool = True
    xml_export: bool = True
    bulk_export: bool = True
    share_links: bool = True

    # Collab + integrations
    workspace: bool = True
    folders: bool = True
    auto_save: bool = True
    analytics: bool = True
    webhooks: bool = True
    api_tokens: bool = True

    # Limits (None = unbounded)
    max_clips_per_export: int | None = None
    max_brand_fonts: int | None = None
    max_voiceover_seconds: int | None = None

    # Marker for downstream debugging.
    tier: str = "business"


_DEFAULT = Capabilities()


def capabilities_for(workspace_id: str) -> Capabilities:
    """Return the resolved tier for a workspace.

    Single-tenant: always BUSINESS. Wave 4+ flips this to a DB lookup.
    """
    return _DEFAULT


def capability(workspace_id: str, name: str) -> bool | int | str | None:
    cap = capabilities_for(workspace_id)
    if not hasattr(cap, name):
        raise KeyError(f"unknown capability: {name!r}")
    return getattr(cap, name)


def as_dict(workspace_id: str) -> Mapping[str, object]:
    return asdict(capabilities_for(workspace_id))
