# generate:// Stage 2 Runbook — going live with real AI video

This runbook takes the `generate://` AI video mode from its dark-launched,
stub-only default to a real, operator-verified configuration. It is the *only*
path by which `YTVIDEO_GENERATE_ENABLED` should ever be flipped to `true` on a
real host.

Nothing here changes committed defaults. The repo ships with generation OFF and
both producers on `stub`; everything below is host-local (`.env`) configuration
that an operator applies after the preflight gates pass.

> **Last verified against:** `main` at `1b692d3` (2026-10-10), by reading the
> code listed under *How a job runs*, running the offline test suite
> (`pytest -q`: 2327 passed, 21 deselected), and driving the real API and
> orchestrator against a **fake** LTX fork (a shell script standing in for
> `inference.py`) to capture the failure messages below.
> **Not run: a real LTX generation**, a real Voicebox sidecar, or
> `scripts/generate_preflight.sh`. This runbook does not claim that real
> generation works end to end on any host; Gate A (step 6) is the check that
> does, and it is the operator's to run. Statements marked
> *(not verified in this repo)* come from outside this repository or could not
> be checked here.

## The setup in one paragraph

The `ltx` provider does **not** import LTX-Video. ReelSmith runs the LTX-Video
fork's `inference.py` as a **subprocess**, using a Python interpreter from a
**separate environment that you create and own**, because the fork's
dependency tree conflicts with ReelSmith's (`app/services/ltx_producer.py`
module docstring; `app/settings.py:189-193`). ReelSmith's own environment needs
none of the fork's packages: nothing under `app/`, `scripts/` or `tests/`
imports `torch`, `diffusers`, `transformers`, `safetensors` or `ltx_video`, and
`tests/unit/test_ltx_producer.py::test_importing_ltx_producer_does_not_import_torch`
asserts the producer stays torch-free at import time. Do **not** install the
fork, or `requirements-generate.txt`, into ReelSmith's environment.

## Gates at a glance

| Gate | What it proves | Tool |
|------|----------------|------|
| **A** | The real `ltx` provider produces a playable, correctly-sized, non-black clip from your fork environment | `scripts/ltx_smoke.py` |
| **B** | The Voicebox sidecar is healthy and returns a valid WAV | `scripts/voicebox_smoke.py` |
| **C** | A ~30 s brand-voice reference is recorded and a profile id exists | manual (this runbook) |

Gates A and B are independent of each other in the app: the LTX provider and
the TTS provider are separate settings (`YTVIDEO_LTX_PROVIDER`,
`YTVIDEO_GENERATE_TTS_PROVIDER`), so you can run real LTX with the `stub`
(silent) voice-over. Only `scripts/generate_preflight.sh` demands both A and B.

**Exit-code legend** (every gate and the orchestrator share it):

| Code | Meaning |
|------|---------|
| `0` | PASS: gate green |
| `1` | FAIL: gate red (black frames, wrong dims, over budget, bad WAV, health down) |
| `2` | NOT_CONFIGURED: gate skipped; setup still needed (not a pass) |

What Gate A checks (`scripts/ltx_smoke.py`, `main`): the three LTX settings are
set and exist (else exit 2); the real provider runs once; the clip's size equals
the requested width and height rounded to a multiple of 32 (default
`704x1216`, not `1080x1920`); duration is above zero; the sampled frames are not
all near-black; and, only if you pass `--max-seconds`, the wall-clock time is
within that budget. It does **not** check which device the fork used.

## How a job runs

A `generate://` job is the ordinary job pipeline with a different download
step. Code path at `1b692d3`:

1. `POST /generate` (also `POST /api/generate`; see *Smoke test*) validates the
   brief and answers `400` if `generate_enabled` is false
   (`app/routers/generate.py:67-71`). Otherwise it writes
   `<YTVIDEO_GENERATE_BRIEF_DIR>/<brief_id>.json` atomically (`:73-86`), creates
   a job with `url=generate://<brief_id>` and
   `download_path=default_download_path` (`:88-99`), enqueues it (`:114-119`),
   and returns `202 {"job_id", "brief_id"}`. Configuration errors do **not**
   show up here: the `202` comes back even if LTX is not configured.
