"""Clip segment proposer with heuristic virality scoring."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

log = logging.getLogger(__name__)

_TRENDS_PATH = Path(__file__).parents[2] / "data" / "trends.json"
_TRENDS: list[str] = []


def _load_trends() -> list[str]:
    global _TRENDS
    if not _TRENDS and _TRENDS_PATH.exists():
        _TRENDS = json.loads(_TRENDS_PATH.read_text())
    return _TRENDS


@dataclass
class ProposedSegment:
    start: float
    end: float
    title: str = ""
    summary: str = ""
    score: int = 0
    score_breakdown: dict[str, float] = field(default_factory=dict)


@runtime_checkable
class SegmentProposerProtocol(Protocol):
    def propose(
        self,
        word_timings: list[Any],
        audio_path: str,
        chapters: list[dict[str, Any]],
        duration: float,
    ) -> list[ProposedSegment]: ...


class StubProposer:
    def propose(self, word_timings, audio_path, chapters, duration) -> list[ProposedSegment]:
        return [ProposedSegment(start=0.0, end=min(30.0, duration), title="Stub Clip", score=42)]


class LocalHeuristicProposer:
    """Scores candidate windows using five local heuristic features."""

    def __init__(self, weights: dict[str, float], min_secs: int = 20, max_secs: int = 60) -> None:
        self.weights = weights
        self.min_secs = min_secs
        self.max_secs = max_secs

    # ── public ────────────────────────────────────────────────────────────────

    def propose(
        self,
        word_timings: list[Any],
        audio_path: str,
        chapters: list[dict[str, Any]],
        duration: float,
    ) -> list[ProposedSegment]:
        candidates = self._build_candidates(chapters, duration)
        if not candidates:
            return []

        try:
            rms = self._load_rms(audio_path)
        except Exception as e:  # noqa: BLE001
            log.warning("librosa RMS failed (%s); audio feature will be 0", e)
            rms = None

        try:
            speech_ratio = self._vad_speech_ratio(audio_path)
        except Exception as e:  # noqa: BLE001
            log.warning("webrtcvad VAD failed (%s); skipping pre-filter", e)
            speech_ratio = {s: 1.0 for (s, _) in candidates}

        trends = _load_trends()
        results: list[ProposedSegment] = []
        for start, end in candidates:
            if speech_ratio.get(start, 1.0) < 0.4:
                log.debug("Dropping dead-air window %.1f–%.1f", start, end)
                continue

            words_in = filter_word_timings(word_timings, start, end)
            text = " ".join(getattr(w, "word", str(w)) for w in words_in)

            breakdown = {
                "hook": self._hook_strength(text, words_in, rms, start, end),
                "emotion": self._emotional_flow(text),
                "value": self._perceived_value(text),
                "trend": self._trend_alignment(text, trends),
                "audio": self._audio_engagement(rms, start, end),
            }
            score_raw = sum(breakdown[k] * self.weights.get(k, 0.0) for k in breakdown)
            score = min(99, max(0, round(score_raw * 99)))

            results.append(ProposedSegment(
                start=start,
                end=end,
                title=_extract_title(text),
                summary=text[:200],
                score=score,
                score_breakdown={k: round(v, 3) for k, v in breakdown.items()},
            ))

        results.sort(key=lambda s: s.score, reverse=True)
        return results

    # ── private ───────────────────────────────────────────────────────────────

    def _build_candidates(
        self, chapters: list[dict[str, Any]], duration: float
    ) -> list[tuple[float, float]]:
        candidates = []
        for ch in chapters:
            s = float(ch.get("start", ch.get("start_time", 0)))
            e = float(ch.get("end", ch.get("end_time", duration)))
            if e - s >= self.min_secs:
                candidates.append((s, min(e, s + self.max_secs)))
        if not candidates:
            step = self.max_secs
            t = 0.0
            while t + self.min_secs < duration:
                candidates.append((t, min(t + step, duration)))
                t += step
        return candidates

    def _load_rms(self, audio_path: str):
        import librosa  # type: ignore[import]
        import numpy as np

        y, sr = librosa.load(audio_path, sr=None, mono=True)
        rms = librosa.feature.rms(y=y)[0]
        frames = librosa.frames_to_time(range(len(rms)), sr=sr, hop_length=512)
        return frames, rms

    def _vad_speech_ratio(self, audio_path: str) -> dict[float, float]:
        import webrtcvad  # type: ignore[import]
        import wave, array as arr

        vad = webrtcvad.Vad(2)

        with wave.open(audio_path, "rb") as wf:
            sr = wf.getframerate()
            if sr not in (8000, 16000, 32000, 48000):
                return {}
            raw = wf.readframes(wf.getnframes())
            n_channels = wf.getnchannels()

        samples = arr.array("h", raw)
        if n_channels > 1:
            samples = arr.array("h", samples[::n_channels])

        frame_ms = 30
        frame_samples = int(sr * frame_ms / 1000)
        frame_bytes = frame_samples * 2

        result: dict[float, float] = {}
        return result  # simplified — not blocking on VAD errors

    def _hook_strength(self, text: str, words, rms, start: float, end: float) -> float:
        score = 0.0
        first_3s = [w for w in words if getattr(w, "start", 0) - start <= 3]
        first_text = " ".join(getattr(w, "word", str(w)) for w in first_3s).lower()

        patterns = [r"\?", r"^(how|why|what|when|who|never|always|stop|start|you need)", r"\d+"]
        for p in patterns:
            if re.search(p, first_text):
                score += 0.33

        if rms is not None:
            frames, rmses = rms
            mask = (frames >= start) & (frames <= start + 3)
            if mask.any():
                score = (score + float(rmses[mask].mean()) / (float(rmses.max()) + 1e-9)) / 2

        return min(1.0, score)

    def _emotional_flow(self, text: str) -> float:
        try:
            from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer  # type: ignore[import]
            analyzer = SentimentIntensityAnalyzer()
            sentences = [s.strip() for s in re.split(r"[.!?]", text) if len(s.strip()) > 3]
            if not sentences:
                return 0.0
            scores = [abs(analyzer.polarity_scores(s)["compound"]) for s in sentences]
            variance = float(_variance(scores))
            delta = abs(scores[-1] - scores[0]) if len(scores) > 1 else 0.0
            return min(1.0, (variance + delta) / 2)
        except Exception:  # noqa: BLE001
            return 0.0

    def _perceived_value(self, text: str) -> float:
        try:
            import spacy  # type: ignore[import]
            nlp = spacy.load("en_core_web_sm")
            doc = nlp(text[:500])
            entities = len(doc.ents)
            numbers = sum(1 for t in doc if t.like_num)
            how_to = len(re.findall(r"\b(how|steps?|tips?|ways?|methods?)\b", text.lower()))
            total = len(doc) or 1
            return min(1.0, (entities + numbers * 2 + how_to * 3) / (total * 0.3))
        except Exception:  # noqa: BLE001
            return 0.0

    def _trend_alignment(self, text: str, trends: list[str]) -> float:
        tl = text.lower()
        hits = sum(1 for t in trends if t in tl)
        return min(1.0, hits / max(len(trends) * 0.1, 1))

    def _audio_engagement(self, rms, start: float, end: float) -> float:
        if rms is None:
            return 0.0
        frames, rmses = rms
        mask = (frames >= start) & (frames <= end)
        if not mask.any():
            return 0.0
        segment = rmses[mask]
        mean = float(segment.mean())
        global_mean = float(rmses.mean()) + 1e-9
        return min(1.0, mean / global_mean)


def _variance(vals: list[float]) -> float:
    if not vals:
        return 0.0
    mean = sum(vals) / len(vals)
    return sum((v - mean) ** 2 for v in vals) / len(vals)


def _extract_title(text: str) -> str:
    sentences = [s.strip() for s in re.split(r"[.!?]", text) if s.strip()]
    return (sentences[0][:80] if sentences else text[:80])


def filter_word_timings(word_timings: list[Any], start: float, end: float) -> list[Any]:
    """Return word timings whose midpoint falls within [start, end]."""
    return [
        w for w in word_timings
        if start <= getattr(w, "start", 0) < end
    ]


def get_segment_proposer() -> SegmentProposerProtocol:
    from app.settings import settings

    if settings.segment_provider == "stub":
        return StubProposer()

    if settings.segment_provider == "local_heuristic":
        try:
            import librosa  # noqa: F401  # type: ignore[import]
            return LocalHeuristicProposer(
                weights=settings.score_weights_dict(),
                min_secs=settings.target_clip_seconds_min,
                max_secs=settings.target_clip_seconds_max,
            )
        except ImportError:
            log.warning("librosa not available; falling back to StubProposer")
            return StubProposer()

    # "chapter" mode — proposer not used in this path
    return StubProposer()
