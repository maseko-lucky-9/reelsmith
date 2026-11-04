# Vendored: sentrysearch (subset)

- Source: https://github.com/maseko-lucky-9/sentrysearch, branch `master`
- Commit: `3694dd0cb27cc57920b2b816996ce5a22662e45c`
- Licence: Apache-2.0 (`LICENSE` in this folder). Each file keeps an SPDX header and a
  "MODIFIED" note where it differs from upstream.
- Why and how: `/VENDOR.md`, `docs/decisions/013-local-scene-search-models.md`.

Runs only under `.venv-mlx` for embedding (`mlx_embedder`); the other modules are pure
Python/numpy and are unit-tested in the API venv (`tests/unit/test_scene_vendor.py`).
Nothing here imports chromadb, google-genai, click or torch.

| File | Status vs upstream |
|---|---|
| `base_embedder.py` | identical apart from the header and ruff-format whitespace |
| `mlx_embedder.py` | header, ruff-format whitespace, the "missing dependencies" message, and `_load_forced_embedding_model()` (upstream's `load_embedding_model(..., config_overrides=...)` call raises a duplicate-keyword TypeError on mlx-vlm 0.7.11; the helper falls back to passing a ready-made config). The `_video_capable_class` shim is **required**: without it mlx-vlm silently drops the video pixels and every chunk embeds to a meaningless vector |
| `chunker.py` | trimmed to `expected_chunk_spans`, `is_still_frame_chunk`, `preprocess_chunk` and helpers; bundled imageio-ffmpeg only (no system ffmpeg/ffprobe); `render_chunk()` added (one-pass accurate cut + 480p/5fps); `-vsync` replaced by `-fps_mode`; timeouts and temp-dir cleanup added; `preprocess_chunk` kept for parity but unused by `scene_index.py` |
| `highlights.py` | numpy scoring kept; `rank_highlights()` takes `(embeddings, metas)` instead of a chromadb store |
| `reranker.py` | only the pure helpers (`RerankScore`, `RERANK_SCHEMA`, `build_rerank_prompt`, `parse_rerank_response`, `_sort_key`); `rerank_results()` and its trimmer import removed |

Not vendored: `store.py` (chromadb), `embedder.py`/`gemini_*`/`litellm_*`/`qwen_cloud_*`
(cloud), `local_embedder.py`/`qwen_reranker.py` (torch), `cli.py` (click), `trimmer.py`,
`overlay.py`, `metadata.py`, `dashcam_pb2.py`.
