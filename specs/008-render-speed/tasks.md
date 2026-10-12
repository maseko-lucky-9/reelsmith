# Tasks: Render speed on Apple Silicon

**Input**: [spec.md](spec.md), [ADR-008](../../docs/decisions/008-no-gpu-encode.md)

- [x] T053 [FR-001..004] (done: reels render on a canvas at most 1080 px wide; `ReelGeometry.inset_height`; `background_still` scales the frame to the canvas before blurring; the 720p sync oracle now reads the scaled inset) Cap the reel canvas. Proving test: `tests/unit/test_reel_canvas_cap.py`, `tests/e2e/test_render_canvas_cap.py`
- [x] T054 [FR-005] (done: `bestvideo[ext=mp4][vcodec^=avc1]` first; on a real video the old string chose VP9 format 616 and the new one H.264 format 137) Prefer H.264 downloads. Proving test: `test_download_prefers_h264_*`
- [x] T055 [FR-006] (done: VideoToolbox is 3-5x slower and fails above 4096 px; recorded in ADR-008, nothing built) Measure GPU encode and decide
- [ ] T056 **Decision** Caption size on the capped canvas. The font is a fixed 96 px (`subtitle_image_service.py`), so captions are about 1.8x larger relative to the frame on a 1080p source than they were on the 1920 px canvas (same as a native 1080 px source). Keep, or scale the font with the canvas.
- [ ] T057 Draw each caption on a band-sized image instead of the full canvas (`caption_track._draw_own_crop`). After the cap, caption PNGs are 1.42 s of a 2.40 s captioned 30 s render (70 words). Start only if the render stage is still the bottleneck.
