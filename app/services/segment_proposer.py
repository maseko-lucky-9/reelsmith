"""Clip segment proposer with heuristic virality scoring.

``LocalHeuristicProposer`` needs only numpy and the standard library:

* audio energy is RMS per 0.1 s frame, read from the extracted wav with ``wave``;
* speech ratio is the share of a window covered by word timings (dead-air
  windows are dropped);
* windows are snapped to word boundaries, preferring sentence ends
  (punctuation, or a pause of at least ``SENTENCE_GAP_SECS``);
* a prompt-overlap feature scores how many of the user's prompt terms a
  window mentions.

vaderSentiment (emotion) and spaCy (entity boost for "value") are optional:
each is imported once, lazily, and cached. When a weighted feature cannot be
computed it is left out of ``score_breakdown`` and the remaining weights are
re-normalised, so scores stay on the same 0-100 scale.
"""

from __future__ import annotations

import importlib
import json
import logging
import re
import wave
from bisect import bisect_left
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, NamedTuple, Protocol, Sequence, runtime_checkable

import numpy as np

log = logging.getLogger(__name__)

_TRENDS_PATH = Path(__file__).parents[2] / "data" / "trends.json"
_TRENDS: list[str] = []

FULL_VIDEO_TITLE = "Full Video"
SPEECH_RATIO_MIN = 0.4
SENTENCE_GAP_SECS = 0.6
HOOK_SECS = 3.0
NEUTRAL_ENERGY = 0.5
_ENERGY_HOP_SECS = 0.1

# Weights for features that ``settings.score_weights`` does not carry. A key in
# the configured weights overrides these. "speech" is reported, not weighted.
EXTRA_WEIGHTS: dict[str, float] = {"prompt": 0.30, "speech": 0.0}

_SENTENCE_END_RE = re.compile(r"[.!?][\"')\]]*$")
_TOKEN_RE = re.compile(r"[a-z0-9']+")
_STOPWORDS = frozenset(
    "a an and are as at be but by for from has have i in is it its of on or "
    "so that the their them they this to was we were what with you your".split()
)

# Optional NLP models: name -> loaded object, or None when unavailable.
_OPTIONAL_CACHE: dict[str, Any] = {}


def _load_trends() -> list[str]:
    global _TRENDS
    if not _TRENDS and _TRENDS_PATH.exists():
        _TRENDS = json.loads(_TRENDS_PATH.read_text())
    return _TRENDS


def _optional(name: str, loader: Callable[[], Any]) -> Any:
    """Return ``loader()``'s result, computed once; ``None`` if it ever failed."""
    if name not in _OPTIONAL_CACHE:
        try:
            _OPTIONAL_CACHE[name] = loader()
        except Exception as e:  # noqa: BLE001 — optional extra, any failure means "absent"
            log.info("Optional %s unavailable (%s); feature skipped", name, e)
            _OPTIONAL_CACHE[name] = None
    return _OPTIONAL_CACHE[name]


def _vader() -> Any:
    return _optional(
        "vader",
        lambda: importlib.import_module(
            "vaderSentiment.vaderSentiment"
        ).SentimentIntensityAnalyzer(),
    )


def _spacy_nlp() -> Any:
    return _optional(
        "spacy", lambda: importlib.import_module("spacy").load("en_core_web_sm")
    )


@dataclass
class ProposedSegment:
    start: float
    end: float
    title: str = ""
    summary: str = ""
    score: int = 0
    score_breakdown: dict[str, float] = field(default_factory=dict)
    # Full transcript of the window (``summary`` is cut at 200 characters);
    # clip discovery compares it across clips to skip near-duplicates.
    text: str = ""

    @property
    def virality_score(self) -> int:
        """The 0-100 score under the name clips store it as."""
        return self.score


@runtime_checkable
class SegmentProposerProtocol(Protocol):
    def propose(
        self,
        word_timings: list[Any],
        audio_path: str | None,
        chapters: list[dict[str, Any]],
        duration: float,
        *,
        prompt: str | None = None,
    ) -> list[ProposedSegment]: ...


