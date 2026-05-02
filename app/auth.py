"""Single-user API key authentication."""
from __future__ import annotations

from fastapi import Depends, HTTPException, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

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
