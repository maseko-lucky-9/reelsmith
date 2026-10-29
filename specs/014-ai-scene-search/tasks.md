# Tasks: AI scene search from a prompt

**Input**: [spec.md](spec.md), [plan.md](plan.md)

- [x] T093 [FR-014] (done: seven `scene_*`/`ollama_vision_model` settings, `.env.example`, scene search pinned off in the test `conftest.py`) Settings. Proving test: `tests/unit/test_scene_search.py::test_settings_defaults`, `tests/unit/test_settings_module.py`
- [x] T094 [FR-003,005] (done: `embed-texts` subcommand, `read_texts`, `write_vectors`) Sidecar text embeddings. Proving test: `tests/unit/test_scene_vendor.py` (embed-texts section)
- [x] T095 [FR-004] (done: `app/services/child_process.py`: streaming lines, SIGTERM then SIGKILL of the process group, cancel-proof reaping) Killable child runner. Proving test: `tests/unit/test_child_process.py`
- [x] T096 [FR-003,005] (done: `ensure_index`, `meta.json` fingerprint, fresh-directory swap, stale sweep, sentence-vector cache) Index on disk. Proving test: `tests/unit/test_scene_search.py` (index section)
- [x] T097 [FR-005,006] (done: `sentences_from_words`, `calibrate`, `search`, `shape_window`, `focus`) Moments and windows. Proving test: `tests/unit/test_scene_search.py` (search section)
- [x] T098 [FR-005,006] (done: medians and threshold sweep on the ADR-013 set, plan.md) Calibration.
- [x] T099 [FR-007,008,009] (done: `segment_rerank.judge_scenes`, `build_judge_prompt`, `parse_verdict`, `grab_judge_frames`; text judge for speech-led moments) Judge. Proving test: `tests/unit/test_scene_judge.py`
- [x] T100 [FR-001,002,004,009,010] (done: `_index_scenes`, `_transcribe_source`, `_scene_chapters`, `_run_job` wiring, `_discover_segments(words=)`) Orchestrator, run. Proving test: `tests/unit/test_orchestrator_scene_search.py`
- [x] T101 [FR-002,003,009] (done: `_reprompt_job` wiring, `reprompt_unavailable_reason(prompt)`, router) Orchestrator, reprompt. Proving test: `test_reprompt_reuses_the_scene_index_and_rebuilds_it_when_the_source_changes`, `test_reprompt_with_no_matching_moment_fails_and_keeps_the_clips`, `tests/contract/test_reprompt_router.py`
- [x] T102 [FR-011] (done: `SceneIndexProgress`, `ScenesIndexed`, `sceneIndexProgress`, progress bar, chapters row, reprompt status line) Progress events. Proving test: `web/src/lib/sceneIndexProgress.test.ts`, `web/src/components/job-progress-timeline.scenes.test.tsx`
- [x] T103 [FR-012] (done: `POST /uploads` `prompt` form field; `ScenePromptField` on both pages; reprompt help text) Prompt boxes. Proving test: `tests/contract/test_uploads_router.py::test_upload_passes_its_prompt_to_the_job`, `web/src/routes/jobs.new.test.tsx`, `web/src/routes/uploads.new.test.tsx`
- [x] T104 [FR-013] (done: `SceneMatch` on `ClipCard` and `ClipListRow`) Emotion chip and reason. Proving test: `web/src/components/dashboard/SceneMatch.test.tsx`
- [x] T105 [FR-003,004,007] (done: `tests/integration/test_scene_search_real.py`, opt-in) Real-model test.
- [x] T106 [FR-005..007] (done: satellite talk, three prompts plus one, plan.md) Real-model end-to-end measurement; drove the `focus` and text-judge changes.
- [ ] T107 [FR-011..013] End-to-end on the owner's stack: `scripts/start.sh`, a job with a prompt from `/jobs/new` and from `/uploads/new` (timeline shows "Indexing scenes n/m"), a reprompt that returns in seconds, clip cards with the emotion chip; playwright-cli in dark and light.
- [ ] T108 [FR-007] Emotion prompts: "a person who looks excited and happy" kept nothing on the satellite talk while a laughing woman was on screen at 210-219 s. Measure emotion recall with more labelled prompts; consider more frames or a wider candidate pool for emotion prompts.
- [ ] T109 [FR-006] Speech windows: snapping out to whole sentences turned a 5 s sentence into a 55 s clip. Consider centring the window on `focus` and snapping inwards first when the clip range is wide.
- [ ] T110 [FR-007] Re-run `scripts/scene_bench.py judge` with the shipped judge prompt (query moved into a `<query>` block, focus spans) to confirm precision stays >= 0.80; record it in ADR-013.