2. The orchestrator resolves the `generate://` scheme to `GenerateAdapter`
   (`app/services/platforms/__init__.py:33`, `app/workers/orchestrator.py:126`),
   creates the per-job folder (`:133-139`) and runs
   `adapter.download(url, destination)` in a worker thread under
   `asyncio.wait_for(..., timeout=settings.download_timeout_seconds)`
   (`:161-170`).
3. `GenerateAdapter.download` (`app/services/platforms/generate.py:75-160`)
   re-checks `generate_enabled`, loads the brief, writes the voice-over
   `vo.wav` with `tts_service.synthesize` (`:109-119`), then, **one shot at a
   time, in order**, calls `ltx_producer.generate_shot` for each brief shot
   into `shot_NNN.mp4` (`:121-141`; `seconds` is clamped to `0.1..30.0`), and
   assembles everything into `generated.mp4` with one bundled-ffmpeg pass
   (`:144-145`, `_assemble`).
4. For `provider="ltx"`, `generate_shot` -> `_ltx_shot`
   (`app/services/ltx_producer.py:90-194`):
   - checks that each of the three settings is set **and the path exists**
     (`:108-123`);
   - rounds width and height to a multiple of 32 and derives the frame count
     from `seconds x frame_rate`, rounded to the nearest valid `8n+1`
     (`:125-128`, helpers `:43-68`; 3 s at 24 fps is 73 frames);
   - makes a private temp folder (`tempfile.mkdtemp(prefix="ltx_out_")`, in the
     system temp directory) and runs
     `<ltx_python> <inference_script> --prompt <prompt> --pipeline_config <yaml> --output_path <tmp> --height <h> --width <w> --num_frames <n> --frame_rate <fps> --seed <seed>`
     with `subprocess.run(..., capture_output=True, text=True, timeout=900)`
     (`:137-170`);
   - takes the newest `*.mp4` anywhere under the temp folder, copies it to
     `shot_NNN.mp4` and deletes the temp folder (`:179-194`).
5. The assembled `generated.mp4` goes through the normal pipeline: no chapters,
   so (with the default `YTVIDEO_SEGMENT_PROVIDER=chapter`) one "Full Video"
   clip, then transcription, captions, render, thumbnail, export and
   `manifest.csv` (`app/workers/orchestrator.py:198-335`).

**Process lifetime.** The fork is a child of the API process, started per shot
(a cold start: the fork loads the model on every invocation, so every shot
pays model load; `ltx_producer.py:34-36`). The only thing that kills it is
`subprocess.run`'s own `timeout=900` (hard-coded as `_LTX_TIMEOUT_SECONDS`,
`ltx_producer.py:36`, not a setting): on expiry Python kills the child and
waits for it. Checked with the fake fork: the child was gone when the job
reported the timeout. There is no job-cancel route. See *Failure modes* for the
job-level timeout, which does **not** kill it.

**Request body limits.** `title` 1-200 chars; `script` 1-10 000 chars; up to 50
`shots`, each `{"prompt": <=2 000 chars, "seconds": 0.1-30.0 (default 2.0)}`;
optional `voice_profile`; optional `music_url` (must be empty or `http(s)://`).
The request has **no `seed` field**, so every shot created through the API runs
with `--seed 0`; a brief file written by hand may carry a per-shot `seed`.
`music_url` is validated and stored in the brief but nothing in `app/` reads it.

## Prerequisites

- ReelSmith running from its own environment (`requirements.txt`,
  `uvicorn app.main:app`). Assembly and rendering use the bundled ffmpeg
  (`imageio-ffmpeg`), not a system one.
