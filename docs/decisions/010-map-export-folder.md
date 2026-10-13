# ADR-010: Map the Export Folder Into the Database; the Expiry Sweep Deletes Only Below Managed Roots

**Status:** Accepted
**Date:** 2026-10-13
**Author:** Thulani Maseko
**Implements:** T064-T066 (spec 010)

## Context

`YTVIDEO_EXPORT_BASE_FOLDER` points at a Syncthing share. Every job exports its clips and a `manifest.csv` to `<folder>/<job_id>/`, and the share is synced to other devices. We compared it with the database before changing anything:

| Finding | Evidence |
|---|---|
| The share holds 63 job folders (plus 3 test leftovers that are not job ids), 183 manifest rows that have their file and 184 mp4 files. | `read_inbox` over the share. |
| Only 11 of the 63 folders have a job in the database; 52 have none (runs on the old in-memory store lost their records on restart). | job ids vs `jobs.id`. |
| All 80 clip paths in the database are `/tmp/yt/...`, which macOS cleared. 33 live clips (and 22 retired ones) have their file only in the share. | path existence vs the share, matched on file name. |
| The manifests already agree with the database on title, description and hashtags (58 of 58 matched clips). Only paths differ, so nothing else needs correcting. | field-by-field comparison. |
| `sweep_expired_clips` deleted whatever path a clip held with a bare `Path.unlink()`, with no managed-root check. The other two sweeps already use `delete_below_root`. | `app/services/retention.py` before this change. |

The last row decided the order of work. Pointing live clips at the share without fixing the sweep would let the janitor delete the share's files once they passed `YTVIDEO_RETENTION_DAYS`, and Syncthing would delete them on the other devices too.

## Decision

1. **`sweep_expired_clips` deletes only through `delete_below_root`**, below `managed_roots()` (the download path, its `uploads/` and the legacy upload root). A clip that points anywhere else (an export copy) is still retired in the database, and its file is kept. This makes the "Safe deletes" rule in `docs/architecture.md` true for every sweep.
2. **`scripts/map_inbox_to_db.py` maps the share into the database.** A dry run unless `--apply`; one transaction; idempotent; never writes to the share.
   - For a job the database knows: a clip whose recorded file is gone is re-pointed at the file of the same name in the folder, falling back to the pre-enhancement name (`<stem>.enhanced<suffix>` and `.enhanced-audio` are what `enhance_speech` writes; only `<stem><suffix>` was exported). A path that still resolves is never replaced. Matching reads the mp4 files actually in the folder, because a manifest row can lack its filename (an enhanced clip's row did).
   - For a folder the database has never seen: a completed job `inbox://<job_id>` titled `Inbox export <id>`, dated from the manifest's modification time, with a chapter and a clip per manifest row (title, description, hashtags, duration). The manifest is untrusted input from a synced share: a filename that is not a plain file name is skipped, and chapter indexes are made unique.
   - Folder names that are not 32-hex job ids (test leftovers such as `job-1`) are ignored.
3. **Nothing the app does writes into the share.** Pointing clips there exposed a second writer: `POST /clips/{id}/enhance-speech` and `/enhance-audio` write `<stem>.enhanced<suffix>` beside the clip's file with `ffmpeg -y` (and the first re-points the clip at it), so one click would have put a new file in the share and Syncthing would have copied it to other devices. Both endpoints now answer 409 for a clip whose file is not below a managed root (`retention.is_managed_file`). The script also refuses an inbox that lies below a managed root (the janitor would own those files), resolves the inbox to an absolute path, and compares file names in Unicode NFC (macOS can store a name decomposed where the manifest has it composed).
4. **The share is not edited.** 21 manifest rows of one job still say `/data/reelsmith-inbox/...` (another machine's path); fixing them would sync an edit to other devices, so only the database is corrected.

## Result on the real data

> **Correction ([ADR-011](011-repair-job-metadata.md)):** 48 of the 52 imported folders held a single 1-byte test-run file, not a real video. They were dropped again and the tool now skips such files. The "162 of 163 live clips play" below counted those files; 115 live clips remained afterwards, all with a real file.

- 55 clips re-pointed (33 live, 22 retired), then 1 more (the enhanced clip, found by playing every clip, not by the counts). 52 folders imported as jobs with 125 clips; 75 jobs in total. A database dump was taken first.
- 162 of 163 live clips answer a range request with HTTP 206. The one that does not has no recorded path and no file anywhere: its folder holds only a manifest.
- The share was byte-identical before and after (258 files, sizes and modification times).

## Consequences

- Imported jobs have start 0, end = duration, no score, transcript or thumbnail, and no source video: re-render and reprompt answer 409 "source video not retained". The enhanced clip now plays its pre-enhancement export.
- Thumbnails are not in the share, so cards show a placeholder.
- "Enhance speech" is unavailable for clips that live in the share (all imported clips and the re-pointed ones). Making it write to a managed folder instead is a larger change, left for when it is wanted.
- Re-render and reprompt cannot reach the share today: no job has a source video (`video_path` is empty for all 75), and both answer 409 without one. A job that did keep its source would still render over its re-pointed clip; none does.
- The janitor now keeps files outside the managed roots. A deployment with clips in a non-managed folder (for example a custom path outside `YTVIDEO_DEFAULT_DOWNLOAD_PATH`) will see those clips retired in the database but their files left on disk.
- An API process started before this change still holds the old sweep in memory. Restart it to get the guard.
- Rejected: copying the share's files into `data/downloads` (doubles 3 GB and detaches the app from the share) and rewriting the manifests (syncs an edit to other devices).
