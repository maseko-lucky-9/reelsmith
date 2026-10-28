# ADR-013: Local Scene Search Runs Qwen3-VL-Embedding 2B (MLX, 8-bit) in a Sidecar Venv, Judged by qwen2.5vl:7b

**Status:** Accepted
**Date:** 2026-10-28
**Author:** Thulani Maseko
**Implements:** Phase 0 of the AI scene search plan (feeds spec 014)

## Context

The next feature is "type a prompt, find the matching moments in this video": embed fixed-length chunks of a source with a model that puts text and video in one vector space, rank chunks by cosine, then let a vision model confirm the top few. It must run on the owner's MacBook Pro (Apple M5 Pro, 24 GB unified memory, macOS 27.0.1) beside Postgres and a browser, with no cloud call. Phase 0 measured candidates before any product code depends on them.

Setup used for every number below: 6 local sources from `data/downloads` (13.15 source minutes, 101 chunks of 8 s, no overlap, 480p / 5 fps), 34 hand-labelled prompts (20 visual, 5 emotion, 9 speech), Python 3.14.6, mlx 0.32.4, mlx-vlm 0.7.11, Ollama on localhost, nothing else heavy running.

## Findings that shaped the decision

| Finding | Evidence |
|---|---|
| mlx, mlx-vlm and opencv all install on Python 3.14; no older interpreter was needed. | `scripts/setup_scene_venv.sh`, `requirements-scene.txt` (`pip check`: no broken requirements) |
| The silent-video-drop bug is real and the vendored shim fixes it. Two different chunks (0-8 s glass of ice, 36-44 s silhouette) embed to cosine **1.0000000** without `_video_capable_class` and **0.644** with it. | one-off ablation on the 8-bit model; `Model.__call__` in `mlx_vlm/models/qwen3_vl_embedding` still has no `pixel_values_videos` parameter in 0.7.11 |
| Upstream sentrysearch's loader call no longer works on mlx-vlm 0.7.11: `load_embedding_model(path, config_overrides=...)` raises "got multiple values for keyword argument 'config_overrides'". The vendored copy falls back to passing a ready-made config. | first run of `scripts/scene_index.py` |
| The embedding model reads on-screen text but does not hear. Speech prompts only retrieve when the words are also burned in as captions. | table below, "speech, audio only" row |
| gemma4 as judge hallucinates matches (it called a law-firm slide "a rolled joint and white powder" at confidence 0.95; its confidence is 0.9-0.95 for nearly every answer). | `scene_bench.py judge` rows |
| Hugging Face downloads stalled with the default xet transport; `HF_HUB_DISABLE_XET=1` gave ~2 MB/s. | download logs |

## Results

Embedding models (3 repos x the same 101 chunks; each source indexed by its own `scene_index.py` process, so RSS is that of one real job):

| Model | Index s per source-minute | Projected min per source hour | Peak RSS (MLX GPU peak) | Top-5 recall, pooled over all 6 sources (all 34 / 30 prompts whose content is visible) | Top-5 recall inside the labelled source (chance 0.50) | Cosine, first chunks of different sources (max / min) |
|---|---|---|---|---|---|---|
| `mlx-community/Qwen3-VL-Embedding-2B-4bit` | 4.44 | 4.4 (+2 s load) | 2.5 GB (2.9 GB) | 0.794 / 0.867 | 0.853 | 0.62 / 0.40 |
| **`mlx-community/Qwen3-VL-Embedding-2B-8bit`** | **4.44** | **4.4 (+2 s load)** | **3.0 GB (3.8 GB)** | **0.853 / 0.900** | **0.882** | **0.55 / 0.37** |
| `arthurcollet/Qwen3-VL-Embedding-2B-mlx-4bit` | 3.97 | 4.0 (+2 s load) | 2.4 GB (2.9 GB) | 0.794 / 0.867 | 0.853 | 0.62 / 0.40 |

Top-5 recall of the 8-bit model by prompt type (pooled): visual 0.95 (n=20), emotion 0.80 (n=5), speech 0.67 (n=9). The speech prompts whose words are not captioned on screen (n=4, satellite video) reach 0.50, which is incidental visual overlap, not hearing. Top-1 pooled recall is 0.735 for all three. The first 4-bit run measured 3.21 s per source-minute while a download was running, so index speed varies about 3.2-4.4 on this machine; every row clears the gate by more than 2x.

Judges (candidates = the 5 best chunks inside the labelled source for each of the 34 prompts, 170 candidates, 4 frames of 512 px each, Ollama `/api/generate` with a JSON schema, `think:false`, temperature 0, `keep_alive:"60s"`):

| Judge | Precision | Recall | Accuracy | Latency mean / median / p95 | Notes |
|---|---|---|---|---|---|
| `gemma4:latest`, plan's schema and prompt | 0.660 | 0.660 | 0.788 | 1.59 s / 1.58 s / 1.75 s | 18 false positives; 3 of them are label gaps on inspection, so about 0.72 after adjudication, still below the gate |
| `gemma4:latest`, "describe first, be strict" prompt | 0.530 | 0.660 | 0.712 | 2.94 s / 2.74 s / 4.54 s | worse and slower; not used |
| `gemma4:latest`, same prompt, candidates ranked across all sources (4-bit run) | 0.618 | 0.723 | 0.800 | 1.61 s / - / 1.87 s | harder scope, same story |
| **`qwen2.5vl:7b`**, plan's schema and prompt | **0.844** | 0.717 | 0.871 | 5.79 s / 2.95 s / 10.75 s (first call 12.3 s cold) | 7 false positives, at least 2 of them label-boundary gaps |

## Gate

