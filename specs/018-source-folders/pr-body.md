## What

One folder per source, finished videos you can find, and the groundwork for a YouTube-only TikTok publisher (spec 018, ADR-016).

- New jobs write to `<download_path>/<YouTube|TikTok|Facebook|Instagram> Videos/<slug>-<job8>/`. Uploads and generated videos are unchanged.
- `YTVIDEO_EXPORT_GROUP_BY_SOURCE` (default **off**) exports to `<export>/<group>/<job_id>/`. Leave it off until the sidecar and n8n PRs are deployed (below).
- `scripts/group_downloads_by_source.py` moves existing job folders and rewrites every stored path (dry run by default, per-job transaction, shared folders skipped, crash-recoverable). `scripts/map_inbox_to_db.py` reads both inbox layouts.
- Job page: folder path and an **Open folder** button (`POST /jobs/{id}/reveal`, loopback only, path from the job row, strictly below a managed root); a render-off job says "Source downloaded, no clips were rendered" instead of showing a dead preview.

## The "TikTok/Facebook videos aren't saved" report

Not a storage bug. Database check: all 28 completed TikTok jobs with render on have their file; the only completed jobs without one are the two that ran with render off. Facebook Reels were not treated as short-form, so the Advanced tab started with every stage off (render included), and a repeat request was answered with the same render-off job. Fixed: Facebook Reels default to Render on, a rendering request no longer reuses a render-off job, and the job page explains and links the folder. Separate and not fixed here: seven failed TikTok jobs are yt-dlp "Log in for access" (age-restricted posts need TikTok cookies).

## Rollout order (other repos)

1. tiktok-sidecar PR: accepts `YouTube Videos/<job>/<file>.mp4` (literal allow-list), ledger key unchanged.
2. n8n-self-hosting [#92](https://github.com/maseko-lucky-9/n8n-self-hosting/pull/92): scans the flat inbox and `YouTube Videos/` only.
3. Then set `YTVIDEO_EXPORT_GROUP_BY_SOURCE=true` here.
4. Back up (`pg_dump`), stop the API, run `python -m scripts.group_downloads_by_source --apply`.

## Test plan

- [x] `pytest -q`: 3852 passed (before the review fixes; re-run in CI)
- [x] `pnpm test` (755 passed), `pnpm build`, `tsc --noEmit`
- [x] Each new test mutation-checked (folder grouping, export flag, dedup, reveal guard and managed-dir check, move rollback, whole-component rewrite, shared folders, crash recovery, `updated_at` preserved)
- [x] Independent review; its shared-folder defect, crash-recovery, `updated_at` and test-hook findings fixed
- [x] Dry run of the move script on the real local database: 28 folders to move, 27 jobs without a recorded folder left, nothing written

🤖 Generated with [Claude Code](https://claude.com/claude-code)