class StubProposer:
    def propose(
        self, word_timings, audio_path, chapters, duration, *, prompt=None
    ) -> list[ProposedSegment]:
        return [
            ProposedSegment(
                start=0.0, end=min(30.0, duration), title="Stub Clip", score=42
            )
        ]


class _Word(NamedTuple):
    text: str
    start: float
    end: float


Energy = tuple[np.ndarray, np.ndarray]  # (frame start times, frame RMS)


class LocalHeuristicProposer:
    """Scores word-snapped candidate windows with local heuristic features."""

    def __init__(
        self, weights: dict[str, float], min_secs: int = 20, max_secs: int = 60
    ) -> None:
        self.weights = weights
        self.min_secs = min_secs
        self.max_secs = max_secs

    # ── public ────────────────────────────────────────────────────────────────

    def propose(
        self,
        word_timings: list[Any],
        audio_path: str | None,
        chapters: list[dict[str, Any]],
        duration: float,
        *,
        prompt: str | None = None,
    ) -> list[ProposedSegment]:
        """Return scored candidate segments, best first (ties: earliest start).

        Candidates overlap; use :func:`select_segments` to pick clips.
        """
        if duration <= 0:
            return []
        words = _clean_words(word_timings, duration)
        energy = _load_energy(audio_path)

        starts = [w.start for w in words]
        has_transcript = bool(words)

        if duration < self.min_secs:
            seg = self._score_window(
                words, has_transcript, energy, prompt, 0.0, float(duration)
            )
            seg.title = FULL_VIDEO_TITLE
            return [seg]

        results: list[ProposedSegment] = []
        for start, end in self._build_candidates(chapters, duration, words):
            nearby = _words_near(words, starts, start, end)
            if has_transcript and not _has_enough_speech(
                _speech_ratio(nearby, start, end)
            ):
                log.debug("Dropping dead-air window %.1f–%.1f", start, end)
                continue
            results.append(
                self._score_window(nearby, has_transcript, energy, prompt, start, end)
            )

        results.sort(key=lambda s: (-s.score, s.start, s.end))
        return results

    # ── private ───────────────────────────────────────────────────────────────

    def _score_window(
        self,
        nearby: Sequence[_Word],
        has_transcript: bool,
        energy: Energy | None,
        prompt: str | None,
        start: float,
        end: float,
    ) -> ProposedSegment:
        words_in = [w for w in nearby if start <= w.start < end]
        text = " ".join(w.text for w in words_in)

        breakdown: dict[str, float] = {
            "hook": self._hook_strength(text, words_in, energy, start, end),
            "value": self._perceived_value(text),
            "trend": self._trend_alignment(text, _load_trends()),
            "audio": self._audio_engagement(energy, start, end),
        }
        emotion = self._emotional_flow(text)
        if emotion is not None:
            breakdown["emotion"] = emotion
        overlap = _prompt_overlap(text, prompt)
        if overlap is not None:
            breakdown["prompt"] = overlap
        if has_transcript:
            breakdown["speech"] = _speech_ratio(nearby, start, end)

        breakdown = {k: round(v, 3) for k, v in breakdown.items()}
        return ProposedSegment(
            start=start,
            end=end,
            title=_extract_title(text),
            summary=text[:200],
            score=_combine_score(breakdown, {**EXTRA_WEIGHTS, **self.weights}),
            score_breakdown=breakdown,
            text=text,
        )

    def _build_candidates(
        self,
        chapters: list[dict[str, Any]],
        duration: float,
        words: Sequence[_Word] = (),
    ) -> list[tuple[float, float]]:
        candidates = []
        for ch in chapters:
            s = float(ch.get("start", ch.get("start_time", 0)))
            e = float(ch.get("end", ch.get("end_time", duration)))
            if e - s >= self.min_secs:
                candidates.append((s, min(e, s + self.max_secs)))
        if candidates:
            return candidates
        if words:
            return self._snapped_windows(words)
        step = self.max_secs
        t = 0.0
        while duration - t >= self.min_secs:
            candidates.append((t, min(t + step, duration)))
            t += step
        return candidates

    def _snapped_windows(self, words: Sequence[_Word]) -> list[tuple[float, float]]:
        """Windows of ``min_secs``..``max_secs`` whose edges sit on word edges.

        Windows start at sentence starts (plus a plain word start whenever no
        sentence has started for ``min_secs``) and end at the sentence end
        closest to the middle of the length range, or the closest word end if
        no sentence ends in range.
        """
        n = len(words)
        sentence_end = [
            i == n - 1
            or bool(_SENTENCE_END_RE.search(words[i].text))
            or words[i + 1].start - words[i].end >= SENTENCE_GAP_SECS
            for i in range(n)
        ]
        target = (self.min_secs + self.max_secs) / 2
        windows: list[tuple[float, float]] = []
        last_start = float("-inf")
        for i in range(n):
            sentence_start = i == 0 or sentence_end[i - 1]
            if not sentence_start and words[i].start - last_start < self.min_secs:
                continue
            last_start = s = words[i].start
            best: tuple[bool, float, int] | None = None
            for j in range(i, n):
                length = words[j].end - s
                if length > self.max_secs:
                    break
                if length < self.min_secs:
                    continue
                key = (not sentence_end[j], abs(length - target), j)
                if best is None or key < best:
                    best = key
            if best is not None:
                window = (s, words[best[2]].end)
                if not windows or windows[-1] != window:
                    windows.append(window)
        return windows

    def _hook_strength(
        self, text: str, words, energy: Energy | None, start: float, end: float
    ) -> float:
        first = [w for w in words if getattr(w, "start", 0) - start <= HOOK_SECS]
        first_text = " ".join(
            str(getattr(w, "text", getattr(w, "word", w))).strip() for w in first
        ).lower()

        score = 0.0
        patterns = [
            r"\?",
            r"^(how|why|what|when|who|never|always|stop|start|you need)",
            r"\d+",
        ]
        for p in patterns:
            if re.search(p, first_text):
                score += 1 / 3

        if energy is not None:
            opening = _relative_energy(energy, start, min(end, start + HOOK_SECS))
            score = (score + opening) / 2
        return min(1.0, score)

    def _emotional_flow(self, text: str) -> float | None:
        """Sentiment swing across sentences; ``None`` when vader is absent."""
        if not text.strip():
            return 0.0
        analyzer = _vader()
        if analyzer is None:
            return None
        sentences = [s.strip() for s in re.split(r"[.!?]", text) if len(s.strip()) > 3]
        if not sentences:
            return 0.0
        scores = [abs(analyzer.polarity_scores(s)["compound"]) for s in sentences]
        delta = abs(scores[-1] - scores[0]) if len(scores) > 1 else 0.0
        return min(1.0, (_variance(scores) + delta) / 2)

    def _perceived_value(self, text: str) -> float:
        tokens = _TOKEN_RE.findall(text.lower())
        if not tokens:
            return 0.0
        numbers = sum(1 for t in tokens if any(c.isdigit() for c in t))
        how_to = len(re.findall(r"\b(how|steps?|tips?|ways?|methods?)\b", text.lower()))
        entities = 0
        nlp = _spacy_nlp()
        if nlp is not None:
            entities = len(nlp(text[:500]).ents)
        return min(1.0, (entities + numbers * 2 + how_to * 3) / (len(tokens) * 0.3))

    def _trend_alignment(self, text: str, trends: list[str]) -> float:
        tl = text.lower()
        hits = sum(1 for t in trends if re.search(rf"\b{re.escape(t)}\b", tl))
        return min(1.0, hits / max(len(trends) * 0.1, 1))

    def _audio_engagement(
        self, energy: Energy | None, start: float, end: float
    ) -> float:
        if energy is None:
            return NEUTRAL_ENERGY
        return _relative_energy(energy, start, end)


