"""Scene search (spec 014): sentences, calibration, moments, windows, the index on disk.

Pure numpy with fixture vectors; the sidecar is ``tests/fixtures/fake_scene_index.py``.
A prompt embeds to ``e0``; a chunk or sentence with similarity ``a`` to it is
``a*e0 + sqrt(1-a^2)*e_k`` for its own ``k``.
"""

from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path

import numpy as np
import pytest

from app.services import scene_search as ss
from app.services.transcription_service import WordTiming

FAKE = Path(__file__).resolve().parents[1] / "fixtures" / "fake_scene_index.py"
E0 = np.eye(1, ss.EMBED_DIMS, 0, dtype=np.float32)[0]


def _vec(a: float, slot: int) -> np.ndarray:
    v = np.zeros(ss.EMBED_DIMS, dtype=np.float32)
    v[0] = a
    v[1 + slot] = math.sqrt(max(0.0, 1 - a * a))
    return v


def _index(sims: list[float], chunk: float = 8.0) -> ss.SceneIndex:
    return ss.SceneIndex(
        np.stack([_vec(a, i) for i, a in enumerate(sims)]),
        [(i * chunk, (i + 1) * chunk) for i in range(len(sims))],
    )


# Raw cosines: 0.0 is unrelated (calibrated 0), 0.41 a strong match (1.0),
# 0.27 a typical match (0.5), 0.20 below the 0.35 bar (0.25).
STRONG, TYPICAL, WEAK = 0.41, 0.27, 0.20


# ── sentences ─────────────────────────────────────────────────────────────────


def test_sentences_split_on_punctuation_pauses_and_length():
    words = [
        WordTiming("Hello", 0.0, 0.4), WordTiming("there.", 0.5, 0.9),
        WordTiming("After", 1.0, 1.3), WordTiming("a", 1.4, 1.5),
        WordTiming("pause", 2.5, 2.9),  # 1.0 s gap >= SENTENCE_GAP_SECS
        {"word": "dict", "start": 3.0, "end": 3.2}, WordTiming("", 3.3, 3.4),
    ]  # fmt: skip
    got = ss.sentences_from_words(words)
    assert [(s.start, s.end, s.text) for s in got] == [
        (0.0, 0.9, "Hello there."),
        (1.0, 1.5, "After a"),
        (2.5, 3.2, "pause dict"),
    ]
    run_on = [WordTiming(f"w{i}", i * 0.5, i * 0.5 + 0.4) for i in range(80)]  # 40 s, no pause
    assert all(s.end - s.start <= ss.MAX_SENTENCE_SECONDS for s in ss.sentences_from_words(run_on))


def test_calibration_maps_floor_to_zero_match_to_half_and_clamps():
    got = ss.calibrate(np.array([0.0, 0.13, 0.27, 0.41, 0.9]), 0.13, 0.27)
    assert got.tolist() == pytest.approx([0.0, 0.0, 0.5, 1.0, 1.0])


# ── search ────────────────────────────────────────────────────────────────────


def test_adjacent_hits_merge_into_one_moment_and_misses_split_moments():
    index = _index([0, STRONG, TYPICAL, 0, 0, 0, TYPICAL, 0, 0, 0])
    got = ss.search(index, E0, min_s=1, max_s=60, duration=80)
    # chunks 1-2 (8-24 s) are one moment, chunk 6 (48-56 s) another; silent: padded 1 s.
    assert [(m.start, m.end) for m in got] == [(7.0, 25.0), (47.0, 57.0)]
    assert [m.score for m in got] == [100, 50]
    assert got[0].score_breakdown == {"visual": 1.0, "speech": 0.0}


def test_chunks_below_the_threshold_are_not_hits():
    index = _index([0, WEAK, 0, STRONG, 0])
    got = ss.search(index, E0, min_s=1, max_s=60, duration=40)
    assert [(m.start, m.end) for m in got] == [(23.0, 33.0)]
    # The same chunk is a hit once the bar is below it.
    lowered = ss.search(index, E0, min_s=1, max_s=60, duration=40, threshold=0.2)
    assert len(lowered) == 2


