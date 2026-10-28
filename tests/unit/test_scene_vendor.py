"""Pure helpers of the vendored sentrysearch subset and scripts/scene_index.py.

Runs in the API venv: numpy only, no mlx, no ffmpeg, no model.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from app.vendor.sentrysearch.chunker import (
    _parse_duration_from_ffmpeg_output,
    expected_chunk_spans,
)
from app.vendor.sentrysearch.highlights import (
    _dedupe_indices,
    _exclude_baseline_mask,
    _normalize,
    _score_knn,
    rank_highlights,
)
from app.vendor.sentrysearch.reranker import (
    RerankScore,
    _sort_key,
    build_rerank_prompt,
    parse_rerank_response,
)
from scripts.scene_bench import chance_top_k, is_hit, judge_scores, percentile
from scripts.scene_index import MODEL_ENV, plan_spans, resolve_model, write_index

# --- expected_chunk_spans -------------------------------------------------


def test_spans_for_video_shorter_than_one_chunk_is_a_single_span():
    assert expected_chunk_spans(5.0, 8, 0) == [(0.0, 5.0)]


def test_spans_with_no_overlap_tile_the_video_and_clip_the_tail():
    assert expected_chunk_spans(20.0, 8, 0) == [(0.0, 8.0), (8.0, 16.0), (16.0, 20.0)]


def test_spans_with_overlap_step_by_chunk_minus_overlap():
    spans = expected_chunk_spans(60.0, 30, 5)
    assert spans == [(0.0, 30.0), (25.0, 55.0), (50.0, 60.0)]


def test_spans_stop_when_the_remaining_tail_is_only_overlap():
    # after (25, 55) the next start 50 + overlap 5 reaches the end: no extra (50, 55) span
    assert expected_chunk_spans(55.0, 30, 5) == [(0.0, 30.0), (25.0, 55.0)]


def test_spans_reject_overlap_not_smaller_than_chunk():
    with pytest.raises(ValueError, match="must be less than chunk_duration"):
        expected_chunk_spans(60.0, 8, 8)


def test_spans_cover_exactly_the_duration_for_awkward_lengths():
    spans = expected_chunk_spans(371.3, 8, 0)
    assert spans[0][0] == 0.0
    assert spans[-1][1] == pytest.approx(371.3)
    assert all(b[0] == a[1] for a, b in zip(spans, spans[1:], strict=False))


def test_parse_duration_reads_ffmpeg_banner():
    banner = "  Duration: 00:01:02.50, start: 0.000000, bitrate: 900 kb/s"
    assert _parse_duration_from_ffmpeg_output(banner) == pytest.approx(62.5)


def test_parse_duration_counts_hours():
    assert _parse_duration_from_ffmpeg_output("Duration: 01:02:03.50, start: 0") == pytest.approx(3723.5)


def test_parse_duration_raises_file_not_found_for_missing_input():
    with pytest.raises(FileNotFoundError):
        _parse_duration_from_ffmpeg_output("x.mp4: No such file or directory")


# --- scripts/scene_index.py pure parts ------------------------------------


def test_plan_spans_drops_a_sub_second_tail():
    assert plan_spans(16.4, 8, 0) == [(0.0, 8.0), (8.0, 16.0)]


def test_plan_spans_keeps_a_lone_short_span():
    assert plan_spans(0.5, 8, 0) == [(0.0, 0.5)]


def test_plan_spans_keeps_a_tail_of_one_second_or_more():
    assert plan_spans(17.0, 8, 0)[-1] == (16.0, 17.0)


def test_resolve_model_prefers_cli_then_env_then_default(monkeypatch):
    monkeypatch.setenv(MODEL_ENV, "org/from-env")
    assert resolve_model("org/from-cli") == "org/from-cli"
    assert resolve_model(None) == "org/from-env"
    monkeypatch.delenv(MODEL_ENV)
    assert "Qwen3-VL-Embedding" in resolve_model(None)


def test_write_index_writes_both_files_and_leaves_no_temp_files(tmp_path):
    rows = np.eye(3, 768, dtype=np.float32)
    meta = [{"start": 0.0, "end": 8.0, "skipped_still": False}] * 3
    write_index(tmp_path / "idx", rows, meta)
    out = tmp_path / "idx"
    assert sorted(p.name for p in out.iterdir()) == ["chunks.json", "embeddings.npy"]
    loaded = np.load(out / "embeddings.npy")
    assert loaded.shape == (3, 768) and loaded.dtype == np.float32
    assert json.loads((out / "chunks.json").read_text()) == meta


# --- highlights (numpy ranking) -------------------------------------------


def _cluster_with_outlier() -> tuple[np.ndarray, list[dict]]:
    rng = np.random.default_rng(7)
    base = np.zeros(16, dtype=np.float32)
    base[0] = 1.0
    rows = [base + 0.01 * rng.standard_normal(16).astype(np.float32) for _ in range(9)]
    outlier = np.zeros(16, dtype=np.float32)
    outlier[5] = 1.0
    rows.insert(4, outlier)  # index 4 is the odd one out
    metas = [{"source_file": "v.mp4", "start_time": 8.0 * i, "end_time": 8.0 * i + 8} for i in range(10)]
    return np.stack(rows), metas


@pytest.mark.parametrize("method", ["centroid", "knn", "lof"])
def test_rank_highlights_puts_the_unusual_chunk_first(method):
    emb, metas = _cluster_with_outlier()
    top = rank_highlights(emb, metas, count=1, method=method, neighbors=3)
    assert top[0]["start_time"] == 32.0  # the outlier is chunk index 4


def test_rank_highlights_rejects_metadata_row_mismatch():
    emb, metas = _cluster_with_outlier()
    with pytest.raises(ValueError, match="one metadata dict per row"):
        rank_highlights(emb, metas[:-1], count=1)


def test_rank_highlights_rejects_unknown_method():
    emb, metas = _cluster_with_outlier()
    with pytest.raises(ValueError, match="method must be one of"):
        rank_highlights(emb, metas, count=1, method="nope")


def test_rank_highlights_on_empty_index_returns_nothing():
    assert rank_highlights(np.zeros((0, 0)), [], count=3) == []


def test_rank_highlights_within_mode_ignores_chunks_outside_the_query_pool():
    emb, metas = _cluster_with_outlier()
    query = emb[0]  # a typical chunk: the odd one out is dissimilar to it, so outside a pool of 3
    top = rank_highlights(emb, metas, count=1, against_embedding=query, against_mode="within", against_pool=3)
    assert top[0]["start_time"] != 32.0


def test_rank_highlights_against_a_query_restricts_to_its_best_matches():
    emb, metas = _cluster_with_outlier()
    query = emb[4]  # the outlier itself
    top = rank_highlights(emb, metas, count=2, against_embedding=query, against_mode="within", against_pool=2)
    assert 32.0 in {r["start_time"] for r in top}


def test_knn_score_is_the_mean_cosine_distance_to_the_k_nearest_neighbours():
    xn = _normalize(np.array([[1, 0], [0.8, 0.6], [0, 1]], dtype=np.float32))
    # row 0: distances to rows 1, 2 are 1-0.8=0.2 and 1-0=1.0; the two nearest average 0.6
    assert _score_knn(xn, 2)[0] == pytest.approx(0.6)
    assert _score_knn(xn, 1)[0] == pytest.approx(0.2)


def test_normalize_gives_unit_rows_and_survives_zero_rows():
    out = _normalize(np.array([[3.0, 4.0], [0.0, 0.0]], dtype=np.float32))
    assert np.linalg.norm(out[0]) == pytest.approx(1.0)
    assert not np.isnan(out).any()


def test_dedupe_drops_near_duplicates_of_a_higher_ranked_pick():
    xn = _normalize(np.array([[1, 0], [0.999, 0.04], [0, 1]], dtype=np.float32))
    assert _dedupe_indices(np.array([0, 1, 2]), xn, threshold=0.9, limit=3) == [0, 2]


def test_exclude_baseline_keeps_the_far_half_including_the_outlier():
    xn = _normalize(np.array([[1, 0], [1, 0.01], [1, 0.02], [0, 1]], dtype=np.float32))
    mask = _exclude_baseline_mask(xn)
    assert mask.sum() == 2 and mask[3]


def test_exclude_baseline_keeps_everything_when_the_index_is_tiny():
    assert _exclude_baseline_mask(_normalize(np.eye(3, dtype=np.float32))).all()


# --- reranker parse helpers ------------------------------------------------


def test_parse_rerank_response_accepts_plain_and_fenced_json():
    plain = '{"rerank_match": true, "rerank_confidence": 0.75}'
    assert parse_rerank_response(plain) == RerankScore(True, 0.75)
    assert parse_rerank_response(f"```json\n{plain}\n```") == RerankScore(True, 0.75)


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        "[1, 2]",
        '{"rerank_match": "yes", "rerank_confidence": 0.5}',
        '{"rerank_match": true, "rerank_confidence": true}',
        '{"rerank_match": true, "rerank_confidence": 1.5}',
        '{"rerank_match": true, "rerank_confidence": -0.1}',
        '{"rerank_match": true}',
        '```xml\n{"rerank_match": true, "rerank_confidence": 0.5}\n```',
        None,
    ],
)
def test_parse_rerank_response_rejects_invalid_output(text):
    assert parse_rerank_response(text) is None


def test_parse_rerank_response_rejects_non_finite_confidence():
    assert parse_rerank_response('{"rerank_match": true, "rerank_confidence": NaN}') is None


def test_sort_key_orders_matches_by_confidence_then_unscored_then_non_matches():
    items = [
        ({}, 0, RerankScore(False, 0.9)),
        ({}, 1, None),
        ({}, 2, RerankScore(True, 0.4)),
        ({}, 3, RerankScore(True, 0.8)),
    ]
    order = [rank for _r, rank, _s in sorted(items, key=_sort_key)]
    assert order == [3, 2, 1, 0]


def test_rerank_prompt_embeds_the_query():
    assert "red car" in build_rerank_prompt("red car")


# --- scripts/scene_bench.py pure helpers -----------------------------------


def test_is_hit_needs_two_seconds_of_overlap_or_half_a_short_span():
    assert is_hit((0.0, 8.0), [[6.0, 20.0]])  # 2 s overlap
    assert not is_hit((0.0, 8.0), [[7.0, 20.0]])  # 1 s overlap of a long span
    assert is_hit((0.0, 8.0), [[7.0, 9.0]])  # 1 s overlap is half of a 2 s span
    assert not is_hit((8.0, 16.0), [[0.0, 8.0]])  # touching is not overlapping


def test_chance_top_k_is_hypergeometric():
    assert chance_top_k(10, 0) == 0.0
    assert chance_top_k(5, 1) == 1.0  # top 5 of 5 chunks always contains it
    assert chance_top_k(10, 1) == pytest.approx(0.5)
    assert chance_top_k(10, 2) == pytest.approx(1 - 56 / 252)  # 1 - C(8,5)/C(10,5)


def test_percentile_uses_nearest_rank():
    assert percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 95) == 10
    assert percentile([1, 2, 3, 4], 50) == 2
    assert percentile([], 95) == 0.0


def test_judge_scores_count_precision_recall_and_accuracy():
    pairs = [(True, True), (True, False), (False, True), (False, True), (False, False), (True, True)]
    scores = judge_scores(pairs)
    assert scores["precision"] == pytest.approx(2 / 3)  # 2 of the 3 'match' verdicts were right
    assert scores["recall"] == pytest.approx(2 / 4)  # 2 of the 4 real matches were found
    assert scores["accuracy"] == pytest.approx(3 / 6)
