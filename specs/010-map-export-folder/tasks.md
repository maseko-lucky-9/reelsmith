# Tasks: Map the export folder into the database

**Input**: [spec.md](spec.md), [ADR-010](../../docs/decisions/010-map-export-folder.md)

- [x] T064 [FR-001] (done: `sweep_expired_clips` deletes through `delete_below_root` below `managed_roots()`; the janitor tick passes its roots; a clip pointing at an export copy is retired and its file kept) Stop the expiry sweep deleting outside the managed roots. Proving test: `tests/unit/test_retention_sweep.py`
- [x] T065 [FR-002..005] (done: `scripts/map_inbox_to_db.py`; run on the real data: 55 clips re-pointed, 52 folders imported as 125 clips, the share byte-identical afterwards) Map the export folder into the database. Proving test: `tests/unit/test_map_inbox_to_db.py`
- [x] T066 [FR-006] (done: imported jobs and a backfill for inbox jobs already imported get the title `Inbox export <id>`) Give imported jobs a title so the job page does not say "Processing…". Proving test: `test_an_inbox_job_imported_without_a_title_gets_one_and_a_chosen_title_is_kept`
- [x] T067 [FR-007] (done: enhance-speech/enhance-audio answer 409 for a clip outside the managed roots; the script refuses an inbox below one, resolves it, compares names in NFC, reports live clips with no file and flushes on a dry run; an expired clip that is a symlink is kept) Close the holes an independent review found. Proving tests: `tests/contract/test_enhance_speech_router.py`, `tests/unit/test_map_inbox_to_db.py`, `tests/unit/test_retention_sweep.py`
