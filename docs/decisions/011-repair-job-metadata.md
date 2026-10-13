# ADR-011: Repair Job Metadata From the URL, Not by Re-Running the Job; Drop the Placeholder Imports

**Status:** Accepted
**Date:** 2026-10-13
**Author:** Thulani Maseko
**Implements:** T068-T070 (spec 011)

## Context

After [ADR-010](010-map-export-folder.md) the job list still had gaps: the 23 original jobs showed "Processing…" (the old store never kept a title or duration), and 52 jobs imported from the export folder were labelled `Inbox export <id>`. The request was to re-run the application on each URL, on the grounds that the videos were already downloaded. We checked before building anything:

| Finding | Evidence |
|---|---|
| The source videos are not on disk. `data/downloads` and `/tmp/yt` are gone; one 534 KB source survives (`Me at the zoo`). | filesystem inventory |
| The app cannot re-run an existing job. `POST /jobs` always makes a new job id, downloads again (no reuse by video id) and exports to a new folder in the share. | `app/routers/jobs.py:108-122`, `orchestrator.py`, `_yt_dlp_base.py` |
| The 52 imported jobs have no URL anywhere: not in the database, the SQLite copies, the dumps, the manifests or the mp4 tags. | search of all of them |
| **48 of the 52 imports were test-run leftovers**: each held one 1-byte `.mp4`. ADR-010 imported them as real jobs and its "162 of 163 live clips play" counted those 1-byte files. The 4 real imports hold 25, 50, 1 and 1 clips. | file sizes of every clip |

## Decision

1. **Metadata only, from the URL, onto the existing job.** `scripts/repair_job_metadata.py` asks yt-dlp for the title and duration with `download=False` and writes them into the job's `state`. Nothing is downloaded, no job is created and nothing is written to the share. A title or duration that is already set is never overwritten, one URL is fetched once however many jobs share it, and a failed fetch leaves the job as it was and is reported. An `upload://` job gets its file stem (what the upload adapter uses); an `inbox://` job gets the title in its manifest's thumbnail path (`…/<SLUG>-<id8>/clips/…`) when the id matches.
2. **`--drop-placeholders`** deletes an `inbox://` job (with its chapters and clips) only when it has clips and every one of them is a file that exists and is smaller than 8 bytes. A file that is merely missing does not count, so an unmounted share cannot make real jobs look like junk. Jobs with a real URL are never touched.
3. **`map_inbox_to_db.py` skips an mp4 under 8 bytes** (an mp4 starts with a box header of at least 8 bytes) and reports it. Without this the next `--apply` would import the dropped folders again.
4. Rejected: re-running every URL through the app (about 17 duplicate jobs, GBs of downloads, a new share folder per run) and a script that re-downloads and grafts transcripts and scores onto the existing rows (needs a source file per job and hours of transcription).

## Result on the real data

- A dump first (`data/backups/reelsmith-pg-before-metadata-repair-20261013T204217.sql`: 75 jobs, 204 chapters, 205 clips).
- 48 placeholder jobs dropped: 27 jobs, 156 chapters, 157 clips left; no orphan rows; the 23 original jobs still 13 completed / 10 failed.
- 18 jobs titled: 7 YouTube (title and duration), 5 TikTok (caption and duration), 4 uploads (file stem, which is a UUID for these), 2 imports (slug). The job page now shows the title and length instead of "Processing…".
- 7 jobs stay untitled: a fake YouTube id, `example.com`, and five on one TikTok post that needs a login.
- 2 imports (`47a0e928`, `df96c4d9`, single "Full Video" clips) keep `Inbox export <id>`: no URL and no slug exist.
- The share was byte-identical before and after (258 files). All 114 live clip files with a path exist.

## Consequences

- Transcripts, scores, thumbnails and start/end times of the imported clips are not recovered; that needs the source videos, which are gone. Re-processing a URL later is still possible through `POST /jobs`, as a new job.
- `www.tiktok.com` did not always resolve on this network; the script is idempotent, so a later run fills in whatever failed.
- The 3 `job-*` test folders and the 21 manifest rows with another machine's path stay in the share.
