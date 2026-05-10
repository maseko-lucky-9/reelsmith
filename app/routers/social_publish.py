"""Social publishing router (W1.6).

Enqueues a publish_job (immediate or scheduled) and runs it via
``social_publish_service``. Real adapters POST to the platform; the
default stub provider writes a JSON descriptor.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from typing import Awaitable, Callable

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ClipRecord, PublishJob, SocialAccount
from app.db.session import get_session, get_session_factory
from app.services.social_publish_service import run_publish_job

PublishRunner = Callable[[str], Awaitable[None]]


async def get_publish_runner() -> PublishRunner:
    """FastAPI dep — returns an async runner bound to the global session
    factory. Tests override this to bind to an in-memory engine."""
    factory = get_session_factory()

    async def _run(publish_job_id: str) -> None:
        async with factory() as session:
            await run_publish_job(session, publish_job_id)

    return _run

router = APIRouter(prefix="/api/social", tags=["social-publish"])


class PublishCreate(BaseModel):
    clip_id: str
    social_account_id: str
    title: str | None = None
    description: str | None = None
    hashtags: list[str] = Field(default_factory=list)
    schedule_at: datetime | None = None  # immediate when None


class SocialAccountCreate(BaseModel):
    platform: str
    account_handle: str
    display_name: str | None = None
    access_token: str  # plaintext at the boundary; encrypted before store
    refresh_token: str | None = None
    expires_at: datetime | None = None
    scopes: list[str] | None = None


def _account_to_dict(a: SocialAccount) -> dict[str, Any]:
    return {
        "id": a.id,
        "platform": a.platform,
        "account_handle": a.account_handle,
        "display_name": a.display_name,
        "expires_at": a.expires_at.isoformat() if a.expires_at else None,
        "scopes": a.scopes or [],
        "active": a.active,
        "created_at": a.created_at.isoformat(),
    }


def _job_to_dict(pj: PublishJob) -> dict[str, Any]:
    return {
        "id": pj.id,
        "clip_id": pj.clip_id,
        "social_account_id": pj.social_account_id,
        "title": pj.title,
        "description": pj.description,
        "hashtags": pj.hashtags or [],
        "status": pj.status,
        "schedule_at": pj.schedule_at.isoformat() if pj.schedule_at else None,
        "posted_at": pj.posted_at.isoformat() if pj.posted_at else None,
        "external_post_id": pj.external_post_id,
        "external_post_url": pj.external_post_url,
        "error": pj.error,
        "attempts": pj.attempts,
        "created_at": pj.created_at.isoformat(),
    }


# ── Accounts ────────────────────────────────────────────────────────────────


@router.get("/accounts")
async def list_accounts(session: AsyncSession = Depends(get_session)):
    res = await session.execute(
        select(SocialAccount).order_by(SocialAccount.created_at.desc())
    )
    return [_account_to_dict(a) for a in res.scalars().all()]


@router.post("/accounts", status_code=201)
async def create_account(
    body: SocialAccountCreate, session: AsyncSession = Depends(get_session)
):
    from app.services import token_vault
    from app.services.social import supported_platforms

    if body.platform not in supported_platforms():
        raise HTTPException(status_code=422, detail=f"unsupported platform: {body.platform}")
    a = SocialAccount(
        platform=body.platform,
        account_handle=body.account_handle,
        display_name=body.display_name,
        access_token_enc=token_vault.encrypt(body.access_token),
        refresh_token_enc=(
            token_vault.encrypt(body.refresh_token) if body.refresh_token else None
        ),
        expires_at=body.expires_at,
        scopes=list(body.scopes) if body.scopes else None,
    )
    session.add(a)
    await session.commit()
    await session.refresh(a)
    return _account_to_dict(a)


@router.delete("/accounts/{account_id}", status_code=204)
async def delete_account(account_id: str, session: AsyncSession = Depends(get_session)):
    res = await session.execute(
        select(SocialAccount).where(SocialAccount.id == account_id)
    )
    a = res.scalar_one_or_none()
    if a is None:
        raise HTTPException(status_code=404, detail="account not found")
    await session.delete(a)
    await session.commit()


# ── Publish jobs ────────────────────────────────────────────────────────────


@router.post("/publish", status_code=201)
async def create_publish(
    body: PublishCreate,
    bg: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
    runner: PublishRunner = Depends(get_publish_runner),
):
    if not (await session.execute(
        select(ClipRecord.id).where(ClipRecord.id == body.clip_id)
    )).scalar_one_or_none():
        raise HTTPException(status_code=404, detail="clip not found")
    if not (await session.execute(
        select(SocialAccount.id).where(SocialAccount.id == body.social_account_id)
    )).scalar_one_or_none():
        raise HTTPException(status_code=404, detail="social account not found")

    pj = PublishJob(
        clip_id=body.clip_id,
        social_account_id=body.social_account_id,
        title=body.title,
        description=body.description,
        hashtags=body.hashtags or None,
        schedule_at=body.schedule_at,
        status="pending" if body.schedule_at else "queued",
    )
    session.add(pj)
    await session.commit()
    await session.refresh(pj)

    if pj.status == "queued":
        # Fire-and-forget; orchestrator opens its own session.
        bg.add_task(runner, pj.id)

    return _job_to_dict(pj)


@router.get("/publish/{job_id}")
async def get_publish(job_id: str, session: AsyncSession = Depends(get_session)):
    res = await session.execute(select(PublishJob).where(PublishJob.id == job_id))
    pj = res.scalar_one_or_none()
    if pj is None:
        raise HTTPException(status_code=404, detail="publish job not found")
    return _job_to_dict(pj)


@router.get("/publish")
async def list_publish_for_clip(
    clip_id: str, session: AsyncSession = Depends(get_session)
):
    res = await session.execute(
        select(PublishJob).where(PublishJob.clip_id == clip_id)
        .order_by(PublishJob.created_at.desc())
    )
    return [_job_to_dict(pj) for pj in res.scalars().all()]


