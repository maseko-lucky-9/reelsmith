# ADR-012: The Sidecar Is the Gatekeeper for TikTok Posting; a 15-Minute Dispatcher Replaces the Wait

**Status:** Accepted
**Date:** 2026-10-17
**Author:** Thulani Maseko
**Implements:** T071-T077 (spec 012)

## Context

Clips posted to @reelsmith get no traffic and the automatic push stopped. The request was to add trending sounds and hashtags and to fix "the expired token". We checked the system before building anything:

| Finding | Evidence |
|---|---|
| There is no TikTok token. Posting uses a browser session cookie in `tiktok-sidecar`, last refreshed 2026-06-02. The "Expired" badge is a 21-day timer that nothing checks against TikTok. | `app/routers/social_publish.py:226-227`, sidecar Secret metadata |
| The n8n workflow is inactive since 2026-06-20 and, as stored, could not post: it sends the Mac's absolute path, joins a JSON string, captions with the title cut to 150 characters, and its Wait node releases a whole job at once. | `workflow_entity` row, live node parameters |
| The copy of the workflow in git is older than the live one (the claim store is missing). | 3,265 vs 1,110 characters in `Assign Peak Window` |
| The official Content Posting API cannot replace the cookie path: an unaudited app posts private-only, the audit rejects tools that post to accounts you manage, and there is no sound field. | developers.tiktok.com, Direct Post reference and content-sharing guidelines |
| The ArgoCD app for these manifests syncs manually; the auto-sync copy in `n8n-self-hosting` is stale. | `homelab-infra/argocd/applications/reelsmith-social-publish.yaml` |
| The host loses power or is switched off most evenings, starts with a wrong clock for hours, and its network can be down after boot. | `journalctl --list-boots`, `systemd-timesyncd` lines |
| Clips reach 248 MB; n8n's worker has a 512Mi limit. | clip sizes, `values-live.yaml` |

## Decision

1. **The sidecar is the single gatekeeper.** It reads the clip itself from a read-only mount of the inbox, validates the path and the file, keeps the ledger, enforces the daily cap, refuses duplicates by name and by content hash, and holds the kill switch (`POSTING_ENABLED`, off by default). n8n only chooses a clip and records the outcome.
2. **A dispatcher replaces the Wait.** n8n runs every 15 minutes between 12:00 and 20:45 SAST and posts at most one clip, in three daily slots, newest job first, only from YouTube-sourced jobs dated 2026-10-11 or later. Slot and claim markers are exclusive-create files, so two runs cannot take the same slot or clip.
3. **No video bytes pass through n8n.** The sidecar reads the file; n8n sends a relative path.
4. **Fail closed, never post twice.** The ledger is written before the upload; an unfinished attempt blocks that clip until a person resolves it. Only outcomes that certainly did not start release the clip.
5. **Deploys stay manual.** The workflow is updated in place through the n8n API (same id, deactivation is the rollback); manifests change only on an ArgoCD sync the owner runs.
6. **The YouTube branch is dropped.** It uploaded private videos and loaded each clip into n8n.

Rejected: fixing the Wait in place (it resumes every item at item 0's time, and its static data rolls back after a resume); a rolling 24-hour cap (yesterday's post still counts at today's slot, so a slot is lost most days); `adjtimex` for the clock check (the default seccomp profile blocks it); the official API (see above).

## Consequences

- New clips reach the cluster only while the Mac is awake with Tailscale up, and nothing posts while the homelab is off. The owner needs it on from 11:30 to 21:00 SAST.
- The cookie still needs a login about monthly (`make refresh-cookies`); an invalid session is now detected and alerted instead of failing silently.
- Posting through a reverse-engineered web upload stays against TikTok's terms; the risk accepted at the 2026-05 cutover is unchanged. Three posts a day, one per slot, and the duplicate checks keep the account's behaviour as ordinary as the tool can.
- The web uploader returns no post id, so the ledger's `posted` row is the record; `unpublished-inventory.js` reads it.
- Phase 2 (trending hashtags) and phase 3 (posting from the app) build on the sidecar's contract; the sound stays on hold.
