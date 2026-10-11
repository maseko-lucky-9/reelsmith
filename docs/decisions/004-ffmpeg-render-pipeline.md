# ADR-004: One-Pass ffmpeg Render Pipeline (MoviePy Removed)

**Status:** Accepted
**Date:** 2026-10-09
**Author:** Thulani Maseko
**Implemented in:** PR #17 (P1), with follow-ups in PR #16 (P0 fixtures and golden tests) and PR #18 (P2 dependency pins)

## Context

Rendering was the slowest and most memory-hungry part of the pipeline, and the output it produced was not portable:

- **MoviePy 1.0.3.** Rendering was built on the last 1.x release. Upstream development has moved to the incompatible 2.x API, so the 1.x line receives no fixes. It also needed `app/compat.py` to patch deprecated stdlib names before every import.
- **One full-canvas image per caption word.** Every word became a full-canvas RGBA `ImageClip` (about 66 MB each at 1920x3413, measured), and all of them were held in memory at once.
- **Two x264 encodes per chapter.** `clip_service` first wrote the chapter to an intermediate mp4 (medium preset), then `render_service` decoded and encoded it again.
- **Non-portable output.** A 1920x1080 source produced a 1920x3413 **yuv444p** (High 4:4:4) file. MoviePy skips `-pix_fmt yuv420p` when a dimension is odd, and many players and platforms reject the result.

The product constraints still apply: captions must look identical, stay in sync with the audio word for word, keep the source-width output, and keep the reel's original audio.

## Decision

Render each chapter in **one ffmpeg process that reads the source directly**, and remove MoviePy.

- **Binaries and probes.** `app/services/ffmpeg_tools.py` runs the ffmpeg shipped by `imageio-ffmpeg` and uses PyAV for duration, fps and frame grabs. It never uses a system ffmpeg: Homebrew builds differ in their filters (for example, no libass or drawtext). imageio-ffmpeg ships no ffprobe, which is why probing goes through PyAV. `run()` kills the child process on timeout, on cancel and on any `BaseException`.
- **Captions.** The PIL renderer (`subtitle_image_service.create_subtitle_image`) is **unchanged**. `app/services/caption_track.py` draws each unique `(text, highlight)` caption once. It then crops every PNG to one shared rectangle: the union of all alpha bounding boxes, expanded outward to even x, y, w and h. The PNGs are fed to ffmpeg as **one** ffconcat image-sequence input with `option framerate 1000` per entry. Expanding the rectangle only adds transparent pixels, so the composite is pixel-identical to overlaying the full canvas, and memory is O(1) in the number of unique captions.
- **Filtergraph** (`app/services/render_service.py`, documented in the module docstring): trim with `-ss/-t` on the source, add a blurred background still that is decoded once and looped, overlay the inset, overlay the captions, crop to even width and height, then `format=yuv420p`. Video is libx264 ultrafast CRF 28 and audio is AAC. The output is written to a hidden `.partial.mp4` file in the same directory and moved into place with `os.replace`.

## Consequences

### Measured (PR #17, single runs on an M-series Mac)

The input was a 75.9 s 1920x1080 video with 3 chapters, transcribed with real Whisper `base`. The MoviePy baseline came from a `main` worktree using the same venv.

| Metric | MoviePy | ffmpeg one-pass | Change |
|---|---:|---:|---:|
| Wall clock (s) | 158.60 | 17.74 | -88.8% |
| Render stage (s) | 138.53 | 12.02 | -91.3% |
| Peak RSS, self (MiB) | 4750 | 1258 | -73.5% |
| Output | yuv444p 1920x3413 | yuv420p 1920x3412 | even dimensions |

### Deliberate behaviour changes

