# Social publish handoff: reelsmith → n8n → tiktok-sidecar

Decision and reasoning: [ADR-012](decisions/012-tiktok-posting-gatekeeper.md). Requirements: [spec 012](../specs/012-tiktok-posting-and-trends/spec.md). Operating detail for the cluster side lives in `home-lab/n8n-self-hosting/projects/reelsmith-social-publish/README.md` and `home-lab/tiktok-sidecar/README.md`.

## What reelsmith produces

After every completed job, the orchestrator writes two things into a dedicated sub-folder:

```
$YTVIDEO_EXPORT_BASE_FOLDER/
└── <job_id>/
    ├── <clip-stem>.mp4    (one file per rendered clip)
    └── manifest.csv       (written last, after all clips are copied)
```

`manifest.csv` is always written **after** all clips have been fully copied (`shutil.copy2`). Syncthing may still be copying a clip when the manifest arrives, so a consumer must wait for a missing file rather than skip or archive it.

## manifest.csv schema

| Column | Type | Notes |
|---|---|---|
| `filename` | string | Basename of the clip file |
| `title` | string | Human-readable clip title |
| `duration_seconds` | float | Clip duration in seconds |
| `file_size_mb` | float | File size in megabytes |
| `description` | string | Auto-generated description (the caption) |
| `hashtags` | JSON string | `json.dumps(list[str])`, without `#` |
| `export_path` | string | Absolute path on the **producing machine**. Consumers must not use it; they join `filename` to the manifest's own folder. |
| `thumbnail_path` | string | Absolute path to the thumbnail (may be empty) |
| `job_id` | string | Matches the parent folder name |
| `broll_credits` | JSON string | B-roll attributions (`[]` when none) |
| `source` | string | The job's platform id: `youtube`, `tiktok`, `upload`, `generate`, `instagram`, `facebook`; empty for jobs created before the column existed |

`manifest.csv` is raw, machine-read data: cells are written as they are, with no spreadsheet escaping, so a caption starting with `@`, `-`, `+` or `=` is posted as written. Do not open it in a spreadsheet to edit it; the bulk-export zip (`GET /clips/bulk-export.zip`) has a spreadsheet-safe manifest instead. With a scene-search prompt (spec 014) `title` and `description` are model output (the vision judge's title and reason) or transcript text: treat them as untrusted text, never as markup or a command.

## How a clip reaches TikTok

```
Reelsmith job done
  └▶ <share>/<job_id>/ clips + manifest.csv
       └▶ Syncthing: Mac (send-only) ─▶ cluster PVC syncthing-reelsmith-pvc
            (only while the Mac is awake with Tailscale up)
            └▶ n8n "Reelsmith Social Publish", every 15 minutes, 12:00-20:45 SAST
                 Pick Due Clip ─▶ TikTok Sidecar Upload ─▶ Log Results ─▶ Archive Manifest
                      └▶ tiktok-sidecar (the gatekeeper): reads the clip from /inbox, checks, posts
                           └▶ TikTok web upload, signed in with the session cookie
```

Rules enforced:

- **Slots:** at most one clip per run, on or after 12:00, 15:00 or 18:00 SAST, at least 170 minutes after the previous slot was taken, never after 21:00. A slot missed while the host was off posts late, the same day.
- **Which clips:** manifests dated 2026-10-11 or later, rotating across source videos (never the same video twice in a row; least recently used first), then row order within a video. A video's `00_*` intro (or a clip titled "Intro") is never posted. Only `source = youtube` (a manifest without the column only if its job id is on the workflow's legacy list). A missing file waits; a file of 1 KB or less, an empty name or a non-`.mp4` name is junk and is skipped. A manifest is archived into `processed/` only when every row is posted, posted elsewhere or junk.
- **Never twice:** the sidecar refuses a clip already posted (by name or by content hash) with 409, and blocks a clip whose last upload has no recorded outcome (`ambiguous`) until a person resolves it.
- **Caps:** three posts per SAST day; a pause after two failed attempts in a row.
- **Markers** live on the cluster volume, outside the Syncthing folder (`.slots/`, `.state/`) or beside the job (`.claims/`, `results.log.csv`). The share on the Mac is never edited.

## Alerts and operating commands

| Event | What happens |
|---|---|
| Session cookie rejected | Telegram: "cookie expired, run `make refresh-cookies`" (repo `tiktok-sidecar`) |
| Upload may have reached TikTok (timeout, reset, rejected publish) | clip kept as failed or `ambiguous`; Telegram with the file name and an `error_id`; resolve with `make ledger-resolve KEY=<job>/<file> OUTCOME=posted\|not_posted` |
| Network or clock not ready, sidecar busy or paused | clip and slot released; Telegram after three in a row |
| No post for 48 hours, publisher not running for 25 hours, sidecar down, error execution | watchdog Telegram (ongoing conditions once a day) |

Switches: `POSTING_ENABLED` on the sidecar (off by default) and the workflow's `DRY_RUN`. Rollback: `scripts/deploy-workflow.sh --deactivate`, then set `POSTING_ENABLED=false` or revert the manifests PR and sync.

## Syncthing replication

Mac-side Syncthing replicates `$YTVIDEO_EXPORT_BASE_FOLDER` into the cluster:

| Mac folder | Type | Cluster path |
|---|---|---|
| `~/SyncthingShares/reelsmith-inbox` | Send Only | PVC `syncthing-reelsmith-pvc`, folder `reelsmith-inbox/` |

n8n mounts the PVC root at `/data/reelsmith-inbox`, so a job is at `/data/reelsmith-inbox/reelsmith-inbox/<job_id>/`. The sidecar mounts only the `reelsmith-inbox` subfolder, read-only, at `/inbox`, so a job is at `/inbox/<job_id>/`. The Mac reaches the cluster Syncthing over its Tailscale address, so nothing syncs while Tailscale is off or the Mac is asleep.

## Environment variable

```
YTVIDEO_EXPORT_BASE_FOLDER=/Users/ltmas/SyncthingShares/reelsmith-inbox
```

Leave blank to fall back to `<download_path>/<video>/exports` (no per-job sub-folder, so not suitable for n8n). Tests never write to this folder (`conftest.py` redirects it).

## Standing prerequisites

- The Mac awake with Tailscale up while a job exports, so the clips reach the cluster.
- The homelab on from about 11:30 to 21:00 SAST.
- A fresh session cookie about monthly.

## Testing the sidecar without posting

```bash
# Port-forward from a machine with homelab kubectl access
kubectl -n n8n-live port-forward svc/tiktok-sidecar 8000:8000 &

# A dry run runs every check and builds the caption, but uploads nothing
curl -s -X POST http://127.0.0.1:8000/api/upload \
  -F "clip_path=<job_id>/<file>.mp4" \
  -F "caption=Your caption here" \
  -F 'hashtags=["gadgets","tech"]' \
  -F "dry_run=true"
```

The full request and response contract is in the sidecar README (Contract v1).