- A separate Python environment for the LTX-Video fork on a host that can run
  it. The earlier version of this runbook targeted Apple Silicon (MPS), and
  `app/settings.py:185` says "GPU/MPS host", but **ReelSmith performs no device
  check** and imposes no hardware requirement of its own; what the fork can run
  on is the fork's concern *(not verified in this repo)*.
- Enough disk and time for the model weights and a cold model load per shot
  *(sizes and speeds not verified in this repo)*.
- For real voice-over only: a running Voicebox sidecar (steps 4-5). Not needed
  to test LTX on its own.

## Steps

### 1. Create the LTX environment (separate from ReelSmith's)

The LTX-Video fork referenced by the previous runbook is
<https://github.com/maseko-lucky-9/LTX-Video> (a public fork of
`Lightricks/LTX-Video`; its existence and root `inference.py` were confirmed
through the GitHub API on 2026-10-10, not from this repo). Use your own fork
or checkout if you have one.

1. Clone it somewhere outside the ReelSmith checkout.
2. Create the fork's own virtual environment and install it **following the
   LTX-Video project's own install instructions** (the README in the fork,
   "Run locally", "Installation"). This repo gives no authoritative install
   command and pins no LTX package versions, so none are given here.
3. Note two absolute paths: the environment's interpreter
   (`<ltx-env>/bin/python`) and the fork's `inference.py`.

ReelSmith never activates this environment. It only runs the interpreter path
you give it, so the shell you start ReelSmith from does not need the fork
environment active.

Optional sanity check that the interpreter and script start (the fork's CLI is
built on `HfArgumentParser`, so `--help` should print its flags; not run here,
*not verified in this repo*):

```bash
/absolute/path/to/ltx-env/bin/python /absolute/path/to/LTX-Video/inference.py --help
```

### 2. Weights and the pipeline yaml

`YTVIDEO_LTX_PIPELINE_CONFIG` must point at a pipeline config yaml that the
fork understands; ReelSmith passes it through unread as `--pipeline_config`.
The fork ships example configs under its `configs/` folder (for instance
`ltxv-2b-0.9.8-distilled.yaml`), and the weights those configs name are the
fork's business: get them as the fork's own instructions describe. Two
fork-side behaviours worth knowing, both read from the fork's GitHub `main`
(`4b2d053`) on 2026-10-10 and *not verified in this repo*:

- if the yaml's `checkpoint_path` (and the spatial upscaler path) is not an
  existing local file, the fork downloads it from the Hugging Face hub on
  first use. That time counts against ReelSmith's per-shot limit
  (see *Failure modes*), so put local files in the yaml, or warm the cache
  with a manual fork run first;
- the fork picks its own device (CUDA, then MPS, then CPU) with no error on
  CPU. A CPU run will probably exceed ReelSmith's per-shot limit.

Choose a model variant that finishes a shot well inside 900 seconds on your
host. The earlier claim that the 2B distilled variant is "the one Gate A is
budgeted against" no longer holds: Gate A has no built-in budget, only the
optional `--max-seconds`.

### 3. Point ReelSmith at the LTX environment

Exact names are the `YTVIDEO_` prefix plus the field in `app/settings.py`
(`env_prefix`, `:34`); they match the commented block in
[`.env.example`](../.env.example) (`:201-222`).

| Env var | What it must point at | Default |
|---------|-----------------------|---------|
| `YTVIDEO_LTX_PYTHON` | the interpreter inside the LTX environment (`<ltx-env>/bin/python`) | empty |
| `YTVIDEO_LTX_INFERENCE_SCRIPT` | the fork's `inference.py` | empty |
| `YTVIDEO_LTX_PIPELINE_CONFIG` | the fork's pipeline yaml (step 2) | empty |

All three must be set **and exist**, else the provider raises NOT_CONFIGURED
(`ltx_producer.py:108-123`). The guard is `Path.exists()` only: a directory, or
a file that is not executable, passes it and fails later (a non-executable
interpreter fails the job with `[Errno 13] Permission denied`).

