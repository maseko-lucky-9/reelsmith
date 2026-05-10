"""Social-publish orchestration (W1.5).

Run order per ``publish_jobs`` row:

    1. Load PublishJob + SocialAccount + ClipRecord.
    2. Decrypt access_token via token_vault.
    3. Resolve platform adapter via registry.
    4. status: queued -> posting; bump attempts.
    5. Call adapter.publish().
    6. status: posting -> published (record external id/url, posted_at)
                       -> failed   (record error string).

Idempotency: a job already past ``posting`` is skipped; same external
id is preserved.

Caller (router or scheduler tick) is responsible for invoking this
once per queued id and emitting EventType events on the bus.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ClipRecord, PublishJob, SocialAccount
from app.services import token_vault
from app.services.social import (
    PlatformAdapter,
    PublishRequest,
    PublishResult,
    UnsupportedPlatformError,
    get_adapter,
)

log = logging.getLogger(__name__)


class PublishError(RuntimeError):
    """Wraps any adapter / network failure for the caller's policy."""


async def run_publish_job(
    session: AsyncSession,
    publish_job_id: str,
    *,
    adapter: PlatformAdapter | None = None,
    stub_dir: str | None = None,
) -> PublishJob:
    """Execute one publish_job row end-to-end. Returns the updated row.

    ``adapter`` override is intended for tests; production resolves
    through the registry.
    """
    pj = await _load(session, publish_job_id)
    if pj.status in ("published", "failed", "cancelled"):
        log.info("publish_job %s already terminal (%s); skipping", pj.id, pj.status)
        return pj

    acct = (await session.execute(
        select(SocialAccount).where(SocialAccount.id == pj.social_account_id)
    )).scalar_one()

    clip = (await session.execute(
        select(ClipRecord).where(ClipRecord.id == pj.clip_id)
    )).scalar_one()

    if not clip.output_path:
        await _fail(session, pj, "clip has no output_path")
        return pj

    try:
        plain_token = token_vault.decrypt(acct.access_token_enc)
    except ValueError as exc:
        await _fail(session, pj, f"token decrypt failed: {exc}")
        return pj

    try:
        plat_adapter = adapter or get_adapter(acct.platform)
    except UnsupportedPlatformError as exc:
        await _fail(session, pj, str(exc))
        return pj

    pj.status = "posting"
    pj.attempts = pj.attempts + 1
    await session.commit()

    request = PublishRequest(
        platform=acct.platform,
        account_handle=acct.account_handle,
        clip_path=clip.output_path,
        title=pj.title or (clip.title or ""),
        description=pj.description or (clip.summary or ""),
        hashtags=tuple(pj.hashtags or clip.hashtags or []),
        access_token=plain_token,
        stub_dir=stub_dir,
    )

    try:
        result: PublishResult = await plat_adapter.publish(request)
    except Exception as exc:  # noqa: BLE001
        await _fail(session, pj, f"{type(exc).__name__}: {exc}")
        return pj

    pj.status = "published"
    pj.external_post_id = result.external_post_id
    pj.external_post_url = result.external_post_url
    pj.posted_at = datetime.now(timezone.utc)
    pj.error = None
    await session.commit()
    await session.refresh(pj)
    return pj


async def _load(session: AsyncSession, pj_id: str) -> PublishJob:
    res = await session.execute(select(PublishJob).where(PublishJob.id == pj_id))
    pj = res.scalar_one_or_none()
    if pj is None:
        raise PublishError(f"publish_job {pj_id!r} not found")
    return pj


async def _fail(session: AsyncSession, pj: PublishJob, msg: str) -> None:
    pj.status = "failed"
    pj.error = msg[:1024]
    await session.commit()
    await session.refresh(pj)
    log.warning("publish_job %s failed: %s", pj.id, msg)
