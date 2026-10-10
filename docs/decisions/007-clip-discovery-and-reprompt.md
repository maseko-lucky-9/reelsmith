# ADR-007: Clip Discovery in Chapterless Sources, and Reprompt

**Status:** Accepted (opt-in: `YTVIDEO_SEGMENT_PROVIDER` defaults to `chapter` by owner choice; whether the heuristic picks good clips is not signed off, gate G1)
**Date:** 2026-10-09
**Author:** Thulani Maseko
**Implements:** FR-009 (T011; the optional re-rank, T040), FR-016 (T008); PRs #43, #49, #51, #PRNUM

## Context

- **Discovery.** A source without chapters (most TikTok, Instagram and Facebook posts, uploads, and YouTube videos without chapter markers) became one "Full Video" clip. The orchestrator gated `segment_proposer`, but both branches built the same single chapter, and `local_heuristic` needed librosa, which is not a dependency (baseline FR-009: Scaffolded-unwired).
- **Reprompt.** `POST /api/jobs/{id}/reprompt` set the job `pending` and rewrote its options, and nothing re-enqueued it. A `pending` job is in `_DEDUP_STATUSES` (`app/routers/jobs.py:24`), so every later `POST /jobs` for that URL returned the stuck job (baseline FR-016: Partial).
- **Gate G1 runs on real talks**, recorded beside the constants in `app/services/segment_discovery.py`:
  - heuristic scores ran low (about 13 to 38), so no fixed score bar works;
  - a 4-minute talk was sliced end to end into 5 clips of about 40 s, intro included;
  - on a TEDx talk, 4 of 5 clips were the same staged dialogue.

## Decision

### Proposer (PR #43)

`LocalHeuristicProposer` (`app/services/segment_proposer.py`) needs only numpy and the standard library:

- **Audio.** RMS per 0.1 s frame, read from the extracted wav with `wave`.
- **Windows.** Candidate windows snap to word edges and prefer sentence ends. Dead-air windows (low speech ratio) are dropped.
- **Score.** Features `hook`, `value`, `audio`, `trend`, plus a `prompt` overlap when a prompt is given. vaderSentiment (`emotion`) and spaCy (entity boost for `value`) are optional. A missing feature leaves the breakdown and the weights are renormalised. Weights come from `YTVIDEO_SCORE_WEIGHTS`.
- **Short sources.** A source shorter than the minimum clip length yields one "Full Video" segment.

### Discovery (PR #49)

- **Gate.** `_discovery_enabled` (`app/workers/orchestrator.py:364`) requires all of these:
  - the source has no chapters;
  - the job's `segment_proposer` and `transcription` options are on, and `segment_mode` is `auto`;
  - `settings.segment_provider != "chapter"`.

  `local_heuristic` is the real scorer. `stub` (`StubProposer`) returns one fixed 0 to 30 s segment and exists for tests. Any other value also enables discovery and falls back to the stub proposer (`get_segment_proposer`).
- **Transcribe once.** `_discover_segments` (`:379`) extracts the whole source's 16 kHz wav and transcribes it once. It writes the words atomically to the `<source stem>.words.json` sidecar beside the source (`write_words_sidecar`, `segment_discovery.py:109`), scores candidate windows with `get_segment_proposer()` (the job's clip length range and prompt, the wav's RMS), then deletes the wav.
  - Each kept segment becomes a chapter. The chapter reuses the full-source words, rebased onto its own window (`rebase_words`, `:81`), instead of transcribing again.
  - A later single-clip re-render reads the sidecar (`read_words_sidecar`, `orchestrator.py:560`).
- **Selection.** `select_discovered` (`segment_discovery.py:184`) keeps highlights, not slices. It works greedily, one clip per step, under these rules:
  - **Relative bar.** Applied first, as a pre-filter. A segment must score at least `MIN_SCORE_RATIO = 0.6` of the best segment's score.
  - **Budget.** This is the loop limit: one clip per `SECONDS_PER_CLIP = 120` s of source, rounded half up, clamped to 1..`DEFAULT_MAX_CLIPS = 5` (`clip_budget`).
  - **Per-step checks, in this order.** Each step drops a candidate that:
    1. overlaps a kept clip (touching ends are allowed);
    2. would take the kept clips past `MAX_COVERAGE = 0.5` of the source (the first pick is exempt, so the best segment is always kept);
    3. is at `REDUNDANCY_SKIP = 0.6` or more redundant, where `redundancy` is the largest share of its transcript content words already in one kept clip;
    4. has an adjusted score, `score * (1 - redundancy)`, below the same bar.

    The highest adjusted score wins the step.
