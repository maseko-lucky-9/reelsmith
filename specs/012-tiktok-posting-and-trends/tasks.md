# Tasks: Reliable automatic TikTok posting, then trending hashtags

**Input**: [spec.md](spec.md), [ADR-012](../../docs/decisions/012-tiktok-posting-gatekeeper.md). The sidecar lives in `home-lab/tiktok-sidecar`, the workflow and manifests in `home-lab/n8n-self-hosting` (`projects/reelsmith-social-publish/`).

## Phase 1: posting works, safely

- [x] T071 [FR-001,002] Add the `source` column to `manifest.csv` and keep tests out of the real export folder. Proving test: `tests/unit/test_manifest_service.py`, the export-folder fixture in `conftest.py`
- [ ] T072 [FR-004,005,006,009] Sidecar 0.2.0: the gatekeeper (inbox validation, ledger, caps, kill switch, caption rules, redacted outcomes). Proving test: `tiktok-sidecar/tests/test_clip.py`, `test_ledger.py`, `test_caption.py`, `test_upload_api.py`
- [ ] T073 [FR-007] Session probe, 401 on an invalid session, and `make refresh-cookies`. Proving test: `tiktok-sidecar/tests/test_network.py`, `tests/test_scripts.py`; one live refresh at go-live
- [ ] T074 [FR-003,006,009] n8n dispatcher: Pick Due Clip, Log Results, Archive Manifest and the workflow JSON. Proving test: `scripts/*.selfcheck.js` in `n8n-self-hosting`
- [ ] T075 [FR-008] Watchdog rewrite (suspended until go-live). Proving test: `scripts/watchdog.selftest.sh`
- [ ] T076 [FR-010] Manifests, deploy script, README and runbook. Proving test: `kubectl apply --dry-run=server` output, `scripts/workflow-contract.selfcheck.js`
- [ ] T077 **Decision (owner)** Go-live runbook steps 1-9: probe spike, merge A, ship the image, merge B, ArgoCD sync, cookie refresh, dry run, forced-failure alert, `POSTING_ENABLED=true`, first public post, two days of observation. Record the phase-0 gate answers (account status, For You eligibility, clickable tags) and the views baseline in [baseline.md](baseline.md) first.

## Phase 2: trending hashtags (starts after T077 is signed off)

- [ ] T078 [FR-011,012] Spike: two live Apify runs (7 and 30 days, US) to confirm field names and the "rising today" derivation. Proving test: saved fixtures
- [ ] T079 [FR-011,012] Sidecar trends module, token from Vault, daily cache, fail-open. Proving test: `tiktok-sidecar/tests/test_trends.py`
- [ ] T080 Compare views 14 days after the first trended post with the baseline.

## Phase 3: posting from the app (starts after phase 2)

- [ ] T081 [FR-013] Bearer token on the sidecar, `GET /api/posts/{clip_key}`, the clip page status and Post now, the badge from `/healthz/session`.
- [ ] T082 [FR-014] Repair the fallback uploader's imports and error text; refuse the sidecar's handle.

## Phase 4: trending sound

- [ ] T083 **Decision (owner)** On hold: the owner chooses an approach.