```bash
# .env (host-local, never commit)
YTVIDEO_LTX_PYTHON=/absolute/path/to/ltx-env/bin/python
YTVIDEO_LTX_INFERENCE_SCRIPT=/absolute/path/to/LTX-Video/inference.py
YTVIDEO_LTX_PIPELINE_CONFIG=/absolute/path/to/LTX-Video/configs/<your-config>.yaml
```

Optional tuning, same names as `.env.example`:

| Env var | Default | Effect |
|---------|---------|--------|
| `YTVIDEO_LTX_HEIGHT` / `YTVIDEO_LTX_WIDTH` | `1216` / `704` | portrait frame size; each is rounded to the nearest multiple of 32 (minimum 32) |
| `YTVIDEO_LTX_FRAME_RATE` | `24` | passed as `--frame_rate`; with the shot's `seconds` it sets `--num_frames` |
| `YTVIDEO_GENERATE_BRIEF_DIR` | `data/generate-briefs` | where briefs are stored; **relative to the API process's working directory** |

**Settings that no longer exist.** `ltx_model_path`, `ltx_use_mps` and
`ltx_num_frames` were removed in T014 (`tests/unit/test_settings_module.py`
asserts they stay gone). `YTVIDEO_LTX_MODEL_PATH`, `YTVIDEO_LTX_USE_MPS` and
`YTVIDEO_LTX_NUM_FRAMES` in an old `.env` are ignored (`extra="ignore"`,
`app/settings.py:36`); weights now live in the fork's yaml and the frame count
is derived per shot.

**`requirements-generate.txt` is not part of this setup.** Its header, the
comment at `.env.example:203-204` and the comment at `app/settings.py:185`
still say the real providers need it; that predates the subprocess provider.
Nothing in the repo imports what it installs.

### 4. Voicebox sidecar (only for real voice-over)

Run the Voicebox sidecar by its own project's instructions *(install and run
steps not verified in this repo; the sidecar is not part of this repo)*, then
point the app at its **base URL**:

```bash
# .env (host-local, never commit)
YTVIDEO_VOICEBOX_ENDPOINT=http://<sidecar-host>:<port>
YTVIDEO_VOICEBOX_API_KEY=<optional-bearer-token>   # only if the sidecar requires auth
YTVIDEO_VOICEBOX_ENGINE=kokoro                     # default
```

What the app calls, from `app/services/tts_service.py`: `POST <base>/generate`
with `{profile_id, text, engine}`, then polls `GET <base>/history/<id>` (up to
300 s) and downloads `GET <base>/audio/<id>`. A trailing `/generate` on the
configured endpoint is stripped (`_normalize_base`, `:79`). The old
`.../synthesize` endpoint is **not** what the app calls any more.

Gate B probes `<base>/health` (`scripts/voicebox_smoke.py:59`), so configure
the base URL, not `.../generate` (that would probe `.../generate/health`).
That the sidecar actually serves `/health`, `/generate`, `/history/<id>` and
`/audio/<id>` is *not verified in this repo*.

```bash
curl -fsS http://<sidecar-host>:<port>/health
```

### 5. Gate C: record the brand-voice reference (manual)

1. Record about 30 seconds of clean reference audio (quiet room, consistent
   level, no music).
2. Create the Voicebox profile from it using the sidecar's own flow and note
   the profile id *(flow not verified in this repo)*.
3. Set it:

   ```bash
   # .env (host-local, never commit)
   YTVIDEO_GENERATE_VOICE_PROFILE=<profile-id>
   ```

A brief's own `voice_profile` overrides this setting. With the `voicebox`
provider and no profile, synthesis fails with `voicebox: voice_profile
(profile_id) is required — set YTVIDEO_GENERATE_VOICE_PROFILE`
(`tts_service.py:198-202`). Gate C has no automated check.

### 6. Run the preflight / Gate A

Run the scripts from ReelSmith's environment (they import `app.settings`,
PyAV and httpx; the LTX environment is only used by the provider itself):

