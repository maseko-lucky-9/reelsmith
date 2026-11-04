# Vendored third-party code

## makiisthenes/TiktokAutoUploader

- **Source:** https://github.com/makiisthenes/TiktokAutoUploader
- **Commit:** 73475dbb67be5d8e5e7181af665fbf7f0db7fff4
- **Vendored:** 2026-06-02
- **Location:** `app/vendor/tiktok_uploader/`
- **Why vendored:** No PyPI release; the upload path requires a co-located
  `tiktok-signature/` Node.js bundle (`playwright-chromium`) that must sit
  relative to the Python module. Vendoring keeps both together and pins the
  exact revision.
- **What was excluded:** `Browser.py` (Selenium/undetected-chromedriver),
  `Video.py` (moviepy/yt-dlp). `__init__.py` was rewritten to export only
  what the adapter uses.
- **Local modifications:** `Video.py` (upstream's MoviePy/yt-dlp clip
  helper) was deleted when ReelSmith dropped MoviePy (perf P1). Nothing in the
  adapter path used it — `tiktok.py` only imported the name — so `Video` was
  also dropped from the `from tiktok_uploader import ...` line in `tiktok.py`.
  Re-vendoring upstream brings both back; delete them again (MoviePy is not a
  dependency).
- **Upstream changes:** pin this file before upgrading. Breaking changes are
  common — TikTok frequently rotates API endpoints and anti-bot measures.
- **Setup:** `scripts/tiktok-setup.sh` installs the Node bundle. See
  `requirements-tiktok.txt` for Python deps.

## maseko-lucky-9/sentrysearch

- **Source:** https://github.com/maseko-lucky-9/sentrysearch (branch `master`, Apache-2.0)
- **Commit:** 3694dd0cb27cc57920b2b816996ce5a22662e45c
- **Vendored:** 2026-10-28
- **Location:** `app/vendor/sentrysearch/` (file-by-file status in its `README.md`)
- **Why vendored:** The fork is not on PyPI and is a CLI around chromadb, google-genai, click
  and torch. Reelsmith needs only its chunk spans, still-frame check, the MLX
  Qwen3-VL-Embedding embedder (with the `_video_capable_class` shim that stops mlx-vlm
  silently dropping video pixels) and some numpy/parse helpers. Vendoring pins the exact
  revision and keeps those four heavy dependencies out of both venvs. ADR-013.
- **What was excluded:** `store.py` (chromadb), the cloud/litellm/Gemini embedders and
  rerankers, `local_embedder.py` and `qwen_reranker.py` (torch), `cli.py` (click),
  `trimmer.py`, `overlay.py`, `metadata.py`, `dashcam_pb2.py`, `dlq.py`, `search.py`.
- **Local modifications:** `chunker.py` trimmed, bundled imageio-ffmpeg only, and gained
  `render_chunk()` (one-pass accurate cut to 480p/5fps; upstream's stream-copy cut snaps
  to keyframes); `highlights.py` takes `(embeddings, metas)` instead of a store;
  `reranker.py` keeps only the pure parse helpers; `mlx_embedder.py` changes an error
  message and adds `_load_forced_embedding_model()` (upstream's `config_overrides=` call to
  mlx-vlm's `load_embedding_model` raises a duplicate-keyword TypeError on mlx-vlm 0.7.11);
  `base_embedder.py` only gains the header. Every changed file says so in its
  header (Apache-2.0 section 4(b)).
- **Upstream changes:** `mlx_embedder.py` depends on mlx-vlm internals
  (`mlx_vlm.embedding_loader`, `models.qwen3_vl_embedding`, `utils.prepare_inputs`), so
  `requirements-scene.txt` pins `mlx-vlm` below 0.9. Re-test
  `tests/integration/test_scene_index_cli.py` (cosine between different chunks < 0.99)
  before upgrading mlx-vlm; the shim can be deleted once upstream forwards
  `pixel_values_videos`.
- **Setup:** `scripts/setup_scene_venv.sh` recreates `.venv-mlx` from `requirements-scene.txt`.

## maseko-lucky-9/OpenCut (draft: completed by spec 017 T158)

- **Source:** fork `maseko-lucky-9/OpenCut` of https://github.com/OpenCut-app/OpenCut (MIT,
  "Copyright 2025 OpenCut"), local checkout `~/Repo/experiments/demo/OpenCut`
- **Commit:** 3037a7ecbf23ac8db4a3a237eecfd72af9068258 (2025-07-12, "fix: handle border")
- **Vendored:** 2026-11-04
- **Location:** `web/src/editor/opencut/` (upstream file names kept, relative to
  `apps/web/src/`); the MIT text is `web/src/editor/LICENSE-OpenCut`. Every file starts with
  `// Copied from OpenCut (MIT) apps/web/src/<path> @ 3037a7e` (or `Adapted from`) and a line
  naming its local changes; `web/src/editor/vendor.test.ts` checks the headers.
- **Why vendored:** OpenCut is a Next.js app, not a package. Reelsmith takes its browser
  timeline as the clip editor's editing surface over timeline schema v2 (ADR-015, spec 017).
  New runtime dependency: `zustand` (exact pin in `web/package.json`).
- **Copied so far (T141):** `types/timeline.ts`, `types/playback.ts`,
  `constants/timeline-constants.ts`, `lib/time.ts`, `stores/playback-store.ts`,
  `hooks/use-selection-box.ts`, `hooks/use-timeline-zoom.ts`,
  `components/editor/selection-box.tsx` (as `components/selection-box.tsx`),
  `components/editor/properties-panel/property-item.tsx` (as
  `components/properties-panel/property-item.tsx`).
- **Local modifications so far:** `types/timeline.ts`: `MediaType` is a local
  `'image' | 'video' | 'audio'` union and `generateUUID()` is `crypto.randomUUID()`;
  `use-timeline-zoom.ts`: React 19 `RefObject<HTMLDivElement | null>`; `use-selection-box.ts`:
  debug `console.log` calls and an unused rectangle removed; `selection-box.tsx`: no
  `"use client"`, fill via `color-mix()` over the app's oklch `--foreground`; imports re-pointed
  from `@/` to `@/editor/opencut/`. Files are formatted by Prettier's defaults.
- **Adapted so far (T144):** `stores/timeline-store.ts`: `storageService`, the media, project
  and editor stores, `separateAudio`, `replaceElementMedia`, project load/save and auto-save
  removed; tracks carry Reelsmith roles and schema v2 origins (`web/src/editor/types.ts`);
  history holds whole snapshots capped at 50 and redo stays undoable; segment split, trim and
  delete go through `web/src/lib/timelineEdit.ts`; refused edits return a reason; new `load`,
  `replaceFromTimeline`, `adoptTimeline`, `setSourceDuration`, `toTimeline`, `readOnly`.
- **Never taken:** media panel and import, `lib/storage/**` (IndexedDB/OPFS), ffmpeg.wasm
  (`lib/ffmpeg-utils.ts`, `lib/media-processing.ts`), the waveform (wavesurfer.js), the media,
  project, editor and panel stores (spec 017 FR-023; research R2 has the full inventory).
