"""Unit tests for W3.5 webhook_dispatcher + W3.6 api_token_service."""
from __future__ import annotations

import json

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import ApiToken, Webhook, Workspace
from app.services import (
    api_token_service as ats,
    token_vault,
    webhook_dispatcher as wd,
)


@pytest.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture(autouse=True)
def _vault_key(monkeypatch):
    monkeypatch.setenv("YTVIDEO_OAUTH_ENCRYPT_KEY", Fernet.generate_key().decode())
    token_vault.reset_for_tests()
    yield
    token_vault.reset_for_tests()


# ── W3.5 webhook_dispatcher ────────────────────────────────────────────


def test_sign_payload_stable():
    s = wd.sign_payload("k", b"hello")
    assert isinstance(s, str)
    assert len(s) == 64  # sha256 hex
    # Same input -> same sig.
    assert wd.sign_payload("k", b"hello") == s
    # Different secret -> different sig.
    assert wd.sign_payload("z", b"hello") != s


async def test_select_subscribed_filters_by_event(factory):
    async with factory() as session:
        session.add_all([
            Webhook(url="https://h/all", events=["*"],
                    secret_enc=token_vault.encrypt("s")),
            Webhook(url="https://h/clip", events=["clip.published"],
                    secret_enc=token_vault.encrypt("s")),
            Webhook(url="https://h/job", events=["job.failed"],
                    secret_enc=token_vault.encrypt("s")),
            Webhook(url="https://h/inactive", events=["clip.published"],
                    secret_enc=token_vault.encrypt("s"), active=False),
        ])
        await session.commit()

        subs = await wd.select_subscribed(session, "clip.published")
        urls = sorted(w.url for w in subs)
        assert urls == ["https://h/all", "https://h/clip"]


async def test_deliver_happy_path(factory):
    async with factory() as session:
        wh = Webhook(url="https://hooks.test/abc",
                     events=["*"],
                     secret_enc=token_vault.encrypt("hush"))
        session.add(wh)
        await session.commit()
        wh_id = wh.id

    captured: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(req)
        return httpx.Response(200)

    async with factory() as session:
        from sqlalchemy import select as _s
        wh = (await session.execute(_s(Webhook).where(Webhook.id == wh_id))).scalar_one()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await wd.deliver(wh, "clip.published", {"clip_id": "c1"}, http=http)

    assert result.status_code == 200
    assert result.attempts == 1
    body = json.loads(captured[0].content)
    assert body == {"type": "clip.published", "data": {"clip_id": "c1"}}
    assert captured[0].headers["x-reelsmith-event"] == "clip.published"
    assert captured[0].headers["x-reelsmith-signature"].startswith("sha256=")


async def test_deliver_retries_on_5xx(factory):
    async with factory() as session:
        wh = Webhook(url="https://hooks.test/abc",
                     events=["*"],
                     secret_enc=token_vault.encrypt("hush"))
        session.add(wh)
        await session.commit()
        wh_id = wh.id

    n = 0
    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal n
        n += 1
        if n < 3:
            return httpx.Response(503)
        return httpx.Response(200)

    async with factory() as session:
        from sqlalchemy import select as _s
        wh = (await session.execute(_s(Webhook).where(Webhook.id == wh_id))).scalar_one()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await wd.deliver(wh, "x", {}, http=http, max_retries=3)
    assert result.status_code == 200
    assert result.attempts == 3


async def test_deliver_no_retry_on_4xx(factory):
    async with factory() as session:
        wh = Webhook(url="https://hooks.test/abc", events=["*"],
                     secret_enc=token_vault.encrypt("hush"))
        session.add(wh)
        await session.commit()
        wh_id = wh.id

    n = 0
    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal n
        n += 1
        return httpx.Response(403)

    async with factory() as session:
        from sqlalchemy import select as _s
        wh = (await session.execute(_s(Webhook).where(Webhook.id == wh_id))).scalar_one()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await wd.deliver(wh, "x", {}, http=http, max_retries=5)
    assert result.status_code == 403
    assert result.attempts == 1
    assert "non-retryable" in (result.error or "")
    assert n == 1


# ── W3.6 api_token_service ─────────────────────────────────────────────


def test_mint_token_shape():
    full, prefix = ats.mint_token()
    assert prefix == "rs_"
    assert full.startswith("rs_")
    assert len(full) > 10


def test_hash_and_verify_round_trip():
    full, _ = ats.mint_token()
    h = ats.hash_token(full)
    assert ats.verify_token(full, h) is True
    assert ats.verify_token(full + "x", h) is False


def test_verify_token_handles_garbage():
    assert ats.verify_token("anything", "not-a-bcrypt-hash") is False


async def test_create_and_authenticate(factory):
    async with factory() as session:
        ws = Workspace(id="ws1", name="ws1")
        session.add(ws)
        await session.commit()
        plain, row = await ats.create_token(session, name="ci", workspace_id="ws1")
    assert plain.startswith("rs_")

    async with factory() as session:
        found = await ats.authenticate(session, plain)
    assert found is not None
    assert found.id == row.id
    assert found.last_used_at is not None


async def test_authenticate_unknown(factory):
    async with factory() as session:
        result = await ats.authenticate(session, "rs_does-not-exist")
    assert result is None


async def test_authenticate_revoked(factory):
    async with factory() as session:
        ws = Workspace(id="ws-rev", name="ws-rev")
        session.add(ws)
        await session.commit()
        plain, row = await ats.create_token(session, name="rev", workspace_id="ws-rev")
        await ats.revoke(session, row.id)

    async with factory() as session:
        result = await ats.authenticate(session, plain)
    assert result is None