```bash
python scripts/ltx_smoke.py                  # Gate A alone
python scripts/ltx_smoke.py --max-seconds 90 # also fail if slower than 90 s
python scripts/voicebox_smoke.py             # Gate B alone
bash scripts/generate_preflight.sh           # A then B; GO only if both exit 0
```

`ltx_smoke.py` renders a 3 s clip by default (`--seconds`, `--prompt`,
`--width`, `--height`, `--frame-rate` and `--out` override; default output is
`ltx_smoke_shot.mp4` in the system temp directory) and prints a report with
dims, duration, wall-clock and the black-frame verdict. With the three
settings empty it exits `2` and lists what is missing (observed on `1b692d3`).

`generate_preflight.sh` exits `0` only when **both** A and B return `0`
(`scripts/generate_preflight.sh:61`). If you are not using a Voicebox sidecar,
Gate B is NOT_CONFIGURED and the preflight reports NO-GO even when Gate A is
green: use `ltx_smoke.py` directly to qualify LTX on its own. A gate that
returns `2` is shown as SKIPPED/SETUP-NEEDED and is never a pass.

### 7. Flip the providers (host-local `.env` only)

Only after Gate A (and, if you use real voice-over, B and C) are green, and
never in committed defaults:

```bash
# .env (host-local, never commit)
YTVIDEO_GENERATE_ENABLED=true
YTVIDEO_LTX_PROVIDER=ltx
YTVIDEO_GENERATE_TTS_PROVIDER=voicebox   # or leave as stub for a silent voice-over
YTVIDEO_DOWNLOAD_TIMEOUT_SECONDS=<seconds>   # see below; default 600
```

Restart the API: settings are read once at import (`settings = Settings()`,
`app/settings.py:245`).

**Raise `YTVIDEO_DOWNLOAD_TIMEOUT_SECONDS`.** The default is `600`
(`app/settings.py:235`) and it bounds the **whole** `generate://` download:
the voice-over plus every shot in sequence. One shot alone may take up to 900 s
and a brief may have up to 50 shots, so a real run can exceed 600 s while
nothing is wrong. Set it to at least `(number of shots x your worst per-shot
time) + voice-over time`. The worst case per shot is 900 s, and the Voicebox
poll can add up to 300 s.

### 8. Smoke test through the API

Start with **one short shot**. Use the host and port you started `uvicorn`
with (`uvicorn` defaults to `127.0.0.1:8000`).

```bash
curl -sS -X POST http://127.0.0.1:8000/generate \
  -H 'Content-Type: application/json' \
  -d '{
        "title": "LTX smoke",
        "script": "A short test of the generate pipeline.",
        "shots": [{"prompt": "a calm sunrise over a quiet city skyline, slow drift", "seconds": 3}]
      }'
# 202 {"job_id": "<job_id>", "brief_id": "<brief_id>"}
```

Every route answers at both `/x` and `/api/x` (`app/api_prefix.py`,
`app/main.py:199`, [ADR-005](decisions/005-api-route-prefix.md)), so
`/api/generate` is the same endpoint. If `YTVIDEO_REQUIRE_AUTH=true`, add
`-H "Authorization: Bearer $YTVIDEO_API_KEY"` (`app/auth.py:14-22`). A `400`
with `generate mode disabled: set YTVIDEO_GENERATE_ENABLED=true` means step 7
was not applied or the API was not restarted.

Follow the job:

```bash
curl -sS http://127.0.0.1:8000/jobs/<job_id>           # status, current_step, error, output_paths
curl -N  http://127.0.0.1:8000/jobs/<job_id>/events    # SSE; ends on JobCompleted or JobFailed
```

A healthy run goes `pending` -> `running` (`current_step` `folder`, `download`,
then the render steps) -> `completed`. The `download` step is where LTX runs, so
expect it to take the longest. `error` holds the failure text if the job ends
`failed`.

