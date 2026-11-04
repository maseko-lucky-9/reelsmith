"""Find the moments of one source that match a prompt (spec 014, ADR-013).

Stateless and numpy-only: the API venv never imports MLX. The vectors come
from ``scripts/scene_index.py``, which runs in the sidecar venv
(``settings.scene_python``) as a killable child process:

* ``<scenes>/chunks.json`` + ``embeddings.npy``: one L2-normalised 768-d
  vector per 8 s chunk of the source (what is *seen*, on-screen text and
  faces included), written by ``index``;
* ``<scenes>/sentences.json`` + ``sentence_embeddings.npy``: one vector per
  transcript sentence, embedded as text by ``embed-texts`` (what is *said*:
  the embedding model does not hear, ADR-013);
* the prompt's vector, embedded with the same query instruction.

``<scenes>`` is ``<job folder>/scenes/`` and is reused by a reprompt; its
``meta.json`` records the source's size and mtime and the model, and any
change makes the next search index again.

Scoring (``search``):

1. ``visual[i]`` is the cosine of the prompt and chunk ``i``; ``speech[i]``
   is the best cosine of the prompt and a sentence that overlaps chunk ``i``.
2. Each side is calibrated on its own (``calibrate``): text-to-text cosines
   run higher than text-to-video ones, so a raw maximum would let speech win
   everywhere. ``(raw - floor) / (2 * (match - floor))``, clamped to 0..1, puts
   "unrelated" at 0 and "a typical labelled match" at 0.5 on both sides.
3. ``score[i] = max(visual_cal[i], speech_cal[i])``.
4. Chunks scoring at least ``HIT_THRESHOLD`` are hits; adjacent hits merge
   into one moment. Without any hit the ``FALLBACK_CHUNKS`` best chunks with
   a score above 0 are ``tentative`` candidates: the judge may confirm one,
   otherwise nothing matched and the job keeps its chapters.
5. Each moment becomes a window inside the clip length range: a long one is
   cut to ``max_s`` around its best chunk, a short one grows to ``min_s``
   around its centre; then its ends snap out to sentence boundaries when it
   has speech, or it is padded by ``PAD_SECONDS`` when it is silent.
6. Dedupe: a window that overlaps a better one is dropped, and so is a
   visual moment whose mean vector is a near copy (cosine above
   ``DEDUPE_COSINE``) of a better visual moment (sentrysearch
   ``_dedupe_indices``).
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import shutil
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from app.services import child_process
from app.services.segment_proposer import SENTENCE_GAP_SECS, ProposedSegment
from app.settings import settings

log = logging.getLogger(__name__)

PROVIDER_AUTO = "auto"
PROVIDER_MLX = "mlx"
PROVIDER_OFF = "off"

EMBED_DIMS = 768
CHUNK_SECONDS = 8
SCENES_DIRNAME = "scenes"
SCENE_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "scene_index.py"
"""The sidecar CLI. Module level so a test can point it at a fake child."""

INDEX_FORMAT = 1
"""Bumped when the index layout changes; an index of another format is rebuilt."""

# ── calibration ─────────────────────────────────────────────────────────────
# Medians measured on the ADR-013 labelled set (34 prompts, 6 sources, 101
# chunks, 141 transcript sentences; specs/014-ai-scene-search/plan.md).

VISUAL_FLOOR = 0.13
"""Cosine of a prompt and a chunk outside its labels (median of 705)."""
VISUAL_MATCH = 0.27
"""Cosine of a prompt and a chunk inside its labels (median of 64)."""
SPEECH_FLOOR = 0.45
"""Cosine of a speech prompt and a sentence outside its labels (median of 266)."""
SPEECH_MATCH = 0.58
"""Cosine of a speech prompt and a sentence inside its labels (median of 20)."""

HIT_THRESHOLD = 0.35
"""Calibrated score a chunk needs to be a hit. 0.35 is sentrysearch's MLX
start value; on the labelled set the top-1 hit rate is flat from 0.3 to 0.5
(0.88-0.91) and drops above it as moments vanish."""

NO_JUDGE_VISUAL = 0.35
"""Raw visual cosine a moment needs to become a clip when the judge did not
answer: above every unrelated chunk measured (max 0.347 of 705; p95 0.239,
p99 0.316). 6 of the 64 labelled chunks clear it."""
NO_JUDGE_SPEECH = 0.74
"""Raw speech cosine a moment needs to become a clip when the judge did not
answer: above every unrelated sentence measured (max 0.732 of 961; p95 0.549,
p99 0.627, speech prompts alone p95 0.613). 6 of the 20 labelled sentences
clear it. With both bars, 12 of the 34 labelled prompts still find a labelled
moment without a judge (visual 5/20, speech 6/9, emotion 1/5). A p95 bar was
not enough: a 10-minute talk has ~120 sentences, so ~6 unrelated ones would
clear it."""

FALLBACK_CHUNKS = 3
"""Without any hit, this many best chunks become single-chunk ``tentative``
candidates: something for the judge to look at, never clips on their own."""

MAX_MOMENTS = 20
"""Most candidates ``search`` returns, best first."""

PAD_SECONDS = 1.0
"""Padding on each side of a silent moment, room permitting."""

DEDUPE_COSINE = 0.95
"""Two visual moments whose mean vectors are closer than this show the same
thing; the lower-scoring one is dropped."""

MAX_SENTENCE_SECONDS = 15.0
"""A sentence is cut here when the transcript has no punctuation or pause."""

MAX_SENTENCE_CHARS = 300
"""Most characters of one sentence sent to the embedder."""

_SENTENCE_END_RE = re.compile(r"[.!?…][\"')\]]*$")


class SceneSearchError(RuntimeError):
    """Scene search could not run (no index, sidecar failure); callers fall back."""


# ── provider ──────────────────────────────────────────────────────────────────


def enabled(prompt: str | None) -> bool:
    """Whether a request with ``prompt`` uses scene search (``settings``).

    Needs a non-blank prompt. ``auto`` also needs ``scene_python`` to exist;
    ``mlx`` does not (a missing sidecar then fails the search, which falls
    back). Any other value is off.
    """
    if not prompt or not prompt.strip():
        return False
    provider = settings.scene_search_provider
    if provider == PROVIDER_MLX:
        return True
    if provider == PROVIDER_AUTO:
        return Path(settings.scene_python).is_file()
    if provider != PROVIDER_OFF:
        log.warning("Unknown YTVIDEO_SCENE_SEARCH_PROVIDER %r; scene search is off", provider)
    return False


# ── sentences ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Sentence:
    start: float
    end: float
    text: str


def _field(item: Any, key: str) -> Any:
    return item.get(key) if isinstance(item, dict) else getattr(item, key, None)


def sentences_from_words(words: Sequence[Any]) -> list[Sentence]:
    """Group word timings into sentences.

    A sentence ends after a word with closing punctuation, before a pause of
    at least ``SENTENCE_GAP_SECS``, or once it spans ``MAX_SENTENCE_SECONDS``.
    Accepts ``WordTiming`` objects or ``{word, start, end}`` dicts.
    """
    out: list[Sentence] = []
    current: list[tuple[str, float, float]] = []

    def _close() -> None:
        if current:
            text = " ".join(w for w, _, _ in current).strip()
            if text:
                out.append(Sentence(current[0][1], current[-1][2], text))
            current.clear()

    for item in words:
        word = str(_field(item, "word") or "").strip()
        if not word:
            continue
        start = float(_field(item, "start") or 0.0)
        end = max(start, float(_field(item, "end") or start))
        if current and (start - current[-1][2] >= SENTENCE_GAP_SECS or end - current[0][1] > MAX_SENTENCE_SECONDS):
            _close()
        current.append((word, start, end))
        if _SENTENCE_END_RE.search(word):
            _close()
    _close()
    return out


def sentence_texts(sentences: Sequence[Sentence]) -> list[str]:
    """What is embedded for each sentence: its text, cut to ``MAX_SENTENCE_CHARS``."""
    return [s.text[:MAX_SENTENCE_CHARS] for s in sentences]


# ── the index on disk ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SceneIndex:
    vectors: np.ndarray  # n x EMBED_DIMS, float32, rows L2-normalised
    spans: list[tuple[float, float]]


def scenes_dir(job_folder: str | Path) -> Path:
    return Path(job_folder) / SCENES_DIRNAME


def load_index(index_dir: Path) -> SceneIndex:
    """The index in ``index_dir``; ``SceneSearchError`` if missing, torn or
    not plain numbers (``allow_pickle=False``: a planted object array is an
    error, never code run on load)."""
    try:
        chunks = json.loads((index_dir / "chunks.json").read_text(encoding="utf-8"))
        rows = np.load(index_dir / "embeddings.npy", allow_pickle=False)
    except (OSError, ValueError, EOFError) as exc:
        raise SceneSearchError(f"no complete scene index in {index_dir}: {exc}") from exc
    if not isinstance(chunks, list) or rows.ndim != 2 or rows.shape[1] != EMBED_DIMS:
        raise SceneSearchError(f"{index_dir}: embeddings have shape {rows.shape}")
    if rows.shape[0] != len(chunks):
        raise SceneSearchError(f"{index_dir}: {rows.shape[0]} rows but {len(chunks)} chunks (torn index)")
    try:
        spans = [(float(c["start"]), float(c["end"])) for c in chunks]
    except (KeyError, TypeError, ValueError) as exc:
        raise SceneSearchError(f"{index_dir}/chunks.json is malformed: {exc}") from exc
    return SceneIndex(_finite(rows, index_dir / "embeddings.npy"), spans)


def _finite(rows: np.ndarray, source: Path | str) -> np.ndarray:
    """``rows`` as float32 with NaN and infinities set to 0 (that row then
    matches nothing), logged; the rest of the index stays usable."""
    if not np.issubdtype(rows.dtype, np.number):
        raise SceneSearchError(f"{source} does not hold numbers ({rows.dtype})")
    rows = rows.astype(np.float32, copy=False)
    if not np.isfinite(rows).all():
        log.warning("%s has non-finite values; those entries are treated as 0", source)
        rows = np.nan_to_num(rows, nan=0.0, posinf=0.0, neginf=0.0)
    return rows


def source_fingerprint(video_path: str | Path) -> dict[str, Any]:
    """What identifies the indexed source: its size and modification time."""
    stat = Path(video_path).stat()
    return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def _meta(video_path: str | Path, model: str) -> dict[str, Any]:
    return {
        "format": INDEX_FORMAT,
        "model": model,
        "chunk_seconds": CHUNK_SECONDS,
        "source": source_fingerprint(video_path),
    }


def index_is_current(index_dir: Path, video_path: str | Path, model: str) -> bool:
    """True when ``index_dir`` holds a complete index of this source, as it is
    now, by ``model`` (``meta.json`` matches and the index loads)."""
    try:
        meta = json.loads((index_dir / "meta.json").read_text(encoding="utf-8"))
        if meta != _meta(video_path, model):
            return False
        load_index(index_dir)
    except OSError, ValueError, EOFError, SceneSearchError:
        return False
    return True


def _write_json(path: Path, data: Any) -> None:
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _sidecar_argv(command: str, *positionals: str) -> list[str]:
    """``scene_index.py <command> --model M -- <positionals>``: the ``--`` keeps
    a path or text starting with ``-`` from being read as a flag (ADR-013)."""
    return [
        settings.scene_python,
        str(SCENE_SCRIPT),
        command,
        "--model",
        settings.scene_model,
        "--",
        *positionals,
    ]


_PROXY_NAMES = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY")
_ENV_NAMES = frozenset({
    "PATH", "HOME", "TMPDIR", "TMP", "TEMP", "LANG", "USER", "LOGNAME", "SHELL", "XDG_CACHE_HOME",
    # The model download behind a proxy or a private CA.
    *_PROXY_NAMES, *(name.lower() for name in _PROXY_NAMES),
    "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
})  # fmt: skip
_ENV_PREFIXES = ("LC_", "HF_", "TRANSFORMERS_", "HUGGINGFACE_", "MLX", "YTVIDEO_SCENE_")


def sidecar_env(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """The sidecar's environment: what it needs to run and find the model
    cache, never the app's secrets (no other ``YTVIDEO_*`` value, no tokens).
    Read from the process environment because the child inherits from it;
    configuration itself stays in ``settings`` (constitution I)."""
    source = os.environ if environ is None else environ
    return {k: v for k, v in source.items() if k in _ENV_NAMES or k.startswith(_ENV_PREFIXES)}


Progress = Callable[[int, int], Awaitable[None]]


async def _run_sidecar(argv: list[str], *, timeout: float, on_progress: Progress | None) -> None:
    async def _on_line(line: str) -> None:
        try:
            data = json.loads(line)
        except ValueError:
            log.debug("scene_index stdout: %s", line)
            return
        if on_progress is not None and isinstance(data, dict):
            done, total = data.get("done"), data.get("total")
            if isinstance(done, int) and isinstance(total, int):
                await on_progress(done, total)

    try:
        returncode, stderr = await child_process.run_streaming(
            argv, timeout=timeout, on_line=_on_line, env=sidecar_env()
        )
    except TimeoutError as exc:  # before OSError: TimeoutError is one
        raise SceneSearchError(f"the scene sidecar took longer than {timeout:g} s") from exc
    except OSError as exc:
        raise SceneSearchError(f"cannot start the scene sidecar {argv[0]!r}: {exc}") from exc
    except ValueError as exc:  # unreadable output; the child was stopped
        raise SceneSearchError(f"the scene sidecar's output was unreadable: {exc}") from exc
    if returncode != 0:
        last = stderr.strip().splitlines()[-1:] or [""]
        raise SceneSearchError(f"scene_index.py {argv[2]} exited {returncode}: {last[0]}")


async def ensure_index(
    video_path: str | Path,
    index_dir: Path,
    *,
    on_progress: Progress | None = None,
) -> tuple[SceneIndex, bool]:
    """The source's scene index and whether it was reused.

    Reuses ``index_dir`` when it is current (``index_is_current``); otherwise
    indexes into a fresh sibling directory (the sidecar's rule) and swaps it
    in only once it is complete, so a cancel or a failure leaves the old
    index, or none, never a half-written one. Raises ``SceneSearchError``.
    """
    model = settings.scene_model
    video = Path(video_path).resolve()
    if index_is_current(index_dir, video, model):
        return load_index(index_dir), True
    # A job indexes one source at a time (a reprompt never runs beside its
    # run), so a fresh directory still here was left by a crash.
    for stale in index_dir.parent.glob(f".{index_dir.name}-*"):
        shutil.rmtree(stale, ignore_errors=True)
    fresh = index_dir.with_name(f".{index_dir.name}-{uuid.uuid4().hex[:8]}")
    try:
        await _run_sidecar(
            _sidecar_argv("index", str(video), str(fresh)),
            timeout=settings.scene_index_timeout_seconds,
            on_progress=on_progress,
        )
        index = load_index(fresh)
        _write_json(fresh / "meta.json", _meta(video, model))
        shutil.rmtree(index_dir, ignore_errors=True)
        os.replace(fresh, index_dir)
    finally:
        shutil.rmtree(fresh, ignore_errors=True)
    return index, False


EMBED_SECONDS_PER_TEXT = 0.3
"""Budget per text for ``embed-texts``: measured about 0.12 s a sentence on an
idle M5 Pro and 2.4x that under load (spec 014 plan)."""

EMBED_MIN_TIMEOUT = 120.0
"""Floor of an ``embed-texts`` timeout: the model load alone takes 2-12 s."""

SENTENCE_BATCH = 200
"""Sentences per ``embed-texts`` call. Each finished batch is cached, so a
retry after a timeout or a cancel resumes instead of starting over; each
call reloads the model (2-5 s), so batches are not small."""


def embed_timeout(count: int) -> float:
    """Seconds allowed for embedding ``count`` texts in one call, at least
    ``EMBED_MIN_TIMEOUT`` and at most ``scene_index_timeout_seconds``."""
    return min(float(settings.scene_index_timeout_seconds), max(EMBED_MIN_TIMEOUT, EMBED_SECONDS_PER_TEXT * count))


async def embed_texts(texts: Sequence[str], work_dir: Path, *, timeout: float | None = None) -> np.ndarray:
    """Vectors (n x 768) for ``texts`` from the sidecar's ``embed-texts``;
    the timeout defaults to ``embed_timeout(len(texts))``."""
    work_dir.mkdir(parents=True, exist_ok=True)
    tag = uuid.uuid4().hex[:8]
    texts_path = work_dir / f".texts-{tag}.json"
    out = work_dir / f".texts-{tag}.npy"
    limit = embed_timeout(len(texts)) if timeout is None else timeout
    try:
        texts_path.write_text(json.dumps(list(texts)), encoding="utf-8")
        await _run_sidecar(_sidecar_argv("embed-texts", str(texts_path), str(out)), timeout=limit, on_progress=None)
        rows = np.load(out, allow_pickle=False)
    except (OSError, ValueError, EOFError) as exc:
        raise SceneSearchError(f"text embeddings unreadable: {exc}") from exc
    finally:
        texts_path.unlink(missing_ok=True)
        out.unlink(missing_ok=True)
    if rows.shape != (len(texts), EMBED_DIMS):
        raise SceneSearchError(f"text embeddings have shape {rows.shape}, expected ({len(texts)}, {EMBED_DIMS})")
    return _finite(rows, "embed-texts output")


def _load_sentence_cache(index_dir: Path, model: str) -> dict[str, np.ndarray]:
    """Cached vectors by sentence text (``sentences.json`` lists the texts,
    ``sentence_embeddings.npy`` holds at least as many rows, in order).

    Keyed by text, so a re-transcribed sentence never reuses another's
    vector. Anything unreadable, foreign or torn is an empty cache.
    """
    try:
        meta = json.loads((index_dir / "sentences.json").read_text(encoding="utf-8"))
        rows = np.load(index_dir / "sentence_embeddings.npy", allow_pickle=False)
        if not isinstance(meta, dict) or meta.get("model") != model:
            return {}
        texts = meta.get("texts")
        if not isinstance(texts, list) or rows.ndim != 2 or rows.shape[1] != EMBED_DIMS or rows.shape[0] < len(texts):
            return {}
        rows = _finite(rows, index_dir / "sentence_embeddings.npy")
    except (OSError, ValueError, EOFError, SceneSearchError) as exc:
        if not isinstance(exc, FileNotFoundError):
            log.warning("Ignoring the sentence-vector cache in %s: %s", index_dir, exc)
        return {}
    return {str(t): rows[i] for i, t in enumerate(texts)}


def _save_sentence_cache(index_dir: Path, model: str, cache: dict[str, np.ndarray]) -> None:
    """Rows first, then the texts they belong to: a crash between the two
    leaves extra rows, which the reader ignores."""
    texts = list(cache)
    npy_tmp = index_dir / f".sentence_embeddings-{uuid.uuid4().hex[:8]}.npy"
    try:
        rows = np.stack([cache[t] for t in texts]) if texts else np.zeros((0, EMBED_DIMS), np.float32)
        np.save(npy_tmp, rows.astype(np.float32), allow_pickle=False)
        os.replace(npy_tmp, index_dir / "sentence_embeddings.npy")
        _write_json(index_dir / "sentences.json", {"model": model, "texts": texts})
    except OSError as exc:
        log.warning("Could not cache sentence vectors in %s: %s", index_dir, exc)
    finally:
        npy_tmp.unlink(missing_ok=True)


async def prompt_and_sentence_vectors(
    index_dir: Path, prompt: str, sentences: Sequence[Sentence]
) -> tuple[np.ndarray, np.ndarray]:
    """The prompt's vector and one per sentence.

    Sentences already in the cache (by text) are not embedded again; the
    rest go in batches of ``SENTENCE_BATCH``, the prompt with the first, and
    each finished batch is cached at once. A reprompt of an unchanged
    transcript embeds only its prompt.
    """
    model = settings.scene_model
    texts = sentence_texts(sentences)
    cache = _load_sentence_cache(index_dir, model)
    missing = list(dict.fromkeys(t for t in texts if t not in cache))
    batches = [missing[i : i + SENTENCE_BATCH] for i in range(0, len(missing), SENTENCE_BATCH)] or [[]]
    prompt_vec: np.ndarray | None = None
    for batch in batches:
        head = [prompt] if prompt_vec is None else []
        rows = await embed_texts([*head, *batch], index_dir)
        if head:
            prompt_vec, rows = rows[0], rows[1:]
        if batch:
            cache.update(zip(batch, rows))
            _save_sentence_cache(index_dir, model, cache)
    assert prompt_vec is not None
    sentence_rows = np.stack([cache[t] for t in texts]) if texts else np.zeros((0, EMBED_DIMS), np.float32)
    return prompt_vec, sentence_rows


# ── search ───────────────────────────────────────────────────────────────────


def calibrate(raw: np.ndarray, floor: float, match: float) -> np.ndarray:
    """Map raw cosines so ``floor`` -> 0 and ``match`` -> 0.5, linearly,
    clamped to 0..1 (1 is a match as far above typical as typical is above
    unrelated). Not clamped at the match level, so strong matches still rank
    above typical ones."""
    return np.clip((np.asarray(raw, dtype=np.float32) - floor) / (2.0 * (match - floor)), 0.0, 1.0)


@dataclass(frozen=True)
class _Moment:
    first: int  # chunk indexes, inclusive
    last: int
    peak: int
    score: float
    visual: float
    speech: float

    @property
    def speech_led(self) -> bool:
        return self.speech > self.visual


def _merge_hits(scores: np.ndarray, threshold: float) -> list[tuple[int, int]]:
    """Runs of adjacent chunk indexes whose score is >= ``threshold``."""
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for i, score in enumerate(scores):
        if score >= threshold:
            if start is None:
                start = i
        elif start is not None:
            runs.append((start, i - 1))
            start = None
    if start is not None:
        runs.append((start, len(scores) - 1))
    return runs


def _sentence_at(sentences: Sequence[Sentence], t: float) -> Sentence | None:
    for s in sentences:
        if s.start <= t < s.end:
            return s
    return None


def shape_window(
    start: float,
    end: float,
    peak: float,
    *,
    sentences: Sequence[Sentence],
    min_s: float,
    max_s: float,
    duration: float,
) -> tuple[float, float]:
    """The clip window for a moment ``start``-``end`` whose best point is ``peak``.

    Cut to ``max_s`` around ``peak`` or grown to ``min_s`` around the
    centre, kept inside ``0..duration``; then each end snaps outwards to the
    sentence it falls in (inwards to a sentence boundary when outwards would
    pass ``max_s``), or, without any sentence in the window, both ends are
    padded by ``PAD_SECONDS`` up to ``max_s``.
    """
    duration = max(duration, 0.0)
    max_s = min(max_s, duration) if duration else max_s
    min_s = min(min_s, max_s)
    s, e = start, end
    if e - s > max_s:
        s = min(max(peak - max_s / 2, start), end - max_s)
        e = s + max_s
    if e - s < min_s:
        centre = (s + e) / 2
        s, e = centre - min_s / 2, centre + min_s / 2
    s, e = _inside(s, e, duration)

    spoken = [x for x in sentences if x.start < e and x.end > s]
    if spoken:
        snapped = _snap(s, e, sentences, min_s, max_s)
        # Snapping inwards may leave the best point outside; never lose it.
        if snapped[0] <= peak <= snapped[1]:
            s, e = snapped
    else:
        room = max(0.0, max_s - (e - s))
        pad = min(PAD_SECONDS, room / 2)
        s, e = s - pad, e + pad
    s, e = _inside(s, e, duration)
    return round(s, 3), round(e, 3)


def _inside(s: float, e: float, duration: float) -> tuple[float, float]:
    """Shift ``s``-``e`` into ``0..duration`` keeping its length when it fits."""
    length = e - s
    if s < 0:
        s, e = 0.0, length
    if duration and e > duration:
        e = duration
        s = max(0.0, duration - length)
    return s, e


def _snap(s: float, e: float, sentences: Sequence[Sentence], min_s: float, max_s: float) -> tuple[float, float]:
    head = _sentence_at(sentences, s)
    tail = _sentence_at(sentences, e - 1e-6)
    new_s = head.start if head else s
    new_e = tail.end if tail else e
    if new_e - new_s > max_s:
        # Outwards is too long: snap the end inwards to the last sentence end
        # inside the window, then the start inwards to the next sentence start.
        ends = [x.end for x in sentences if new_s < x.end <= e]
        if ends and max(ends) - new_s >= min_s:
            new_e = max(ends)
        if new_e - new_s > max_s:
            starts = [x.start for x in sentences if s <= x.start < new_e]
            new_s = min(starts) if starts else s
        if new_e - new_s > max_s or new_e - new_s < min_s:
            return s, e
    return new_s, new_e


def _text_between(sentences: Sequence[Sentence], s: float, e: float) -> str:
    return " ".join(x.text for x in sentences if x.start < e and x.end > s)


def _best_sentence(sentences: Sequence[Sentence], scores: np.ndarray, s: float, e: float) -> str:
    """The best-matching sentence that overlaps ``s``-``e``, cut to 80 characters."""
    inside = [j for j, x in enumerate(sentences) if x.start < e and x.end > s]
    if not inside:
        return ""
    return sentences[max(inside, key=lambda j: scores[j])].text[:80]


def search(
    index: SceneIndex,
    prompt_vec: np.ndarray,
    *,
    sentences: Sequence[Sentence] = (),
    sentence_vecs: np.ndarray | None = None,
    min_s: float,
    max_s: float,
    duration: float,
    threshold: float = HIT_THRESHOLD,
) -> list[ProposedSegment]:
    """Candidate clips for the prompt, best first (see the module docstring).

    ``score`` is the calibrated peak, 0-100; ``score_breakdown`` holds the
    calibrated ``visual`` and ``speech`` sides at the peak; ``text`` and
    ``summary`` the transcript of the window.
    """
    n = len(index.spans)
    if n == 0:
        return []
    query = np.asarray(prompt_vec, dtype=np.float32)
    query = query / max(float(np.linalg.norm(query)), 1e-12)
    visual_raw = index.vectors @ query
    visual = calibrate(visual_raw, VISUAL_FLOOR, VISUAL_MATCH)
    speech = np.zeros(n, dtype=np.float32)
    speech_raw = np.full(n, -1.0, dtype=np.float32)
    sent_cal = np.zeros(len(sentences), dtype=np.float32)
    if sentences and sentence_vecs is not None and len(sentence_vecs) == len(sentences):
        sent_raw = np.asarray(sentence_vecs, dtype=np.float32) @ query
        sent_cal = calibrate(sent_raw, SPEECH_FLOOR, SPEECH_MATCH)
        # Each sentence lifts the chunks it overlaps (spans are in time order).
        chunk_starts = np.array([c0 for c0, _ in index.spans])
        chunk_ends = np.array([c1 for _, c1 in index.spans])
        for x, value, raw in zip(sentences, sent_cal, sent_raw):
            lo = int(np.searchsorted(chunk_ends, x.start, side="right"))
            hi = int(np.searchsorted(chunk_starts, x.end, side="left"))
            if lo < hi:
                speech[lo:hi] = np.maximum(speech[lo:hi], value)
                speech_raw[lo:hi] = np.maximum(speech_raw[lo:hi], raw)
    scores = np.maximum(visual, speech)
    strong = (visual_raw >= NO_JUDGE_VISUAL) | (speech_raw >= NO_JUDGE_SPEECH)

    runs = _merge_hits(scores, threshold)
    tentative = not runs
    if tentative:
        # Nothing cleared the bar. The floors are medians of unrelated
        # cosines, so about half of all chunks score above 0: these are only
        # something for the judge to look at, never clips on their own
        # (``tentative``; ``judge_scenes`` drops them unless it confirms).
        best = [int(i) for i in np.argsort(-scores, kind="stable")[:FALLBACK_CHUNKS]]
        runs = [(i, i) for i in best if scores[i] > 0]
    moments = []
    for first, last in runs:
        peak = first + int(np.argmax(scores[first : last + 1]))
        moments.append(_Moment(first, last, peak, float(scores[peak]), float(visual[peak]), float(speech[peak])))
    moments.sort(key=lambda m: (-m.score, m.first))

    kept: list[tuple[_Moment, float, float, np.ndarray]] = []
    for m in moments:
        c0, c1 = index.spans[m.peak]
        s, e = shape_window(
            index.spans[m.first][0],
            index.spans[m.last][1],
            (c0 + c1) / 2,
            sentences=sentences,
            min_s=min_s,
            max_s=max_s,
            duration=duration,
        )
        if e - s <= 0:
            continue
        mean = index.vectors[m.first : m.last + 1].mean(axis=0)
        mean = mean / max(float(np.linalg.norm(mean)), 1e-12)
        if any(s < ke and ks < e for _, ks, ke, _ in kept):
            continue
        if not m.speech_led and any(not km.speech_led and float(mean @ kv) > DEDUPE_COSINE for km, _, _, kv in kept):
            continue
        kept.append((m, s, e, mean))
        if len(kept) >= MAX_MOMENTS:
            break

    out: list[ProposedSegment] = []
    for m, s, e, _ in kept:
        text = _text_between(sentences, s, e)
        # What the judge looks at: the best chunk and its hit neighbours.
        f0 = max(s, index.spans[max(m.first, m.peak - 1)][0])
        f1 = min(e, index.spans[min(m.last, m.peak + 1)][1])
        out.append(
            ProposedSegment(
                start=s,
                end=e,
                # The judge names a clip; until then a spoken moment is named
                # by the sentence that matched and a silent one stays untitled.
                title=_best_sentence(sentences, sent_cal, f0, f1) if m.speech_led else "",
                summary=text[:200],
                score=int(min(100, max(0, math.floor(m.score * 100 + 0.5)))),
                score_breakdown={"visual": round(m.visual, 3), "speech": round(m.speech, 3)},
                text=text,
                focus=(round(f0, 3), round(f1, 3)),
                focus_text=_text_between(sentences, f0, f1),
                tentative=tentative,
                strong=bool(strong[m.first : m.last + 1].any()),
            )
        )
    return out