def test_without_any_hit_the_best_chunks_are_tentative_candidates():
    index = _index([0.0, 0.14, 0.0, WEAK, 0.0, 0.15, 0.0, 0.0, 0.0, 0.0])
    got = ss.search(index, E0, min_s=1, max_s=60, duration=80)
    # best first, FALLBACK_CHUNKS of them (padded by 1 s), for the judge only
    assert [m.start + 1 for m in got] == [24.0, 40.0, 8.0]
    assert all(m.tentative for m in got)
    hits = ss.search(_index([0, STRONG, 0]), E0, min_s=1, max_s=10, duration=24)
    assert [m.tentative for m in hits] == [False]


# Raw visual cosines of chunks OUTSIDE their label (spec 014 plan, calibration
# T098): percentiles 10/25/50/75/90 over 705 prompt x chunk pairs.
OUTSIDE_PERCENTILES = [0.10, 0.25, 0.50, 0.75, 0.90]
OUTSIDE_VISUAL = [0.057, 0.086, 0.126, 0.164, 0.202]


def outside_cosines(n: int, seed: int = 14) -> list[float]:
    """``n`` raw cosines drawn from the measured unrelated distribution (inverse
    CDF through its percentiles, stratified so a small sample keeps its
    shape, in a shuffled order; the tails are held at p10 and p90)."""
    u = np.random.default_rng(seed).permutation((np.arange(n) + 0.5) / n)
    return np.interp(u, OUTSIDE_PERCENTILES, OUTSIDE_VISUAL).tolist()


def test_unrelated_footage_gives_no_clip_only_tentative_candidates():
    """Review HIGH: the floor is the median of unrelated cosines, so half of an
    unrelated source scores above 0. Nothing of it may become a clip unless
    the judge confirms it (``judge_scenes`` drops tentative candidates)."""
    sims = outside_cosines(45)
    assert float(np.median(sims)) == pytest.approx(0.126, abs=0.002)
    assert sum(ss.calibrate(np.array(sims), ss.VISUAL_FLOOR, ss.VISUAL_MATCH) > 0) > 10
    got = ss.search(_index(sims), E0, min_s=8, max_s=60, duration=360)
    assert got and all(m.tentative for m in got)
    assert [m for m in got if not m.tentative] == []


def test_a_silent_moment_is_found_by_what_is_seen():
    """Footage with no speech at the match: the visual side alone finds it."""
    index = _index([0, 0, 0, 0, STRONG, 0])
    talk = [ss.Sentence(0.0, 30.0, "a long talk about something else")]
    got = ss.search(index, E0, sentences=talk, sentence_vecs=np.stack([_vec(0.0, 300)]), min_s=4, max_s=30, duration=48)
    assert got[0].start == 31.0 and got[0].end == 41.0  # 32-40 s padded, no sentence to snap to
    assert got[0].score_breakdown["visual"] == 1.0
    assert got[0].text == "" and got[0].title == ""


def test_a_moment_found_by_speech_snaps_to_its_sentences():
    index = _index([0.0] * 8)  # nothing on screen matches
    sentences = [
        ss.Sentence(1.0, 12.5, "first we talk about the weather"),
        ss.Sentence(13.0, 21.0, "then the encryption was not even turned on"),
        ss.Sentence(21.5, 30.0, "and that surprised everyone"),
        ss.Sentence(31.0, 60.0, "the rest"),
    ]
    vecs = np.stack([_vec(0.0, 300), _vec(0.71, 301), _vec(0.3, 302), _vec(0.0, 303)])
    got = ss.search(index, E0, sentences=sentences, sentence_vecs=vecs, min_s=10, max_s=30, duration=64)
    best = got[0]
    # chunks 1-2 (8-24 s) overlap the matching sentence; the window grows to
    # 10 s+ and its ends snap out to the sentences they fall in.
    assert (best.start, best.end) == (1.0, 30.0)
    assert best.score_breakdown["speech"] == 1.0 and best.score_breakdown["visual"] == 0.0
    # Named by the sentence that matched; the judge looks at the hit chunks.
    assert best.title == "then the encryption was not even turned on"
    assert best.focus == (8.0, 24.0)
    assert "encryption" in best.focus_text and "the rest" not in best.focus_text
    assert "encryption" in best.text


