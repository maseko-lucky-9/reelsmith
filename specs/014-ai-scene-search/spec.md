# Feature Specification: AI scene search from a prompt

**Feature**: `014-ai-scene-search` | **Date**: 2026-10-29 | **Plan**: [plan.md](plan.md) | **Decision**: [ADR-013](../../docs/decisions/013-local-scene-search-models.md)

## Problem

A prompt today is keyword overlap on the transcript (`segment_proposer._prompt_overlap`): no frame is ever looked at, windows need 40 % speech, so "every shot of a red car" can never match, and the prompt is silently ignored when the source has YouTube chapters or `segment_provider=chapter` (the default). `/jobs/new` and `/uploads/new` take no prompt at all. Phase 0 (ADR-013) measured a local stack that can do better: Qwen3-VL-Embedding 2B (MLX, 8-bit) in a sidecar venv finds what is seen (top-5 recall 0.85 on 34 labelled prompts), and `qwen2.5vl:7b` through Ollama judges candidates at 0.844 precision. The embedding does not hear, so speech is matched through the transcript.

## Requirements

### Search

- **FR-001**: A job, upload or reprompt with a non-blank prompt MUST use scene search when `YTVIDEO_SCENE_SEARCH_PROVIDER` is `mlx`, or `auto` (the default) and `YTVIDEO_SCENE_PYTHON` exists; `off`, an unknown value or no prompt MUST keep today's behaviour. A job's `segment_proposer=false` option still means one Full Video clip.
- **FR-002**: With scene search, the prompt MUST pick the clips whatever the source's YouTube chapters and the job's `segment_mode`, and a reprompt MUST NOT be refused for `segment_provider=chapter`.
- **FR-003**: The source MUST be indexed by `scripts/scene_index.py` in the sidecar venv, called with `--` before its positionals, into a fresh directory that is swapped into `<job folder>/scenes/` only once complete. The index MUST be reused while the source's size and mtime, the model and the index format are unchanged (`meta.json`), and rebuilt otherwise.
- **FR-004**: Indexing MUST run beside the whole-source transcription, as a child process: a cancel of the job MUST SIGTERM it (it removes its temp files and publishes nothing), SIGKILL its process group after a grace period, and end the job `cancelled`. A timeout (`YTVIDEO_SCENE_INDEX_TIMEOUT_SECONDS`) stops it the same way.
- **FR-005**: The score of a chunk MUST be `max(visual, speech)`, each side calibrated on its own: visual is the cosine of the prompt and the chunk; speech is the best cosine of the prompt and a transcript sentence overlapping the chunk, both sentences and prompt embedded as text by the sidecar's `embed-texts`. Sentence vectors MUST be cached in `scenes/` so a reprompt embeds only its prompt.
- **FR-006**: Adjacent chunks at or above the hit threshold MUST merge into one moment; without any hit the best few chunks are the candidates. Each moment MUST become a window inside the clip length range (`clip_length_range`): cut to the maximum around its best chunk or grown to the minimum around its centre, then snapped out to sentence boundaries when it has speech or padded when it is silent. Overlapping windows and visual near-duplicates MUST be dropped.
- **FR-007**: A vision judge MUST look at the best `YTVIDEO_SCENE_JUDGE_MAX_CANDIDATES` (8) candidates, one Ollama call each (`YTVIDEO_OLLAMA_VISION_MODEL`, 4 frames of the span that matched, JSON-schema `format`, `think:false`, temperature 0), within `YTVIDEO_SCENE_JUDGE_BUDGET_SECONDS`. A candidate found by its speech MUST be judged on its transcript (same model, text only). A non-match MUST be dropped; a match MUST keep its place with `blend(similarity, confidence)` and carry the judge's title, reason and emotion.
- **FR-008**: The prompt and the transcript MUST reach the judge only as delimited data (`_as_data`: one line, no angle brackets, byte-capped), and only the schema's fields of the reply are read.
- **FR-009**: Scene search MUST never fail a job: an index or embedding failure falls back to today's path (with `StageSkipped(scene_search)`), Ollama off, down, slow, missing the model or out of budget leaves the unjudged candidates in similarity order, and a search that keeps nothing falls back too. A reprompt whose search keeps nothing MUST fail softly (`RepromptFailed`) and keep the old clips.
- **FR-010**: The kept moments MUST go through the unchanged `select_discovered` → `segments_to_chapters` → `_process_chapter` path (render, captions, export). The judge's title becomes the clip title, its reason the summary (unless social copy replaces it) and both reason and emotion are stored in the clip's `score_breakdown` JSON: no migration.

### Progress and UI

- **FR-011**: Indexing MUST emit `SceneIndexProgress` (`done`, `total` chunks; at most one a second, first and last always) and then `ScenesIndexed` (`chunks`, `reused`, `seconds`). The web client MUST listen for both (drift test) and show "Indexing scenes n/m" on the progress bar and the active "Detect chapters" row, and during a reprompt on the job page.
- **FR-012**: `/jobs/new` and `/uploads/new` MUST offer an optional prompt (≤ 2000 characters, blank = none) with help text; `POST /uploads` MUST accept it as a form field and store and queue it like `POST /jobs`. The reprompt form changes only its help text.
- **FR-013**: Clip cards (grid and list) MUST show the judge's emotion as a chip and its reason when present.

### Configuration

