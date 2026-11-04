# Quickstart: validate spec 017

**Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md) | **Tasks**: [tasks.md](tasks.md)

A run guide, not code. Each step names the command and what "pass" looks like; paste the raw output as evidence (no claim without it).

## Prerequisites

- Repo venv active (`pip install -r requirements.txt`), `cd web && pnpm install --frozen-lockfile`.
- For the live checks: Docker (compose Postgres), at least one completed job with a clip and a retained source, `npx playwright install chromium` for `playwright-cli`.
- Optional: a Piper voice (`python -m scripts.fetch_piper_voice`); without it the voice-over is the silent stub and the editor must say so.

## 1. Offline suites

```bash
ruff check app tests scripts
pytest                                   # default run: no integration/live/playwright
pytest tests/contract/test_job_source_route.py tests/unit/test_http_range.py \
       tests/contract/test_media_router.py tests/unit/test_editor_preview_assets.py -q
cd web && pnpm test && pnpm build
```

Pass: zero failures; the summary lines pasted. `pnpm build` runs `tsc -b`, so a leftover import of `MultiTrackTimeline` or `useTimelineEditor` fails it.

## 2. Mutation checks

Per new test, break the code it covers and see it go red, then restore (examples: [contracts/timeline-adapter.md](contracts/timeline-adapter.md#mutation-checks-the-test-must-go-red), [contracts/source-video.md](contracts/source-video.md#contract-tests-each-must-fail-when-its-rule-is-broken)). Minimum set:

| Break | Expected red |
|---|---|
| `is_managed_file` check removed from the source route | `test_job_source_route.py` outside-folder and symlink cases |
| 416 branch removed from the range helper | `test_http_range.py`, the 416 cases of both routes |
| `CUT_TOLERANCE` set to `1e-4` | `renderedCut.test.ts`, the 1 ms review cases |
| `origin` ignored in `toTimeline` | `adapter.test.ts` round trips |
| history cap removed | `timeline-store.test.ts` cap case |
| one byte of `web/public/fonts/Anton-Regular.ttf` changed | `test_editor_preview_assets.py` |

## 3. Source route against a real job

```bash
scripts/start.sh                                     # Postgres + API + UI
JOB=<a completed job id with a retained source>
curl -s -o /dev/null -w '%{http_code} %{size_download}\n' http://localhost:8000/api/jobs/$JOB/source -H 'Range: bytes=0-1023'
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8000/api/jobs/$JOB/source -H 'Range: bytes=999999999999-'
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8000/api/jobs/does-not-exist/source
```

Pass: `206 1024`, `416`, `404`.

## 4. Editor flow with `playwright-cli`, dark and light (T157)

```bash
playwright-cli open http://localhost:5173/clips/<clip_id>/edit
playwright-cli run-code "async page => { await page.emulateMedia({ colorScheme: 'dark' }); }"
# flow: play; split at the playhead (S); edit a caption in the properties panel and in the transcript;
#       pick the "hormozi" caption style; add a text overlay; drag it 1 s later; add a voice-over;
#       Re-render; wait for "Re-render finished"; switch to the rendered clip; play it
playwright-cli screenshot --filename=<scratchpad>/017-dark.png
playwright-cli run-code "async page => { await page.emulateMedia({ colorScheme: 'light' }); }"
# repeat the flow on another clip (or reload)
playwright-cli screenshot --filename=<scratchpad>/017-light.png
playwright-cli run-code "async page => document.fonts.check('48px \"Reelsmith Caption\"')"
playwright-cli close
```

Pass, in both schemes: no contrast or layout defect in the screenshots; after the split the preview shows the cut at once; the caption on the preview is in the chosen style and the font check is `true`; the result view's `<video>` `currentTime` advances after play; every `/api/jobs/<id>/source` request after the first seek is a 206 (browser network log). Screenshots stay in the scratchpad, not in git (`/*.png` is ignored at the root only).

## 5. Docs

- Spec Status table updated with the real states and tests; tasks ticked with commit hashes.
- `specs/README.md` and `tasks/todo.md` rows for 017 match tasks.md.
- `VENDOR.md` has the OpenCut entry; `web/src/editor/LICENSE-OpenCut` exists.
- Relative markdown links in the changed files resolve (checked with a link checker, as in the spec 001 close-out; no checker is committed).
