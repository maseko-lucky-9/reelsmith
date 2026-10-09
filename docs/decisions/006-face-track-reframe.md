# ADR-006: Face-Tracked Reframe with YuNet on onnxruntime

**Status:** Accepted (provider default stays `letterbox` until the owner signs off gate G2)
**Date:** 2026-10-09
**Author:** Thulani Maseko
**Implements:** FR-010 (reframe half of T012)
**Numbering:** merged as `005-face-track-reframe.md` (PR #52) alongside [ADR-005](005-api-route-prefix.md) (PR #50); renumbered to 006 in the Spec Kit close-out. The content is unchanged.

## Context

`render_service.render_clip(crop_track=...)` (PR #44) can pan a full-height 9:16 window across a landscape source, but nothing produced a track: every reel was a letterboxed inset over a blurred background, so a speaker in a 1920x1080 talk filled about a third of the reel's width. The scaffolded `FaceTrackReframe` needed MediaPipe and OpenCV, neither of which is a dependency, so `face_track` silently fell back.

The detector had to run on CPU inside the existing worker threads, add no heavy dependency, and be reproducible.

## Spike S1 (60 frames sampled evenly across each of two real talks)

Sources: a Wikimania 2025 lightning talk (1920x1080 VP9, 236 s, a speaker inset beside slides, audience cutaways) and "What is theatre capable of" at TEDxSydney (854x480 VP9, 585 s, a dark stage, wide shots, crowds). Hit rates were judged by viewing every frame with its boxes drawn.

| Candidate | Wikimania frames with a face box | TEDx frames with a face box | False positives seen | Median ms/frame |
|---|---|---|---|---|
| (A) YuNet 2023mar, onnxruntime, frame fitted into 640 px | 56/60 (all 50 frames showing the speaker, and he is the largest box in each) | 17/60 (11 multi-face crowd frames) | 0 | 4.44 / 4.47 |
| (A) same, frame fitted into 320 px | 50/60 (audience faces missed) | 11/60 | 1 (the back of a head, score 0.71) | 4.31 / 4.34 |
| (B) `cv2.FaceDetectorYN`, same model, 640 px | 56/60 | 17/60 | 0 | 3.96 (cv 5.0) / 4.39 (cv 4.14) |
| (B) Haar frontal cascade, 320 / 640 px | 1/60 / 14/60 | 2/60 / 8/60 | 0 | 1.78 / 4.94 |

- The (A) decoder matched OpenCV's on the same frames to within 0.02 px and 0.0001 in score (60/60 Wikimania frames, 59/60 TEDx frames with the same face count).
- The YuNet ONNX graph has a fixed 1x3x640x640 input, so a 320 px frame costs the same model time as a 640 px one. It finds fewer faces and produced the only false positive. Frames are therefore fitted into 640 px, not the 320 px first planned.
- (B) would add `opencv-python-headless` (a 48.3 MB wheel). Version 5.0 also drops `CascadeClassifier` and ships its own `libavdevice`, which clashes with PyAV's at load time (an objc duplicate-class warning).

## Decision

- **Detector.** YuNet 2023mar (MIT, Shiqi Yu, from opencv_zoo) runs through the already-installed `onnxruntime` with our own pre- and post-processing in `app/services/face_detector.py`, behind a `FaceDetector` protocol. No requirements change.
  - The model is not committed. On first use it is downloaded from a pinned opencv_zoo commit into `YTVIDEO_REFRAME_MODEL_DIR` (default `data/models`, gitignored).
  - A download is accepted only if its SHA-256 equals the digest in the repository's Git LFS pointer (`8f2383e4…2552fa4`, 232,589 bytes). Nothing downloads at import or in the default test run.
- **Track.** `reframe_service` builds the track:
  - **Sampling.** PyAV decodes the chapter at 2 fps, plus its last frame, on the render's `-ss` clock, with display rotation applied.
  - **Faces.** Faces below a 0.6 score or shorter than 5 % of the frame height are ignored. The primary face is the largest; faces within 10 % of its area go to the one nearest the frame centre.
  - **Gaps.** A frame without a face holds the last position.
  - **Smoothing.** A zero-phase EMA (forward and backward, padded by reflection, so no lag), then a dead zone of 0.1 window widths, then a speed cap of 0.5 window widths per second. The crop never snaps.
  - **Keyframes.** At most 64, chosen by greedy top-down simplification that keeps both endpoints.
- **Fallbacks.** The reel keeps the letterbox exactly as before, and `StageSkipped(reframe, reason)` is emitted, when any of these holds:
  - a split screen (`active_speaker_service.detect_split_screen`);
  - several faces of similar size in most face frames;
  - no face in the clip;
  - a source with no horizontal pan room;
  - any error, including a failed download.
- **Wiring.** The orchestrator's `_reframe_step` runs only when the job's `reframe` option is on and `YTVIDEO_REFRAME_PROVIDER=face_track`. It runs in `to_thread_cancellable` (a cancel stops the decode within one frame) for renders and re-renders alike. Cancellation propagates.
- **Default.** `letterbox`. The owner switches it after reviewing the G2 reels.

## Consequences

- **Cost.** Measured on the G2 runs: 0.49 to 0.68 s per 40 to 45 s chapter, including the decode, which is about 6 to 8 ms per sample on an M-series Mac. The first call also pays for the 232 KB download: about 0.8 s here, two HTTP requests through GitHub's LFS redirect.
- **Network.** The first face-tracked render needs network access once. Offline, the reel falls back to the letterbox with the download error as the skip reason.
- **Known limits (G2).**
  - A camera cut is followed by a speed-capped pan, not a hard cut: up to about 2 s to cross a 1080p frame.
  - Wide shots where the face is too small to detect hold the last position, so a small subject can sit at the window's edge.
  - Slides and credits are cropped like any other content.
  - Two people of similar size, or crowds, fall back to the letterbox for the whole clip.
- **Not used.** `smooth_cues` from `active_speaker_service`: its count-window average has no speed cap. MediaPipe and OpenCV are not dependencies.
