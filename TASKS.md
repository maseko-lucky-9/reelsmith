# Reelsmith Parallel Factory — Tasks

Gap analysis mapping Opus Clip M-class features to Reelsmith. H-defer features (ClipAnything, AI Reframe, AI B-Roll, AI Editor, Team Workspace, Agent Opus) are intentionally excluded. One M feature (Animated Captions, styled variant) is promoted to **H-stub**.

Source-of-truth probe was performed against `app/services/`, `app/routers/`, `app/domain/events.py`, `app/db/models.py`, and `tests/`. The repo is already more advanced than a green-field starting point — every M feature has scaffolding. Tasks therefore target concrete missing extensions, not from-scratch implementations.

---

## T-01 thumbnail-text-composite
- **Source**: Opus Clip Thumbnail Generator
- **Gap**: `thumbnail_service.generate_thumbnail` extracts a midpoint frame but produces no text overlay. Opus Clip ships a frame + headline composite; Reelsmith stops at the raw frame.
- **Files touched (predicted)**: `app/services/thumbnail_service.py`, `tests/unit/test_thumbnail_service.py`
- **Independence**: none
- **Difficulty**: M
- **Required markers**: (default)
- **Acceptance criteria**:
  - New function `compose_thumbnail(clip_path, output_path, *, headline: str, font_path: str | None = None, position: str = "bottom") -> str` returns the output path.
  - Generated JPEG is 320x569 (existing `_TARGET_W/_TARGET_H`) with the headline rendered using Pillow `ImageDraw`, stroked black 2px + filled white by default.
  - Test asserts the produced file opens via PIL, has the expected dimensions, and that calling with `headline=""` produces a file byte-identical (or hash-identical) to `generate_thumbnail` for the same input.
  - Test asserts an `ImportError`-safe fallback path (mirroring the cv2 → moviepy pattern already present) when Pillow is unavailable raises a clear `ThumbnailError`.

