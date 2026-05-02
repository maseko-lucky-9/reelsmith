"""CRUD endpoints for BrandTemplate."""
from __future__ import annotations

import mimetypes
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.db.models import BrandTemplate

router = APIRouter(prefix="/brand-templates", tags=["brand-templates"])

_BRAND_ASSETS_ROOT = Path("data/brand_assets")

_ALLOWED_MIME: dict[str, set[str]] = {
    "logo": {"image/png", "image/jpeg"},
    "font": {"font/ttf", "font/otf", "application/font-ttf", "application/font-otf",
             "application/x-font-ttf", "application/x-font-otf"},
    "intro": {"video/mp4"},
    "outro": {"video/mp4"},
}


class BrandTemplateCreate(BaseModel):
    name: str
    primary_color: str = "#ffffff"
    secondary_color: str = "#000000"
    caption_style: dict = {}


class BrandTemplateUpdate(BaseModel):
    name: str | None = None
    primary_color: str | None = None
    secondary_color: str | None = None
    caption_style: dict | None = None


def _to_dict(t: BrandTemplate) -> dict:
    return {
        "id": t.id, "name": t.name,
        "logo_path": t.logo_path, "font_path": t.font_path,
        "primary_color": t.primary_color, "secondary_color": t.secondary_color,
        "caption_style": t.caption_style or {},
        "intro_clip_path": t.intro_clip_path, "outro_clip_path": t.outro_clip_path,
        "created_at": t.created_at.isoformat(),
    }


@router.get("")
async def list_templates(session: AsyncSession = Depends(get_session)):
    from sqlalchemy import select
    result = await session.execute(select(BrandTemplate).order_by(BrandTemplate.created_at.desc()))
    return [_to_dict(t) for t in result.scalars().all()]


@router.post("", status_code=201)
async def create_template(
    body: BrandTemplateCreate, session: AsyncSession = Depends(get_session)
):
    t = BrandTemplate(
        name=body.name,
        primary_color=body.primary_color,
        secondary_color=body.secondary_color,
        caption_style=body.caption_style,
    )
    session.add(t)
    await session.commit()
    await session.refresh(t)
    return _to_dict(t)


@router.get("/{template_id}")
async def get_template(template_id: str, session: AsyncSession = Depends(get_session)):
    from sqlalchemy import select
    result = await session.execute(
        select(BrandTemplate).where(BrandTemplate.id == template_id)
    )
    t = result.scalar_one_or_none()
    if t is None:
        raise HTTPException(status_code=404, detail="template not found")
    return _to_dict(t)


@router.put("/{template_id}")
async def update_template(
    template_id: str,
    body: BrandTemplateUpdate,
    session: AsyncSession = Depends(get_session),
):
    from sqlalchemy import select
    result = await session.execute(
        select(BrandTemplate).where(BrandTemplate.id == template_id)
    )
    t = result.scalar_one_or_none()
    if t is None:
        raise HTTPException(status_code=404, detail="template not found")
    if body.name is not None:
        t.name = body.name
    if body.primary_color is not None:
        t.primary_color = body.primary_color
    if body.secondary_color is not None:
        t.secondary_color = body.secondary_color
    if body.caption_style is not None:
        t.caption_style = body.caption_style
    await session.commit()
    await session.refresh(t)
    return _to_dict(t)


@router.delete("/{template_id}", status_code=204)
async def delete_template(template_id: str, session: AsyncSession = Depends(get_session)):
    from sqlalchemy import select
    result = await session.execute(
        select(BrandTemplate).where(BrandTemplate.id == template_id)
    )
    t = result.scalar_one_or_none()
    if t is None:
        raise HTTPException(status_code=404, detail="template not found")
    await session.delete(t)
    await session.commit()


@router.post("/{template_id}/assets")
async def upload_asset(
    template_id: str,
    asset_type: str,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
):
    if asset_type not in _ALLOWED_MIME:
        raise HTTPException(status_code=400, detail=f"Unknown asset_type '{asset_type}'")

    content_type = file.content_type or mimetypes.guess_type(file.filename or "")[0] or ""
    allowed = _ALLOWED_MIME[asset_type]
    if content_type not in allowed:
        raise HTTPException(
            status_code=415,
            detail=f"'{content_type}' not allowed for {asset_type}. Allowed: {sorted(allowed)}",
        )

    from sqlalchemy import select
    result = await session.execute(
        select(BrandTemplate).where(BrandTemplate.id == template_id)
    )
    t = result.scalar_one_or_none()
    if t is None:
        raise HTTPException(status_code=404, detail="template not found")

    dest_dir = _BRAND_ASSETS_ROOT / template_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(file.filename or "asset").suffix or ".bin"
    dest = dest_dir / f"{asset_type}{ext}"

    content = await file.read()
    dest.write_bytes(content)

    if asset_type == "logo":
        t.logo_path = str(dest)
    elif asset_type == "font":
        t.font_path = str(dest)
    elif asset_type == "intro":
        t.intro_clip_path = str(dest)
    elif asset_type == "outro":
        t.outro_clip_path = str(dest)

    await session.commit()
    return {"asset_type": asset_type, "path": str(dest)}
