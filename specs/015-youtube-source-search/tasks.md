# Tasks: YouTube search for clean sources

**Input**: [spec.md](spec.md), [plan.md](plan.md)

- [x] T111 [FR-006,008] (done: `source_check_model`, `source_check_timeout_seconds`, `youtube_search_timeout_seconds` in `app/settings.py`) Settings. No `.env` change
- [x] T112 [FR-001..003] (done: `GET /sources/youtube/search`, registered in `app/main.py`) Search route over `run_bounded`, with query and limit validation. Proving test: `tests/contract/test_sources_router.py`
- [x] T113 [FR-004] (done: `GET /sources/youtube/{video_id}/clean`, `fullmatch` on `^[A-Za-z0-9_-]{11}$`, 400 / 429) Clean route and id validation. Proving test: `tests/contract/test_sources_router.py`
- [x] T114 [FR-005] (done: `resolve_stream`, `extract_frames` in `app/services/source_check.py`) Stream URL and four frames through the bundled ffmpeg. Proving test: `tests/unit/test_source_check.py`
- [x] T115 [FR-006..009] (done: `judge_frame`, `judge_frames`, `decide`, `check_clean`: vision judgement, vote, early stop, LRU cache, shared in-flight check, busy limit) Vision judgement and cache. Proving test: `tests/unit/test_source_check.py`
- [x] T116 [FR-010] (done: `api.searchYoutube`, `api.checkYoutubeClean`, `YoutubeSearchItem`, `SourceCleanCheck`) API client. Proving test: `web/src/api/client.test.ts`
- [x] T117 [FR-010..012] (done: `web/src/routes/sources.youtube.tsx`: cards, lazy badge with 3 at a time, hide toggle on by default, "Use this video") Search page. Proving test: `web/src/routes/sources.youtube.test.tsx`
- [x] T118 [FR-010] (done: `routeTree.ts`, sidebar entry, home "Search YouTube" button) Navigation. Proving test: `web/src/routeTree.test.ts`, `web/src/components/layout/Sidebar.test.tsx`, `web/src/routes/index.test.tsx`
- [x] T119 [FR-001..012] (done: 51 mutations, all caught; one survivor, "unknown videos hidden too", led to a stronger test) Mutation-check every new default test
- [x] T120 [FR-001] (done: `tests/e2e/test_live_source_search.py`, `live` marker: a real search returns videos with the documented fields) Live search test
- [x] T121 [FR-006] (done: `tests/integration/test_source_check_real.py::test_the_real_model_tells_a_burned_in_subtitle_from_a_plain_picture`, real Ollama and model) Integration test of the judge on a real model
- [x] T122 [FR-005,007] (written: `test_a_real_video_with_burned_in_captions_is_not_clean`, `test_a_real_talk_without_captions_is_clean` on `tbnzAVRZ9Xc` and `5MuIMqhT8DM`; they skip while YouTube refuses the stream request from the address, which it did for every video tried on 2026-10-29) Integration tests of the whole check on two real videos
- [x] T123 [FR-006] (done: prompt measured on 35 real search results, 3 YouTube sample frames each, ground truth eyeballed first; the prompt and the reason-first schema were tuned on the first 17 and checked on the other 18; numbers in the spec, "Measured") Measure the clean check on real results
- [x] T124 [FR-010] (done: real API and Vite dev server driven with playwright-cli: search, cards, badges "Unchecked" while YouTube refused the stream) UI smoke on the real API
- [ ] T125 [FR-005,007,011] End-to-end check on the owner's stack once YouTube answers the stream request: `scripts/start.sh`, search "ted talk sleep", watch the badges settle (clean vs has captions) three at a time, hide-captions toggle, "Use this video" opens `/workflow` with the URL, a job starts. Also run `pytest -m integration tests/integration/test_source_check_real.py` and record the result in the spec's Measured section