## T-02 brand-vocabulary-tests
- **Source**: Opus Clip Brand Templates (vocabulary substitution sub-feature)
- **Gap**: `brand_vocabulary_service.py` exists with a case-preserving replacement engine, but `tests/unit/test_brand_vocabulary_service.py` does not exist. Untested code blocks brand-template parity claims.
- **Files touched (predicted)**: `tests/unit/test_brand_vocabulary_service.py`
- **Independence**: none
- **Difficulty**: M
- **Required markers**: (default)
- **Acceptance criteria**:
  - At least 6 parametrised cases covering: ALL-CAPS, Titlecase, lowercase, mixed-case-internal, single-character source, and word-boundary safety (don't replace "OpusClip" inside "OpusClipper").
  - Mapping `{"OpusClip": "ReelSmith"}` applied to `"OpusClip is great, OPUSCLIP rocks, opusclip rules"` yields case-preserved replacements per token.
  - Empty mapping is a pass-through (input == output).
  - Empty source string returns empty string without raising.

## T-03 voiceover-piper-provider
- **Source**: Opus Clip AI Voice-Over
- **Gap**: `voiceover_service.py` advertises `coqui | piper | stub` in its docstring, but only `stub` and a `coqui` branch exist. The `piper` provider is unimplemented, leaving the documented API incomplete.
- **Files touched (predicted)**: `app/services/voiceover_service.py`, `tests/unit/test_voiceover_service.py`
- **Independence**: none
- **Difficulty**: M
- **Required markers**: (default)
- **Acceptance criteria**:
  - Setting `YTVIDEO_VOICEOVER_PROVIDER=piper` causes `synthesise(text, out_path)` to build a piper argv `["piper", "--model", <path>, "--output_file", <out>]` and feed `text` on stdin via the patchable `_invoke` hook.
  - When the piper binary is missing (`shutil.which("piper") is None`), the service raises `VoiceoverError` with a message containing "piper" — does NOT silently fall back to stub.
  - Test patches `_invoke` and asserts argv shape; second test patches `shutil.which` to return None and asserts `VoiceoverError`.
  - The model path comes from `YTVIDEO_PIPER_MODEL`; missing env var raises `VoiceoverError` before invocation.

## T-04 xml-export-davinci-multitrack-stub
- **Source**: Opus Clip Export to XML
- **Gap**: `xml_export_service.render` emits single-clip XML for both Premiere and DaVinci. The service docstring acknowledges "Multi-track / overlay export ships in W2". Add a multi-track stub that includes a caption-overlay track entry referencing the clip's burnt captions, so downstream NLEs see two tracks.
- **Files touched (predicted)**: `app/templates/davinci_fcpxml.xml.j2`, `app/services/xml_export_service.py`, `tests/contract/test_xml_export_router.py`
- **Independence**: none
- **Difficulty**: M
- **Required markers**: (default)
- **Acceptance criteria**:
  - `render(clip, "davinci_fcpxml")` output contains exactly two `<spine>` or `<asset-clip>` entries when `clip.captions_burnt_path` is non-null; one entry otherwise.
  - Existing single-track test for premiere still passes unchanged.
  - New contract test hits `/api/clips/{id}/export.xml?format=davinci` for a clip with a captions path stored and asserts both track references appear in the response body.
  - Template uses `{% if clip.captions_burnt_path %}` guard so legacy clips render single-track.

## T-05 social-scheduler-list-endpoint
- **Source**: Opus Clip Social Scheduler
- **Gap**: `publish_scheduler.py` flips rows and `social_publish_service` runs them, but `app/routers/social_publish.py` only exposes a create endpoint. Opus Clip lets users see their queued/scheduled posts — Reelsmith has no `GET /api/social/jobs` listing or filter.
- **Files touched (predicted)**: `app/routers/social_publish.py`, `tests/contract/test_social_publish_router.py`
- **Independence**: none
- **Difficulty**: M
- **Required markers**: (default)
- **Acceptance criteria**:
  - New `GET /api/social/jobs?status=pending|queued|posting|published|failed&clip_id=<id>` returns a JSON list with fields `id, clip_id, status, schedule_at, posted_at, platform, external_url, error`.
  - Status filter is multi-valued (`?status=pending&status=queued` works) and absence returns all.
  - Empty result set returns `[]` with 200, not 404.
  - Contract test seeds two `PublishJob` rows of different statuses and asserts both filter paths.

## T-06 animated-caption-styled-burn (H-stub)
- **Source**: Opus Clip Animated Captions (97% ASR, word-level styled overlays)
- **Gap**: `animated_caption_service.py` produces frame descriptors and tuples but does not actually burn captions onto a video. The full Opus Clip experience requires word-level highlight timing from ASR. We stub the ASR-driven word timing (use evenly-spaced word boundaries from the existing transcript) but implement the burn-in path so a styled overlay actually composites onto the rendered MP4.
- **Files touched (predicted)**: `app/services/animated_caption_service.py`, `app/services/timeline_render_service.py`, `tests/unit/test_animated_caption_service_burn.py`
- **Independence**: none
- **Difficulty**: H-stub
- **Required markers**: (default; live ffmpeg path gated behind `integration`)
- **Acceptance criteria**:
  - New `burn_animated_captions(clip_path, srt_path, output_path, style: str = "hormozi") -> str` returns output_path on success.
  - Word-timing distribution is **stubbed**: each caption cue's words are evenly distributed across the cue's duration (real ASR word-timestamps deferred — documented in docstring as `STUB`).
  - Unit test patches the actual ffmpeg subprocess; asserts the argv contains `-vf "ass=<temp.ass>"` and an `.ass` file was written with one `Dialogue:` line per *word* (not per cue) for the `hormozi` style.
  - Test for `style="static"` produces one Dialogue line per *cue* (existing static behaviour preserved).
  - Integration test (skipped without ffmpeg) actually runs ffmpeg on a 3s fixture and asserts the output MP4 exists and is non-empty.

## T-07 audio-enhance-router
- **Source**: Opus Clip AI Audio Enhancement
- **Gap**: `audio_enhance_service.py` and `app/routers/enhance_speech.py` both exist, but the router targets a "speech-only" enhancement (single provider path). There is no HTTP endpoint that lets a caller select between `loudnorm | rnnoise | passthrough` providers documented in the service module.
- **Files touched (predicted)**: `app/routers/enhance_speech.py`, `tests/contract/test_enhance_speech_router.py`
- **Independence**: none
- **Difficulty**: M
- **Required markers**: (default)
- **Acceptance criteria**:
  - `POST /api/clips/{id}/enhance-audio` accepts `{"provider": "loudnorm" | "rnnoise" | "passthrough"}` and returns 202 with a job id.
  - Unknown provider returns 422 with a body listing the allowed values.
  - Test patches `audio_enhance_service._invoke` so no real ffmpeg runs; asserts the argv built matches the requested provider.
  - Missing clip → 404; clip without `output_path` → 409 (mirroring `xml_export` router conventions).

---

## VALIDATION
- Phantom paths (predicted file does not exist AND is not a new-creation): none — all predicted edits are either to files confirmed present via `ls` of `app/services/`, `app/routers/`, `app/templates/`, `tests/unit/`, `tests/contract/`, or are explicitly new test/template files declared as additions.
- Undeclared overlaps (two tasks share a predicted file but neither lists the other in Independence): none — each task touches a disjoint file set. Verified pairwise: T-01 (thumbnail_service), T-02 (brand_vocabulary tests-only), T-03 (voiceover_service), T-04 (xml_export_service + davinci template), T-05 (social_publish router), T-06 (animated_caption_service + timeline_render_service), T-07 (enhance_speech router). No file appears in two tasks.
- Conftest convention detected: **Option B (hard-coded)** — `conftest.py` filters Docker containers via the literal string `label=pytest=reelsmith` and does not read `REELSMITH_PYTEST_LABEL` or any env var. Factory orchestrator should hardcode `pytest=reelsmith` when starting test containers, or patch conftest to read an env var first.
