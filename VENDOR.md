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

## maseko-lucky-9/OpenCut

- **Source:** fork `maseko-lucky-9/OpenCut` of https://github.com/OpenCut-app/OpenCut (MIT,
  "Copyright 2025 OpenCut"), local checkout `~/Repo/experiments/demo/OpenCut`
- **Commit:** 3037a7ecbf23ac8db4a3a237eecfd72af9068258 (2025-07-12, "fix: handle border")
- **Vendored:** 2026-11-04 (spec 017, ADR-015)
- **Location:** `web/src/editor/opencut/` (upstream file names kept, relative to
  `apps/web/src/`, except `components/editor/*` which is `components/*`). The MIT notice is
  `web/src/editor/LICENSE-OpenCut` ("Copyright 2025 OpenCut", text verbatim). Every file starts
  with `// Copied from OpenCut (MIT) apps/web/src/<path> @ 3037a7e` (or `Adapted from`), then a
  `Local changes:` line and a `Licence:` pointer; `web/src/editor/vendor.test.ts` checks the
  headers, the licence text and that every file below is named in this entry.
- **Why vendored:** OpenCut is a Next.js app, not a package. Reelsmith takes its browser
  timeline as the clip editor's editing surface over timeline schema v2 (ADR-015, spec 017).
  New runtime dependency: `zustand` (exact pin in `web/package.json`).

### Copied unchanged or with import-only edits (paths under `web/src/editor/opencut/`)

| File | Local changes |
|---|---|
| `types/timeline.ts` | `MediaType` is a local `'image' \| 'video' \| 'audio'` union; `generateUUID()` is `crypto.randomUUID()` |
| `types/playback.ts` | none |
| `constants/timeline-constants.ts` | `TrackType` import re-pointed to `@/editor/opencut/` |
| `lib/time.ts` | none |
| `stores/playback-store.ts` | types import re-pointed |
| `hooks/use-selection-box.ts` | debug `console.log` calls and an unused rectangle removed (`noUnusedLocals`) |
| `hooks/use-timeline-zoom.ts` | React 19 `RefObject<HTMLDivElement \| null>`, imported as a type |
| `components/selection-box.tsx` (upstream `components/editor/selection-box.tsx`) | no `"use client"`; fill via `color-mix()` over the app's oklch `--foreground` |
| `components/properties-panel/property-item.tsx` (upstream `components/editor/properties-panel/property-item.tsx`) | none |

### Adapted (paths under `web/src/editor/opencut/`)

| File | Local changes |
|---|---|
| `stores/timeline-store.ts` | `storageService`, the media, project and editor stores, `separateAudio`, `replaceElementMedia`, project load/save and auto-save removed; tracks carry Reelsmith roles and schema v2 origins (`web/src/editor/types.ts`); history holds whole snapshots capped at 50 and redo stays undoable; segment split, trim and delete go through `web/src/lib/timelineEdit.ts`; refused edits return a reason; new `load`, `replaceFromTimeline`, `adoptTimeline`, `setSourceDuration`, `toTimeline`, `readOnly` |
| `hooks/use-playback-controls.ts` | the key map of research R12 (Space, S, Delete/Backspace, Cmd/Ctrl+Z, Shift+Cmd/Ctrl+Z, Cmd/Ctrl+Y); upstream's Cmd/Ctrl+S/Q/W/D removed; keys ignored while typing and inside an open menu; split gated (R15) |
| `hooks/use-timeline-playhead.ts` | constant 30 fps instead of the project store's; React 19 ref types; Base UI scroll viewports; `handlePlayheadKeyDown` added |
| `hooks/use-timeline-element-resize.ts` | no media store; edge drags preview locally and commit once on release; segment edges are source seconds bounded by the source and neighbours, committed through `trimSegment` |
| `components/timeline.tsx` (upstream `components/editor/timeline.tsx`) | no media/project store or drops; Radix to the Base UI wrappers; Tailwind 4 renames; `require()` to an import; constant 30 fps; toolbar of play/pause, split, keep left/right, delete, undo, redo, zoom |
| `components/timeline-track.tsx` (upstream `components/editor/timeline-track.tsx`) | no media/project store and no drops; the drag ends on the dragged element's track; cross-track moves refused for other roles (FR-009) |
| `components/timeline-element.tsx` (upstream `components/editor/timeline-element.tsx`) | Radix menus to the Base UI wrappers; no media store, thumbnails or images; voice-over is a plain bar (no wavesurfer); menu items only where they apply |
| `components/timeline-playhead.tsx` (upstream `components/editor/timeline-playhead.tsx`) | presentational (the timeline owns the playhead hook); no refs read during render; keyboard operable as a slider |
| `components/preview-panel.tsx` (upstream `components/editor/preview-panel.tsx`, with the sync logic of `components/ui/video-player.tsx`) | one `<video>` of the job source (`GET /jobs/{id}/source`) played through the segments, reel time to source time via `previewClock`, a seek at each segment boundary, the 0.5 s drift correction on the reel clock; 9:16 frame laid out as the render's `reel_geometry` (`previewLayout`); captions in the render's font and looks |
| `components/properties-panel/index.tsx` (upstream `components/editor/properties-panel/index.tsx`) | no project/media store, aspect ratio, FPS select or media/audio properties; captions and text overlays get text properties, other selections an empty state; Base UI `ScrollArea` |
| `components/properties-panel/text-properties.tsx` (upstream `components/editor/properties-panel/text-properties.tsx`) | schema v2 fields only (R9): caption text, overlay text, font size, colour, x/y; no `FontPicker`, rotation, opacity, background, alignment, weight or decoration; commit on blur/Enter, one undo step per edit |