| Change | Detail |
|---|---|
| VFR → CFR at the average rate | `ffmpeg_tools.fps()` returns the stream's `average_rate`, and renders are written at that constant rate (`-r`). MoviePy used the container's nominal rate instead, for example 120 fps for a phone VFR clip. |
| Video may lead audio by ≤ 1 frame | The legacy renderer lagged instead. ffmpeg snaps the first kept frame to the nearest point on the output grid, while MoviePy showed the frame whose interval contained the source time (floor). `tests/sync_checker.py` accepts frame errors in `(-1, +1)` and rejects a whole-frame slip. |
| Blend rounding | ffmpeg blends in YUV and rounds, while MoviePy truncated, so composited pixels can differ by about ±1 LSB. The lossless sync tests allow ±2 against a float-blend oracle. 4:2:0 output already differs from 4:4:4. |
| Inset at an even y | `render_service.inset_y` rounds the centred inset's top row down to even, which can be up to 1 px from the legacy position (for example, 776 instead of 777 for a 1280x720 source). |
| Nearest-frame inset sync | The inset overlay uses `ts_sync_mode=nearest`. mkv/webm store 1 ms timestamps, so frame k can sit up to 0.5 ms either side of k/FPS, and the default "last frame <= t" rule jitters. |
| Exact time grid | The background is stamped with `settb=1/lcm(fps_num, 1000)`, so both the frame grid and millisecond caption starts are exact. When that lcm would overflow ffmpeg's 32-bit time base (huge VFR average numerators), `render_service.grid_rate` falls back to `fps.limit_denominator(1001)`. |
| ffconcat needs `-safe 0` | The concat demuxer rejects per-entry `option` directives (here `option framerate 1000`) in safe mode. The list uses filenames relative to its own directory, so user paths are never interpolated into it. |
| Bundled ffmpeg only | The ffmpeg binary comes only from imageio-ffmpeg (7.1 on the dev Mac; CI's Linux wheel was reported as 7.0.2 in PR #17). |

### Caption-identity guards

- **PNG equality (platform-independent).** `tests/unit/test_caption_track.py` asserts that every cropped caption PNG equals the matching window of the full `create_subtitle_image` canvas, pixel for pixel.
- **Golden hashes.** `tests/unit/test_subtitle_image_golden.py` stores the sha256 of the caption pixels, keyed by `(raqm available, platform.system(), platform.machine())`. `(True, "Darwin", "arm64")` and `(True, "Linux", "x86_64")` (the CI runner) are seeded. Other platforms skip and print the hash they computed.
- **A/V sync.** `tests/sync_checker.py` decodes renders of the P0 fixtures (frame-index bit blocks and an audio click track) to check sync.

### Dependency notes

- **av 18 colour conversion (PR #18).** av 18 bundles FFmpeg 8.1.2. Its YUV→RGB conversion in `grab_frame` (thumbnails and the blurred background still) differs from av 17 by up to 3 LSB (mean 1.155). Decoding is identical, and captions and encoding are unaffected. One rotation test tolerance was widened from `< 1.0` to `< 2.0`.
- **`av` is pinned at 18.1.0.** 19.x breaks faster-whisper 1.2.1 (`open() got an unexpected keyword argument 'metadata_errors'`). `tests/integration/test_whisper_real.py` fails with that TypeError under `av==19.0.1`.
- `imageio-ffmpeg==0.6.0` is a direct dependency. `moviepy` and `app/compat.py` are gone, so the old "import compat before MoviePy" rule no longer applies.

### Trade-offs accepted

- Full-frame SSIM against MoviePy is not used as a gate, because the sizes and frame rates differ. Caption identity is proven by PNG equality and the per-frame caption-band match instead.
- `grab_frame` ignores the container's `start_time` (cosmetic, noted in PR #17).

## Addendum: B-roll inserts (T012, render half)

`render_clip(..., broll=[BrollInsert(path, start, duration)])` overlays up to four B-roll clips in the same one-pass graph. The orchestrator's `_broll_step` passes them (see *Pipeline half* below). The full graph is in the `render_service` module docstring.

- **Placement.** Each insert is one more input (`-stream_loop -1 -t LEN -i insert`, after the captions input so captions stay `[2:v]`). Its chain is cover-fitted to the canvas (`scale=W:H:force_original_aspect_ratio=increase,crop=W:H,setsar=1,format=yuv420p`) and overlaid at `0:0` on the composite (letterbox or pan crop), in start order, **before** the caption overlay, so captions stay on top.
- **The pts trap.** An input starts at its own pts 0. Without a shift, a window that starts after the insert's length finds it already at EOF (nothing shown with `eof_action=pass`, a frozen last frame with the default `repeatlast`). The chain starts `setpts=PTS-STARTPTS+T0/TB,fps=FPS`: `T0` is the time of the first output frame in the window, so the insert's frame 0 lands exactly on it, and `fps` puts every insert frame on the clip's frame grid, so overlay's default sync is exact.
- **Window.** Output frame k shows the insert iff `start <= k/FPS < start + duration`, computed in exact rationals. The enable gate's bounds sit half a frame before the first covered and the first uncovered frame, so a floating-point `t` cannot flip an edge frame (a naive inclusive `between(t,T,T+D)` shows one frame too many when `T+D` falls exactly on a frame). `LEN` is the covered span: a shorter insert loops, and `eof_action=pass` keeps anything from lingering.
- **Grid and audio.** Overlay emits one frame per main-input frame with that frame's timestamp, so the `settb` grid, frame count and duration equal the render without B-roll. Nearest-frame sync of the inset, even dimensions and yuv420p are unchanged. Insert audio is never mapped (only `-map 0:a:0?`).
- **Validation** (`validate_broll`, `ValueError` before any work): files exist, `duration > 0`, `start >= 0`, `start + duration <=` the clip duration (compared at microseconds), no overlaps (touching windows are fine), at most 4, sorted by start. `broll=None` or `[]` leaves the argv byte-identical.
- **Verified** by `tests/e2e/test_render_broll.py` on real renders: per-frame pixel sampling of the windows (frame-exact edges, including frame-aligned ones), caption band over the insert, base frames and source indices outside the windows, identical frame times and decoded audio with and without B-roll, a looped short insert that starts on its own frame 0, and a cover fit of another aspect.

### Pipeline half (T012): planner, providers, wiring

- **Opt-in.** `YTVIDEO_BROLL_PROVIDER` defaults to `none`; then each rendered chapter emits `StageSkipped(broll, "no provider")` and `render_clip` gets `broll=None` (argv byte-identical). The `broll` job option (default on) still gates the step.
- **Planner** (`broll_planner.plan_broll`, pure, stdlib plus the proposer's tokenizer and stopwords): at most two 3 s windows, start >= 3.0 s, end <= duration - 2.0 s, >= 1 s apart, so every plan passes `validate_broll`. Query = longest qualifying token spoken in the window; ranking by query length, then earliest start; one window per query. Rationale: the hook stays on the speaker, the outro is not cut away from, and a long concrete word is a cheap, deterministic stand-in for "visual noun" without spaCy (not installed).
- **Providers.** `local`: keyword-named `*.mp4` in `YTVIDEO_BROLL_LIBRARY_DIR`, whole-word, case- and plural-insensitive, name order. `pexels`: `GET https://api.pexels.com/videos/search` (`orientation=portrait`), the key only in the search request's `Authorization` header and never logged; the first video with an mp4 at most 1080 px wide (portrait preferred, then widest); downloads only over https from `pexels.com` / `*.pexels.com` (SSRF guard; the link comes from the response), no redirects, 50 MB cap enforced on `Content-Length` and while streaming, explicit timeouts plus a 120 s deadline; cache by Pexels video id with atomic writes, so a cached query needs no network. The Pexels documentation's examples still show `player.vimeo.com` links; the live API returns `videos.pexels.com/video-files/...`, which the allowlist accepts, and a Vimeo link would be rejected (no insert).
- **Fetch.** Two queries at a time on the event loop (`fetch_all`); a failure, a miss or a file that does not decode (PyAV probe) drops that insert only.
- **Failure model.** Any error in the step emits `StageSkipped(broll, reason)` and renders without B-roll; `CancelledError` propagates. A failure inside ffmpeg caused by a decodable-but-broken asset would still fail that render (the probe decodes only the first frame).
- **Attribution.** `clips.broll_assets` stores `query, start, duration, provider, asset_id, author, source_url, path`; the job export manifest and the bulk-export zip's manifest have a `broll_credits` column listing `{provider, author, source_url}` per distinct asset. The Pexels API guidelines ask to credit the videographer and link Pexels; the UI does both (T039, `web/src/components/broll-credits.tsx`).
