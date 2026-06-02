"""TikTok cookie-session publish adapter (no official API, no OAuth).

Requires Node 18+ on PATH and the tiktok-signature bundle set up via
``scripts/tiktok-setup.sh``.  The module is safely importable when Node is
absent — the subprocess error surfaces only at publish time.

⚠️  Use a dedicated throwaway account.  Never share this adapter's account
    with the n8n sidecar — sessionid rotation by one client invalidates the
    other.  Keep rate to ≤3 posts/day to avoid soft-bans.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import pickle
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.services.social.base import PublishRequest, PublishResult

log = logging.getLogger(__name__)

# One upload at a time per process — the vendored lib writes a cookie file
# whose path is keyed to the session name.  A per-handle lock prevents two
# concurrent publishes for the same account from racing on that file.
_SEMAPHORE = asyncio.Semaphore(1)
_HANDLE_LOCKS: dict[str, asyncio.Lock] = {}
_LOCK_MAP_MUTEX = asyncio.Lock()

_CAPTION_HARD_CAP = 2200
_TIKTOK_ID_PREFIX = "tiktok_"


async def _handle_lock(handle: str) -> asyncio.Lock:
    async with _LOCK_MAP_MUTEX:
        if handle not in _HANDLE_LOCKS:
            _HANDLE_LOCKS[handle] = asyncio.Lock()
        return _HANDLE_LOCKS[handle]


def _sanitize_handle(handle: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]", "_", handle.lstrip("@"))[:60] or "default"


def _build_caption(description: str, hashtags: tuple[str, ...]) -> str:
    tags = " ".join(f"#{h.lstrip('#')}" for h in hashtags)
    caption = f"{description} {tags}".strip() if tags else description
    return caption[:_CAPTION_HARD_CAP]


def _safe_error(msg: str) -> RuntimeError:
    """Return a non-reflective RuntimeError that cannot contain credential text."""
    allowed = {
        "cookie expired — re-connect",
        "rate limited",
        "signature bundle missing — run scripts/tiktok-setup.sh",
        "malformed cookie blob",
        "upload rejected",
        "missing sessionid cookie",
    }
    safe = msg if msg in allowed else "upload rejected"
    return RuntimeError(f"tiktok: {safe}")


class TikTokCookieAdapter:
    """Publish reels to TikTok using a stored session cookie."""

    platform = "tiktok"

    def __init__(self, *, uploader: Any = None) -> None:
        self._uploader = uploader  # injectable for tests; None → real vendored lib

    async def publish(self, request: PublishRequest) -> PublishResult:
        if not request.access_token:
            raise _safe_error("malformed cookie blob")

        try:
            raw = json.loads(request.access_token)
        except (json.JSONDecodeError, TypeError):
            raise _safe_error("malformed cookie blob")

        # Accept two formats:
        #   list format  – [{"name": "sessionid", "value": "...", ...}, ...]
        #   dict format  – {"sessionid": "...", "tt-target-idc": "useast2a"}
        if isinstance(raw, dict):
            cookie_list: list[dict] = [
                {"name": k, "value": v, "domain": ".tiktok.com"}
                for k, v in raw.items()
            ]
        elif isinstance(raw, list):
            cookie_list = raw
        else:
            raise _safe_error("malformed cookie blob")

        clip_path = Path(request.clip_path)
        if not clip_path.is_file():
            raise FileNotFoundError(f"tiktok: clip not found: {clip_path}")

        # Validate sessionid is present; tt-target-idc defaults to useast2a if absent
        cookie_names = {c.get("name", "") for c in cookie_list}
        if "sessionid" not in cookie_names:
            raise _safe_error("cookie expired — re-connect")
        if "tt-target-idc" not in cookie_names:
            cookie_list.append({"name": "tt-target-idc", "value": "useast2a", "domain": ".tiktok.com"})

        handle = _sanitize_handle(request.account_handle)
        caption = _build_caption(request.description, request.hashtags)

        from app.settings import settings  # lazy import — keep module importable without settings loaded
        cookies_dir = Path(getattr(settings, "tiktok_cookies_dir", "data/tiktok-cookies"))
        cookies_dir.mkdir(parents=True, exist_ok=True)

        lock = await _handle_lock(handle)
        async with _SEMAPHORE, lock:
            cookie_file = cookies_dir / f"tiktok_session-{handle}.cookie"
            try:
                # Create owner-only atomically: use os.open so the mode is set
                # at creation time (no TOCTOU window between open and chmod).
                cookies_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
                fd = os.open(cookie_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "wb") as fh:
                    pickle.dump(cookie_list, fh)

                ok = await asyncio.to_thread(
                    self._upload, handle, str(clip_path), caption, str(cookies_dir)
                )
            finally:
                try:
                    cookie_file.unlink(missing_ok=True)
                except Exception:
                    pass

        if not ok:
            raise _safe_error("upload rejected")

        post_id = f"{_TIKTOK_ID_PREFIX}{handle}_{uuid4().hex[:12]}"
        profile_url = f"https://www.tiktok.com/@{request.account_handle.lstrip('@')}"
        return PublishResult(external_post_id=post_id, external_post_url=profile_url)

    def _upload(self, handle: str, clip_path: str, caption: str, cookies_dir: str) -> bool:
        """Blocking upload — run via asyncio.to_thread."""
        if self._uploader is not None:
            # Test seam: bypass the real vendored lib entirely
            return self._uploader.upload_video(handle, clip_path, caption, 0)

        # Lazy import — never at module level to keep collection safe with Node absent
        try:
            from app.vendor.tiktok_uploader.Config import Config
            from app.vendor.tiktok_uploader.tiktok import upload_video
        except ImportError as exc:
            raise _safe_error("signature bundle missing — run scripts/tiktok-setup.sh") from exc

        # Point the Config singleton at our temp cookies dir for this call
        cfg = Config.get()
        original_dir = getattr(cfg, "_options", {}).get("COOKIES_DIR", "./CookiesDir")
        try:
            cfg._options["COOKIES_DIR"] = cookies_dir
            try:
                ok = upload_video(handle, clip_path, caption, schedule_time=0)
            except FileNotFoundError as exc:
                if "node" in str(exc).lower():
                    raise _safe_error("signature bundle missing — run scripts/tiktok-setup.sh") from exc
                raise
            except SystemExit:
                # upload_video calls sys.exit(1) on missing sessionid — surface as upload failure
                return False
        finally:
            cfg._options["COOKIES_DIR"] = original_dir

        return bool(ok)
