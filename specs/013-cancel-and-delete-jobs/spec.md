# Feature Specification: Cancel running jobs and delete finished ones

**Feature**: `013-cancel-and-delete-jobs` | **Date**: 2026-10-28 | **Plan**: [plan.md](plan.md)

## Problem

A job cannot be stopped once it starts: there is no cancel route, no `cancelled` status and no `JobCancelled` event. The orchestrator keeps its tasks in an anonymous set, so nothing can find one job's task, and a job that a shutdown interrupted stays `running` until the next start fails it. A finished job cannot be removed either: there is no job `DELETE`, so the home page fills with old, failed and test jobs whose sources, clips and `_tmp` leftovers stay on disk. The web client also breaks on every `204 No Content`, because `apiFetch` parses a body that is not there.

## Requirements

### Cancel

- **FR-001**: Every pipeline task of a job (its run, a re-render, a reprompt, waiting for a slot or running) MUST be findable by the job's id while it is not done.
- **FR-002**: A user cancel of a job's run MUST end it with status `cancelled` and one `JobCancelled` event (payload `cancelled_step`), emitted last, and MUST remove the job's `_tmp` folder. A job cancelled while it waits for a slot MUST end the same way and MUST NOT take a slot.
- **FR-003**: A shutdown (the orchestrator task cancelled) MUST keep today's behaviour: the job's status is left as it was (the next start fails it as interrupted) and neither `JobCancelled` nor `RepromptFailed` is emitted.
- **FR-004**: A user cancel of a re-render MUST put the chapter back to the status it had before; of a reprompt, MUST keep the old clips (the existing undo), release the reprompt claim (also when the reprompt was still waiting for a slot) and emit `RepromptFailed` with `reason="cancelled"`. The job stays `completed`.
- **FR-005**: The yt-dlp download thread, which cannot be killed, MUST stop at its next progress tick once its job is cancelled (a progress hook raising `yt_dlp.utils.DownloadCancelled`). Until the thread returns, the job MUST count as active.
- **FR-006**: `POST /jobs/{id}/cancel` MUST answer 404 for an unknown job; 202 `cancelling` when the job has live tasks (they are cancelled); 202 `cancelled` for a `pending` or `running` job without a task (still queued, or left by a process that died), which is marked `cancelled` at once with `JobCancelled`, and whose queued run, if it starts later, does not run; 409 otherwise. The job's event stream MUST close on `JobCancelled`.

### Delete

- **FR-007**: `DELETE /jobs/{id}` MUST delete the job's files first, then its rows (clips, chapters, clip edits, publish jobs, scheduled posts, share links, analytics snapshots), so a crash part-way leaves a job that can be deleted again. It MUST answer 204, also for an unknown job (idempotent), and 409 while the job is `pending` or `running` or a re-render, reprompt or download of it still runs.
- **FR-008**: The files deleted MUST be the job's folder (`destination_folder`: source, sidecar, `clips/`, `_tmp/`, `exports/` when no export folder is set), walked bottom-up, and the files its rows name outside it: the source (an upload's `uploads/<uuid>.mp4`, also before the download step set `video_path`), its sidecar and the files of its clips, retired ones included.
- **FR-009**: Nothing MAY be deleted outside the managed roots (`retention.managed_roots`) or inside a protected folder: `YTVIDEO_EXPORT_BASE_FOLDER` (spec 010 FR-007) above all, and the app's models, B-roll library and cache, brand assets, TikTok cookies and generate briefs. An `inbox://` job's files MUST all be kept (only its rows go). A file another job or another job's clip, live or retired, names MUST be kept.
- **FR-010**: Every removal MUST go through `retention.delete_below_root` (re-validated at deletion, no symlink followed or removed, folders removed only when empty). The folder walk MUST NOT follow symlinks and MUST NOT run when the folder is an unsafe path (NUL, relative, `..`), a symlink, not strictly below a managed root, holds a protected folder, or overlaps another job's folder; then only the job's own named files go.

### UI