def test_snapping_that_would_pass_the_maximum_snaps_inwards():
    sentences = [ss.Sentence(0.0, 20.0, "a"), ss.Sentence(20.0, 40.0, "b"), ss.Sentence(40.0, 60.0, "c")]
    s, e = ss.shape_window(15.0, 45.0, 30.0, sentences=sentences, min_s=10, max_s=30, duration=60)
    assert (s, e) == (20.0, 40.0)


def test_a_long_moment_is_cut_to_the_maximum_around_its_peak():
    s, e = ss.shape_window(0.0, 80.0, 60.0, sentences=[], min_s=10, max_s=20, duration=100)
    assert (s, e) == (50.0, 70.0)  # 20 s around the peak; no room left to pad


def test_inward_snapping_never_cuts_the_peak_out():
    """Review repro: speech ends before the best chunk; the peak must stay in the clip."""
    sentences = [ss.Sentence(111.0, 119.0, "a"), ss.Sentence(120.0, 147.0, "b")]
    s, e = ss.shape_window(88.0, 176.0, 158.0, sentences=sentences, min_s=20, max_s=60, duration=600)
    assert s <= 158.0 <= e and 20 <= e - s <= 60


def test_chunks_scoring_zero_are_not_even_tentative():
    assert ss.search(_index([0.0, 0.0, 0.05]), E0, min_s=1, max_s=10, duration=24) == []


def test_a_sentence_lifts_only_the_chunks_it_overlaps_not_the_ones_it_touches():
    """Survivors of the first mutation run: a sentence ending exactly where a
    chunk starts, or starting exactly where one ends, must not lift it."""
    index = _index([0.0] * 6)  # 8 s chunks, nothing on screen matches
    sentences = [ss.Sentence(0.0, 8.0, "the match"), ss.Sentence(24.0, 32.0, "the match again")]
    vecs = np.stack([_vec(0.71, 300), _vec(0.71, 301)])
    got = ss.search(index, E0, sentences=sentences, sentence_vecs=vecs, min_s=1, max_s=10, duration=48)
    # chunk 0 (0-8) and chunk 3 (24-32) only; chunks 1 and 2 merely touch a sentence
    assert sorted(m.focus for m in got) == [(0.0, 8.0), (24.0, 32.0)]


def _spoken(vectors: list[np.ndarray], spoken_chunk: int) -> list:
    """Search chunks with these vectors and one strongly matching sentence over ``spoken_chunk``."""
    index = ss.SceneIndex(np.stack(vectors), [(i * 8.0, (i + 1) * 8.0) for i in range(len(vectors))])
    at = spoken_chunk * 8.0
    sentence = [ss.Sentence(at, at + 8.0, "exactly what was asked")]
    return ss.search(index, E0, sentences=sentence, sentence_vecs=np.stack([_vec(0.71, 300)]),
                     min_s=1, max_s=10, duration=8.0 * len(vectors))  # fmt: skip


def test_a_spoken_moment_is_never_deduped_against_a_lookalike_shot():
    """Survivor of the first mutation run: the same talking head looks alike
    everywhere, so a speech-led moment is exempt from the near-duplicate rule."""
    shot = _vec(STRONG, 7)  # visual-led, kept first (score 1.0, earlier)
    lookalike = _vec(0.30, 7)  # cosine 0.99 to ``shot``, visual 0.61 < speech 1.0
    assert float(shot @ lookalike) > ss.DEDUPE_COSINE
    others = [_vec(0.0, 20 + i) for i in range(6)]
    got = _spoken([shot, *others[:4], lookalike, *others[4:]], spoken_chunk=5)
    assert sorted(m.focus for m in got) == [(0.0, 8.0), (40.0, 48.0)]
    # And the other way round: a spoken moment kept first does not dedupe a shot.
    head = _vec(TYPICAL, 7)
    got = _spoken([head, *others[:4], head, *others[4:]], spoken_chunk=0)
    assert sorted(m.focus for m in got) == [(0.0, 8.0), (40.0, 48.0)]


