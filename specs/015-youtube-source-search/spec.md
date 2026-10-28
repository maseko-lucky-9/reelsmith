# Feature Specification: YouTube search for clean sources

**Feature**: `015-youtube-source-search` | **Date**: 2026-10-29 | **Plan**: [plan.md](plan.md)

## Problem

A source is a pasted URL. There is no way to look for one inside Reelsmith, and no way to tell whether a video already has subtitles burned into the picture, which makes Reelsmith's own captions appear twice. The owner wants to type a topic, see the matching YouTube videos, and prefer the ones without burned-in captions, then hand one to the existing `/workflow` page.

## Requirements

### API

- **FR-001**: `GET /sources/youtube/search?q=&limit=` MUST return `{"items": [...]}` with, per video, `id`, `title`, `channel`, `duration_seconds` (`null` when YouTube gives none), `thumbnail_url` and `url` (the watch URL). It runs yt-dlp as `python -m yt_dlp --flat-playlist -J -- ytsearch{limit}:{q}` through `yt_dlp_metadata.run_bounded` (killable child, argv list, never a shell), no API key. Entries whose id is not 11 id characters are dropped.
- **FR-002**: `q` MUST be 1-200 characters, free of control characters, and MUST NOT start with `-`; otherwise 400 (422 for an empty or over-long `q`, from FastAPI). `limit` MUST be 1-50, default 12 (422 outside). The term sits behind a `--` and a `ytsearchN:` prefix, so it can be neither a yt-dlp option nor a second argument.
- **FR-003**: A search MUST be bounded by `YTVIDEO_YOUTUBE_SEARCH_TIMEOUT_SECONDS` (30): 504 on timeout, 502 when yt-dlp fails or prints something that is not JSON.
- **FR-004**: `GET /sources/youtube/{video_id}/clean` MUST answer 400 unless `video_id` matches `^[A-Za-z0-9_-]{11}$` (it reaches a yt-dlp argv; the check is a `fullmatch`, so a trailing newline fails), and otherwise 200 `{clean: bool | null, confidence: number, reason: string, frames_checked: int}`.
- **FR-005**: The check MUST look at real frames: yt-dlp names the best stream of at most 480p (`bv*[height<=480]/b[height<=480]/w`) and the duration; the bundled ffmpeg (`ffmpeg_tools.run`, never a system ffmpeg) seeks with `-ss` before `-i` to 15, 35, 55 and 75 % of the duration and writes one JPEG (640 px wide) each; only an `https://` stream is accepted.
- **FR-006**: Each frame MUST be judged by a local vision model through Ollama `/api/generate` (`images`, a JSON-schema `format` of `{reason, burned_in_captions, confidence}`, `think: false`, `temperature: 0`, `keep_alive: "60s"`). Spoken-word subtitles burned into the picture count; channel logos, watermarks, title cards, news chyrons and headline bars, lower-third name tags, tickers, text on a title edge or corner, slides and text that is part of the scene do not. The model is `YTVIDEO_SOURCE_CHECK_MODEL` (default `qwen2.5vl:7b`, the judge ADR-013 measured). Nothing from YouTube (title, channel) goes into the prompt.
- **FR-007**: The vote MUST be: two or more flagged frames make a video not clean. Frames are judged in order and the vote ends as soon as it is settled (two flagged, or too few frames left to reach two), because Ollama serves one call at a time and a saved call shortens every check queued behind it. Fewer than two judged frames is "unavailable".
- **FR-008**: A check that cannot be made (Ollama down or slow, model missing, no stream, fewer than two frames, no answer within `YTVIDEO_SOURCE_CHECK_TIMEOUT_SECONDS` = 60) MUST answer 200 `{clean: null, confidence: 0, reason: "check unavailable", frames_checked: 0}`, never a 500, and MUST NOT be cached.
- **FR-009**: A definite answer MUST be cached per id (256 entries, least recently used out first); two requests for one id share one check; a fifth distinct video while four checks run answers 429.

### UI

- **FR-010**: `/sources/youtube` MUST have a search box, result cards (thumbnail, title, channel, duration) and "Use this video", which opens `/workflow?url=<the watch URL>` (the route already reads `url`). The sidebar links to it and the home page has a "Search YouTube" button beside Upload and Google Drive.
- **FR-011**: Each card MUST show a badge: "Waiting…" (queued), "Checking…", "Clean", "Has captions" (the reason is its tooltip) or "Unchecked" (the check failed or answered null; a click retries). At most 3 checks run at once, in result order; each video is asked about once per session (no polling, no refetch on focus).
- **FR-012**: "Hide videos with captions" MUST be on by default and hide only videos whose check said `clean: false`. A video whose check is pending, failed or unavailable stays visible. The count hidden is shown.

