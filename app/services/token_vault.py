"""Fernet-based encryption for OAuth tokens at rest (W1.3).

Reads the key from ``settings.oauth_encrypt_key`` (env
``YTVIDEO_OAUTH_ENCRYPT_KEY``). Auto-generates an in-process
ephemeral key when running tests / dev with no key configured —
tokens encrypted with the ephemeral key are NOT recoverable across
restarts, which is the right semantics for a missing key.

Plaintext is bytes/str at the API boundary; ciphertext is bytes
in the column ``access_token_enc`` / ``refresh_token_enc``.
"""
from __future__ import annotations

import logging
import os
import threading
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

log = logging.getLogger(__name__)

_LOCK = threading.Lock()
_EPHEMERAL_KEY: bytes | None = None


def _resolve_key() -> bytes:
    global _EPHEMERAL_KEY
    configured = os.environ.get("YTVIDEO_OAUTH_ENCRYPT_KEY", "").strip()
    if configured:
        return configured.encode("utf-8")

    # No key in env. Use a process-local ephemeral key so dev/CI keep working.
    # Persisted ciphertexts are unreadable after restart in this mode — by
    # design, since the operator hasn't supplied a stable key.
    with _LOCK:
        if _EPHEMERAL_KEY is None:
            _EPHEMERAL_KEY = Fernet.generate_key()
            log.warning(
                "YTVIDEO_OAUTH_ENCRYPT_KEY not set — using ephemeral key. "
                "Tokens will not survive process restarts."
            )
        return _EPHEMERAL_KEY


@lru_cache(maxsize=1)
def _fernet_from(key: bytes) -> Fernet:
    return Fernet(key)


def _fernet() -> Fernet:
    return _fernet_from(_resolve_key())


def encrypt(plaintext: str | bytes) -> bytes:
    if plaintext is None:
        raise ValueError("cannot encrypt None")
    if isinstance(plaintext, str):
        plaintext = plaintext.encode("utf-8")
    return _fernet().encrypt(plaintext)


def decrypt(ciphertext: bytes) -> str:
    if ciphertext is None:
        raise ValueError("cannot decrypt None")
    try:
        return _fernet().decrypt(ciphertext).decode("utf-8")
    except InvalidToken as e:  # pragma: no cover — operator misconfig
        raise ValueError("invalid OAuth token ciphertext") from e


def reset_for_tests() -> None:
    """Drop cached Fernet + ephemeral key. Tests only."""
    global _EPHEMERAL_KEY
    with _LOCK:
        _EPHEMERAL_KEY = None
    _fernet_from.cache_clear()