def test_windows_stay_inside_the_source():
    assert ss.shape_window(0.0, 2.0, 1.0, sentences=[], min_s=10, max_s=30, duration=50) == (0.0, 12.0)
    assert ss.shape_window(46.0, 50.0, 48.0, sentences=[], min_s=10, max_s=30, duration=50) == (38.0, 50.0)


def test_overlapping_windows_keep_only_the_better_one():
    index = _index([STRONG, 0, TYPICAL, 0, 0, 0])  # 0-8 s and 16-24 s; at 20 s each they overlap
    got = ss.search(index, E0, min_s=20, max_s=30, duration=48)
    assert len(got) == 1 and got[0].score == 100


def test_visual_near_duplicates_are_deduped():
    shot = _vec(STRONG, 10)
    vectors = np.stack([shot, _vec(0, 1), _vec(0, 2), _vec(0, 3), shot * 0.999 + _vec(0, 4) * 0.001, _vec(0, 5)])
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    index = ss.SceneIndex(vectors, [(i * 8.0, (i + 1) * 8.0) for i in range(6)])
    got = ss.search(index, E0, min_s=1, max_s=10, duration=48)
    assert [(m.start, m.end) for m in got] == [(0.0, 10.0)]  # the same shot at 32 s is dropped


def test_search_on_an_empty_index_finds_nothing():
    empty = ss.SceneIndex(np.zeros((0, ss.EMBED_DIMS), np.float32), [])
    assert ss.search(empty, E0, min_s=1, max_s=10, duration=10) == []


# ── provider ──────────────────────────────────────────────────────────────────


def test_enabled_needs_a_prompt_and_the_provider(monkeypatch, tmp_path):
    python = tmp_path / "python"
    monkeypatch.setattr(ss.settings, "scene_python", str(python))
    monkeypatch.setattr(ss.settings, "scene_search_provider", "auto")
    assert ss.enabled("a red car") is False  # auto: no sidecar venv
    python.write_text("")
    assert ss.enabled("a red car") is True
    assert ss.enabled("   ") is False and ss.enabled(None) is False
    monkeypatch.setattr(ss.settings, "scene_search_provider", "off")
    assert ss.enabled("a red car") is False
    monkeypatch.setattr(ss.settings, "scene_search_provider", "mlx")
    python.unlink()
    assert ss.enabled("a red car") is True
    monkeypatch.setattr(ss.settings, "scene_search_provider", "bogus")
    assert ss.enabled("a red car") is False


def test_settings_defaults(monkeypatch):
    for name in ("SCENE_SEARCH_PROVIDER", "SCENE_PYTHON", "SCENE_MODEL", "OLLAMA_VISION_MODEL"):
        monkeypatch.delenv(f"YTVIDEO_{name}", raising=False)
    from app.settings import _PROJECT_ROOT, Settings

    fresh = Settings(_env_file=None)
    assert fresh.scene_search_provider == "auto"
    assert fresh.scene_python == str(_PROJECT_ROOT / ".venv-mlx" / "bin" / "python")
    assert fresh.scene_model == "mlx-community/Qwen3-VL-Embedding-2B-8bit"
    assert fresh.ollama_vision_model == "qwen2.5vl:7b"
    assert fresh.scene_judge_max_candidates == 8
    assert fresh.scene_judge_budget_seconds == 120
    # Nothing else flipped: discovery and the re-rank keep their defaults.
    assert fresh.segment_provider == "chapter" and fresh.segment_rerank_provider == "none"


# ── the index on disk, through the fake sidecar ──────────────────────────────


