# SPDX-License-Identifier: Apache-2.0
# Vendored from https://github.com/maseko-lucky-9/sentrysearch (sentrysearch/reranker.py)
# at commit 3694dd0cb27cc57920b2b816996ce5a22662e45c. See ../README.md and /VENDOR.md.
#
# MODIFIED for Reelsmith (Apache-2.0 section 4(b)): kept only the pure helpers
# (RerankScore, RERANK_SCHEMA, build_rerank_prompt, parse_rerank_response, _sort_key).
# Removed rerank_results() and its `.trimmer` import; the judge is an Ollama call
# made by Reelsmith code, not sentrysearch's torch reranker.
"""VLM reranking helpers for search results (pure parse helpers only)."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass


@dataclass(frozen=True)
class RerankScore:
    """Validated VLM rerank response."""

    rerank_match: bool
    rerank_confidence: float


RERANK_SCHEMA = {
    "type": "object",
    "properties": {
        "rerank_match": {"type": "boolean"},
        "rerank_confidence": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
        },
    },
    "required": ["rerank_match", "rerank_confidence"],
}


def build_rerank_prompt(query: str) -> str:
    return (
        "You are reranking video search candidates.\n"
        "Look at the clip and decide whether it visually matches this search "
        f"query: {query!r}\n\n"
        "Return JSON only with this exact shape:\n"
        '{"rerank_match": true, "rerank_confidence": 0.0}\n'
        "Use rerank_match=true only when the clip contains the requested event. "
        "Use rerank_confidence as your confidence from 0.0 to 1.0."
    )


def _strip_json_code_fence(text):
    if not isinstance(text, str):
        return text
    text = text.strip()
    if not text.startswith("```"):
        return text

    lines = text.splitlines()
    if len(lines) < 3 or lines[-1].strip() != "```":
        return text
    if lines[0].strip() not in ("```", "```json", "```JSON"):
        return text
    return "\n".join(lines[1:-1]).strip()


def parse_rerank_response(text: str) -> RerankScore | None:
    """Parse and validate a VLM rerank JSON response.

    Invalid model output returns ``None`` so callers can fall back to the
    candidate's embedding rank.
    """
    try:
        data = json.loads(_strip_json_code_fence(text))
    except (json.JSONDecodeError, TypeError):
        return None

    if not isinstance(data, dict):
        return None

    rerank_match = data.get("rerank_match")
    if not isinstance(rerank_match, bool):
        return None

    rerank_confidence = data.get("rerank_confidence")
    if isinstance(rerank_confidence, bool) or not isinstance(
        rerank_confidence, (int, float),
    ):
        return None

    rerank_confidence = float(rerank_confidence)
    if not math.isfinite(rerank_confidence):
        return None
    if rerank_confidence < 0.0 or rerank_confidence > 1.0:
        return None

    return RerankScore(
        rerank_match=rerank_match,
        rerank_confidence=rerank_confidence,
    )


def _sort_key(item: tuple[dict, int, RerankScore | None]) -> tuple:
    _result, original_rank, score = item
    if score is None:
        return (1, original_rank)
    if score.rerank_match:
        return (0, -score.rerank_confidence, original_rank)
    return (2, original_rank)