| Gate | Result | Outcome |
|---|---|---|
| Index <= 10 min per source hour | 4.4 min (8-bit) | pass |
| Top-5 recall >= 70 % | 0.853 on all 34 prompts, 0.900 on the 30 whose content is visible; per-source 0.882 vs 0.50 by chance | pass; 8B embedding not needed, not downloaded |
| Judge precision >= 80 % | gemma4 0.660 (fail) -> qwen2.5vl:7b 0.844 | pass on the fallback of the ladder |
| Cosine of two different chunks < 0.99 | 0.644 (shim on) vs 1.0000000 (shim off); 0.55 max across sources in the benchmark; 0.584 on the generated two-scene clip | pass |

## Decision

1. **Embedding:** `mlx-community/Qwen3-VL-Embedding-2B-8bit` (Apache-2.0), 768 dimensions (Matryoshka truncation, re-normalised), 8 s chunks with no overlap, video sampled at 1 fps inside each chunk. It beats both 4-bit repos on recall (0.853 vs 0.794) at the same speed for 0.5 GB more memory. The two 4-bit repos give identical hits, so there is nothing to gain from the community conversion. `scripts/scene_index.py` defaults to it; `YTVIDEO_SCENE_MODEL` or `--model` overrides it.
2. **Judge:** `qwen2.5vl:7b` through the existing Ollama service, run only on the top-k candidates after indexing has finished (about 9 GB resident, so never beside the 3.8 GB MLX process). `gemma4:latest` stays the cheap option (1.6 s a call, 3.5 GB) but misses the gate; a Phase 2 setting may offer it, labelled as less precise.
3. **Speech is not the embedding's job.** "Find where he says X" is answered from the faster-whisper transcript; the embedding covers what is seen, written on screen, and the mood of faces.
4. **Sidecar venv.** `.venv-mlx` (Python 3.14, `requirements-scene.txt`) stays separate from `.venv-314` because mlx-vlm pulls its own fastapi/uvicorn/starlette and depends on mlx-vlm internals. The API talks to `scripts/scene_index.py` by subprocess: `index [flags] -- <video> <out_dir>` writes `chunks.json` and then `embeddings.npy` (n x 768, float32, L2-normalised), and streams `{"done": i, "total": n}` lines on stdout; `query [flags] -- <text>` prints one vector.
   - **Callers MUST pass `--` before the positionals.** Without it argparse reads the query `--help` as a flag (prints usage, exit 0) and `-dog` as an unknown option (exit 2); a video path or text that starts with `-` is only safe after `--`.
   - **Index into a fresh directory.** `embeddings.npy` is written last, so its presence means the index is complete; an old index in the same directory is replaced file by file and a crash between the two renames would pair new chunks with old vectors. `load_index(out_dir)` rejects a torn index (rows != chunks, wrong dimension, missing file).
   - **Cancel by SIGTERM.** The process exits 143, removes its temp chunk directory and kills the ffmpeg child; nothing is published to `<out_dir>`. Temp dirs left by SIGKILL or a crash (`scene_index_*` in the temp root) are swept at the next start once older than 6 hours.
5. **Vendoring.** sentrysearch is vendored at 3694dd0 (`VENDOR.md`, `app/vendor/sentrysearch/README.md`) with chromadb, google-genai, click and torch left out. The one-pass `render_chunk()` replaces upstream's stream-copy cut, which snaps chunk starts to keyframes and would put clip boundaries seconds off.

Recreate the sidecar: `scripts/setup_scene_venv.sh` (needs `/opt/homebrew/bin/python3.14`; `PYTHON=...` to override). The raw numbers behind the tables are committed as `scripts/scene_bench_results.json`. Re-run the measurements: `.venv-mlx/bin/python scripts/scene_bench.py embed --models <ids>` then `... judge --embed-model <id> --judge-models gemma4:latest qwen2.5vl:7b`.

## Consequences

- Indexing a one-hour source costs about 4-5 minutes of GPU and 3-4 GB of memory; transcription (CPU) can run beside it.
- The first search on a new machine downloads about 2.7 GB of weights, and the judge needs `ollama pull qwen2.5vl:7b`. Phase 2 must treat both as "not set up" states and fall back to today's behaviour.
- Phase 2 stores the index per source and searches per source (owner decision); pooled retrieval across sources was measured only to keep the benchmark honest, since short sources make per-source top-5 easy (chance 0.50).
- `mlx_embedder.py` depends on mlx-vlm internals; `requirements-scene.txt` holds mlx-vlm at 0.7.11 (`<0.9`). Upgrading means re-running the opt-in integration test (`tests/integration/test_scene_index_cli.py`, cosine between different chunks < 0.99) before anything else.
- `--skip-still` is available but off by default: it leaves a zero row for a near-static chunk, which would also hide a slide with a voice-over.

## Caveats (read before trusting the numbers)

- 34 prompts from one labeller, labelled from frames sampled every 4 s plus a transcript. Spans have 1-3 s boundary error, and several spans are wide (a whole 40 s shot). Recall is optimistic for the wide ones; judge precision is pessimistic where the judge found a true match the labels missed (three of gemma4's 18 false positives and at least two of qwen2.5vl's 7 were such cases).
- The gemma4 prompt variants were tried on the same 34 prompts they are scored on. The qwen2.5vl result uses the plan's original prompt untouched, so it carries no tuning bias.
- Five of the six sources are portrait social clips with burned-in captions, which flatters retrieval of caption text.
- Peak RSS is `ru_maxrss` of the indexing process; MLX GPU memory is reported separately by `mx.get_peak_memory()`.
- qwen2.5vl:7b latency is bimodal on this machine (median 2.95 s, p95 10.75 s); budget about 6 s a candidate, so a top-10 judge pass takes about a minute.
