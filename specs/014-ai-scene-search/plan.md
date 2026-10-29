# Implementation Plan: AI scene search from a prompt

**Spec**: [spec.md](spec.md) | **Decision**: [ADR-013](../../docs/decisions/013-local-scene-search-models.md)

## Design

- **Sidecar.** `scripts/scene_index.py` (`.venv-mlx`) gains `embed-texts [--model M] -- <texts.json> <out.npy>`: a JSON list of non-empty strings in, an n x 768 float32 L2-normalised `.npy` out (temp file + rename), progress lines on stdout. Every text uses the query instruction, so the prompt and transcript sentences compare text to text. The API venv only reads `.npy`/`.json` with numpy.
- **Killable child.** `app/services/child_process.run_streaming` runs the sidecar in its own session, hands each stdout line to a callback (progress), keeps a stderr tail, and on timeout, cancel or a callback error stops the child: SIGTERM, a 5 s grace, then SIGKILL to the whole process group, reaped through further cancels (`stop_uninterrupted`). `yt_dlp_metadata.run_bounded` is unchanged (it never needed a polite stop).
- **Service.** `app/services/scene_search.py` (stateless, numpy): `enabled(prompt)`, `ensure_index` (reuse when `meta.json` = format, model, chunk length, source size + mtime; else index into `.scenes-<hex>` beside `scenes/`, validate, swap in; stale `.scenes-*` from a crash are removed first), `prompt_and_sentence_vectors` (`embed-texts` in batches of 200 sentences, the prompt with the first; vectors cached by sentence text in `scenes/sentences.json` + `sentence_embeddings.npy` as each batch finishes), `sentences_from_words`, `search`, `shape_window`.
- **Scoring.** `calibrate(raw, floor, match) = clip((raw - floor) / (2 (match - floor)), 0, 1)`: unrelated -> 0, a typical labelled match -> 0.5, strong matches still rank above typical ones (a clamp at the match level tied many moments at 100 in the first sweep). Per chunk `max(visual, speech)`; hits at >= 0.35 merge; windows per FR-006; each candidate carries `focus` (its best chunk and hit neighbours, at most 3 chunks) and `focus_text` (what is said there).
- **Judge.** `segment_rerank.judge_scenes`: sequential calls, budget and cap from settings; frames of `focus` (PyAV `grab_frame`, 512 px JPEG) for a visual-led candidate, the `focus_text` as a text-only prompt for a speech-led one; `JudgeUnavailable` (connect, HTTP, timeout) stops judging, an unusable reply skips one candidate. Based on the measured instruction text, with the query moved into a `<query>` data block and two sentences added saying the tags hold data; neither change, nor the text-only speech prompt, is measured yet (T110).
- **Orchestrator.** `_scene_chapters` runs `_index_scenes` and `_transcribe_source` side by side in a `TaskGroup` (each returns None on failure, so only cancellation crosses it), embeds, searches, judges, then `select_discovered` + `segments_to_chapters`. `_run_job` calls it before the chapter/discovery branch when `segment_proposer` is on and `scene_search.enabled(prompt)`; None or `[]` falls through to today's path, and a failed index hands its words to `_discover_segments(words=...)`. `_reprompt_job` calls it with the job folder (`destination_folder`, else the clips folder's parent) and the sidecar words; `[]` fails the reprompt. `reprompt_unavailable_reason(prompt)` is None when scene search takes the prompt; the router passes the resolved prompt.
- **Storage.** No migration: title and summary are existing columns; `reason` and `emotion` ride in `score_breakdown` (JSON, both engines), next to the numeric `visual`, `speech`, `judge`. `<job folder>/scenes/` goes with the job folder on delete (spec 013 walk; test added).
- **Web.** `SceneIndexProgress` and `ScenesIndexed` join `NON_STAGE_EVENT_TYPES`; `sceneIndexProgress(events)` feeds the progress bar label/percent, the active chapters row and a status line during a reprompt. `ScenePromptField` on `/jobs/new` and `/uploads/new` (`api.uploadVideo(file, prompt)`), `SceneMatch` (emotion chip + reason) on `ClipCard` and `ClipListRow`, help text on the reprompt form.
- **Tests.** Offline: fixture vectors (`tests/unit/test_scene_search.py`), injected Ollama transport (`test_scene_judge.py`), a fake sidecar with the real CLI's argv and files (`tests/fixtures/fake_scene_index.py`) for the index, cache, cancel and orchestrator tests, real child processes for `child_process`. Opt-in: `tests/integration/test_scene_search_real.py` (real model, generated two-scene clip, no database).

