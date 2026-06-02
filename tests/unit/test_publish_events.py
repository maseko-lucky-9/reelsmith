"""Tests for PUBLISH_* event emission in run_publish_job.

Agent B adds a ``bus=`` keyword arg to ``run_publish_job`` so the
orchestrator can pass an AsyncEventBus and receive domain events.  These
tests are skipped when that parameter is not present (pre-Agent-B code)
so collection never fails regardless of merge order.
"""
from __future__ import annotations

import asyncio
import inspect

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import ClipRecord, JobRecord, PublishJob, SocialAccount
from app.domain.events import EventType
from app.services import token_vault
from app.services.social.base import PublishRequest, PublishResult
from app.services.social_publish_service import run_publish_job

# ── Check whether Agent B's bus= param exists ────────────────────────────────

_sig = inspect.signature(run_publish_job)
HAS_BUS_PARAM = "bus" in _sig.parameters

_requires_bus = pytest.mark.skipif(
    not HAS_BUS_PARAM,
    reason="requires Agent B event-wiring changes (bus= param in run_publish_job)",
)


# ── Fake helpers ─────────────────────────────────────────────────────────────


class FakeBus:
    """Minimal in-memory bus that records every published event."""

    def __init__(self) -> None:
        self.events: list = []

    async def publish(self, event) -> None:  # noqa: ANN001
        self.events.append(event)

    def types(self) -> list[EventType]:
        return [e.type for e in self.events]


class FakeAdapter:
    """Succeeds unconditionally — returns a stable sentinel post id."""

    platform = "youtube"

    async def publish(self, request: PublishRequest) -> PublishResult:
        return PublishResult(
            external_post_id="fake_post_001",
            external_post_url="https://fake.example/post/001",
        )


class FailingAdapter:
    """Raises RuntimeError to simulate an adapter-level failure."""

    platform = "youtube"

    def __init__(self, msg: str = "upload failed") -> None:
        self._msg = msg

    async def publish(self, request: PublishRequest) -> PublishResult:
        raise RuntimeError(self._msg)


# ── Factory fixture (in-memory SQLite, mirrors test_social_adapters.py) ──────


@pytest.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _seed(
    factory,
    *,
    output_path: str | None = "/fake/clip.mp4",
    platform: str = "youtube",
    access_token: str = "fake-bearer",
) -> str:
    """Insert a minimal PublishJob row and return its id."""
    token_vault.reset_for_tests()
    enc = token_vault.encrypt(access_token)
    async with factory() as session:
        job = JobRecord(youtube_url="https://example.com/v")
        session.add(job)
        await session.flush()
        clip = ClipRecord(
            job_id=job.id,
            start=0,
            end=10,
            output_path=output_path,
            title="clip title",
            summary="clip summary",
        )
        session.add(clip)
        acct = SocialAccount(
            platform=platform,
            account_handle="@testaccount",
            access_token_enc=enc,
        )
        session.add(acct)
        await session.flush()
        pj = PublishJob(
            clip_id=clip.id,
            social_account_id=acct.id,
            status="queued",
        )
        session.add(pj)
        await session.commit()
        return pj.id


# ── Event-emission tests ──────────────────────────────────────────────────────


@_requires_bus
@pytest.mark.asyncio
async def test_emits_queued_then_completed(factory, monkeypatch, tmp_path):
    """PUBLISH_QUEUED then PUBLISH_COMPLETED are emitted on a successful publish."""
    monkeypatch.setenv("YTVIDEO_OAUTH_ENCRYPT_KEY", Fernet.generate_key().decode())
    output = tmp_path / "clip.mp4"
    output.write_bytes(b"x")

    pj_id = await _seed(factory, output_path=str(output))
    bus = FakeBus()

    async with factory() as session:
        pj = await run_publish_job(
            session,
            pj_id,
            adapter=FakeAdapter(),
            bus=bus,
        )

    # Let any fire-and-forget tasks drain
    await asyncio.sleep(0)

    assert pj.status == "published"
    event_types = bus.types()
    assert EventType.PUBLISH_QUEUED in event_types, (
        f"Expected PUBLISH_QUEUED in {event_types}"
    )
    assert EventType.PUBLISH_COMPLETED in event_types, (
        f"Expected PUBLISH_COMPLETED in {event_types}"
    )
    # Order: queued before completed
    queued_idx = event_types.index(EventType.PUBLISH_QUEUED)
    completed_idx = event_types.index(EventType.PUBLISH_COMPLETED)
    assert queued_idx < completed_idx, (
        f"PUBLISH_QUEUED ({queued_idx}) must precede PUBLISH_COMPLETED ({completed_idx})"
    )