# ── pure helpers ──────────────────────────────────────────────────────────────


def _field(item: Any, key: str, default: Any) -> Any:
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def _clean_words(word_timings: Sequence[Any], duration: float) -> list[_Word]:
    """Normalise word timings (``WordTiming`` objects or dicts) to sorted ``_Word``s.

    Text is stripped (faster-whisper prefixes a space), empty words and words
    starting at or after ``duration`` are dropped, and ends are clamped to it.
    """
    out = []
    for w in word_timings:
        text = str(_field(w, "word", "") or "").strip()
        start = float(_field(w, "start", 0.0) or 0.0)
        end = min(float(_field(w, "end", start) or start), duration)
        if not text or start >= duration:
            continue
        out.append(_Word(text, start, max(start, end)))
    out.sort(key=lambda w: (w.start, w.end))
    return out


def _words_near(
    words: Sequence[_Word], starts: Sequence[float], start: float, end: float
) -> Sequence[_Word]:
    """Words that may overlap ``[start, end)``: those starting inside it, plus
    the one word before it (which can run into the window)."""
    lo = max(0, bisect_left(starts, start) - 1)
    return words[lo : bisect_left(starts, end)]


def _speech_ratio(words: Sequence[_Word], start: float, end: float) -> float:
    """Fraction of ``[start, end]`` covered by the union of word intervals."""
    span = end - start
    if span <= 0:
        return 0.0
    covered = 0.0
    cur_s = cur_e = None
    for w in words:
        s, e = max(w.start, start), min(w.end, end)
        if e <= s:
            continue
        if cur_e is None or s > cur_e:
            if cur_e is not None:
                covered += cur_e - cur_s
            cur_s, cur_e = s, e
        else:
            cur_e = max(cur_e, e)
    if cur_e is not None:
        covered += cur_e - cur_s
    return min(1.0, covered / span)


