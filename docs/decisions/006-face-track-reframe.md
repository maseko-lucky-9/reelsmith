# ADR-006: Face-Tracked Reframe with YuNet on onnxruntime

**Status:** Accepted (provider default stays `letterbox` until the owner signs off gate G2)
**Date:** 2026-10-09
**Author:** Thulani Maseko
**Implements:** FR-010 (reframe half of T012)
**Amended:** 2026-10-10 by T041: primary-face continuity, small faces in wide shots, the face-share fallback; *Known limits* rewritten; revised after the PR #67 review. G2 is still open (T046).
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
  - **Faces.** A face counts from 5 % of the frame height at a 0.6 score, or, in a wide shot, from 2.5 % at a 0.8 score (T041). A frame's main face is the largest; faces within 10 % of its area go to the one nearest the frame centre.
  - **Primary face (T041).** The crop follows one subject. A face within half a window width horizontally of the previous primary, and at least half its height, continues it (the frame's main face if it is one of them, otherwise the nearest), even when another face is larger. Another face takes over only after being the frame's main face for 1.5 s, and the switch is back-dated to its first sample, so a real change of speaker adds no lag. While there is a primary, a face only ever seen under 5 % of the frame height needs 3 s. A competitor's reach grows with the time since its last sighting (one half-window per 0.5 s), so a moving face detected every other sample stays one subject. Durations allow 0.125 s for frame-time jitter, so a face must be on screen 1.5 to 2.0 s, depending on where its shot falls between samples. A clip opens without a primary, so an opening cutaway is treated the same way.
  - **Gaps.** A frame without the primary face holds the last position.
  - **Smoothing.** A zero-phase EMA (forward and backward, padded by reflection, so no lag), then a dead zone of 0.1 window widths, then a speed cap of 0.5 window widths per second. The crop never snaps.
  - **Keyframes.** At most 64, chosen by greedy top-down simplification that keeps both endpoints.
- **Fallbacks.** The reel keeps the letterbox exactly as before, and `StageSkipped(reframe, reason)` is emitted, when any of these holds:
  - a split screen (`active_speaker_service.detect_split_screen`);
  - several faces of similar size in most face frames (these two tests count only faces of at least 5 % of the frame height, as before T041, so a small poster or audience face cannot trigger them);
  - no face in the clip, or a usable face in fewer than half the samples (slides, credits, shots the detector cannot read; T041);
  - a source with no horizontal pan room;
  - any error, including a failed download.
- **Wiring.** The orchestrator's `_reframe_step` runs only when the job's `reframe` option is on and `YTVIDEO_REFRAME_PROVIDER=face_track`. It runs in `to_thread_cancellable` (a cancel stops the decode within one frame) for renders and re-renders alike. Cancellation propagates.
- **Default.** `letterbox`. The owner switches it after reviewing the G2 reels (T046).

## Consequences

- **Cost.** Measured on the G2 runs: 0.49 to 0.68 s per 40 to 45 s chapter, including the decode, which is about 6 to 8 ms per sample on an M-series Mac. The first call also pays for the 232 KB download: about 0.8 s here, two HTTP requests through GitHub's LFS redirect.
- **Network.** The first face-tracked render needs network access once. Offline, the reel falls back to the letterbox with the download error as the skip reason.
- **Known limits (G2).** T041 (2026-10-10) fixed three of them in `reframe_service` (constants and reasons in the module) without touching the render graph. The G2 sign-off is still open and the default stays `letterbox`.
  - **Fixed: an audience cutaway pulled the crop.** A cutaway shorter than 1.5 s no longer moves it. Real run (YuNet, the G2 sources, main `952f49d` against T041): in chapter W2 of the Wikimania talk, an 8 s audience cutaway (29 to 37 s) moved the crop up to 112 px (0.18 window widths) and it was still 61 px off at the end of the reel. With T041 it stays at 0 throughout. The cutaway's one face of at least 5 % appears in two samples (29.0 and 29.5 s), and its other faces are smaller and score under 0.8.
  - **Fixed: small faces in wide shots were ignored.** Faces from 2.5 % of the frame height now count at a 0.8 score instead of the position being held. Covered by synthetic tests; on the two G2 talks it changed no track. Their one real case is the host speaking on the Wikimania stage in the opening wide shot: 3.2 to 4.4 % of the frame height, scores 0.73 to 0.86, at least 0.8 in 4 samples (checked by eye at 2.5 and 7.0 s). A blurred foreground audience head (16 to 17 %, scores 0.64 to 0.75) is the larger face in that shot, so the crop went to it before T041 and still does (see *the largest face wins a frame* below).
  - **Fixed in part: slides and credits.** A clip with a usable face in fewer than half its samples now falls back to the letterbox as a whole. On the G2 TEDx chapters this letterboxes T1 (29 of 91 samples) and T2 (30 of 81), which were face-tracked before.
  - **Remains: mixed clips.** Crop-window logic cannot letterbox part of a clip. A clip that mixes a talking head with slides, and shows a face in at least half its samples, is still cropped on the slides. A clip below that share is letterboxed throughout, talking head included.
  - **Remains: long cutaways.** A cutaway whose main face stays on screen for 1.5 to 2.0 s or more (the effective wait, see *Primary face*) is followed as a change of speaker. A competitor only ever seen small waits 3 s, so a speaker who goes undetected for longer than that with a small audience face in view still loses the crop to it (synthetic: undetected 3.5 s, the crop pans 408 px on 1080p and comes back).
  - **Remains: short opening shots.** An opening shot shorter than 1.5 s gets the next subject's position (it is treated like a cutaway), and a clip that cuts between subjects faster than every 1.5 s from its start gets the last subject's position throughout.
  - **Remains: fast movers.** A face that moves more than half a window between consecutive samples (350 px per 0.5 s on 1080p) is a new subject at every sample: the crop holds until the face slows down, then goes to where it stopped.
  - **Remains: the largest face wins a frame.** A large, unsure foreground audience head beats a small, clear speaker (the Wikimania opening above). Weighting the main-face choice by score is not done; it would need its own evidence.
  - **Remains: faces the detector misses.** YuNet reported no face under 2 % of the frame height in the G2 talks, and none of the performer on the dark TEDx stage in wide shots. Those stretches still hold the last position, or letterbox the clip when they make up more than half of it.
  - **Remains: a new speaker in a clip's last 1.5 s** is not followed (it cannot be told from a cutaway). The same holds for the same speaker reappearing more than half a window away, after a camera cut, in the last 1.5 s.
  - A camera cut is followed by a speed-capped pan, not a hard cut: up to about 2 s to cross a 1080p frame.
  - Two people of similar size, or crowds, fall back to the letterbox for the whole clip.
- **Not used.** `smooth_cues` from `active_speaker_service`: its count-window average has no speed cap. MediaPipe and OpenCV are not dependencies.