@_requires_bus
@pytest.mark.asyncio
async def test_emits_failed_on_error(factory, monkeypatch, tmp_path):
    """PUBLISH_FAILED is emitted when the adapter raises."""
    monkeypatch.setenv("YTVIDEO_OAUTH_ENCRYPT_KEY", Fernet.generate_key().decode())
    output = tmp_path / "clip.mp4"
    output.write_bytes(b"x")

    pj_id = await _seed(factory, output_path=str(output))
    bus = FakeBus()

    async with factory() as session:
        pj = await run_publish_job(
            session,
            pj_id,
            adapter=FailingAdapter("network timeout"),
            bus=bus,
        )

    await asyncio.sleep(0)

    assert pj.status == "failed"
    assert EventType.PUBLISH_FAILED in bus.types(), (
        f"Expected PUBLISH_FAILED in {bus.types()}"
    )


@_requires_bus
@pytest.mark.asyncio
async def test_no_token_in_payload(factory, monkeypatch, tmp_path):
    """SECURITY: the decrypted access_token value must not appear in any event payload."""
    monkeypatch.setenv("YTVIDEO_OAUTH_ENCRYPT_KEY", Fernet.generate_key().decode())
    output = tmp_path / "clip.mp4"
    output.write_bytes(b"x")

    token_value = "super-secret-sessionid-abc123"
    pj_id = await _seed(factory, output_path=str(output), access_token=token_value)
    bus = FakeBus()

    async with factory() as session:
        await run_publish_job(
            session,
            pj_id,
            adapter=FakeAdapter(),
            bus=bus,
        )

    await asyncio.sleep(0)

    for event in bus.events:
        payload_str = str(event.payload)
        assert token_value not in payload_str, (
            f"Token leaked in event {event.type}: {payload_str!r}"
        )


@_requires_bus
@pytest.mark.asyncio
async def test_all_fail_paths_emit_failed_no_output_path(factory, monkeypatch):
    """PUBLISH_FAILED emitted when clip has no output_path."""
    monkeypatch.setenv("YTVIDEO_OAUTH_ENCRYPT_KEY", Fernet.generate_key().decode())
    pj_id = await _seed(factory, output_path=None)
    bus = FakeBus()

    async with factory() as session:
        pj = await run_publish_job(session, pj_id, bus=bus)

    await asyncio.sleep(0)

    assert pj.status == "failed"
    assert EventType.PUBLISH_FAILED in bus.types(), (
        f"Expected PUBLISH_FAILED for missing output_path, got {bus.types()}"
    )


@_requires_bus
@pytest.mark.asyncio
async def test_all_fail_paths_emit_failed_adapter_error(factory, monkeypatch, tmp_path):
    """PUBLISH_FAILED emitted when adapter.publish() raises."""
    monkeypatch.setenv("YTVIDEO_OAUTH_ENCRYPT_KEY", Fernet.generate_key().decode())
    output = tmp_path / "clip.mp4"
    output.write_bytes(b"x")

    pj_id = await _seed(factory, output_path=str(output))
    bus = FakeBus()

    async with factory() as session:
        pj = await run_publish_job(
            session,
            pj_id,
            adapter=FailingAdapter("upstream error"),
            bus=bus,
        )

    await asyncio.sleep(0)

    assert pj.status == "failed"
    assert EventType.PUBLISH_FAILED in bus.types(), (
        f"Expected PUBLISH_FAILED for adapter error, got {bus.types()}"
    )