def _has_enough_speech(ratio: float) -> bool:
    return ratio >= SPEECH_RATIO_MIN


def _load_energy(audio_path: str | None) -> Energy | None:
    """RMS per ``_ENERGY_HOP_SECS`` frame of a PCM wav; ``None`` if unreadable."""
    if not audio_path:
        return None
    try:
        with wave.open(str(audio_path), "rb") as wf:
            sr, channels, width = (
                wf.getframerate(),
                wf.getnchannels(),
                wf.getsampwidth(),
            )
            if width not in (1, 2, 4) or sr <= 0 or wf.getnframes() == 0:
                return None
            hop = max(1, int(sr * _ENERGY_HOP_SECS))
            parts: list[np.ndarray] = []
            while raw := wf.readframes(hop * 600):  # 60 s at a time
                x = _pcm_to_mono(raw, width, channels)
                full = (len(x) // hop) * hop
                if full:
                    parts.append(
                        np.sqrt(np.mean(x[:full].reshape(-1, hop) ** 2, axis=1))
                    )
                if len(x) > full:
                    parts.append(np.array([np.sqrt(np.mean(x[full:] ** 2))]))
    except (OSError, EOFError, wave.Error, ValueError) as e:
        log.info("Audio energy unavailable for %s (%s); using neutral", audio_path, e)
        return None
    if not parts:
        return None
    rms = np.concatenate(parts)
    return np.arange(rms.size) * (hop / sr), rms


def _pcm_to_mono(raw: bytes, width: int, channels: int) -> np.ndarray:
    if width == 1:
        x = (np.frombuffer(raw, dtype=np.uint8).astype(np.float64) - 128.0) / 128.0
    elif width == 2:
        x = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0
    else:
        x = np.frombuffer(raw, dtype="<i4").astype(np.float64) / 2147483648.0
    usable = (len(x) // channels) * channels
    return x[:usable].reshape(-1, channels).mean(axis=1)


def _relative_energy(energy: Energy, start: float, end: float) -> float:
    """Window mean RMS over twice the global mean: 0.5 is an average window."""
    times, rms = energy
    global_mean = float(rms.mean())
    mask = (times >= start) & (times < end)
    if global_mean <= 1e-12 or not mask.any():
        return NEUTRAL_ENERGY
    return min(1.0, float(rms[mask].mean()) / (2 * global_mean))


def _content_tokens(text: str) -> set[str]:
    tokens = set()
    for t in _TOKEN_RE.findall(text.lower()):
        t = t.strip("'")
        if len(t) < 2 or t in _STOPWORDS:
            continue
        tokens.add(t[:-1] if len(t) > 3 and t.endswith("s") else t)
    return tokens


def _prompt_overlap(text: str, prompt: str | None) -> float | None:
    """How on-prompt ``text`` is, 0..1; ``None`` without a prompt.

    The mean of *coverage* (share of the prompt's content terms present) and
    *focus* (share of the text's sentences that mention any prompt term), so a
    window that is about the prompt throughout beats one that touches it once.
    """
    if not prompt:
        return None
    wanted = _content_tokens(prompt)
    if not wanted:
        return None
    coverage = len(wanted & _content_tokens(text)) / len(wanted)
    sentences = [s for s in re.split(r"[.!?]+", text) if _content_tokens(s)]
    if not sentences:
        return 0.0
    focus = sum(1 for s in sentences if wanted & _content_tokens(s)) / len(sentences)
    return (coverage + focus) / 2


def _combine_score(breakdown: dict[str, float], weights: dict[str, float]) -> int:
    """Weighted mean of the present features, as an int 0-100."""
    total = sum(weights.get(k, 0.0) for k in breakdown)
    if total <= 0:
        return 0
    raw = sum(v * weights.get(k, 0.0) for k, v in breakdown.items()) / total
    return int(min(100, max(0, round(raw * 100))))


def _variance(vals: list[float]) -> float:
    if not vals:
        return 0.0
    mean = sum(vals) / len(vals)
    return sum((v - mean) ** 2 for v in vals) / len(vals)


def _extract_title(text: str) -> str:
    sentences = [s.strip() for s in re.split(r"[.!?]", text) if s.strip()]
    return sentences[0][:80] if sentences else text[:80]


def filter_word_timings(word_timings: list[Any], start: float, end: float) -> list[Any]:
    """Return word timings whose start falls within ``[start, end)``."""
    return [w for w in word_timings if start <= getattr(w, "start", 0) < end]


def select_segments(
    segments: list[ProposedSegment], max_clips: int, min_score: int
) -> list[ProposedSegment]:
    """Pick the best non-overlapping segments, returned in start-time order.

    Segments scoring below ``min_score`` are dropped (``score == min_score`` is
    kept). The rest are taken greedily by score (ties: earliest start), and a
    segment that overlaps one already picked is skipped. Touching segments
    (one's ``end`` equals the other's ``start``) do not overlap.
    """
    if max_clips <= 0:
        return []
    ranked = sorted(
        (s for s in segments if s.score >= min_score),
        key=lambda s: (-s.score, s.start, s.end),
    )
    picked: list[ProposedSegment] = []
    for seg in ranked:
        if len(picked) >= max_clips:
            break
        if any(seg.start < p.end and p.start < seg.end for p in picked):
            continue
        picked.append(seg)
    return sorted(picked, key=lambda s: (s.start, s.end))


def get_segment_proposer(
    *, min_secs: int | None = None, max_secs: int | None = None
) -> SegmentProposerProtocol:
    """The configured proposer; ``min_secs``/``max_secs`` override the
    ``target_clip_seconds_*`` settings (a job's clip length range)."""
    from app.settings import settings

    if settings.segment_provider == "local_heuristic":
        return LocalHeuristicProposer(
            weights=settings.score_weights_dict(),
            min_secs=min_secs or settings.target_clip_seconds_min,
            max_secs=max_secs or settings.target_clip_seconds_max,
        )
    # "stub", and "chapter" mode (proposer not used in that path)
    return StubProposer()