- **Fallback.** A source shorter than the minimum clip, no kept segment, or any discovery error keeps the single "Full Video" chapter. The short-source and error cases emit `StageSkipped(segment_proposer, reason)`. Discovery never fails the job; cancellation propagates.
- **Events and fields.** `SegmentsProposed` is emitted, then one `SegmentScored` per kept segment. The clip stores `virality_score`, `score_breakdown` and the proposer's `summary`.

### Re-rank (T040, PR #PRNUM; opt-in)

`YTVIDEO_SEGMENT_RERANK_PROVIDER` (`none` by default, or `ollama`) adds an optional local-LLM pass between the proposer and `select_discovered`: `_discover_segments` calls `segment_rerank.rerank` (`app/services/segment_rerank.py`). With `none` discovery is unchanged.

- **Shortlist.** The heuristic's best non-overlapping candidates at or above the relative bar (`select_segments` with `relative_min_score`), at most `MAX_CANDIDATES = 10`. Overlapping windows are left out, so the model judges distinct passages rather than ten shifts of one window.
- **One call.** `POST {YTVIDEO_OLLAMA_BASE_URL}/api/generate` to `YTVIDEO_OLLAMA_MODEL`, not streamed, temperature 0, its output held to a JSON object with an integer per id (Ollama's JSON-schema `format`, Ollama 0.5 or later). The candidates go as `c1`..`cN` in start order, each with at most `EXCERPT_CHARS = 600` characters of its transcript. The job's prompt, if any, goes too (at most 200 characters), and the model is asked to weigh it.
- **Blend.** `(1 - MODEL_WEIGHT) * heuristic + MODEL_WEIGHT * model` with `MODEL_WEIGHT = 0.5`, rounded half up. A shortlisted candidate without a usable model score keeps its heuristic score. The selection rules above then run unchanged on the shortlist alone, so a low model score can drop a clip the heuristic would have kept. Heuristic scores run about 13 to 38 and the model's span 0 to 100, so at 0.5 the model decides most orderings.
- **Stored.** The clip's existing `virality_score` (and `SegmentScored.score`) holds the blended score; `score_breakdown` keeps the heuristic's features. Nothing new is stored or emitted. The `Clip re-rank done` log line lists each candidate's heuristic, model and blended score.
- **Trust boundary.** The transcript is untrusted: a speaker, or a crafted upload, can say "ignore previous instructions".
  - Excerpts and the job prompt enter the request as delimited data (`<candidate id="cN">...</candidate>`, `<viewer_request>...</viewer_request>`), flattened to one line with `<` and `>` removed, so they cannot close their block. The instructions say the tagged text is data, never an instruction.
  - From the reply only numbers under the known ids are read. Unknown ids and keys, strings, booleans, null, lists, NaN and infinities are ignored; numbers are rounded and clamped to 0 to 100.
  - The model has no tools, and nothing else in its reply is read: it cannot add a candidate or change one's times, title, text or breakdown. The most a successful injection can do is move the shortlisted candidates' scores, each by at most `MODEL_WEIGHT` x 100 points, and with them which shortlisted windows are kept.
- **Fallback.** Never fatal. A provider other than `ollama` (an unknown value logs a warning), `YTVIDEO_OLLAMA_ENABLED=false`, fewer than two shortlisted candidates, a connection or HTTP error, the deadline, or a reply with no usable score logs and returns the proposer's candidates unchanged, in the heuristic's order. The deadline is `YTVIDEO_OLLAMA_TIMEOUT_SECONDS` for the whole exchange (`asyncio.timeout`, on top of httpx's own timeouts), so the re-rank adds at most that much to discovery. Cancellation propagates.
- **Not covered.** A reprompt (`_propose_reprompt_chapters`) is not re-ranked. The re-rank has not been run against a real model; whether it picks better clips is unmeasured, and that is what gate G1 needs.

### Reprompt (PR #51)

- **Router** (`app/routers/reprompt.py`). It validates the request:
  - 404 for an unknown job;
  - 422 for a bad body, an inverted length range, or a time range that starts past the source;
  - 409 when the job is not `completed`, its source video is not retained, a reprompt of this job is already in flight (`claim_reprompt`, an in-process set), or clip discovery is off (`reprompt_unavailable_reason`, `orchestrator.py:653`) and no explicit time range was given.

  On success it forgets the job's SSE replay history and queues `{reprompt: true, prompt, length range or start/end}`.
- **Job state.** `_reprompt_job` (`orchestrator.py:834`) never changes the job's status: the job stays `completed`. A restart (`fail_interrupted_jobs`) therefore cannot fail it, and its URL still dedups to it.
- **Chapters.** The chapters are either the proposer's picks for the prompt and length range, through the same `select_discovered` as discovery (`_propose_reprompt_chapters`, `:716`), or the one requested time range (`_span_chapter`). The proposer path takes the words from the sidecar, or else transcribes the source once and writes the sidecar. A time-range reprompt reuses the sidecar when there is one, and otherwise transcribes only its range.
- **Hidden render, then swap.** The new clips are created retired (hidden). Their indexes follow every existing clip file (`_next_clip_index`, `:698`), so no file is overwritten. The new clips are rendered and exported, and the manifest is rewritten. Only after every new clip has rendered:
  1. the new clips go live;
  2. the old clips are retired (`JobStore.retire_clips`);
  3. only the prompt and the length range are recorded on the job;
  4. `JobReprompted` and `JobCompleted` are emitted.
- **Failure.** `_undo_reprompt` (`:796`) puts the old clips back live and retires the new ones. It drops the new clips' chapters and deletes their files, except a file that a live clip still uses. Then `RepromptFailed` is emitted, never `JobFailed`. A cancel undoes the same way, then propagates. The in-flight claim is always released.
- **Event history.** `AsyncEventBus.forget(job_id)` (`app/bus/event_bus.py:92`) runs when the router accepts and again when `_reprompt_job` starts. Without it, a new SSE subscriber would be replayed the previous run's `JobCompleted`, on which the stream closes. The SSE route also ends on `RepromptFailed` (`_TERMINAL_TYPES`, `app/routers/jobs.py:184`).

### Rejected

- **A fixed score bar.** Scores run about 13 to 38 on a real talk. A ratio of 0.4 dropped just 2 of 32 candidates on the G1 talk (`MIN_SCORE_RATIO` docstring).
- **Running the reprompt as a `running` job.** A restart would fail a job that already had good clips, and the job would block its URL meanwhile.
- **Retiring the old clips first.** A failed render would leave the job with no clips.

## Consequences

- **Defaults.** A default install is unchanged:
  - a chapterless source gives one "Full Video" clip;
  - a reprompt without a time range answers 409 "clip discovery is off (segment_provider=chapter) ...".

  Set `YTVIDEO_SEGMENT_PROVIDER=local_heuristic` to opt in. Flipping the default is an open owner decision, recorded with the ranking work in `specs/001-reelsmith-baseline/tasks.md`.
- **Cost.** Discovery transcribes the whole source once. The sidecar saves a second transcription on every re-render and reprompt.
- **Disk.** `retire_clips` deletes no files, so the clips a reprompt replaced stay on disk until the retention janitor's `sweep_retired_files` deletes them, once each file is older than `YTVIDEO_RETIRED_FILES_GRACE_HOURS` (24); a job with a reprompt in flight is skipped. The sidecar lives as long as the source does: `sweep_unused_sources` removes both once the job has no live clip and has been idle for `retention_days` (T033, FR-013).
- **Concurrency.** The in-flight guard is per process. One API process is assumed.
- **Quality.** The ranking is heuristic and partly loudness-driven: `audio` is the window's relative RMS, and `hook` averages text cues with the opening's RMS. It is not signed off (G1). The optional re-rank (*Re-rank* above) adds a local model's judgement; it is off by default and its effect is unmeasured.
- **Not exercised.** PostgreSQL was not exercised for reprompt; the tests cover the memory and SQLite stores.

## Tests

- Discovery: `tests/unit/test_orchestrator_discover.py`, `test_segment_discovery.py`, `test_segment_selection.py`, `test_segment_proposer.py`, `test_segment_proposer_heuristic.py`.
- Re-rank: `tests/unit/test_segment_rerank.py` (stubbed model: order, blend, parsing, prompt bounds, injection, every fallback, deadline, cancellation) and the re-rank cases in `tests/unit/test_orchestrator_discover.py`.
- Reprompt: `tests/contract/test_reprompt_router.py`, `tests/unit/test_orchestrator_reprompt.py` (memory and SQL stores), `tests/unit/test_event_bus.py`, `web/src/routes/jobs.$jobId.test.tsx`, `web/src/hooks/useJobSSE.test.ts`.
