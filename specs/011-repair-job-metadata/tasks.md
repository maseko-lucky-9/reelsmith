# Tasks: Repair job metadata from the URL

**Input**: [spec.md](spec.md), [ADR-011](../../docs/decisions/011-repair-job-metadata.md)

- [x] T068 [FR-001..003,006] (done: `scripts/repair_job_metadata.py`; on the real data 18 jobs titled, 7 reported as unreachable) Fill a job's missing title and duration from its URL, manifest slug or upload name, without downloading or writing to the share. Proving test: `tests/unit/test_repair_job_metadata.py`
- [x] T069 [FR-004,006] (done: `--drop-placeholders`; 48 placeholder jobs dropped after a dump, 27 jobs left) Drop the `inbox://` jobs that hold only placeholder files. Proving test: `test_only_a_job_that_is_nothing_but_placeholders_is_dropped`
- [x] T070 [FR-005] (done: `MIN_MP4_BYTES` in `scripts/map_inbox_to_db.py`) Stop `map_inbox_to_db` importing placeholder files. Proving test: `test_a_one_byte_placeholder_mp4_is_reported_and_never_imported_or_used_as_a_copy`