@pytest.fixture
def sidecar(tmp_path, monkeypatch):
    spec = tmp_path / "spec.json"

    def _write(**data):
        spec.write_text(json.dumps(data))
        return spec

    monkeypatch.setenv("YTVIDEO_SCENE_FAKE_SPEC", str(spec))
    monkeypatch.setattr(ss.settings, "scene_python", sys.executable)
    monkeypatch.setattr(ss, "SCENE_SCRIPT", FAKE)
    _write(chunks=[[0, 8, 0.0], [8, 16, 0.5]], prompt="red car")
    return _write


def _calls(tmp_path: Path) -> list[str]:
    path = tmp_path / "spec.json.calls"
    return path.read_text().split() if path.exists() else []


async def test_ensure_index_builds_then_reuses_then_rebuilds_on_source_change(sidecar, tmp_path):
    video = tmp_path / "job" / "source.mp4"
    video.parent.mkdir()
    video.write_bytes(b"v1")
    index_dir = ss.scenes_dir(video.parent)
    progress: list[tuple[int, int]] = []

    async def _progress(done, total):
        progress.append((done, total))

    index, reused = await ss.ensure_index(video, index_dir, on_progress=_progress)
    assert reused is False and index.spans == [(0.0, 8.0), (8.0, 16.0)]
    assert progress == [(0, 2), (1, 2), (2, 2)]
    meta = json.loads((index_dir / "meta.json").read_text())
    assert meta["source"] == {"size": 2, "mtime_ns": video.stat().st_mtime_ns}

    _, reused = await ss.ensure_index(video, index_dir)
    assert reused is True and _calls(tmp_path) == ["index"]

    video.write_bytes(b"v2 longer")  # the source changed: size and mtime
    _, reused = await ss.ensure_index(video, index_dir)
    assert reused is False and _calls(tmp_path) == ["index", "index"]
    assert [p.name for p in video.parent.iterdir() if p.name.startswith(".scenes")] == []


async def test_a_failed_index_raises_and_keeps_the_previous_one(sidecar, tmp_path):
    video = tmp_path / "source.mp4"
    video.write_bytes(b"v1")
    index_dir = tmp_path / "scenes"
    await ss.ensure_index(video, index_dir)
    os.utime(video, ns=(1, 1))
    sidecar(chunks=[], fail="index")
    with pytest.raises(ss.SceneSearchError, match="exited 1: fake failure"):
        await ss.ensure_index(video, index_dir)
    assert ss.load_index(index_dir).spans == [(0.0, 8.0), (8.0, 16.0)]
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".scenes")] == []


async def test_a_missing_sidecar_is_a_scene_search_error(tmp_path, monkeypatch):
    monkeypatch.setattr(ss.settings, "scene_python", str(tmp_path / "no-python"))
    video = tmp_path / "source.mp4"
    video.write_bytes(b"v")
    with pytest.raises(ss.SceneSearchError, match="cannot start"):
        await ss.ensure_index(video, tmp_path / "scenes")


def test_a_torn_or_foreign_index_is_not_current(tmp_path):
    video = tmp_path / "source.mp4"
    video.write_bytes(b"v")
    index_dir = tmp_path / "scenes"
    index_dir.mkdir()
    (index_dir / "chunks.json").write_text(json.dumps([{"start": 0, "end": 8}, {"start": 8, "end": 16}]))
    np.save(index_dir / "embeddings.npy", np.zeros((1, ss.EMBED_DIMS), np.float32))
    (index_dir / "meta.json").write_text(json.dumps(ss._meta(video, "m")))
    with pytest.raises(ss.SceneSearchError, match="torn"):
        ss.load_index(index_dir)
    assert ss.index_is_current(index_dir, video, "m") is False
    np.save(index_dir / "embeddings.npy", np.zeros((2, ss.EMBED_DIMS), np.float32))
    assert ss.index_is_current(index_dir, video, "m") is True
    assert ss.index_is_current(index_dir, video, "another-model") is False


