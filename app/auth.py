"""Single-user API key auth + W3 stubs."""
from __future__ import annotations

from fastapi import Depends, HTTPException, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.settings import settings

_bearer = HTTPBearer(auto_error=False)


async def require_api_key(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    token: str | None = Query(default=None),
) -> None:
    if not settings.require_auth:
        return
    key = (credentials.credentials if credentials else None) or token
    if not key or key != settings.api_key:
        raise HTTPException(status_code=401, detail="Unauthorized")


# ── W3.8 — multi-user auth stubs ────────────────────────────────────────────
#
# Single-tenant default: returns 'local' so existing routers and the
# default workspace_id keep working unchanged. When YTVIDEO_AUTH_ENABLED=true
# the dep tries to resolve a bearer token via the W3.6 api_token_service.


async def current_user_id(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: AsyncSession = Depends(get_session),
) -> str:
    if not getattr(settings, "auth_enabled", False):
        return "local"

    if credentials and credentials.credentials:
        from app.services import api_token_service as ats
        token = await ats.authenticate(session, credentials.credentials)
        if token is not None:
            return token.workspace_id

    raise HTTPException(status_code=401, detail="Unauthorized")


async def current_workspace_id(user_id: str = Depends(current_user_id)) -> str:
    """Until full multi-tenant lands, the user_id IS the workspace_id."""
    return user_id
