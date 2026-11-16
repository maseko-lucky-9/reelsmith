# Feature Specification: One folder per source, and finished videos you can find

**Feature Branch**: `feat/018-source-folders` (spec directory `specs/018-source-folders`)

**Created**: 2026-11-16

**Status**: Implemented; publisher rollout (T170-T172) and the local folder move (T173) open (decisions final: owner request, no `/speckit-clarify`)

**Tasks**: [tasks.md](tasks.md) | **Decision**: [ADR-016](../../docs/decisions/016-per-source-folders.md) | **Builds on**: [spec 010](../010-map-export-folder/spec.md), [spec 012](../012-tiktok-posting-and-trends/spec.md)

**Input**: User description: "Each source gets its own folder: YouTube videos in 'YouTube Videos', TikTok in 'TikTok Videos', Facebook in 'Facebook Videos'. Point the TikTok publisher at the grouped folder; only YouTube videos are published to TikTok for now. Finished TikTok and Facebook videos are not saved locally: diagnose, and make them seamless to reach on the local machine."

## Problem

Every job writes to `<download_path>/<title>-<job8>/` and, with an export folder set, to `<export>/<job_id>/`: one flat pile for all sources, with generic names (`tiktok_video-<job8>`) when the title lookup fails and no way to open a job's folder from the UI. The TikTok publisher (n8n "Reelsmith Social Publish") scans the flat export folder for manifests.

The "TikTok/Facebook videos are not saved" report is not a storage fault. Rendered clips are written and kept for every source (database: all 28 completed TikTok jobs with render on have their file). The jobs without a file are the two that ran with render off. The web form's Advanced tab starts with every stage off unless the URL counts as short-form, and Facebook Reels did not (`isShortFormUrl`), so a Facebook Reel sent from it was only downloaded; the job page then hid the "source downloaded" note (a file-less clip row exists) and a repeat request returned the same render-off job. Seven failed TikTok jobs are separate: yt-dlp "Log in for access" on age-restricted posts.

## Requirements

- **FR-001**: A new job's folder is `<download_path>/<group>/<slug>-<job8>/` with group `YouTube Videos`, `TikTok Videos`, `Facebook Videos` (and `Instagram Videos`). Uploads, generated videos and `POST /folders` without a known platform stay directly under the download path.
- **FR-002**: With `YTVIDEO_EXPORT_GROUP_BY_SOURCE=true` (default false) and an export folder set, a job exports to `<export>/<group>/<job_id>/`; otherwise as before (`<export>/<job_id>/`).
- **FR-003**: The publisher scans the flat inbox and `YouTube Videos/` only, never `TikTok Videos/` or `Facebook Videos/`; the sidecar accepts `[YouTube Videos/]<job>/<file>`; the ledger and state keys stay the bare `<job>/<file>`. Both live in other repositories (PRs).
- **FR-004**: `scripts/group_downloads_by_source.py` moves existing job folders into their group and rewrites every stored path (dry run by default, one transaction per job, folder put back on failure, idempotent). `scripts/map_inbox_to_db.py` reads both inbox layouts.
- **FR-005**: Facebook Reels (`/reel/`, `/reels/`, `/share/r/`, `fb.watch`) are short-form: the Advanced tab starts with Render on.
- **FR-006**: A request that renders is not answered with an existing job that did not render.
- **FR-007**: A completed job's page shows its folder and an **Open folder** button; a render-off job shows "Source downloaded, no clips were rendered" instead of a file-less clip card. `POST /jobs/{id}/reveal` opens the job's clips folder on the server machine: the path comes only from the job row, must be an existing folder strictly below a managed root, and the caller must be local (403 otherwise).

## Out of scope

Publishing TikTok- or Facebook-sourced clips; TikTok cookies for age-restricted downloads; moving export-inbox history.