### Rewritten, not vendored

- `web/src/components/ui/context-menu.tsx`, `menu.tsx`, `tooltip.tsx`, `scroll-area.tsx`,
  `slider.tsx`, `textarea.tsx` (T146): new shadcn-style wrappers on `@base-ui/react` for the
  ported components. OpenCut's own `components/ui/*` are Radix and were not copied, so these
  files carry no OpenCut header and no OpenCut code; the other files in that directory
  (`badge`, `button`, `card`, `input`, `label`, `progress`, `select`, `sheet`, `skeleton`,
  `sonner`, `tabs`) are Reelsmith's.
- Reelsmith-owned beside the copy in `web/src/editor/`: `adapter.ts`, `types.ts`,
  `useClipEdit.ts`, `renderedCut.ts`, `previewClock.ts`, `previewLayout.ts`, `editor.css`,
  `caption-looks.json`, `reel-geometry-cases.json`.

### Never taken

Media panel and import (`components/editor/media-panel/**`), `lib/storage/**` (IndexedDB/OPFS),
ffmpeg.wasm (`lib/ffmpeg-utils.ts`, `lib/media-processing.ts`), the waveform
(`components/editor/audio-waveform.tsx`, wavesurfer.js), `timeline-toolbar.tsx` and
`speed-control.tsx` (Radix; unused or no speed in schema v2), the media, project, editor and
panel stores, and `app/editor/[project_id]/page.tsx` (the TanStack route
`web/src/routes/clips.$clipId.edit.tsx` is the page). Spec 017 FR-023; research R2 has the
inventory. `vendor.test.ts` fails if `wavesurfer.js`, `@ffmpeg/*`, `radix-ui` or `next` becomes
a dependency.

### Upgrade notes

- React 19: ref types are `RefObject<T | null>`; the compiler lint forbids reading refs during
  render (why `timeline-playhead.tsx` is presentational).
- Base UI (`@base-ui/react` 1.8.0), not Radix: scroll viewports are
  `[data-slot="scroll-area-viewport"]`; menus and tooltips come from `web/src/components/ui/`.
- Tailwind 4: class renames applied in `timeline.tsx` (research R14).
- Key map: keep `use-playback-controls.ts` to the R12 list; do not re-take upstream's
  Cmd/Ctrl+S/Q/W/D.
- To upgrade: diff the new upstream file against the `@ 3037a7e` original, re-apply each
  `Local changes:` line, bump the revision in every header and in `OPENCUT_REVISION`, then run
  `cd web && pnpm vitest run src/editor` (header, licence, VENDOR.md coverage and the timeline
  tests).
