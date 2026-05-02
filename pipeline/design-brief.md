# Design Brief — Word-Synced Subtitles

## Goal
Replace uniform-rate caption splitting with real per-word timestamps so subtitles pop on-screen in sync with speech. Keep MoviePy/PIL rendering pipeline unchanged; the change is concentrated in transcription + caption generation.

## The load-bearing decision: transcription backend

### Verified constraint
`SpeechRecognition.recognize_google` (the free Google Web Speech endpoint) **does not** return word-level timestamps under any flag, including `show_all=True`. This is confirmed by reading `app/services/transcription_service.py:23` and by the `SpeechRecognition` library's documented response shape (alternatives list with `transcript` + `confidence` only). The acceptance criterion AC-5 ("no new pip dependency unless justified") therefore puts a real fork in the road.

### Three options considered

| # | Option | New dep | Model size | Accuracy of word timing | Offline | Notes |
|---|---|---|---|---|---|---|
| A | Heuristic forced alignment using existing transcript + audio (silence detection via stdlib `wave` + `audioop`, or via MoviePy's audio array) | none | none | Poor — word boundaries are guessed from silence gaps; misses for fast speech, music beds, or no-silence segments | yes | Preserves AC-5 strictly; output quality is the worst |
| B | `openai-whisper` (or `faster-whisper`) | yes (`openai-whisper` ~30MB pkg, `tiny`/`base` model 75-150MB download) | 75-150MB (small models) | Excellent — native word timestamps via `word_timestamps=True` | yes (after first download) | First-class word timing; CPU inference acceptable on macOS for short clips |
| C | `vosk` | yes (~5MB pkg, en-us-small model ~50MB) | 50MB | Good — word timings via `Result.words` | yes | Smaller footprint than Whisper; lower accuracy on noisy audio; still good for clean voice tracks |

### Chosen: Option B — `faster-whisper`

**Rationale:**
- This is a learning-project YouTube reels pipeline. Word-sync subtitles are **the** feature; landing them with poor timing (Option A) would defeat the goal.
- `faster-whisper` (CTranslate2-backed) gives 2-4× the CPU throughput of `openai-whisper` for the same accuracy, and runs comfortably on macOS without GPU.
- Word timestamps are first-class: `model.transcribe(..., word_timestamps=True)` yields `[Segment(words=[Word(start, end, word, probability), ...])]`.
- AC-5's "unless justified" clause is satisfied: the existing backend physically cannot meet AC-1 — a swap is required.
- Whisper also subsumes the existing functionality (returns the plain transcript via `" ".join(w.word for w in words)`), so we can deprecate the `recognize_google` path or keep it as a fallback for environments without the model.

**Justified addition to `requirements.txt`:** `faster-whisper` (single new dep). Model files are downloaded on first use into a cache directory (HF hub default `~/.cache/huggingface`); no model checked into the repo.

**Rejected — Option A:** quality ceiling is too low for the stated goal.
**Rejected — Option C:** smaller, but Whisper's accuracy advantage on real-world YouTube audio (music beds, multiple speakers, accents) is decisive for this learning project.

## Other Design Decisions

### D1. Module shape
Add a new function `transcribe_with_word_timing(audio_path, language) -> list[WordTiming]` in `app/services/transcription_service.py`, where `WordTiming` is a typed dict / dataclass `{word: str, start: float, end: float}`. Keep `speech_to_text(audio_path, language) -> str` for backwards compatibility (it can either be re-implemented on top of `transcribe_with_word_timing` or left as the legacy Google path — see D6).

### D2. Caption granularity
Add a setting `YTVIDEO_CAPTION_WORDS_PER_SEGMENT` (default **2**). Rationale: 1-word/karaoke is jittery and hard to read on short reels; 2 words is the readability sweet spot for vertical short-form video. Configurable so callers can opt into pure karaoke (1) or phrase mode (3-4).

### D3. New caption-generation path
Add `generate_captions_from_word_timings(word_timings, format, words_per_segment) -> SubRipFile | WebVTT` in `app/services/caption_service.py`. Each output segment's `start` = first word's `start`, `end` = last word's `end`. Existing `generate_captions(text, start, end, format)` is preserved as legacy code path for callers without word timings.

### D4. Stub provider
When `settings.transcription_provider == "stub"`, return a deterministic `[WordTiming]` list — e.g. five fake words evenly spaced over the audio's known duration. Tests that previously asserted `"stub transcription text for testing"` continue to assert the same plain-text equivalent (` ".join(w.word for w in stub)`).

### D5. Settings additions
- `YTVIDEO_CAPTION_WORDS_PER_SEGMENT: int = 2`
- `YTVIDEO_TRANSCRIPTION_PROVIDER` already exists; new valid value: `"whisper"`. Existing values `"stub"` and `"google"` retained.
- `YTVIDEO_WHISPER_MODEL: str = "base"` (one of `tiny`, `base`, `small`, `medium`, `large`). `base` chosen as default for accuracy/speed balance.

### D6. Backwards compatibility
- `speech_to_text(...)` keeps its signature and returns `str`. Internally:
  - if provider == "google": existing path (no word timings).
  - if provider == "whisper": delegate to `transcribe_with_word_timing(...)` then join words.
  - if provider == "stub": return existing placeholder string.
- `transcribe_with_word_timing(...)` is the new function. For provider == "google" it raises `NotImplementedError` ("Google backend does not return word timings; use 'whisper' or 'stub'"). Orchestrator must check provider before calling.

### D7. Orchestrator wiring
The orchestrator already calls `speech_to_text` once per chapter. Add a new code path that, when the provider supports word timings, calls `transcribe_with_word_timing` and threads the result into `generate_captions_from_word_timings`. When not supported, fall back to the existing uniform-rate path so users on the `google` backend still get captions (just not word-synced).

### D8. Test strategy
- Unit test: a synthetic `WordTiming` list → `generate_captions_from_word_timings` produces the expected number of SubRipItems with the right `start`/`end` values for `words_per_segment` ∈ {1, 2, 3}.
- Unit test: `generate_captions_from_word_timings` with empty input → empty SubRipFile.
- Unit test: stub-provider `transcribe_with_word_timing` is deterministic.
- Integration test (marked `live`, opt-in): real whisper inference on a 5-second fixture WAV, asserting that at least N>=2 words are returned with monotonically increasing `start` values. Fixture audio kept small (<200KB) and committed.
- The existing default `pytest` run must remain green — `live`-marked tests excluded by default per `pyproject.toml`.

### D9. Render path is unchanged
`subtitle_image_service.create_subtitle_image()` and `clip_service.add_captions_to_clip()` already iterate per-segment and stamp at `caption.start` (`clip_service.py:140-145`). Going from ~12 long segments to ~80 short segments per chapter is functionally equivalent — just more `ImageClip` instances composited. No code change needed there.

## Trade-offs Accepted

- **First-run cost**: downloading the `base` Whisper model (~150MB) on first transcription. Acceptable for a local learning project; cached afterwards.
- **CPU time**: Whisper inference on `base` model ≈ 0.3-0.5× realtime on Apple Silicon CPU (i.e. a 60-second chapter takes 20-30s to transcribe). Existing Google backend is faster (~2s) but lacks timing. Trade-off accepted because word-sync is the feature.
- **Whisper hallucination on silence**: known issue; mitigated by `vad_filter=True` (built into `faster-whisper`).
- **macOS-only verification path**: no Linux/Windows CI runs in this repo today; the new dep is pure-Python wheels for all three OSs, low risk.

## Open Questions
- **OQ1**: Confirm the user accepts the `faster-whisper` dependency addition. If rejected, fall back to Option A (heuristic) and accept lower accuracy.
- **OQ2**: Confirm default `YTVIDEO_CAPTION_WORDS_PER_SEGMENT = 2`. Alternatives: 1 (karaoke), 3 (phrase).
- **OQ3**: Confirm the `google` backend stays as a non-word-timing fallback (i.e. it still works but doesn't produce karaoke captions), versus removing it entirely.

## Approval

Awaiting explicit user approval of:
1. The transcription backend choice (Option B — `faster-whisper`).
2. Default words-per-segment = 2.
3. Keeping `google` provider as a non-word-timing fallback.

Once approved, Phase 1 (Implementation Plan) begins.
