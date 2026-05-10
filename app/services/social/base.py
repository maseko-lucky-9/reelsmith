"""Common contract for social-platform adapters (W1.5)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


class UnsupportedPlatformError(ValueError):
    """Raised when a platform name has no registered adapter."""


@dataclass(frozen=True)
class PublishRequest:
    platform: str
    account_handle: str
    clip_path: str
    title: str
    description: str
    hashtags: tuple[str, ...] = ()
    access_token: str = ""  # plaintext at the boundary; storage is encrypted
    # Stub-only: writes a JSON descriptor instead of POST'ing.
    stub_dir: str | None = None


@dataclass(frozen=True)
class PublishResult:
    external_post_id: str
    external_post_url: str | None = None


@runtime_checkable
class PlatformAdapter(Protocol):
    """Each adapter publishes to one platform.

    Implementations MUST be async, idempotent on the wire (network
    errors must not double-post), and return a stable external id
    to the caller.
    """

    platform: str

    async def publish(self, request: PublishRequest) -> PublishResult: ...
