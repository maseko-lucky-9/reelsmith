"""REST API tokens (W3.6).

Tokens are minted as ``rs_<16-byte secret>`` and stored as bcrypt
hashes — only the prefix is queryable. Verification uses bcrypt's
constant-time compare.

bcrypt is added at the requirements.txt boundary (see W3.6 PR
description). The pure-function ``hash_token`` and ``verify_token``
are testable in isolation; the persistence wrappers are also kept
small.
"""
from __future__ import annotations

import logging
import secrets
from datetime import datetime, timezone
from typing import Sequence

import bcrypt
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ApiToken

log = logging.getLogger(__name__)

DEFAULT_PREFIX = "rs_"


def mint_token(prefix: str = DEFAULT_PREFIX) -> tuple[str, str]:
    """Return (token_plaintext, token_prefix). The plaintext is shown to
    the user once; only the bcrypt hash + prefix are persisted."""
    secret = secrets.token_urlsafe(24)
    full = f"{prefix}{secret}"
    return full, prefix


def hash_token(plaintext: str) -> str:
    return bcrypt.hashpw(plaintext.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_token(plaintext: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plaintext.encode("utf-8"), hashed.encode("utf-8"))
    except (TypeError, ValueError):
        return False


async def create_token(
    session: AsyncSession,
    *,
    name: str,
    workspace_id: str = "local",
    prefix: str = DEFAULT_PREFIX,
) -> tuple[str, ApiToken]:
    plaintext, _ = mint_token(prefix)
    row = ApiToken(
        name=name,
        token_hash=hash_token(plaintext),
        token_prefix=prefix,
        workspace_id=workspace_id,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return plaintext, row


async def authenticate(
    session: AsyncSession, plaintext: str
) -> ApiToken | None:
    """Constant-time-ish lookup by prefix; verifies bcrypt for matches.

    Iterates all candidates with the same prefix; bcrypt's verify is
    O(work-factor) per candidate. For the ~tens of tokens this app
    will see, this is fine.
    """
    if not plaintext or "_" not in plaintext:
        return None
    prefix = plaintext.split("_", 1)[0] + "_"
    rows = (
        await session.execute(
            select(ApiToken)
            .where(ApiToken.token_prefix == prefix)
            .where(ApiToken.revoked.is_(False))
        )
    ).scalars().all()
    for row in rows:
        if verify_token(plaintext, row.token_hash):
            await session.execute(
                update(ApiToken)
                .where(ApiToken.id == row.id)
                .values(last_used_at=datetime.now(timezone.utc))
            )
            await session.commit()
            return row
    return None


async def revoke(session: AsyncSession, token_id: str) -> bool:
    res = await session.execute(
        update(ApiToken).where(ApiToken.id == token_id).values(revoked=True)
    )
    await session.commit()
    return (res.rowcount or 0) > 0
