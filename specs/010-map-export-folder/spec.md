# Feature Specification: Map the export folder into the database

**Feature**: `010-map-export-folder` | **Date**: 2026-10-13 | **Decision**: [ADR-010](../../docs/decisions/010-map-export-folder.md)

## Problem

The export folder (`YTVIDEO_EXPORT_BASE_FOLDER`, a Syncthing share) holds the clips and manifests of 66 jobs, but the database knew 11 of them, and every clip path in it pointed at `/tmp/yt`, which macOS had cleared. The jobs' history and their playable files were out of sync.

## Requirements

- **FR-001**: The expiry sweep MUST delete a clip's files only below a managed root (`managed_roots()`); a clip pointing elsewhere is retired and its files are kept.
- **FR-002**: For a job the database knows, a clip whose recorded file does not exist MUST be re-pointed at the file of the same name in `<folder>/<job_id>/`, falling back to the name without `.enhanced` / `.enhanced-audio`. An exact name MUST beat the fallback, and a recorded path that still exists MUST NOT be changed.
- **FR-003**: A job folder (32-hex name, with a manifest) that the database does not know MUST be imported as one completed job, one chapter and one clip per manifest row that has its file, in one transaction, dated from the manifest's modification time.
- **FR-004**: The tool MUST be a dry run unless `--apply`, MUST be idempotent, MUST report every row or file it skips, and MUST NEVER write to the export folder.
- **FR-005**: The manifest is untrusted: a filename that is not a plain file name MUST be skipped, and chapter indexes MUST be unique within a job.
- **FR-006**: An imported job MUST carry a title (`Inbox export <id>`), because the job page shows `Processing…` for a job without one; a title already set MUST NOT be overwritten and a job that is not an inbox import MUST NOT be retitled.

## Out of scope

- Thumbnails (not in the share), start/end times, scores and transcripts of imported clips.
- Rewriting the share's manifests (21 rows still hold another machine's path).
- Recovering a clip that has no recorded path and no file.

## Status

| Requirement | State | Proving test |
|---|---|---|
| FR-001 | Done | `tests/unit/test_retention_sweep.py` (3 tests: outside kept, inside deleted, a full janitor tick) |
| FR-002 | Done | `tests/unit/test_map_inbox_to_db.py::test_dead_clip_paths_of_a_known_job_are_pointed_at_the_inbox_copy` |
| FR-003 | Done | `test_a_folder_the_database_never_saw_becomes_a_completed_job`, `test_a_folder_with_nothing_importable_is_reported_not_silently_dropped` |
| FR-004 | Done | `test_a_dry_run_reports_and_writes_nothing`, `test_a_second_run_changes_nothing`, `test_nothing_in_the_inbox_is_ever_modified` |
| FR-005 | Done | `test_a_filename_that_is_not_a_plain_file_name_is_skipped`, `test_duplicate_or_missing_number_prefixes_still_give_unique_chapter_indexes` |
| FR-006 | Done | `test_an_inbox_job_imported_without_a_title_gets_one_and_a_chosen_title_is_kept` |