## Calibration (T098)

Measured on the ADR-013 labelled set: the six sources indexed with the 8-bit model (101 chunks), transcribed with faster-whisper base (the Tesla clip has no audio: 141 sentences from five sources), prompts and sentences embedded with `embed-texts`. Raw cosines, percentiles 10 / 25 / 50 / 75 / 90:

| Side | Inside the label | Outside |
|---|---|---|
| Visual, all prompts | 0.144 / 0.171 / **0.268** / 0.301 / 0.347 (n=64) | 0.057 / 0.086 / **0.126** / 0.164 / 0.202 (n=705) |
| Visual, visual prompts | 0.193 / 0.249 / 0.294 / 0.312 / 0.357 (n=37) | 0.064 / 0.091 / 0.126 / 0.163 / 0.205 (n=404) |
| Speech, speech prompts | 0.423 / 0.506 / **0.581** / 0.781 / 0.893 (n=20) | 0.275 / 0.374 / **0.451** / 0.521 / 0.581 (n=266) |
| Speech side of visual/emotion prompts | 0.272 / 0.308 / 0.345 / 0.413 / 0.506 (n=58) | 0.222 / 0.264 / 0.316 / 0.377 / 0.433 (n=695) |

So text-to-text cosines run about 0.3 above text-to-video ones: the sides need their own scale. Floors and matches are the bold medians (0.13 / 0.27 visual, 0.45 / 0.58 speech). A moment counts as found when its window overlaps a label by `min(2 s, half the span)` (clip range 8-60 s):

| Hit threshold | 0.20 | 0.25 | 0.30 | **0.35** | 0.40 | 0.50 |
|---|---|---|---|---|---|---|
| Top-1 / top-3 found (34 prompts) | 0.91 / 0.94 | 0.91 / 0.94 | 0.91 / 0.94 | **0.88 / 0.91** | 0.88 / 0.91 | 0.88 / 0.94 |

