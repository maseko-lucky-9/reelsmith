# Implementation Plan: Render speed on Apple Silicon

**Spec**: [spec.md](spec.md) | **Decision**: [ADR-008](../../docs/decisions/008-no-gpu-encode.md)

- **Canvas cap.** `clip_service.MAX_CANVAS_WIDTH` (constant) and `ReelGeometry.inset_height`. `render_service.inset_y` and `background_still` use them. The pan chain, B-roll and captions already read `canvas_size`.
- **Downloads.** `_yt_dlp_base._DEFAULT_FORMAT` tries `[vcodec^=avc1]` first.
- **No new setting, dependency or ffmpeg flag.** Constitution III (one ffmpeg) is untouched.
- **Verification.** `pytest` (default run), `scripts/bench.py --compare` before and after on the same source, and frames of old and new captioned reels.
