"""Unit tests for W3.8 auth stubs + W3.9 capabilities."""
from __future__ import annotations

import pytest

from app.services import capabilities as caps


def test_capabilities_for_local_returns_business():
    c = caps.capabilities_for("local")
    assert c.tier == "business"
    assert c.animated_captions is True
    assert c.publish_youtube is True
    assert c.api_tokens is True


def test_capability_lookup_known():
    assert caps.capability("local", "voiceover") is True
    assert caps.capability("local", "tier") == "business"


def test_capability_lookup_unknown_raises():
    with pytest.raises(KeyError):
        caps.capability("local", "definitely_not_a_capability")


def test_as_dict_round_trip():
    d = caps.as_dict("local")
    assert d["tier"] == "business"
    assert d["analytics"] is True
    # Limits are None in this tier (unbounded).
    assert d["max_clips_per_export"] is None


# auth stubs need an in-process FastAPI dep test, but the simple shape
# (auth_disabled -> 'local'; auth_enabled + valid token -> workspace_id)
# is exercised through api_token_service tests in the W3.5-7 bundle.
async def test_current_user_id_disabled_returns_local(monkeypatch):
    monkeypatch.setattr("app.settings.settings.auth_enabled", False)
    from app.auth import current_user_id

    # The dep is an async function; calling it as a plain coroutine should
    # short-circuit on the auth_enabled check before touching credentials.
    result = await current_user_id(credentials=None, session=None)  # type: ignore[arg-type]
    assert result == "local"
