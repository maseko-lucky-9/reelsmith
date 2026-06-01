# Social publish handoff — reelsmith → n8n contract

## What reelsmith produces

After every completed job, the orchestrator writes two artifacts into a dedicated sub-folder:

```
$YTVIDEO_EXPORT_BASE_FOLDER/
└── <job_id>/
    ├── <clip-stem>.mp4    (one file per rendered clip)
    └── manifest.csv       (written last — safe trigger target after all clips copied)
```

`manifest.csv` is always written **after** all clips have been fully copied (`shutil.copy2`).

## manifest.csv schema

| Column | Type | Notes |
|---|---|---|
| `filename` | string | Basename of the clip file |
| `title` | string | Human-readable clip title |
| `duration_seconds` | float | Clip duration in seconds |
| `file_size_mb` | float | File size in megabytes |
| `description` | string | Auto-generated description |
| `hashtags` | JSON string | `json.dumps(list[str])` — strip `#` for YouTube tags |
| `export_path` | string | Absolute path to the `.mp4` inside the job sub-folder |
| `thumbnail_path` | string | Absolute path to thumbnail image (may be empty) |
| `job_id` | string | Matches the parent folder name |

## n8n workflow — current architecture (post-2026-05-27 sidecar cutover)

**Workflow:** `Reelsmith Social Publish` (id `SKLkX5qUDYLlWTxo`)
**Trigger:** `Scanner Trigger` cron — `0 */8 * * *` (every 8 hours)

> ⚠️ The `LocalFileTrigger` node was removed in the n8n 2.x upgrade — use the
> `Scanner Trigger` cron instead. Any documentation referencing `LocalFileTrigger`
> is outdated.

### Publish pipeline

```
Scanner Trigger (0 */8 * * *)
  └─▶ Scan Manifests (Code) — walks /data/reelsmith-inbox for manifest.csv, skips processed/
        └─▶ Index Clips (Code) — fans out per clip row; resolves export_path, sets clipIndex
              └─▶ Assign Peak Window (Code) — distributes clips across 4-region peak windows
                    └─▶ Wait — holds until scheduledAt
                          └─▶ Read Clip File — reads mp4 as binary
                                ├─▶ TikTok branch:
                                │     └─▶ TikTok Rate Limit (≤3 posts/day cap)
                                │           └─▶ TikTok Sidecar Upload
                                │                 POST http://tiktok-sidecar.n8n-live.svc.cluster.local:8000/api/upload
                                │                 (multipart: video, caption, hashtags, account=reelsmith)
                                └─▶ YouTube branch:
                                      └─▶ YouTube Init → YouTube Capture Session → YouTube Upload
                                            (OAuth2 credential: reelsmith-youtube, privacyStatus: private)
                                                │
                                 Merge Results ◀┘
                                      └─▶ Log Results (results.log.csv: timestamp,clip,title,status,tiktok_post_id,tiktok_post_url)
                                            └─▶ Archive Manifest (moves manifest.csv → processed/manifest-<ISO>.csv)
```

### TikTok publish mechanism

TikTok is published via a **self-hosted FastAPI sidecar** (`tiktok-sidecar`) running in the `n8n-live`
namespace. The sidecar wraps `makiisthenes/TiktokAutoUploader` and authenticates using **session
cookies** (not the official TikTok Content Posting API).

- **Cookie auth** — no developer app, no OAuth flow, no API registration required.
- **Public posts** — posts land as public on the `@reelsmith` account.
- **Cookie rotation** — session cookies expire in 2–4 weeks; monthly refresh required
  (see `apps/tiktok-sidecar/README.md §6`).
- **Rate cap** — the `TikTok Rate Limit` n8n node enforces ≤3 posts/day using
  `$getWorkflowStaticData('global')` (UTC-day-scoped).
- **NEVER** `kubectl delete pod tiktok-sidecar-*` — use `rollout restart` to preserve
  the seeded cookies PVC.

### Peak-time publishing

Clips from each manifest are distributed one-per-peak-window across four regions:

| Region | Timezone | Daily peaks (local) |
|---|---|---|
| South Africa | Africa/Johannesburg | 12:00, 18:00, 20:00 |
| US East | America/New_York | 12:00, 18:00, 20:00 |
| US West | America/Los_Angeles | 12:00, 18:00, 20:00 |
| UK / EU | Europe/London | 12:00, 19:00, 21:00 |

`clip[0]` → earliest upcoming peak window, `clip[1]` → second, and so on.
DST is handled automatically via `luxon` inside the Assign Peak Window Code node.

## Syncthing replication

Mac-side Syncthing replicates `$YTVIDEO_EXPORT_BASE_FOLDER` into the cluster:

| Mac folder | Type | Cluster mount |
|---|---|---|
| `~/SyncthingShares/reelsmith-inbox` | Send Only | `/data/reelsmith-inbox` (n8n-live PVC `syncthing-reelsmith-pvc`) |

The `Scan Manifests` node walks `/data/reelsmith-inbox` inside the n8n pod. After processing,
`Archive Manifest` moves the manifest to `processed/manifest-<ISO>.csv` (a rename, not delete —
the Mac Send-Only folder stays clean).

## Environment variable

```
YTVIDEO_EXPORT_BASE_FOLDER=/Users/ltmas/SyncthingShares/reelsmith-inbox
```

Leave blank to fall back to `<download_path>/<video>/exports` (no per-job sub-folder —
not suitable for n8n integration).

## Direct sidecar publish (for testing / immediate post without waiting for cron)

```bash
# Port-forward the sidecar from any machine with homelab kubectl access
kubectl -n n8n-live port-forward svc/tiktok-sidecar 8000:8000 &

# POST the clip
curl -s -X POST http://127.0.0.1:8000/api/upload \
  -F "video=@/path/to/clip.mp4" \
  -F "caption=Your caption here" \
  -F "hashtags=#gadgets #tech #fyp" \
  -F "account=reelsmith"
# → {"status":"success","message":"Published successfully","post_url":"...","post_id":"..."}
```

This bypasses the cron + peak-window scheduler and posts immediately.
Used for smoke-testing and on-demand live verification (e.g. 2026-05-27 B-7 test).
