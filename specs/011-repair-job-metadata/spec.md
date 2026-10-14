# Feature Specification: Repair job metadata from the URL

**Feature**: `011-repair-job-metadata` | **Date**: 2026-10-13 | **Decision**: [ADR-011](../../docs/decisions/011-repair-job-metadata.md)

## Problem

The 23 original jobs have no stored title or duration (the page says "Processing…"), and 52 jobs imported from the export folder carry the label `Inbox export <id>`, 48 of them for 1-byte test-run files. The source videos are gone and the app cannot re-run an existing job, so the fix has to come from the URL (or the manifest) alone.

## Requirements

- **FR-001**: For a job with an `http(s)` URL whose title is blank (or `Inbox export …`) or whose duration is unset, the tool MUST fetch the title and duration of that single video without downloading it, once per distinct URL, and only for a `completed` or `failed` job, and write them into the job's `state`. A title or duration already set MUST NOT be overwritten.
- **FR-002**: A failed fetch MUST leave the job unchanged, MUST be reported with the job and the reason, and MUST NOT stop the other jobs.
- **FR-003**: An `upload://` job MUST get its file stem as title and no fetch. An `inbox://` job MUST get the title of the `<SLUG>-<id8>` in its manifest's `thumbnail_path` only when the id matches the job; the export folder MUST only be read.
- **FR-004**: `--drop-placeholders` MUST delete an `inbox://` job, its chapters and its clips only when it has clips and every clip file exists and is under 8 bytes. A missing file, a real file, a job with no clips and a job with a real URL MUST all be kept.
- **FR-005**: `map_inbox_to_db` MUST skip an mp4 under 8 bytes and report it, so a dropped job is not imported again.
- **FR-006**: Both modes MUST be a dry run unless `--apply`, and a second run MUST change nothing.

## Out of scope

- Transcripts, scores, thumbnails and start/end of imported clips (no source videos).
- A URL for the imports that have none; titles for videos that no longer exist or need a login.

## Status

| Requirement | State | Proving test |
|---|---|---|
| FR-001 | Done | `tests/unit/test_repair_job_metadata.py`: `test_a_job_without_title_or_duration_gets_both_from_the_url`, `…never_overwritten`, `…a_duration_that_stays`, `…a_title_is_filled_next_to_a_duration_that_stays`, `test_five_jobs_on_one_url_cost_one_fetch` |
| FR-002 | Done | `test_a_failed_fetch_is_reported_and_leaves_that_job_alone_while_the_rest_proceed` |
| FR-003 | Done | `test_an_upload_job_is_titled_with_its_file_stem`, `test_an_inbox_job_is_titled_from_the_slug_in_its_manifest_and_the_share_is_untouched` |
| FR-004 | Done | `test_an_inbox_job_whose_files_are_all_placeholders_is_dropped_with_its_rows`, `test_only_a_job_that_is_nothing_but_placeholders_is_dropped` |
| FR-005 | Done | `tests/unit/test_map_inbox_to_db.py::test_a_one_byte_placeholder_mp4_is_reported_and_never_imported_or_used_as_a_copy` |
| FR-006 | Done | `test_a_dry_run_writes_nothing_and_a_second_run_changes_nothing`, `test_a_dry_run_drops_nothing` |