- **FR-011**: `apiFetch` MUST resolve a 204 without reading a body. `api.cancelJob` and `api.deleteJob` MUST call the routes above.
- **FR-012**: A `pending` or `running` job MUST offer Cancel on its home-page card and in the job page header (also while a reprompt runs); a finished job MUST offer a delete on its card. Both ask `window.confirm` first and do not open the job. A cancelled job shows an amber `cancelled` badge and a notice on its page; a cancelled reprompt is reported as a cancel, not a failure.
- **FR-013**: The home page MUST have a Select mode: checkboxes on the cards, Select all, and "Delete selected (n)", which sends one `DELETE` per job (`Promise.allSettled`, no bulk endpoint), reports the ones that failed and refreshes the list. A `pending` or `running` job cannot be selected.

## Out of scope

- Killing an ffmpeg or transcription thread mid-call beyond what `ffmpeg_tools` already does on cancel.
- Deleting export copies in `YTVIDEO_EXPORT_BASE_FOLDER`, or the export folder's files of an `inbox://` job.
- A bulk delete endpoint, an undo or a trash bin.
- Resuming a cancelled job.

## Status

| Requirement | State | Proving test |
|---|---|---|
| FR-001 | Done | `tests/unit/test_orchestrator_cancel.py::test_user_cancel_marks_a_running_job_cancelled_and_emits_job_cancelled_last`, `test_cancelling_one_job_leaves_the_other_running` |
| FR-002 | Done | `test_user_cancel_marks_a_running_job_cancelled_and_emits_job_cancelled_last`, `test_user_cancel_of_a_job_waiting_for_a_slot_marks_it_cancelled` |
| FR-003 | Done | `test_shutdown_leaves_a_running_job_as_it_was`, `test_shutdown_of_a_waiting_job_leaves_it_pending`, the `shutdown` cases of the re-render and reprompt tests |
| FR-004 | Done | `test_a_cancelled_rerender_puts_the_chapter_back_only_on_a_user_cancel`, `test_a_cancelled_reprompt_keeps_the_old_clips_and_reports_only_a_user_cancel`, `test_a_reprompt_cancelled_while_waiting_releases_its_claim` |
| FR-005 | Done | `test_user_cancel_stops_the_download_thread_and_the_job_stays_active_until_it_ends`, `test_the_download_thread_counts_as_active_while_it_runs`, `tests/unit/test_download_tracker.py::test_download_is_aborted_with_download_cancelled_once_the_stop_check_turns_true` |
| FR-006 | Done | `tests/contract/test_jobs_cancel_delete_router.py` (cancel section), `test_a_queued_job_marked_cancelled_never_runs` |
| FR-007 | Done | `tests/contract/test_jobs_cancel_delete_router.py` (delete section), `tests/unit/test_job_store_delete.py` |
| FR-008 | Done | `tests/unit/test_retention_delete_job.py`: `test_a_url_job_loses_its_whole_folder_and_nothing_else`, `test_an_upload_source_goes_with_its_only_job`, `test_an_upload_that_failed_before_its_download_step_still_loses_its_source` |
| FR-009 | Done | `test_export_folder_copies_are_never_deleted`, `test_an_export_folder_inside_the_download_path_is_still_never_touched`, `test_a_job_folder_that_holds_a_protected_folder_is_not_walked`, `test_an_inbox_job_keeps_every_file`, `test_a_source_another_job_still_uses_is_kept`, `test_a_file_another_jobs_clip_names_is_kept_even_when_retired` |
| FR-010 | Done | `test_symlinks_inside_the_job_folder_are_never_followed`, `test_a_symlinked_folder_inside_the_job_folder_is_not_entered`, `test_a_symlink_to_a_file_elsewhere_in_the_root_keeps_its_target`, `test_a_job_folder_that_is_a_symlink_out_of_the_root_deletes_nothing`, `test_a_job_folder_that_is_a_symlink_inside_the_root_is_not_walked`, `test_a_destination_folder_outside_the_managed_roots_deletes_nothing`, `test_a_dot_dot_folder_is_refused_even_when_it_lands_inside_the_root`, `test_a_job_folder_that_is_a_managed_root_is_not_walked`, `test_a_folder_shared_with_another_job_is_not_walked`, `test_nothing_outside_the_managed_roots_is_even_attempted` |
| FR-011 | Done | `web/src/api/client.test.ts` |
| FR-012 | Done | `web/src/components/dashboard/ProjectCard.test.tsx`, `web/src/routes/jobs.$jobId.test.tsx` (cancel section) |
| FR-013 | Done | `web/src/routes/index.test.tsx` |
