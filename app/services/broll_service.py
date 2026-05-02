"""Local B-Roll service — matches noun phrases from transcript to local clip library."""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

log = logging.getLogger(__name__)

_BROLL_DIR = Path(__file__).parents[2] / "data" / "broll"


@runtime_checkable
class BRollProtocol(Protocol):
    def find_broll(self, text: str) -> str | None: ...


class NoBRoll:
    def find_broll(self, text: str) -> str | None:
        return None


class LocalBRoll:
    """Matches noun phrases from text to filenames in data/broll/."""

    def find_broll(self, text: str) -> str | None:
        noun_phrases = self._extract_noun_phrases(text)
        if not noun_phrases:
            return None

        clips = list(_BROLL_DIR.glob("*.mp4")) + list(_BROLL_DIR.glob("*.MP4"))
        if not clips:
            log.debug("No B-Roll clips found in %s", _BROLL_DIR)
            return None

        for phrase in noun_phrases:
            keywords = re.split(r"[\s_-]+", phrase.lower())
            for clip_path in clips:
                stem = clip_path.stem.lower()
                if any(kw in stem for kw in keywords):
                    return str(clip_path)

        return None

    def _extract_noun_phrases(self, text: str) -> list[str]:
        try:
            import spacy  # type: ignore[import]
            nlp = spacy.load("en_core_web_sm")
            doc = nlp(text[:500])
            return [chunk.text.lower() for chunk in doc.noun_chunks][:3]
        except Exception:  # noqa: BLE001
            words = re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b", text)
            return [w.lower() for w in words[:3]]


def get_broll_service() -> BRollProtocol:
    from app.settings import settings
    if settings.broll_provider == "local":
        return LocalBRoll()
    return NoBRoll()