At 0.35 by category: visual 0.95 / 0.95 (n=20), speech 0.89 / 1.00 (n=9), emotion 0.60 / 0.60 (n=5). With the speech side switched off, speech prompts fall to 0.56 / 0.67, so the transcript side is what answers "where does he say X". 0.35 (sentrysearch's MLX start value) is kept: the curve is flat around it and the constants were not tuned on held-out data. Chunk-to-chunk cosine inside one source has a median of 0.71-0.89 (0.99 at p90 on a static prayer clip), so `DEDUPE_COSINE` is 0.95 and applies to visual moments only (a talking head would dedupe every spoken moment).

## End-to-end with the real models (T106)

`orchestrator._scene_chapters` on a copy of the satellite talk (372 s, 720x960, 47 chunks; part of the labelled set, the prompts below are new), qwen2.5vl:7b, clip range 20-60 s, M5 Pro 24 GB:

| Step | Time |
|---|---|
| Index alone (`scene_index.py index`, nothing else running) | 29.2 s wall: 2.4 s load + 24.7 s embedding = 3.99 s per source minute; peak RSS 0.62 GB, MLX peak 3.79 GB |
| Index in the pipeline, beside whisper (18.6 s, 1193 words) | 47.0 s |
| Index while the full pytest suite also ran | 112.4 s |
| Prompt + 77 sentences, one `embed-texts` call | about 12 s (model load included) |
| Judge, vision call (4 frames) | 9.3-10.7 s each (one 1.4 s); 13-25 s while other jobs ran |
| Judge, text call (speech-led) | 1.0-2.4 s each |
| Search with the index reused | 8.9-75 s, almost all judge time |

| Prompt | Kept | Judge | Looked at the frames |
|---|---|---|---|
| visual: "a row of large white satellite dishes at dusk" | 262.6-290.4 s | match 0.90: "a row of large white satellite dishes at dusk" | Right: dishes at dusk 273-287 s, the row at 284 s; the first ~8 s are a tablet close-up |
| speech: "they decoded a stranger's voice phone call on the cellular network" | 196.8-251.5 s, "Decoding a Stranger's Voice Call", emotion excitement | match 0.90 (text) | Right: the sentence is at 220.1-225.1 s; the clip is wide (sentence snapping up to 60 s) |
| emotion: "a person who looks excited and happy" | nothing (2 candidates, both rejected at 0.50) | | Miss: the candidates show the professor talking calmly; the laughing woman at 210-219 s was not a candidate. A job would fall back to its chapters |
| extra: "a woman laughing" | 176.8-201.7 s, emotion happy | match 0.90: "smiling and appears to be laughing" | Partial: a woman smiles at 185-190 s; the clearly laughing woman at 210-219 s was missed |

Two changes came out of this run. First version: 0 of 3 found, because the judge saw 4 frames spread over a 25-55 s clip window and missed the shot; it now looks at the matched span (`focus`). Second: the vision judge rejected the speech moment (confidence 0.18: frames of a talking head cannot show a phone call), so speech-led candidates are judged on their transcript.

## Review round (code-reviewer on PR #92)

- The scene stage is wrapped: any error but cancellation becomes `StageSkipped(scene_search)` and a fallback (`_scene_chapters` around `_scene_search_stage`); the sidecar's output is read in chunks (a `\r` progress bar longer than 64 KiB raised `LimitOverrunError`); `EOFError` from an empty `.npy` is a `SceneSearchError`.
- `shape_window` never snaps the best point out of the window (2.7 % of 200,000 fuzzed cases did).
- Candidates beyond the judge's cap are dropped once the judge has answered (rejecting the best 8 no longer promotes the 9th). The no-hit fallback skips chunks scoring 0; that alone did not make "nothing matched" fall back (see fix round 1).
- `stop` kills the process group also when the child obeyed SIGTERM or had already exited.
- The UI clears "Indexing scenes n/m" on `StageSkipped(scene_search)` and `ChaptersDetected`; `search` runs in a worker thread and maps sentences to chunks with `searchsorted`.

## Fix round 1 (code, security and test review on PR #92)

- **No-hit fallback.** The floors are medians of unrelated cosines, so about half of an unrelated source scores above 0: the old fallback turned unrelated chunks into clips that overrode the chapters whenever the judge could not run. Fallback candidates are now `tentative`; `judge_scenes` keeps one only when the judge answered and matched (Ollama off, down or the model missing drops them), so nothing matched really returns `[]` and the job keeps its chapters with `StageSkipped` "no moment matched the prompt". This left the hits themselves (fixed in round 2). Tested on cosines drawn from the measured unrelated distribution above (p10/25/50/75/90 0.057/0.086/0.126/0.164/0.202).
- **Embedding timeout and resume.** `embed-texts` gets `min(scene_index_timeout_seconds, max(120, 0.3 s x texts))` (0.12 s a sentence idle, 2.4x under load) instead of a fixed 600 s, and sentences go in batches of 200, each cached as it finishes, so a retry resumes. `TimeoutError` was caught as `OSError` ("cannot start the sidecar"); the order is fixed.
- **Untrusted files.** `allow_pickle=False` on every `np.load`; non-numeric arrays are errors and non-finite values become 0.
- **Dedupe.** `POST /jobs` with a prompt creates a new job unless the newest job of the URL has the same prompt (round 2: any job of the URL, normalised); `/jobs/new` says when it opened an existing job.
- **Hardening.** Child environment allow-list, fixed `StageSkipped` reasons, CSV formula neutralisation (round 2: bulk export only), `emotion` as an enum in the schema and the parser, the source path resolved before the sidecar, a relative `scene_python` taken from the project root, an incremental UTF-8 decoder for the child's output, the Ollama probe of the real-model tests moved out of import time, and "Indexing scenes" cleared on `RepromptFailed` and `JobCancelled`.

## Fix round 2 (re-review of 6d1c292)

- **Hits without a judge.** About one unrelated sentence in five clears the speech hit bar (raw 0.541, the unrelated p80), so with the judge unavailable an unrelated prompt on a 10-minute talk still produced 10-14 clips that replaced the chapters (the reviewer's simulation, 50 of 50 seeds). The outside p95 is not a safe bar either: 0.613 for speech prompts, and a talk has ~120 sentences, so ~6 unrelated ones clear it. When the judge answered nothing, a moment now needs a raw cosine above the largest unrelated one measured (`NO_JUDGE_VISUAL` 0.35 > 0.347 of 705, `NO_JUDGE_SPEECH` 0.74 > 0.732 of 961; `ProposedSegment.strong`). On the labelled set that keeps 6 of 64 labelled chunks, 6 of 20 labelled sentences and 0 unrelated pairs; 12 of 34 prompts still find a labelled moment (visual 5/20, speech 6/9, emotion 1/5). Once the judge has answered, nothing changes. The `StageSkipped` reason gains " (the judge was unavailable)" (`judge_scenes(stats=)`). The reviewer's simulation now keeps nothing in 50 of 50 seeds; the test draws 50 talks from the measured distributions, with the tails up to the measured maxima.
- **Raw `manifest.csv`.** The Social Publish workflow posts its `description` as the caption and `scripts/map_inbox_to_db.py` and `repair_job_metadata.py` read it back, so the round-1 quoting would have posted "'@NASA just launched". Neutralisation moved to the bulk-export zip only (`bulk_export.csv_safe`); the job manifest is tested verbatim through the inbox reader.
- **Dedupe** compares the prompt (whitespace collapsed, case folded) with every job of the URL, newest first (`find_job_by_url(where=)`, both stores); no prompt still returns the newest job.
- **Model text cleaned at the source.** With `manifest.csv` raw, `parse_verdict` strips a leading run of `= + - @`, tab, CR and spaces from the judge's title and reason, so a model-written title or summary never starts a formula there. YouTube chapter titles reached the same columns before spec 014; that sink is unchanged.
- **Sidecar env** also passes `XDG_CACHE_HOME`, the proxy variables in both cases and the CA bundle variables.

## Verification

`pytest` (offline), `ruff check app tests scripts`, `pytest -m integration tests/integration/test_scene_search_real.py` (no database), `cd web && pnpm test && pnpm build`.

Mutation checks. First round: 45 mutations, all red (36 backend, 9 frontend). The reviewer then found 3 survivors those 45 did not cover: the speech-led exemption from the near-duplicate dedupe, and either `searchsorted` side on a sentence that only touches a chunk boundary. Fix round 1 kills them (`test_a_spoken_moment_is_never_deduped_against_a_lookalike_shot` against 3 variants of the exemption, `test_a_sentence_lifts_only_the_chunks_it_overlaps_not_the_ones_it_touches` against both sides) and checks every new or changed test: 50 mutations (N1-N50: 46 backend, 4 frontend), all red, run with bytecode caching off (two same-size mutants of one file in the same second had loaded a stale `.pyc` and looked green). Fix round 2: 33 more (P1-P33: the no-judge bars raised and lowered, the p95 bar, the judge-answered branch, the stats, the reason text, both manifest directions, dedupe, the env names, the formula strip per field and per character), all red, same settings. Tab and CR in the strip set are not separately killable: `_as_data` already turns them into spaces.
