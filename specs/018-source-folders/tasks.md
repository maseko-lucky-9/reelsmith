# Tasks: One folder per source (spec 018)

- [x] T160 `folder_service.SOURCE_FOLDERS` / `source_folder`; `create_video_subfolder` and `POST /folders` group by source (tests: `test_folder_service.py`)
- [x] T161 `_export_dir` helper and `YTVIDEO_EXPORT_GROUP_BY_SOURCE` (default off) (tests: `test_orchestrator_export_dir.py`)
- [x] T162 `scripts/group_downloads_by_source.py` (tests: `test_group_downloads_by_source.py`)
- [x] T163 `scripts/map_inbox_to_db.py` reads the grouped inbox
- [x] T164 `isShortFormUrl` counts Facebook Reels; job page note and Open folder; `POST /jobs/{id}/reveal`; dedup skips render-off jobs for a rendering request
- [x] T165 Publisher PR: n8n-self-hosting #92 (flat + `YouTube Videos/`, YouTube only)
- [ ] T170 Sidecar PR merged and rolled out (accepts `YouTube Videos/<job>/<file>`)
- [ ] T171 n8n PR #92 merged and the workflow deployed (after T170)
- [ ] T172 Set `YTVIDEO_EXPORT_GROUP_BY_SOURCE=true`; the next YouTube job exports to `YouTube Videos/<job_id>/` and posts
- [ ] T173 Back up (`pg_dump`), stop the API, run `group_downloads_by_source --apply` on the owner's stack, check every moved clip's `output_path` exists