## Threats

- **T1 (high, prevented).** The query and the id are attacker-shaped strings that reach a subprocess argv. Mitigations: argv list; `--` before the term; `ytsearchN:` prefix; a leading `-` and every control character refused; id `fullmatch` against 11 id characters before anything runs (the route takes `{video_id:path}` so a `../x` reaches that check and gets a 400 instead of a 404).
- **T2 (medium, prevented).** Prompt injection from the video: text inside a frame can say "answer false". The prompt is a constant that tells the model to ignore instructions in the image; no title or channel name is in it; the model's answer is a typed schema whose only effect is a badge and a filter, never a command or a path. A frame that talks the model into "clean" costs one wrong badge.
- **T3 (low, prevented).** A stream URL from yt-dlp goes to ffmpeg: only `https://` is accepted and ffmpeg is limited to `https,tls,tcp,crypto` (no `file:`, `concat:` or `http:` redirects to local protocols).
- **T4 (low, bounded).** A hostile client can start many checks: at most 4 distinct ids run at once (429 beyond); each is bounded by the 60 s budget and the ffmpeg 30 s per-frame limit.
- **Limitation.** YouTube may refuse the stream request from an address with "Sign in to confirm you're not a bot". The check then answers `clean: null` ("Unchecked") and the search still works (it does not use the watch page). Reelsmith does not work around that: no cookies, no alternative player clients.

## Measured

Setup: Apple M5 Pro, Ollama, `qwen2.5vl:7b`, the production `judge_frame` and `decide` from `app/services/source_check.py`. The stream step could not run here (YouTube refused `yt-dlp` from this address with its bot check, 5 of 5 videos tried, 2026-10-29), so the frames are the three sample frames YouTube publishes per video (`https://i.ytimg.com/vi/<id>/hq1.jpg`..`hq3.jpg`, 480x360, at 25, 50 and 75 % of the video), eyeballed by me **before** the model ran. Results are in the summary table below; this is the model and prompt on real YouTube frames, not the full stream path, and it uses 3 frames, not 4.

| Measure (35 search results from 11 queries: podcasts, TED, motivational speeches, news, comedy, tutorials, council meetings, documentaries, hearings) | Result |
|---|---|
| Ground truth, eyeballed from the 3 frames of each video before the model ran | 5 captioned, 1 mixed (a styled caption on 1 of 3 frames), 29 clean |
| Final prompt, vote of 2 flagged frames out of 3: captioned videos found | 5 of 5 |
| Final prompt: clean videos judged clean (logos, news chyrons and headline bars, lower-thirds, a title card "The Stream Will Start Shortly", step titles "STEP 3 - ZOOMS", on-screen tutorial text, a lap counter, a venue sign) | 29 of 29 |
| Final prompt: the mixed video | judged clean (1 of 3 frames flagged; by the rule it is clean) |
| Final prompt: single frames wrongly flagged | 1 frame in about 87 clean frames: a channel title banner (`AWjZwffTyqA`, "MOTIVATED the extra push", top-left); the vote absorbed it |
| First prompt (a plain "logos, titles and lower-thirds do not count" list), same method | 6 of 21 clean videos wrongly rejected (the 12 clean of the first 17, and 9 of the second 18): both CBS news videos (chyron plus lower-third, 3 of 3 frames flagged), the title banner above, and 3 headline bars on news/hearing videos (`d_NIYEwbmKs`, `UqfqDugzYb8`, `jT7ReIWT9lc`). Plain broadcast logos (9 News, 7 News, a CBS bug, Dick Cavett) were not rejected |
| Latency of one frame (qwen2.5vl:7b, warm) | 2.5-4.5 s per frame in most runs; 10-19 s per frame in others (cause not determined: other jobs may have shared Ollama). A check of up to 4 frames can therefore take 10-75 s, and 3 at once are served one after another, which is why the vote stops early and why the 60 s budget can run out (the badge then reads "Unchecked", one click retries) |

How the numbers were reached, and what they do not show:

- **Tuned, then re-run.** Three prompt edits were made, driven by the failures above and by a synthetic subtitle on a black strip that a first rewrite had excluded ("no banner or box"): name chyrons, headline bars, tickers and corner/top-edge text as non-captions; allow a dark strip behind a real subtitle; put `reason` before the verdict in the schema. The final prompt was then run again on all 35 videos, so the 29/29 and 5/5 are not held out from the edits. The 18 videos fetched second were first judged with the second prompt version (written from the first 17's failures), which found no caption in 17 of them and missed the 18th, a captioned one; the first prompt version, run on 10 of them afterwards, rejected 3 clean ones.
- **Small n for captions.** 5 captioned videos. 5 of 5 is not a recall estimate with any confidence; it says the model reads large burned-in captions reliably. The one hard captioned case (`92JDjbHlgwU`, small bold single words on a screen recording) was missed by the prompt without `reason` first and found with it.
- **Not the production frames.** 3 frames at 25/50/75 % (YouTube's own samples), not 4 at 15/35/55/75 %, and the 480x360 image YouTube serves, not an ffmpeg frame scaled to 640 px. A video that is captioned only in a part of its length can be missed or hit by chance (the mixed video).
- **Run-to-run variation.** Same prompt, temperature 0, the same frame gave a different answer on a different run for `AWjZwffTyqA` (2 of 3 flagged, then 1 of 3), so a borderline frame is a coin flip; the 2-of-4 vote exists for that.
- **Unchecked at all.** The stream path (`yt-dlp -f` then ffmpeg `-ss` on the remote URL): YouTube answered "Sign in to confirm you're not a bot" to every video requested from the build machine (5 of 5 on 2026-10-29), so T125 stays open.

## Out of scope

- A licence filter (owner decision: none).
- Searching other platforms; pagination beyond 50 results; channel or playlist results.
- Filtering by duration or date.
- Working around YouTube's bot check (cookies, alternative player clients).
- Choosing the vision model per request, or a second model setting (the scene-search spec owns `ollama_vision_model`).

## Status

| Requirement | State | Proving test |
|---|---|---|
| FR-001 | Done | `tests/contract/test_sources_router.py`: `test_search_maps_yt_dlp_entries_to_items`, `test_search_limit_defaults_to_twelve_and_the_api_prefix_works` |
| FR-002 | Done | `test_search_refuses_a_query_that_could_be_an_option_or_a_second_argument`, `test_search_refuses_a_limit_outside_1_to_50`, `test_search_passes_shell_metacharacters_as_one_inert_argument`, `test_search_accepts_a_query_of_exactly_200_characters` |
| FR-003 | Done | `test_search_timeout_is_504_and_failure_is_502` |
| FR-004 | Done | `test_clean_refuses_an_id_that_is_not_eleven_id_characters`, `test_clean_never_reaches_the_check_with_a_newline_in_the_id`, `test_the_id_pattern_refuses_a_trailing_newline` |
| FR-005 | Done (stream step proven with stubs and a live refusal only) | `tests/unit/test_source_check.py`: `test_resolve_stream_reads_duration_and_url_from_yt_dlp`, `test_resolve_stream_refuses_what_it_cannot_use`, `test_remote_frames_seek_before_the_input_through_the_bundled_ffmpeg`, `test_extract_frames_decodes_a_real_video`; `integration`: `tests/integration/test_source_check_real.py` (skips while YouTube refuses the stream) |
| FR-006 | Done | `test_each_call_sends_the_frame_with_the_schema_and_no_thinking`; `integration`: `test_the_real_model_tells_a_burned_in_subtitle_from_a_plain_picture` |
| FR-007 | Done | `test_two_flagged_frames_of_four_make_a_video_not_clean`, `test_a_mixed_vote_reports_the_agreeing_share_as_confidence`, `test_a_video_without_captions_is_clean`, `test_a_video_with_captions_in_every_frame_is_not_clean`, `test_one_malformed_reply_is_skipped_and_the_rest_decide` |
| FR-008 | Done | `test_ollama_down_is_unavailable_not_an_error`, `test_ollama_http_error_is_unavailable`, `test_ollama_timeout_is_unavailable`, `test_malformed_replies_leave_too_few_frames_for_a_verdict`, `test_frame_extraction_failure_is_unavailable_and_asks_ollama_nothing`, `test_no_stream_is_unavailable`, `test_a_check_that_outlives_its_budget_is_unavailable`, `test_an_unavailable_answer_is_not_cached`, `test_clean_unavailable_is_a_200_with_null` |
| FR-009 | Done | `test_a_second_request_for_the_same_id_does_not_ask_ollama_again`, `test_the_cache_keeps_at_most_256_videos_and_drops_the_least_recently_used`, `test_two_concurrent_requests_for_one_id_share_one_check`, `test_a_fifth_distinct_video_is_refused_while_four_checks_run`, `test_clean_answers_429_when_too_many_checks_run` |
| FR-010 | Done | `web/src/routes/sources.youtube.test.tsx` ("Use this video"), `web/src/routeTree.test.ts`, `web/src/components/layout/Sidebar.test.tsx`, `web/src/routes/index.test.tsx` |
| FR-011 | Done | `sources.youtube.test.tsx`: "runs at most 3 checks at once", "asks for each video at most once" |
| FR-012 | Done | `sources.youtube.test.tsx`: "hides videos found to have captions", "has the toggle on by default", "shows a video whose check is unavailable as unchecked" |