- **FR-014**: The settings MUST be `scene_search_provider` (`auto`), `scene_python` (`<repo>/.venv-mlx/bin/python`), `scene_model` (`mlx-community/Qwen3-VL-Embedding-2B-8bit`), `scene_index_timeout_seconds` (3600), `ollama_vision_model` (`qwen2.5vl:7b`), `scene_judge_max_candidates` (8), `scene_judge_budget_seconds` (120), all `YTVIDEO_`-prefixed and documented in `.env.example`. No existing default changes; no `.env` edit is needed.

## Threats

- **T1 Prompt injection** (prompt or transcript tells the judge to say `match=true`): the text is flattened data inside one delimited block with its brackets removed, the instructions say it is never an instruction, and a reply can only set the five schema fields; titles and reasons are cut and flattened before storage and rendered as text by React.
- **T2 Argument injection into the sidecar** (a video path or prompt starting with `-`): every positional follows `--`, and the prompt reaches `embed-texts` in a JSON file, never in argv.
- **T3 Orphan processes** (the sidecar's ffmpeg after a kill): the sidecar runs in its own session and the SIGKILL goes to the whole group.

## Known limitations (accepted)

- The judge holds a 9 GB model; it runs after indexing has finished and Ollama unloads it after 60 s (`keep_alive`). Its latency was 1-13 s a call on this machine depending on load (ADR-013: mean 5.8 s), so a full pass of 8 candidates can take a minute or more.
- Calibration constants and the hit threshold come from the same 34 labelled prompts they are scored on (plan.md); five of the six sources are portrait social clips.
- A speech moment's window is snapped out to whole sentences, so a 7 s sentence can come back as a 50 s clip when the clip length range allows it (T109).
- An emotion prompt is only as good as the faces in the 4 frames: "a person who looks excited and happy" found nothing on the satellite talk (T108).

## Out of scope

- Library-wide search across sources, hands-free highlights with no prompt, a speech-emotion model, choosing `gemma4:latest` as judge from the UI.

## Status

| Requirement | State | Proving test |
|---|---|---|
| FR-001 | Done | `tests/unit/test_scene_search.py::test_enabled_needs_a_prompt_and_the_provider`, `tests/unit/test_orchestrator_scene_search.py::test_without_scene_search_the_youtube_chapters_stay` |
| FR-002 | Done | `test_a_prompt_overrides_the_youtube_chapters`, `test_reprompt_with_scene_search_needs_no_discovery_provider`, `tests/contract/test_reprompt_router.py::test_reprompt_with_a_prompt_scene_search_takes_needs_no_clip_discovery` |
| FR-003 | Done | `test_ensure_index_builds_then_reuses_then_rebuilds_on_source_change`, `test_a_failed_index_raises_and_keeps_the_previous_one`, `test_a_torn_or_foreign_index_is_not_current`, `test_reprompt_reuses_the_scene_index_and_rebuilds_it_when_the_source_changes`, `tests/unit/test_scene_vendor.py::test_embed_texts_takes_its_positionals_after_double_dash` |
| FR-004 | Done | `test_cancel_during_indexing_stops_the_sidecar_and_ends_cancelled`, `tests/unit/test_child_process.py` (all four) |
| FR-005 | Done | `test_a_silent_moment_is_found_by_what_is_seen`, `test_a_moment_found_by_speech_snaps_to_its_sentences`, `test_calibration_maps_floor_to_zero_match_to_half_and_clamps`, `test_sentence_vectors_are_cached_so_a_new_prompt_embeds_only_itself` |
| FR-006 | Done | `test_adjacent_hits_merge_into_one_moment_and_misses_split_moments`, `test_chunks_below_the_threshold_are_not_hits`, `test_without_any_hit_the_best_chunks_are_the_candidates`, `test_snapping_that_would_pass_the_maximum_snaps_inwards`, `test_a_long_moment_is_cut_to_the_maximum_around_its_peak`, `test_overlapping_windows_keep_only_the_better_one`, `test_visual_near_duplicates_are_deduped` |
| FR-007 | Done | `tests/unit/test_scene_judge.py` (match/non-match, request shape, focus span, speech on transcript, budget, cap), `tests/integration/test_scene_search_real.py::test_the_vision_judge_confirms_the_fractal_and_rejects_the_test_card` |
| FR-008 | Done | `test_prompt_injection_in_the_query_stays_inert_data`, `test_parse_verdict_cleans_the_text_fields` |
| FR-009 | Done | `test_ollama_down_or_slow_falls_back_to_similarity_order`, `test_an_unusable_reply_leaves_that_candidate_unjudged`, `test_a_failed_index_falls_back_to_the_chapters`, `test_no_matching_moment_falls_back_to_the_chapters`, `test_a_failed_index_hands_its_words_to_discovery`, `test_reprompt_with_no_matching_moment_fails_and_keeps_the_clips` |
| FR-010 | Done | `test_the_judges_title_reason_and_emotion_reach_the_clip` (memory and SQLite stores), `tests/unit/test_alembic_parity.py` (no migration) |
| FR-011 | Done | `web/src/lib/sceneIndexProgress.test.ts`, `web/src/components/job-progress-timeline.scenes.test.tsx`, `web/src/lib/pipelineStages.test.ts` (drift) |
| FR-012 | Done | `web/src/routes/jobs.new.test.tsx` (prompt), `web/src/routes/uploads.new.test.tsx`, `tests/contract/test_uploads_router.py::test_upload_passes_its_prompt_to_the_job` |
| FR-013 | Done | `web/src/components/dashboard/SceneMatch.test.tsx` |
| FR-014 | Done | `test_settings_defaults`, `tests/unit/test_settings_module.py::test_every_setting_is_documented_in_env_example` |
