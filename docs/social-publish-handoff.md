# Social publish handoff — reelsmith → n8n contract

## What reelsmith produces

After every completed job, the orchestrator writes two artifacts into a dedicated sub-folder:

```
$YTVIDEO_EXPORT_BASE_FOLDER/
└── <job_id>/
    ├── <clip-stem>.mp4    (one file per rendered clip)
    └── manifest.csv       (written last — safe trigger target)
```

`manifest.csv` is always written **after** all clips have been fully copied (`shutil.copy2`). An n8n `LocalFileTrigger` watching for `**/manifest.csv` will only fire once the entire job folder is complete.

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

## n8n trigger configuration

```
Node: LocalFileTrigger
Watch path: /data/reelsmith-inbox/**/manifest.csv
Trigger on: file created
```

The job folder path is `dirname(triggeredFilePath)`. All clip `export_path` values are absolute paths valid inside the n8n pod (the Syncthing PVC is mounted at `/data/reelsmith-inbox` on both main and worker pods).

## Peak-time publishing

Clips from each manifest are distributed one-per-peak-window across four regions:

| Region | Timezone | Daily peaks (local) |
|---|---|---|
| South Africa | Africa/Johannesburg | 12:00, 18:00, 20:00 |
| US East | America/New_York | 12:00, 18:00, 20:00 |
| US West | America/Los_Angeles | 12:00, 18:00, 20:00 |
| UK / EU | Europe/London | 12:00, 19:00, 21:00 |

`clip[0]` → earliest upcoming peak window, `clip[1]` → second window, and so on. Both TikTok and YouTube post at the same scheduled time. DST is handled automatically via `luxon` inside the n8n Function node.

## Environment variable

```
YTVIDEO_EXPORT_BASE_FOLDER=/path/to/SyncthingShares/reelsmith-exports
```

Leave blank to fall back to `<download_path>/<video>/exports` (no per-job sub-folder — not suitable for n8n integration).