async def test_sentence_vectors_are_cached_so_a_new_prompt_embeds_only_itself(sidecar, tmp_path):
    sidecar(chunks=[], prompt="red car", texts={"we saw a red car": 0.7})
    sentences = [ss.Sentence(0, 2, "we saw a red car"), ss.Sentence(2, 4, "something else")]
    prompt_vec, vecs = await ss.prompt_and_sentence_vectors(tmp_path, "red car", sentences)
    assert prompt_vec.tolist() == pytest.approx(E0.tolist())
    assert (vecs @ E0).tolist() == pytest.approx([0.7, 0.0], abs=1e-6)

    texts_file = tmp_path / "spec.json.calls"
    texts_file.unlink()
    sidecar(chunks=[], prompt="blue boat", texts={"we saw a red car": 0.9})  # would change the vectors
    prompt_vec, vecs = await ss.prompt_and_sentence_vectors(tmp_path, "blue boat", sentences)
    assert (vecs @ E0).tolist() == pytest.approx([0.7, 0.0], abs=1e-6)  # from the cache
    assert prompt_vec.tolist() == pytest.approx(E0.tolist())
    assert _calls(tmp_path) == ["embed-texts"]
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".texts")] == []


async def test_a_re_transcribed_sentence_never_reuses_a_stale_vector(sidecar, tmp_path):
    """Same number of sentences, different text: the cache is keyed by text."""
    sidecar(chunks=[], prompt="red car", texts={"we saw a red car": 0.7, "a blue boat": 0.2})
    await ss.prompt_and_sentence_vectors(tmp_path, "red car", [ss.Sentence(0, 2, "we saw a red car")])
    _, vecs = await ss.prompt_and_sentence_vectors(tmp_path, "red car", [ss.Sentence(0, 2, "a blue boat")])
    assert (vecs @ E0).tolist() == pytest.approx([0.2], abs=1e-6)
    assert _calls(tmp_path) == ["embed-texts", "embed-texts"]


# ── the sidecar's environment, timeouts and the files it hands back ─────────


async def test_the_sidecar_gets_no_app_secrets(sidecar, tmp_path, monkeypatch):
    spec = sidecar(chunks=[], prompt="p", dump_env=True)
    monkeypatch.setenv("YTVIDEO_PEXELS_API_KEY", "<PLACEHOLDER>")
    monkeypatch.setenv("YTVIDEO_DATABASE_URL", "<PLACEHOLDER>")
    monkeypatch.setenv("GITHUB_TOKEN", "<PLACEHOLDER>")
    monkeypatch.setenv("HF_HOME", "/models/hf")
    monkeypatch.setenv("YTVIDEO_SCENE_EXTRA", "kept")
    passed = {
        "XDG_CACHE_HOME": "/cache", "HTTP_PROXY": "http://proxy:3128", "https_proxy": "http://proxy:3128",
        "ALL_PROXY": "socks5://proxy:1080", "no_proxy": "localhost", "SSL_CERT_FILE": "/ca/cert.pem",
        "SSL_CERT_DIR": "/ca", "REQUESTS_CA_BUNDLE": "/ca/bundle.pem", "CURL_CA_BUNDLE": "/ca/curl.pem",
    }  # fmt: skip
    for name, value in passed.items():
        monkeypatch.setenv(name, value)
    await ss.embed_texts(["p"], tmp_path)
    env = json.loads(Path(f"{spec}.env").read_text())
    assert "YTVIDEO_PEXELS_API_KEY" not in env and "YTVIDEO_DATABASE_URL" not in env
    assert "GITHUB_TOKEN" not in env
    assert env["HF_HOME"] == "/models/hf" and env["YTVIDEO_SCENE_EXTRA"] == "kept"
    assert env["PATH"] == os.environ["PATH"]
    assert {name: env.get(name) for name in passed} == passed


