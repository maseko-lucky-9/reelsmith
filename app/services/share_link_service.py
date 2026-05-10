"""HMAC-signed share links (W3.4).

Tokens encode (clip_id, expires_at) so the verifier doesn't need the
DB row to reject expired/forged tokens — but we still persist the
token in ``share_links`` so it can be revoked.

Token format:
    rs.<base64url(payload)>.<base64url(hmac256)>

Payload is JSON: {"c": clip_id, "e": exp_unix}.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ShareLink

DEFAULT_TTL_HOURS = 72


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    pad = "=" * ((4 - len(s) % 4) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _sign(payload_bytes: bytes, secret: str) -> str:
    sig = hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).digest()
    return _b64url(sig)


def _build_token(clip_id: str, expires_at: datetime, secret: str) -> str:
    payload = {"c": clip_id, "e": int(expires_at.timestamp())}
    pj = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return f"rs.{_b64url(pj)}.{_sign(pj, secret)}"


def _resolve_secret() -> str:
    import os
    secret = os.environ.get("YTVIDEO_SHARE_LINK_SECRET", "").strip()
    if not secret:
        # Dev/CI fallback: a stable per-process secret. Operators MUST set
        # the env var in production for tokens to survive restarts.
        global _DEV_SECRET
        try:
            return _DEV_SECRET
        except NameError:
            _DEV_SECRET = secrets.token_urlsafe(32)
            return _DEV_SECRET
    return secret


async def create_link(
    session: AsyncSession,
    clip_id: str,
    *,
    ttl_hours: int = DEFAULT_TTL_HOURS,
) -> ShareLink:
    expires_at = datetime.now(timezone.utc) + timedelta(hours=ttl_hours)
    token = _build_token(clip_id, expires_at, _resolve_secret())
    row = ShareLink(clip_id=clip_id, token=token, expires_at=expires_at)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


def verify_token(token: str) -> str | None:
    """Return clip_id if the token is well-formed, signature-valid, and
    not expired. Does NOT consult the DB — caller is responsible for
    checking the row's ``revoked`` flag separately."""
    try:
        prefix, payload_b64, sig_b64 = token.split(".", 2)
    except ValueError:
        return None
    if prefix != "rs":
        return None
    try:
        payload_bytes = _b64url_decode(payload_b64)
        expected = _sign(payload_bytes, _resolve_secret())
    except Exception:  # noqa: BLE001
        return None
    if not hmac.compare_digest(expected, sig_b64):
        return None
    try:
        payload = json.loads(payload_bytes)
    except json.JSONDecodeError:
        return None
    exp = payload.get("e", 0)
    if exp < datetime.now(timezone.utc).timestamp():
        return None
    return payload.get("c")


async def revoke(session: AsyncSession, token: str) -> bool:
    res = await session.execute(
        update(ShareLink).where(ShareLink.token == token).values(revoked=True)
    )
    await session.commit()
    return (res.rowcount or 0) > 0


async def is_revoked(session: AsyncSession, token: str) -> bool:
    row = (
        await session.execute(
            select(ShareLink).where(ShareLink.token == token)
        )
    ).scalar_one_or_none()
    return row is None or row.revoked
