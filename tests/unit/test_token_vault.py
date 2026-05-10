"""Unit tests for the Fernet token vault (W1.3)."""
from __future__ import annotations

import os

import pytest
from cryptography.fernet import Fernet


@pytest.fixture(autouse=True)
def _reset_vault():
    from app.services import token_vault
    token_vault.reset_for_tests()
    yield
    token_vault.reset_for_tests()


def test_round_trip_with_configured_key(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("YTVIDEO_OAUTH_ENCRYPT_KEY", key)
    from app.services import token_vault

    ct = token_vault.encrypt("hunter2")
    assert isinstance(ct, bytes) and ct != b"hunter2"
    assert token_vault.decrypt(ct) == "hunter2"


def test_bytes_input_round_trip(monkeypatch):
    """OAuth tokens are ASCII; encrypt accepts bytes for symmetry but
    decrypt returns str (utf-8 decoded), matching real-world usage."""
    monkeypatch.setenv("YTVIDEO_OAUTH_ENCRYPT_KEY", Fernet.generate_key().decode())
    from app.services import token_vault

    ct = token_vault.encrypt(b"ya29.bearer-token")
    assert token_vault.decrypt(ct) == "ya29.bearer-token"


def test_ephemeral_key_when_env_missing(monkeypatch):
    monkeypatch.delenv("YTVIDEO_OAUTH_ENCRYPT_KEY", raising=False)
    from app.services import token_vault

    ct = token_vault.encrypt("ephemeral")
    # Same process can decrypt it.
    assert token_vault.decrypt(ct) == "ephemeral"


def test_decrypt_invalid_ciphertext_raises(monkeypatch):
    monkeypatch.setenv("YTVIDEO_OAUTH_ENCRYPT_KEY", Fernet.generate_key().decode())
    from app.services import token_vault

    with pytest.raises(ValueError):
        token_vault.decrypt(b"not-a-valid-fernet-token")


def test_encrypt_none_raises(monkeypatch):
    monkeypatch.setenv("YTVIDEO_OAUTH_ENCRYPT_KEY", Fernet.generate_key().decode())
    from app.services import token_vault

    with pytest.raises(ValueError):
        token_vault.encrypt(None)  # type: ignore[arg-type]


def test_two_keys_produce_different_ciphertext(monkeypatch):
    """Sanity: rotating the key invalidates old ciphertexts."""
    from app.services import token_vault

    monkeypatch.setenv("YTVIDEO_OAUTH_ENCRYPT_KEY", Fernet.generate_key().decode())
    ct1 = token_vault.encrypt("rotate-me")

    token_vault.reset_for_tests()
    monkeypatch.setenv("YTVIDEO_OAUTH_ENCRYPT_KEY", Fernet.generate_key().decode())

    with pytest.raises(ValueError):
        token_vault.decrypt(ct1)