@_requires_bus
@pytest.mark.asyncio
async def test_failed_event_not_emitted_on_success(factory, monkeypatch, tmp_path):
    """PUBLISH_FAILED must NOT be emitted when the job succeeds."""
    monkeypatch.setenv("YTVIDEO_OAUTH_ENCRYPT_KEY", Fernet.generate_key().decode())
    output = tmp_path / "clip.mp4"
    output.write_bytes(b"x")

    pj_id = await _seed(factory, output_path=str(output))
    bus = FakeBus()

    async with factory() as session:
        pj = await run_publish_job(
            session,
            pj_id,
            adapter=FakeAdapter(),
            bus=bus,
        )

    await asyncio.sleep(0)

    assert pj.status == "published"
    assert EventType.PUBLISH_FAILED not in bus.types(), (
        f"PUBLISH_FAILED was unexpectedly emitted on success: {bus.types()}"
    )


@_requires_bus
@pytest.mark.asyncio
async def test_completed_event_not_emitted_on_failure(factory, monkeypatch, tmp_path):
    """PUBLISH_COMPLETED must NOT be emitted when the adapter raises."""
    monkeypatch.setenv("YTVIDEO_OAUTH_ENCRYPT_KEY", Fernet.generate_key().decode())
    output = tmp_path / "clip.mp4"
    output.write_bytes(b"x")

    pj_id = await _seed(factory, output_path=str(output))
    bus = FakeBus()

    async with factory() as session:
        pj = await run_publish_job(
            session,
            pj_id,
            adapter=FailingAdapter("adapter fail"),
            bus=bus,
        )

    await asyncio.sleep(0)

    assert pj.status == "failed"
    assert EventType.PUBLISH_COMPLETED not in bus.types(), (
        f"PUBLISH_COMPLETED was unexpectedly emitted on failure: {bus.types()}"
    )


@_requires_bus
@pytest.mark.asyncio
async def test_emits_failed_on_decrypt_error(factory, monkeypatch, tmp_path):
    """PUBLISH_FAILED emitted when token_vault.decrypt raises ValueError.

    Seeds the token with key A, then resets the vault so it generates a new
    ephemeral key B, making the stored ciphertext unreadable — triggering the
    decrypt-fail branch in social_publish_service.py.
    """
    key_a = Fernet.generate_key().decode()
    monkeypatch.setenv("YTVIDEO_OAUTH_ENCRYPT_KEY", key_a)
    output = tmp_path / "clip.mp4"
    output.write_bytes(b"x")

    pj_id = await _seed(factory, output_path=str(output))

    # Switch to a different key so the stored ciphertext is unreadable
    key_b = Fernet.generate_key().decode()
    monkeypatch.setenv("YTVIDEO_OAUTH_ENCRYPT_KEY", key_b)
    token_vault.reset_for_tests()

    bus = FakeBus()

    async with factory() as session:
        pj = await run_publish_job(session, pj_id, bus=bus)

    await asyncio.sleep(0)

    assert pj.status == "failed"
    assert EventType.PUBLISH_FAILED in bus.types(), (
        f"Expected PUBLISH_FAILED for decrypt error, got {bus.types()}"
    )


@_requires_bus
@pytest.mark.asyncio
async def test_emits_failed_on_unsupported_platform(factory, monkeypatch, tmp_path):
    """PUBLISH_FAILED emitted when the platform has no live adapter."""
    monkeypatch.setenv("YTVIDEO_OAUTH_ENCRYPT_KEY", Fernet.generate_key().decode())
    # Ensure we use the stub provider (not a per-platform real override) so
    # "unsupported_xyz" hits the UnsupportedPlatformError branch.
    monkeypatch.delenv("YTVIDEO_SOCIAL_PROVIDER", raising=False)
    output = tmp_path / "clip.mp4"
    output.write_bytes(b"x")

    pj_id = await _seed(factory, output_path=str(output), platform="unsupported_xyz")
    bus = FakeBus()

    # No adapter= override — let the service call get_adapter("unsupported_xyz")
    async with factory() as session:
        pj = await run_publish_job(session, pj_id, bus=bus)

    await asyncio.sleep(0)

    assert pj.status == "failed"
    assert EventType.PUBLISH_FAILED in bus.types(), (
        f"Expected PUBLISH_FAILED for unsupported platform, got {bus.types()}"
    )
