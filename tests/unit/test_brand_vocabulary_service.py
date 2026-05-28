"""Unit tests for brand_vocabulary_service (T-02 parity).

Covers the case-preserving replacement engine in apply_vocabulary:
  - ALL-CAPS source token
  - Titlecase source token
  - lowercase source token
  - mixed-case-internal source token (neither all-caps nor title nor lower)
  - single-character source token
  - word-boundary safety (no partial-word replacement)
  - multi-token round-trip with the canonical OpusClip→ReelSmith mapping
  - empty vocabulary is a pass-through
  - empty source string returns empty string without raising
"""
from __future__ import annotations

import asyncio

import pytest

from app.bus.event_bus import AsyncEventBus
from app.domain.events import EventType
from app.services.brand_vocabulary_service import apply_vocabulary


# ---------------------------------------------------------------------------
# Parametrised: case-preserving replacement
# Each tuple: (input_text, vocabulary, expected_output, test_id)
# ---------------------------------------------------------------------------

CASE_PRESERVATION_CASES = [
    # ALL-CAPS source token
    (
        "OPUSCLIP is here",
        {"OpusClip": "ReelSmith"},
        "REELSMITH is here",
        "all_caps",
    ),
    # Titlecase source token
    (
        "Opusclip is here",
        {"OpusClip": "ReelSmith"},
        "Reelsmith is here",
        "titlecase",
    ),
    # lowercase source token
    (
        "opusclip is here",
        {"OpusClip": "ReelSmith"},
        "reelsmith is here",
        "lowercase",
    ),
    # mixed-case-internal (not ALL-CAPS, not Titlecase, not lowercase)
    # e.g. "oPuScLiP" — falls through to raw replacement
    (
        "oPuScLiP is here",
        {"OpusClip": "ReelSmith"},
        "ReelSmith is here",
        "mixed_case_internal",
    ),
    # single-character source token — ALL-CAPS variant
    (
        "A good day",
        {"a": "one"},
        "ONE good day",
        "single_char_all_caps",
    ),
    # single-character source token — lowercase variant
    (
        "a good day",
        {"a": "one"},
        "one good day",
        "single_char_lowercase",
    ),
    # word-boundary safety: "OpusClip" inside "OpusClipper" must NOT be replaced
    (
        "OpusClipper is not OpusClip",
        {"OpusClip": "ReelSmith"},
        "OpusClipper is not ReelSmith",
        "word_boundary_safety",
    ),
]


@pytest.mark.parametrize(
    "text,vocabulary,expected,case_id",
    CASE_PRESERVATION_CASES,
    ids=[c[3] for c in CASE_PRESERVATION_CASES],
)
def test_apply_vocabulary_case_preservation(text, vocabulary, expected, case_id):
    assert apply_vocabulary(text, vocabulary) == expected


# ---------------------------------------------------------------------------
# Canonical multi-token round-trip: OpusClip → ReelSmith
# ---------------------------------------------------------------------------

def test_apply_vocabulary_multi_token_round_trip():
    """Mapping {"OpusClip": "ReelSmith"} applied to a sentence with three
    different case variants must produce case-preserved replacements per token.
    """
    input_text = "OpusClip is great, OPUSCLIP rocks, opusclip rules"
    vocab = {"OpusClip": "ReelSmith"}
    result = apply_vocabulary(input_text, vocab)
    assert result == "ReelSmith is great, REELSMITH rocks, reelsmith rules"


# ---------------------------------------------------------------------------
# Empty vocabulary → pass-through
# ---------------------------------------------------------------------------

def test_apply_vocabulary_empty_mapping_passthrough():
    text = "OpusClip is great"
    assert apply_vocabulary(text, {}) == text
    assert apply_vocabulary(text, None) == text


# ---------------------------------------------------------------------------
# Empty source string → empty string, no exception
# ---------------------------------------------------------------------------

def test_apply_vocabulary_empty_source_returns_empty():
    result = apply_vocabulary("", {"OpusClip": "ReelSmith"})
    assert result == ""


def test_apply_vocabulary_empty_source_with_empty_vocab():
    result = apply_vocabulary("", {})
    assert result == ""


# ── Event-bus emit tests ─────────────────────────────────────────────────────


async def test_apply_vocabulary_emits_when_substitutions_happen():
    bus = AsyncEventBus()
    received = []

    async def collect():
        async for ev in bus.subscribe(types=[EventType.BRAND_VOCAB_APPLIED]):
            received.append(ev)
            return

    consumer = asyncio.create_task(collect())
    await asyncio.sleep(0)

    out = apply_vocabulary(
        "OpusClip rocks, OPUSCLIP rules",
        {"OpusClip": "ReelSmith"},
        bus=bus, job_id="job-bv",
    )
    await asyncio.wait_for(consumer, timeout=1.0)

    assert "ReelSmith" in out and "REELSMITH" in out
    assert received[0].type is EventType.BRAND_VOCAB_APPLIED
    assert received[0].job_id == "job-bv"
    assert received[0].payload["substitutions"] == 2
    assert received[0].payload["vocab_size"] == 1


async def test_apply_vocabulary_no_emit_when_no_match():
    bus = AsyncEventBus()
    received = []

    async def collect():
        async for ev in bus.subscribe(types=[EventType.BRAND_VOCAB_APPLIED]):
            received.append(ev)
            return

    consumer = asyncio.create_task(collect())
    await asyncio.sleep(0)

    out = apply_vocabulary(
        "no matches here", {"OpusClip": "ReelSmith"},
        bus=bus, job_id="job-bv",
    )
    await asyncio.sleep(0.05)
    assert out == "no matches here"
    assert received == []
    consumer.cancel()


def test_apply_vocabulary_without_bus_still_works():
    """Legacy callers keep working."""
    out = apply_vocabulary("OpusClip", {"OpusClip": "ReelSmith"})
    assert out == "ReelSmith"
