"""Outbound webhook dispatcher (W3.5).

Subscribes to the event bus and POSTs each matching webhook with an
HMAC signature header. Retries on 5xx with a fixed retry budget.

Pure-function ``sign_payload`` is testable without httpx;
``deliver`` is the I/O wrapper that production wires to the bus.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
from dataclasses import dataclass
from typing import Sequence

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Webhook
from app.services import token_vault

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 10
DEFAULT_MAX_RETRIES = 3


@dataclass(frozen=True)
class DeliveryResult:
    webhook_id: str
    status_code: int
    attempts: int
    error: str | None = None


def sign_payload(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


async def select_subscribed(
    session: AsyncSession, event_type: str
) -> list[Webhook]:
    rows = (await session.execute(
        select(Webhook).where(Webhook.active.is_(True))
    )).scalars().all()
    # Filter in Python — events JSON contains either '*' or specific names.
    return [w for w in rows if "*" in (w.events or []) or event_type in (w.events or [])]


async def deliver(
    webhook: Webhook,
    event_type: str,
    payload: dict,
    *,
    http: httpx.AsyncClient | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> DeliveryResult:
    body = json.dumps({"type": event_type, "data": payload}).encode("utf-8")
    secret = token_vault.decrypt(webhook.secret_enc)
    sig = sign_payload(secret, body)
    headers = {
        "Content-Type": "application/json",
        "X-ReelSmith-Event": event_type,
        "X-ReelSmith-Signature": f"sha256={sig}",
    }

    client = http or httpx.AsyncClient(timeout=timeout_seconds)
    try:
        last_error: str | None = None
        last_status = 0
        for attempt in range(1, max_retries + 1):
            try:
                resp = await client.post(webhook.url, content=body, headers=headers)
                last_status = resp.status_code
                if 200 <= resp.status_code < 300:
                    return DeliveryResult(
                        webhook_id=webhook.id,
                        status_code=resp.status_code,
                        attempts=attempt,
                    )
                if resp.status_code < 500:
                    # 4xx is non-retryable.
                    return DeliveryResult(
                        webhook_id=webhook.id,
                        status_code=resp.status_code,
                        attempts=attempt,
                        error=f"non-retryable {resp.status_code}",
                    )
                last_error = f"upstream {resp.status_code}"
            except (httpx.HTTPError, httpx.TimeoutException) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
        return DeliveryResult(
            webhook_id=webhook.id,
            status_code=last_status,
            attempts=max_retries,
            error=last_error or "exhausted retries",
        )
    finally:
        if http is None:
            await client.aclose()
