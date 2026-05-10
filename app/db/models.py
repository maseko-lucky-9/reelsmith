"""SQLAlchemy ORM models for persistent job/clip storage."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, DateTime, Float, ForeignKey, Integer, LargeBinary, String, Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class JobRecord(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    youtube_url: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    segment_mode: Mapped[str | None] = mapped_column(String(16), nullable=True)
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    auto_hook: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    brand_template_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    pipeline_options: Mapped[dict | None] = mapped_column(JSON, default=dict, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )

    chapters: Mapped[list[ChapterRecord]] = relationship(
        "ChapterRecord", back_populates="job", cascade="all, delete-orphan"
    )
    clips: Mapped[list[ClipRecord]] = relationship(
        "ClipRecord", back_populates="job", cascade="all, delete-orphan"
    )


class ChapterRecord(Base):
    __tablename__ = "chapters"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    job_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chapter_index: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False, default="")
    start_time: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    end_time: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    job: Mapped[JobRecord] = relationship("JobRecord", back_populates="chapters")
    clips: Mapped[list[ClipRecord]] = relationship(
        "ClipRecord", back_populates="chapter"
    )


class BrandTemplate(Base):
    __tablename__ = "brand_templates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    logo_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    font_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    primary_color: Mapped[str] = mapped_column(String(32), nullable=False, default="#ffffff")
    secondary_color: Mapped[str] = mapped_column(String(32), nullable=False, default="#000000")
    caption_style: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=dict)
    intro_clip_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    outro_clip_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    vocabulary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )


class ClipRecord(Base):
    __tablename__ = "clips"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    job_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chapter_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("chapters.id", ondelete="SET NULL"), nullable=True
    )
    start: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    end: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    output_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    thumbnail_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    hashtags: Mapped[list | None] = mapped_column(JSON, nullable=True)
    virality_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    score_breakdown: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    transcript: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    liked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    disliked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    retired: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    ai_hook_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_hook_audio_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    broll_assets: Mapped[list | None] = mapped_column(JSON, nullable=True)
    caption_style: Mapped[str] = mapped_column(
        String(32), nullable=False, default="static"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )

    job: Mapped[JobRecord] = relationship("JobRecord", back_populates="clips")
    chapter: Mapped[ChapterRecord | None] = relationship(
        "ChapterRecord", back_populates="clips"
    )
    edit: Mapped["ClipEdit | None"] = relationship(
        "ClipEdit", back_populates="clip", uselist=False, cascade="all, delete-orphan"
    )


class ClipEdit(Base):
    """Inline-editor timeline state for a clip (W1.1).

    One row per clip; ``timeline`` JSON holds the multi-track editor
    state (video / caption / text-overlay tracks). ``version``
    increments on each PATCH for optimistic concurrency and audit.
    """

    __tablename__ = "clip_edits"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    clip_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("clips.id", ondelete="CASCADE", name="fk_clip_edits_clip_id"),
        nullable=False,
        unique=True,
        index=True,
    )
    timeline: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )

    clip: Mapped[ClipRecord] = relationship("ClipRecord", back_populates="edit")


class SocialAccount(Base):
    """OAuth identity for a publishing platform (W1.3).

    Tokens are Fernet-encrypted at rest. Refresh handled by
    ``social_publish_service`` with ``SELECT … FOR UPDATE`` to
    serialise concurrent refreshes.
    """

    __tablename__ = "social_accounts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    platform: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    account_handle: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    access_token_enc: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    refresh_token_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    scopes: Mapped[list | None] = mapped_column(JSON, nullable=True)
    owner_id: Mapped[str] = mapped_column(
        String(64), nullable=False, default="local", index=True
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )


# Status state machine for PublishJob (W1.4).
PUBLISH_JOB_STATUSES: tuple[str, ...] = (
    "pending", "queued", "posting", "published", "failed", "cancelled",
)


class PublishJob(Base):
    """Scheduled or immediate publish to a social platform (W1.4)."""

    __tablename__ = "publish_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    clip_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("clips.id", ondelete="CASCADE", name="fk_publish_jobs_clip_id"),
        nullable=False,
        index=True,
    )
    social_account_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey(
            "social_accounts.id",
            ondelete="CASCADE",
            name="fk_publish_jobs_social_account_id",
        ),
        nullable=False,
    )
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    hashtags: Mapped[list | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", index=True
    )
    schedule_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    posted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    external_post_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    external_post_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )


class CaptionStyle(Base):
    """Catalog of caption-style presets (W2.1)."""

    __tablename__ = "caption_styles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    animation_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    font_family: Mapped[str | None] = mapped_column(String(128), nullable=True)
    font_size: Mapped[int] = mapped_column(Integer, nullable=False, default=42)
    primary_color: Mapped[str] = mapped_column(String(16), nullable=False, default="#ffffff")
    highlight_color: Mapped[str | None] = mapped_column(String(16), nullable=True)
    stroke_color: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )


class BrandTemplateFont(Base):
    """Per-role typography for a brand template (W2.8)."""

    __tablename__ = "brand_template_fonts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    brand_template_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey(
            "brand_templates.id",
            ondelete="CASCADE",
            name="fk_brand_template_fonts_template",
        ),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)  # heading|body|caption
    family: Mapped[str] = mapped_column(String(128), nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )


# ── Wave 3 — collab + integrations ─────────────────────────────────────────


class Workspace(Base):
    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )


class WorkspaceMember(Base):
    __tablename__ = "workspace_members"
    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id", name="uq_wm_workspace_user"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("workspaces.id", ondelete="CASCADE", name="fk_wm_workspace"),
        nullable=False, index=True,
    )
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # owner|editor|viewer
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )


class ScheduledPost(Base):
    __tablename__ = "scheduled_posts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    publish_job_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("publish_jobs.id", ondelete="CASCADE", name="fk_sp_publish"),
        nullable=False,
    )
    scheduled_for: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="scheduled"
    )
    worker_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )


class ClipAnalyticsSnapshot(Base):
    __tablename__ = "clip_analytics_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    clip_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("clips.id", ondelete="CASCADE", name="fk_cas_clip"),
        nullable=False, index=True,
    )
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    external_post_id: Mapped[str] = mapped_column(String(255), nullable=False)
    impressions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    views: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    watch_time_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    likes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    comments: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    shares: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )


class ShareLink(Base):
    __tablename__ = "share_links"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    clip_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("clips.id", ondelete="CASCADE", name="fk_sl_clip"),
        nullable=False, index=True,
    )
    token: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )


class Webhook(Base):
    __tablename__ = "webhooks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    events: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    secret_enc: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )


class ApiToken(Base):
    __tablename__ = "api_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    token_prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    workspace_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("workspaces.id", ondelete="CASCADE", name="fk_at_workspace"),
        nullable=False, default="local",
    )
    revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