**First-run caution.** `YTVIDEO_EXPORT_BASE_FOLDER`, when set, receives the
exported clip and `manifest.csv` under `<base>/<job_id>/`. Its own comment in
`.env.example` says that folder feeds downstream n8n publishing, which is
outside this repo *(not verified in this repo)*. For the first runs leave it
blank or point it at a scratch folder. The earlier runbook's "DRY mode" for the
publish handoff has no counterpart in `app/` (no such mode exists in the code
at `1b692d3`), so do not rely on it.

## Expected outputs

Per job, under `default_download_path` (default `<project>/data/downloads`,
`app/settings.py:60`), in `generate_video-<first 8 chars of the job id>/`
(`app/services/folder_service.py:35-74`):

| Path | What |
|------|------|
| `vo.wav` | the voice-over (silence with the `stub` TTS) |
| `shot_000.mp4`, `shot_001.mp4`, ... | one clip per brief shot (a copy of the fork's output with `ltx`; a solid colour with `stub`) |
| `generated.mp4` | shots concatenated on the largest shot's canvas, black-padded, voice-over attached |
| `clips/` | the rendered clip(s), e.g. `00_Full Video.mp4` and its `_thumb.jpg` |
| `exports/` | exported clip(s) and `manifest.csv` (or `<YTVIDEO_EXPORT_BASE_FOLDER>/<job_id>/` when that is set; `orchestrator.py:309-335`) |

Also: the brief at `<YTVIDEO_GENERATE_BRIEF_DIR>/<brief_id>.json`, and the
fork's own temp folder (`ltx_out_*` in the system temp directory), which is
removed after each shot. This layout was observed with a fake fork that copies
a stub clip into `--output_path`, not with real LTX output. How the final
render treats real `704x1216` LTX output has not been checked.

## Failure modes the code handles

Any exception during the download step fails the job with status `failed`, no
retry, no partial clips, and an `error` of the form `download failed: <message>`
(`app/workers/orchestrator.py:168-170`, recorded by `_record_failure`,
`:1036`). The first five rows were reproduced through the real API with the
fake fork (the timeout row with the 900 s constant patched down to 2 s); the
rest are read from the code.

| Situation | Message in the job's `error` (after `download failed: `) | Where |
|-----------|----------------------------------------------------------|-------|
| One of the three LTX settings unset or its path missing | `ltx not configured: set YTVIDEO_LTX_PYTHON / YTVIDEO_LTX_INFERENCE_SCRIPT / YTVIDEO_LTX_PIPELINE_CONFIG (unset or not found: ltx_python, ...)`; raised when the first shot starts, after the voice-over is done | `ltx_producer.py:108-123` |
| Fork exits non-zero | `ltx inference failed (returncode=N): <last 1500 chars of the fork's stderr>` | `:172-177` |
| Fork exits 0 but writes no `*.mp4` | `ltx inference produced no mp4 in the output folder (stdout tail: <last 800 chars>)` | `:184-189` |
| One shot exceeds 900 s | `ltx generation timed out after 900s`; the child is killed | `:36`, `:167-170` |
| Interpreter exists but is not executable | `[Errno 13] Permission denied: '<path>'` (an OS error, not an `LtxError`) | `subprocess.run`, wrapped at `orchestrator.py:168-170` |
| `YTVIDEO_LTX_PROVIDER` is neither `stub` nor `ltx` | `unknown ltx provider: '<value>'` | `:244` |
| `YTVIDEO_GENERATE_ENABLED` false | HTTP `400` from the route; or, for a job already queued, `generate mode disabled: set YTVIDEO_GENERATE_ENABLED=true` | `routers/generate.py:67-71`, `platforms/generate.py:76-79` |
| `voicebox` TTS without endpoint or profile | `voicebox: endpoint is required`; `voicebox: voice_profile (profile_id) is required — set YTVIDEO_GENERATE_VOICE_PROFILE` | `tts_service.py:196-202` |
| Voicebox HTTP error, failed generation, or 300 s poll timeout | `voicebox POST /generate failed (status=...)`; `voicebox generation <id> failed`; `voicebox generation <id> timed out after 300s` | `tts_service.py:118,133,148` |

**The job-level timeout is shorter than the shot timeout and does not stop the
fork.** With the default `YTVIDEO_DOWNLOAD_TIMEOUT_SECONDS=600`, a job whose
voice-over plus shots take longer than 600 s fails with an **empty** `error`
(`''`: it is a bare `asyncio.TimeoutError`, `orchestrator.py:166-167`, which has
no message), `current_step` `download`. The worker thread cannot be cancelled,
so the fork keeps running after the job is already marked failed: with the fake
fork, the child was still alive when the job reported the failure and stopped
only when it finished by itself. The fork's own 900 s limit still applies to
it. Remedy: raise `YTVIDEO_DOWNLOAD_TIMEOUT_SECONDS` (step 7).

Not checked: what happens to a running fork when the API process is shut down
or restarted mid-shot, and whether a fork that starts its own child processes
leaves those behind after the 900 s kill (Python's timeout kills only the
direct child). *(not verified in this repo)*

### Troubleshooting

- **Gate A: all sampled frames near-black.** ReelSmith received a playable clip
  whose content is black and cannot tell why. A torch / device-version mismatch
  inside the fork environment is a plausible cause, but that is a hypothesis
  *(not verified in this repo)*; check the fork environment with the fork's own
  instructions, and run the fork's `inference.py` by hand with the same
  `--pipeline_config`. Do not "fix" it by changing ReelSmith.
- **Gate A: dims mismatch.** Expected size is the configured
  `YTVIDEO_LTX_WIDTH x YTVIDEO_LTX_HEIGHT`, each rounded to a multiple of 32
  (default `704x1216`), or the `--width`/`--height` you passed.
- **Slow or timing out.** Every shot is a cold subprocess that loads the model.
  Check whether the fork is downloading weights or running on CPU (step 2), use
  fewer or shorter shots, and size `YTVIDEO_DOWNLOAD_TIMEOUT_SECONDS` as in
  step 7.
- **To see the exact command ReelSmith ran,** read the API log line
  `ltx subprocess: <cmd>` (INFO, `ltx_producer.py:159`). It includes the
  prompt. The fork's stdout is not logged; only the stderr tail (on failure)
  and stdout tail (when no mp4 appears) are returned in the error.
- **Gate B health probe non-200.** The sidecar is not up, or
  `YTVIDEO_VOICEBOX_ENDPOINT` is not its base URL (step 4).
- **NOT_CONFIGURED (exit 2).** A setting is empty or its path does not exist;
  complete the matching step above. Exit 2 never counts as a pass.

## What the test suite does and does not cover

**The test suite fakes the producer. No test runs a real LTX generation.**

- The pipeline and adapter tests run with `YTVIDEO_LTX_PROVIDER=stub`, which
  renders a solid-colour `1080x1920` clip with ffmpeg
  (`tests/contract/test_generate_pipeline.py:33`,
  `tests/unit/test_generate_adapter.py:103`).
- `tests/unit/test_ltx_producer.py` replaces `subprocess.run` with a function
  that drops a dummy `.mp4` into `--output_path` (or returns a non-zero code,
  nothing, or raises `TimeoutExpired`), using dummy files for the three paths.
  It proves the command line, the `8n+1` / multiple-of-32 rounding and the
  error messages above, not that your fork environment works.
- `tests/unit/test_stage2_smoke.py` covers the gate scripts' helpers and their
  NOT_CONFIGURED paths.

A green `pytest` therefore says nothing about whether your LTX environment,
weights and yaml work. Only Gate A (step 6) and the smoke test (step 8), run
on the real host, do.

## Rolling back

Set `YTVIDEO_LTX_PROVIDER=stub` (solid-colour shots) or
`YTVIDEO_GENERATE_ENABLED=false` (the route answers `400`) in the host `.env`
and restart the API. Jobs already running are not interrupted.
