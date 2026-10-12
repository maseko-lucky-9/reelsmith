# ADR-008: No GPU Encode on Apple Silicon; Cap the Reel Canvas and Prefer H.264 Instead

**Status:** Accepted
**Date:** 2026-10-12
**Author:** Thulani Maseko
**Implements:** T053, T054, T055 (spec 008)

## Context

The ask was to use the Mac's GPU before the CPU for video processing. Every reel is encoded with software `libx264 -preset ultrafast -crf 28` (`app/services/render_service.py`), and the render is the slowest stage of a job. We measured before changing anything (M5 Pro, bundled imageio-ffmpeg 7.1 arm64, `ffmpeg -encoders` lists `h264_videotoolbox`; PyAV 18 reports a `videotoolbox` hwdevice).

| Experiment | libx264 / software | VideoToolbox |
|---|---|---|
| Encode 20 s at reel size (1920x3412), to the null muxer | 2.19 s | 6.48 s |
| Encode 20 s at 1080p | 0.47 s | 2.26 s |
| Decode 25 s of 1080p H.264 | 0.30 s | 1.88 s (`-hwaccel videotoolbox`) |
| Three concurrent 1080p encodes | no scaling either | 8.86 s total, one at a time |

- The media engine tops out at about 600 Mpx/s; x264 ultrafast on this chip is faster. `-q:v`, `-prio_speed`, `-g` and `-threads` do not change that.
- VideoToolbox cannot encode a frame taller than 4096 px (rc=187, `-12903`), so every 1440p or 4K source would fail on the uncapped canvas.
- The bundled build has no AV1 hardware decode. faster-whisper (CTranslate2 4.8.2) has no Metal backend (`unsupported device mps`).

The costs that were real:

1. **The canvas.** It is source width x 16/9: 1920x3413 for a 1080p source, 3840x6826 for 4K. The blur, every caption PNG and the encode scale with it.
2. **The source codec.** yt-dlp's `bestvideo[ext=mp4]` picks the highest-bitrate codec: VP9 on one real video (format 616), AV1 on another (a file in `/tmp/yt`). libaom decodes 1080p AV1 about 10x slower than H.264 (3.1 s vs 0.08 s per 20 s).

## Decision

1. **No VideoToolbox encode or decode, not even opt-in.** It is slower on this hardware, and an opt-in switch would only be a way to make renders slower (owner decision, 2026-10-12). `libx264` stays the encoder; the Whisper and YuNet CPU paths stay too.
2. **Cap the reel canvas at 1080 px wide** (`clip_service.MAX_CANVAS_WIDTH`, a constant, not a setting). `ReelGeometry.inset_height` is the source as scaled to the canvas, matching `scale=W:-2` (half-up to even). `source_size` stays the real source so crop tracks keep source pixels. Sources at or under 1080 px wide are unchanged; trim renders (no captions) keep the source size.
3. **Prefer H.264 downloads:** `bestvideo[ext=mp4][vcodec^=avc1]+bestaudio[ext=m4a]` first, then the old chain. avc1 tops out at 1080p, which is all a 1080-wide canvas can use.

## Consequences

Measured on the same content (two 30 s chapters of a 1080p video, stub transcription, `scripts/bench.py`):

| | wall | render stage | peak RSS | output |
|---|---:|---:|---:|---|
| before (AV1 source, 1920x3412) | 13.05 s | 12.04 s | 778 MiB | 17 MB |
| cap only (same AV1 source) | 10.39 s | 9.53 s | 379 MiB | 9 MB, 1080x1920 |
| cap + H.264 source | 3.22 s | 2.36 s | 348 MiB | 8 MB, 1080x1920 |

A captioned 30 s chapter (70 words) renders in 2.40 s instead of 5.57 s; caption PNGs take 1.42 s instead of 3.36 s.

- **Reels are 1080x1920, not 1920x3412.** That is the standard reel size, and the files are half as big.
- **Captions look about 1.8x larger on a 1080p source** than before: the font is a fixed 96 px (`subtitle_image_service.py`), which is what a native 1080-wide source has always got. Tracked as T056.
- **Face-track crops of a 4K source lose resolution** when avc1 (1080p max) is chosen over a 4K VP9/AV1 stream. The reel is 1080 wide, so only the zoomed-in crop of a wide frame is affected.
- The blur radius is a fixed 40 px, so on the smaller canvas the background is relatively blurrier.
- Caption drawing is still about 60% of a captioned render (T057).
- Revisit VideoToolbox only with a different workload (for example hevc output, or a much larger encode than this app's 1080-wide reels) and a fresh measurement; the numbers above are for this app on an M5 Pro.
