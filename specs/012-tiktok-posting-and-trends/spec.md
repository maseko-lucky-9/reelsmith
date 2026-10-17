# Feature Specification: Reliable automatic TikTok posting, then trending hashtags

**Feature**: `012-tiktok-posting-and-trends` | **Date**: 2026-10-17 | **Decision**: [ADR-012](../../docs/decisions/012-tiktok-posting-gatekeeper.md)

## Problem

Clips posted to @reelsmith get no public traffic, and the automatic push to TikTok has stopped. Read-only checks on 2026-10-17 (phase 0) showed that the cause is not a token:

- No TikTok API is used. A clip travels reelsmith → `manifest.csv` → Syncthing → n8n → `tiktok-sidecar` → TikTok's web upload, signed in with a session cookie. The "Expired" badge in Settings → Social is a 21-day timer from the day the account was connected; nothing compares it with TikTok.
- The n8n workflow has been inactive since 2026-06-20 and, as stored, could not have posted. It sends the producing machine's file path, calls `.join` on a JSON string of hashtags, captions with the title cut to 150 characters ("Full Video" on 8 of 20 jobs), releases a whole job at one moment (the Wait node), and never alerts on a TikTok failure. The copy in git was also older than the live one.
- The cookie was last refreshed on 2026-06-02.
- The homelab is switched off most evenings (power button, `shutdown` over SSH) and starts with a wrong clock until NTP syncs; some mornings the network is down for hours.
- 142 real clips from 20 jobs wait in the share. Of the 17 jobs since 2026-10-11, 10 came from TikTok, 5 from YouTube (107 clips), 1 has no source and 1 is a one-clip "Full Video".

The work is split into phases. Each phase starts only after the owner signs off the one before it.

## Requirements

### Phase 1: posting works, safely

- **FR-001**: `manifest.csv` MUST carry a `source` column (the job's platform id) as its last column; the other columns and their order MUST NOT change.
- **FR-002**: A test run MUST NOT write into the configured export folder.
- **FR-003**: The dispatcher MUST post at most one clip per run, only on or after one of three daily slots (12:00, 15:00 and 18:00 Africa/Johannesburg), at least 170 minutes after the previous slot was taken, never after 21:00, newest job first. It MUST consider only manifests dated 2026-10-11 or later whose source is YouTube (a manifest without a `source` column only when its job is on the listed legacy allowlist).
- **FR-004**: The sidecar MUST upload only a regular `.mp4` file inside its read-only inbox (no traversal, no symlink, a real MP4 header, 1 KB to 300 MB), from a private copy. It MUST refuse a clip already posted by name or by content hash (409), a fourth post in a day (429), and every upload while `POSTING_ENABLED` is false or the ledger is unreadable.
- **FR-005**: The posted text MUST be the description (else the title) followed by at most five hashtags made of `[A-Za-z0-9_]`, in at most 2,200 characters, with emoji, `@` mentions, URLs and a leading `-` removed so they cannot shift the tags or crash the uploader.
- **FR-006**: A clip MUST NOT be posted twice. An upload that may have reached TikTok but has no recorded outcome MUST block that clip (`ambiguous`) until a person resolves it. An upload that certainly did not start (network down, clock unsynced, sidecar busy or paused) MUST release the clip and the slot for a later try.
- **FR-007**: A cookie TikTok no longer accepts MUST be reported as `auth_invalid`, never as a generic failure, by `/healthz/session` and by an upload, and MUST alert on Telegram with the refresh command. `make refresh-cookies` MUST renew it without leaving a copy of the cookie on disk or in a Secret annotation.
- **FR-008**: The watchdog MUST alert on an error execution, an invalid session, an unreachable sidecar, no successful post in 48 hours, and no execution at all in 25 hours; ongoing conditions at most once a day.
- **FR-009**: No response, log line, ledger row, execution record or Telegram message MUST contain a cookie, token, signature or upload output; only a code and an `error_id`.
- **FR-010**: The live workflow MUST be updated in place (same workflow id) and be reversible by deactivation. Nothing posts until the owner sets `POSTING_ENABLED=true`.

### Phase 2: trending hashtags (planned)

- **FR-011**: Before each upload the sidecar MUST add three trending TikTok hashtags (US charts: "rising today" derived from the 7-day chart, the top of the 7-day chart and the top of the 30-day chart, each picked at random from that list's top 3) to two AI hashtags, five in all.
- **FR-012**: Trend data MUST come from one cached fetch a day. A failed fetch MUST fall back to a cache up to 3 days old and then to the AI tags alone. Trend tags MUST pass the same sanitising as FR-005 plus a profanity and tragedy denylist.

### Phase 3: posting from the app (planned)

- **FR-013**: Reelsmith MUST show each clip's auto-post status and a Post now action that goes through the sidecar; the sidecar MUST require a bearer token; the "Expired" badge MUST reflect `/healthz/session`.
- **FR-014**: The in-app uploader MUST report its real import error and MUST refuse the sidecar's handle.

### Phase 4: trending sound (on hold)

The TikTok Content Posting API has no sound field, and the web uploader's `music_info` is unproven. The owner will return to it.

## Out of scope

- Posting clips from TikTok-sourced jobs, and YouTube uploads (the old private-upload branch is gone).
- A TikTok post id or URL: the web uploader does not return one, so the sidecar's ledger is the record.
- Fixing the host's power, clock battery and backups; those are listed for the owner in the plan.

## Status

"Built" means the code and its tests are in an open pull request (A = `tiktok-sidecar`, B = `n8n-self-hosting`); it becomes "Done" only after the go-live runbook step named in the last column.

| Requirement | State | Proving test |
|---|---|---|
| FR-001 | Done | `tests/unit/test_manifest_service.py` |
| FR-002 | Done | `tests/unit/test_export_isolation.py` |
| FR-003 | Built (B) | `scripts/pick-due-clip.selfcheck.js`; live: runbook step 5 |
| FR-004 | Built (A) | `tests/test_clip.py`, `tests/test_upload_api.py` in `tiktok-sidecar` |
| FR-005 | Built (A) | `tests/test_caption.py` in `tiktok-sidecar` |
| FR-006 | Built (A, B) | `tests/test_ledger.py`, `tests/test_upload_api.py`; `scripts/log-results.selfcheck.js` |
| FR-007 | Built (A, B) | `tests/test_network.py`, `tests/test_scripts.py`; `scripts/log-results.selfcheck.js`; live: runbook steps 4 and 6 |
| FR-008 | Built (B) | `scripts/watchdog.selftest.sh`; live: runbook step 8 |
| FR-009 | Built (A, B) | `tests/test_secrets.py`, `tests/test_uploader.py`; `scripts/log-results.selfcheck.js` |
| FR-010 | Built (B) | `scripts/workflow-contract.selfcheck.js`; live: runbook steps 3, 5 and 7 |
| FR-011 to FR-014 | Planned (phases 2 and 3) | |
