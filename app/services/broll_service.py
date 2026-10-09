"""B-roll providers: where a planned insert's clip comes from (FR-010, T012).

``settings.broll_provider`` picks one (``get_broll_provider``):

* ``local``: ``*.mp4`` files in ``settings.broll_library_dir`` named by
  keyword. A query matches a file when one of its keywords equals a word of
  the file name (case-insensitive, a plural ``s`` folded), so
  ``ocean_waves.mp4`` answers "ocean" and "wave". Files are scanned in name
  order, so the pick is deterministic.
* ``pexels``: ``broll_pexels_service.fetch_asset`` (Pexels video search).
* ``none`` (default): no provider; the orchestrator skips the stage.

``fetch_all`` fetches the planner's queries at most ``FETCH_CONCURRENCY`` at a
time; a failed query, a miss or a file that is not a decodable video drops
that one insert. Cancellation propagates.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from app.services import ffmpeg_tools
from app.services.segment_proposer import _STOPWORDS, _TOKEN_RE
from app.settings import settings

log = logging.getLogger(__name__)

PROVIDERS = ("local", "pexels")
FETCH_CONCURRENCY = 2


@dataclass(frozen=True)
class BrollAsset:
    """A B-roll clip on local disk and where it came from (for credits)."""

    path: str
    provider: str
    asset_id: str
    author: str
    source_url: str


@runtime_checkable
class BRollProtocol(Protocol):
    def find_broll(self, text: str) -> str | None: ...


class NoBRoll:
    def find_broll(self, text: str) -> str | None:
        return None


def _fold(token: str) -> str:
    """Case- and plural-folded form used on both sides of a match."""
    token = token.strip("'").lower()
    if token.endswith("'s"):
        token = token[:-2]
    return token[:-1] if len(token) > 3 and token.endswith("s") else token


def _content_words(text: str) -> list[str]:
    """Lowercase content tokens of ``text`` in spoken order, without repeats."""
    seen: list[str] = []
    for raw in _TOKEN_RE.findall(text.lower()):
        token = raw.strip("'")
        if len(token) >= 2 and token not in _STOPWORDS and token not in seen:
            seen.append(token)
    return seen


def _keywords(phrases: Sequence[str]) -> list[str]:
    """Folded keywords of ``phrases`` in order, without repeats (folded once)."""
    seen: list[str] = []
    for phrase in phrases:
        for word in _content_words(phrase):
            folded = _fold(word)
            if folded not in seen:
                seen.append(folded)
    return seen


class LocalBRoll:
    """Matches keywords from text to file names in ``settings.broll_library_dir``."""

    def find_broll(self, text: str) -> str | None:
        keywords = _keywords(self._extract_noun_phrases(text))
        if not keywords:
            return None
        library = Path(settings.broll_library_dir)
        try:
            clips = sorted(
                p
                for p in library.iterdir()
                if p.is_file() and p.suffix.lower() == ".mp4"
            )
        except OSError:
            log.debug("No B-Roll library at %s", library)
            return None
        if not clips:
            log.debug("No B-Roll clips found in %s", library)
            return None

        names = [
            (clip, {_fold(t) for t in re.split(r"[^a-z0-9']+", clip.stem.lower()) if t})
            for clip in clips
        ]
        for keyword in keywords:
            for clip_path, words in names:
                if keyword in words:
                    return str(clip_path)
        return None

    def _extract_noun_phrases(self, text: str) -> list[str]:
        """The text's content words, lowercase, in spoken order."""
        return _content_words(text)


def get_broll_service() -> BRollProtocol:
    if settings.broll_provider == "local":
        return LocalBRoll()
    return NoBRoll()


# ── async providers for the pipeline ──────────────────────────────────────────


class BrollProvider(Protocol):
    name: str

    async def fetch(self, query: str) -> BrollAsset | None: ...


class LocalBrollProvider:
    name = "local"

    async def fetch(self, query: str) -> BrollAsset | None:
        path = await asyncio.to_thread(LocalBRoll().find_broll, query)
        if path is None:
            return None
        return BrollAsset(
            path=path,
            provider=self.name,
            asset_id=Path(path).name,
            author="",
            source_url="",
        )


class PexelsBrollProvider:
    name = "pexels"

    async def fetch(self, query: str) -> BrollAsset | None:
        from app.services import broll_pexels_service

        return await broll_pexels_service.fetch_asset(query)


def get_broll_provider(name: str) -> BrollProvider:
    """The provider called ``name``; ``ValueError`` for ``none`` or unknown."""
    if name == "local":
        return LocalBrollProvider()
    if name == "pexels":
        return PexelsBrollProvider()
    raise ValueError(f"no B-roll provider {name!r} (known: {', '.join(PROVIDERS)})")


def is_usable_video(path: str) -> bool:
    """True when ``path`` is a file whose first video frame decodes."""
    try:
        width, height = ffmpeg_tools.video_size(path)
    except Exception:  # noqa: BLE001 — any decode error means "not usable"
        return False
    return width > 0 and height > 0


async def fetch_all(
    provider: BrollProvider,
    queries: Sequence[str],
    *,
    concurrency: int = FETCH_CONCURRENCY,
) -> list[BrollAsset | None]:
    """One asset (or None) per query, in query order."""
    slots = asyncio.Semaphore(concurrency)

    async def _one(query: str) -> BrollAsset | None:
        async with slots:
            try:
                asset = await provider.fetch(query)
            except Exception as e:  # noqa: BLE001 — a failed query drops one insert
                log.warning(
                    "B-roll %s fetch for %r failed: %s", provider.name, query, e
                )
                return None
        if asset is None:
            log.info("No %s B-roll for %r", provider.name, query)
            return None
        if not await asyncio.to_thread(is_usable_video, asset.path):
            log.warning(
                "B-roll %s asset for %r is not a usable video: %s",
                provider.name,
                query,
                asset.path,
            )
            return None
        return asset

    return list(await asyncio.gather(*(_one(q) for q in queries)))
