# Feature Specification: Render speed on Apple Silicon

**Feature**: `008-render-speed` | **Date**: 2026-10-12 | **Decision**: [ADR-008](../../docs/decisions/008-no-gpu-encode.md)

## Problem

Rendering is the slowest stage of a job (12 s of 13 s for two 30 s chapters of a 1080p video). The request was to use the Mac's GPU before the CPU.

## What was measured

The VideoToolbox encoder is 3-5x slower than the current `libx264 -preset ultrafast` on this app's reels, so the GPU path is not built (ADR-008 has the table). The time goes to three other things: a 1920x3413 canvas for a 1080p source, a non-H.264 (VP9 or AV1) source download, and caption PNGs drawn at canvas size.

## Requirements

- **FR-001**: A reel's canvas MUST be at most 1080 px wide, `int(width / ratio)` tall (1080x1920 for 9:16). Sources at or under 1080 px wide keep their width. Trim renders keep the source size.
- **FR-002**: The inset's height MUST equal what ffmpeg's `scale=W:-2` produces (half-up to even), so the inset, the pan window and the caption anchor line up.
- **FR-003**: `ReelGeometry.source_size` MUST stay the real source size: crop tracks are in source pixels.
- **FR-004**: The blurred background MUST be the frame's centre, cover-scaled to the canvas (no corner crop, no black bands).
- **FR-005**: The default download format MUST prefer H.264 (`avc1`) video and fall back to the previous chain when none exists.
- **FR-006**: The render encoder stays `libx264`; there is no GPU encoder switch.

## Success criteria

- **SC-001**: Two 30 s chapters of a 1080p video: 13.05 s wall before, 3.22 s after (cap + H.264 source), peak RSS 778 to 348 MiB, output 17 MB to 8 MB (`scripts/bench.py --compare`).
- **SC-002**: A 1280x720 and a 1920x1080 source render to 1080x1920 h264 yuv420p, and the frame-exact A/V sync tests pass through the scaled inset.

## Status

| Requirement | State | Proving test |
|---|---|---|
| FR-001, FR-002, FR-003 | Implemented | `tests/unit/test_reel_canvas_cap.py`, `tests/e2e/test_render_canvas_cap.py`, `tests/unit/test_caption_entries_characterization.py` |
| FR-004 | Implemented | `test_background_still_covers_the_canvas_from_the_middle_of_the_frame` |
| FR-005 | Implemented | `test_download_prefers_h264_*`, `test_download_falls_back_to_av1_*` |
| FR-006 | Implemented (by decision) | none: nothing to test |

All new tests were mutation-checked: 11 deliberate breaks of the source, each turned a test red.