@pytest.mark.parametrize(
    ("raised", "message"),
    [(TimeoutError(), "longer than"), (ValueError("bad bytes"), "unreadable"), (OSError("no exec"), "cannot start")],
)
async def test_sidecar_failures_are_scene_search_errors(tmp_path, monkeypatch, raised, message):
    async def _fail(*args, **kwargs):
        raise raised

    monkeypatch.setattr(ss.child_process, "run_streaming", _fail)
    video = tmp_path / "source.mp4"
    video.write_bytes(b"v")
    with pytest.raises(ss.SceneSearchError, match=message):
        await ss.ensure_index(video, tmp_path / "scenes")


async def test_timeouts_index_from_settings_and_embedding_by_text_count(tmp_path, monkeypatch):
    seen: list[tuple[str, float]] = []

    async def _record(argv, *, timeout, on_line, env):
        seen.append((argv[2], timeout))
        return 1, "stopped here"

    monkeypatch.setattr(ss.child_process, "run_streaming", _record)
    monkeypatch.setattr(ss.settings, "scene_index_timeout_seconds", 900)
    video = tmp_path / "source.mp4"
    video.write_bytes(b"v")
    with pytest.raises(ss.SceneSearchError):
        await ss.ensure_index(video, tmp_path / "scenes")
    with pytest.raises(ss.SceneSearchError):
        await ss.embed_texts(["x"] * 1000, tmp_path)
    assert seen == [("index", 900), ("embed-texts", 300.0)]


def test_the_embedding_timeout_scales_with_the_texts_within_the_index_timeout(monkeypatch):
    monkeypatch.setattr(ss.settings, "scene_index_timeout_seconds", 900)
    assert ss.embed_timeout(1) == 120.0  # the model load needs a floor
    assert ss.embed_timeout(1000) == 300.0  # 0.3 s a sentence
    assert ss.embed_timeout(10_000) == 900.0  # capped by the index timeout


async def test_sentence_batches_are_cached_as_they_finish_so_a_retry_resumes(sidecar, tmp_path, monkeypatch):
    monkeypatch.setattr(ss, "SENTENCE_BATCH", 2)
    texts = [f"sentence {i}" for i in range(5)]
    sentences = [ss.Sentence(i, i + 1, t) for i, t in enumerate(texts)]
    known = {t: 0.1 * i for i, t in enumerate(texts)}
    spec = sidecar(chunks=[], prompt="p", texts=known, fail_embed_call=2)
    with pytest.raises(ss.SceneSearchError):
        await ss.prompt_and_sentence_vectors(tmp_path, "p", sentences)
    sidecar(chunks=[], prompt="p", texts=known)
    _, vecs = await ss.prompt_and_sentence_vectors(tmp_path, "p", sentences)

    batches = [json.loads(line) for line in Path(f"{spec}.embeds").read_text().splitlines()]
    assert _calls(tmp_path) == ["embed-texts"] * 4  # the 2nd failed before reading its texts
    assert batches == [
        ["p", "sentence 0", "sentence 1"],  # cached at once
        ["p", "sentence 2", "sentence 3"],  # the retry starts where it failed
        ["sentence 4"],
    ]
    assert (vecs @ E0).tolist() == pytest.approx([0.0, 0.1, 0.2, 0.3, 0.4], abs=1e-6)


class _Planted:
    """Unpickling this runs ``os.mkdir(marker)``: proof that a load ran code."""

    def __init__(self, marker: str) -> None:
        self.marker = marker

    def __reduce__(self):
        return (os.mkdir, (self.marker,))


async def test_a_planted_pickle_in_the_index_is_an_error_not_code(sidecar, tmp_path):
    spec = sidecar(chunks=[[0, 8, 0.5]], prompt="p", pickle=True)
    video = tmp_path / "source.mp4"
    video.write_bytes(b"v")
    with pytest.raises(ss.SceneSearchError):
        await ss.ensure_index(video, tmp_path / "scenes")
    with pytest.raises(ss.SceneSearchError):
        await ss.embed_texts(["p"], tmp_path)
    assert not Path(f"{spec}.pwned").exists()


async def test_a_planted_pickle_in_the_sentence_cache_is_ignored_not_run(sidecar, tmp_path):
    spec = sidecar(chunks=[], prompt="p", texts={"we saw a red car": 0.7})
    sentences = [ss.Sentence(0, 2, "we saw a red car")]
    await ss.prompt_and_sentence_vectors(tmp_path, "p", sentences)
    planted = np.empty(1, dtype=object)
    planted[0] = _Planted(f"{spec}.pwned")
    np.save(tmp_path / "sentence_embeddings.npy", planted, allow_pickle=True)

    _, vecs = await ss.prompt_and_sentence_vectors(tmp_path, "p", sentences)
    assert not Path(f"{spec}.pwned").exists()
    assert (vecs @ E0).tolist() == pytest.approx([0.7], abs=1e-6)  # embedded again
    assert _calls(tmp_path) == ["embed-texts", "embed-texts"]


async def test_a_nan_vector_matches_nothing_instead_of_poisoning_the_search(sidecar, tmp_path):
    sidecar(chunks=[], prompt="p", texts={"broken": "nan", "fine": 0.71})
    sentences = [ss.Sentence(0, 8, "broken"), ss.Sentence(8, 16, "fine")]
    _, vecs = await ss.prompt_and_sentence_vectors(tmp_path, "p", sentences)
    assert np.isfinite(vecs).all() and not vecs[0].any()

    rows = np.stack([_vec(STRONG, 0), np.full(ss.EMBED_DIMS, np.nan, np.float32)])
    (tmp_path / "chunks.json").write_text(json.dumps([{"start": 0, "end": 8}, {"start": 8, "end": 16}]))
    np.save(tmp_path / "embeddings.npy", rows)
    index = ss.load_index(tmp_path)
    assert np.isfinite(index.vectors).all()
    (moment,) = ss.search(index, E0, sentences=sentences, sentence_vecs=vecs, min_s=1, max_s=16, duration=16)
    assert moment.score == 100 and all(math.isfinite(v) for v in moment.score_breakdown.values())


def test_an_index_of_text_instead_of_numbers_is_an_error(tmp_path):
    (tmp_path / "chunks.json").write_text(json.dumps([{"start": 0, "end": 8}]))
    np.save(tmp_path / "embeddings.npy", np.array([["x"] * ss.EMBED_DIMS]))
    with pytest.raises(ss.SceneSearchError, match="does not hold numbers"):
        ss.load_index(tmp_path)


def test_a_relative_scene_python_is_taken_from_the_project_root(monkeypatch):
    from app.settings import _PROJECT_ROOT, Settings

    monkeypatch.setenv("YTVIDEO_SCENE_PYTHON", ".venv-mlx/bin/python")
    assert Settings(_env_file=None).scene_python == str(_PROJECT_ROOT / ".venv-mlx" / "bin" / "python")
    monkeypatch.setenv("YTVIDEO_SCENE_PYTHON", "/opt/mlx/bin/python")
    assert Settings(_env_file=None).scene_python == "/opt/mlx/bin/python"


async def test_the_sidecar_gets_the_resolved_source_path(tmp_path, monkeypatch):
    seen: list[str] = []

    async def _record(argv, *, timeout, on_line, env):
        seen.append(argv[argv.index("--") + 1])
        return 1, ""

    monkeypatch.setattr(ss.child_process, "run_streaming", _record)
    (tmp_path / "job").mkdir()
    (tmp_path / "job" / "source.mp4").write_bytes(b"v")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ss.SceneSearchError):
        await ss.ensure_index("job/../job/source.mp4", tmp_path / "scenes")
    assert seen == [str((tmp_path / "job" / "source.mp4").resolve())]


def test_collecting_the_real_model_tests_makes_no_network_call(monkeypatch):
    import importlib
    import urllib.request

    def _no_network(*args, **kwargs):
        raise AssertionError("network call while importing the test module")

    monkeypatch.setattr(urllib.request, "urlopen", _no_network)
    monkeypatch.delitem(sys.modules, "tests.integration.test_scene_search_real", raising=False)
    importlib.import_module("tests.integration.test_scene_search_real")
