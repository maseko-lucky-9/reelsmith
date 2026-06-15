# generate:// Stage 2 Runbook — going live with real AI video

This runbook takes the `generate://` AI video mode from its dark-launched,
stub-only default to a real, operator-verified configuration on an Apple
Silicon (MPS) host. It is the *only* path by which `YTVIDEO_GENERATE_ENABLED`
should ever be flipped to `true`.

Nothing here changes committed defaults. The repo ships with generation OFF and
both producers on `stub`; everything below is host-local (`.env`) configuration
that an operator applies after the preflight gates pass.

## Gates at a glance

| Gate | What it proves | Tool |
|------|----------------|------|
| **A** | LTX-Video 2B renders a non-black, correctly-sized clip on MPS | `scripts/ltx_smoke.py` |
| **B** | Voicebox/Kokoro sidecar is healthy and returns a valid WAV | `scripts/voicebox_smoke.py` |
| **C** | A 30s brand-voice reference is recorded and a profile id exists | manual (this runbook) |

**Exit-code legend** (every gate and the orchestrator share it):

| Code | Meaning |
|------|---------|
| `0` | PASS — gate green |
| `1` | FAIL — gate red (black frames, wrong dims, over budget, bad WAV, health down) |
| `2` | NOT_CONFIGURED — gate skipped; setup still needed (not a pass) |

## Steps

### 1. Clone the LTX-Video fork

```bash
git clone https://github.com/maseko-lucky-9/LTX-Video
# note the absolute path, e.g. /Users/you/src/LTX-Video
```

Install the fork into the same environment you will run ReelSmith from so
`from ltx_video.inference import generate` resolves (see step 3).

### 2. Download the LTX-Video 2B distilled weights

Download the **2B distilled** weights to a local directory, then point the
app at them:

```bash
# .env (host-local — never commit)
YTVIDEO_LTX_MODEL_PATH=/absolute/path/to/ltx-2b-distilled
```

The 2B distilled variant is the one Gate A is budgeted against; larger
variants will blow the wall-clock budget on an MPS laptop.

### 3. Install the generate-only ML dependencies

These are intentionally kept out of `requirements.txt` so CI stays torch-free.

```bash
pip install -r requirements-generate.txt   # torch / diffusers / transformers / safetensors
```

Runtime prerequisite (not pip-installable): Apple Silicon with MPS. The `ltx`
provider is MPS-only **by design** — it raises rather than silently falling
back to CPU.

### 4. Install and run the Voicebox sidecar (Kokoro-min)

Start the Voicebox/Kokoro-min TTS sidecar and point the app at its
`/synthesize` endpoint:

```bash
# .env (host-local — never commit)
YTVIDEO_VOICEBOX_ENDPOINT=http://127.0.0.1:8080/synthesize
YTVIDEO_VOICEBOX_API_KEY=<optional-bearer-token>   # only if the sidecar requires auth
```

Verify the sidecar's health endpoint responds before continuing:

```bash
curl -fsS http://127.0.0.1:8080/health
```

Gate B derives the health URL automatically (it swaps a trailing
`/synthesize` for `/health`).

### 5. Gate C — record the brand-voice reference (manual)

1. Record **~30 seconds** of clean reference audio (quiet room, consistent
   level, no music) of the brand voice.
2. Create the Voicebox brand-voice profile from that reference (per the
   sidecar's profile-creation flow) and note the returned profile id.
3. Set it:

   ```bash
   # .env (host-local — never commit)
   YTVIDEO_GENERATE_VOICE_PROFILE=<profile-id>
   ```

Gate C has no automated check — `scripts/generate_preflight.sh` lists it as a
manual reminder. The brand-voice profile id is what Gate B passes through to
the sidecar.

### 6. Run the preflight — must be GO

```bash
bash scripts/generate_preflight.sh
```

A **GO** requires:

- **Gate A** — `device=mps`, dims match (default `1080x1920`), `duration > 0`,
  **not** all-black frames, within the wall-clock budget.
- **Gate B** — health `200`, and a synthesized WAV with `nframes > 0` and
  `duration > 0`.

You can tighten Gate A with a budget and run the gates individually:

```bash
python scripts/ltx_smoke.py --max-seconds 90        # fail if slower than 90s
python scripts/voicebox_smoke.py
```

The orchestrator exits `0` only when both A and B return `0`. A gate that
returns `2` (NOT_CONFIGURED) is shown as **SKIPPED/SETUP-NEEDED** and blocks
the GO — it is explicitly **not** treated as a pass.

### 7. Flip the providers (host-local `.env` ONLY)

Only after a GO, and never in committed defaults:

```bash
# .env (host-local — never commit)
YTVIDEO_LTX_PROVIDER=ltx
YTVIDEO_GENERATE_TTS_PROVIDER=voicebox
YTVIDEO_GENERATE_ENABLED=true
```

### 8. Submit a real brief and verify end-to-end

Submit a real text brief via `POST /generate` (or the dashboard) and verify:

- the assembled reel renders,
- the manifest is written, and
- the publish handoff runs in **DRY** mode (no live post) for the first run.

Only promote to a live publish after a clean DRY handoff.

## Troubleshooting

- **All frames near-black (Gate A FAIL).** This is the canonical MPS symptom:
  it usually means a torch / MPS version mismatch producing NaNs on the Metal
  backend rather than a real render. Check the torch + MPS notes in the vault,
  pin a known-good `torch` version, and re-run Gate A. Do **not** work around
  it by switching to CPU.
- **CPU fallback is a hard fail by design.** If MPS is unavailable the `ltx`
  provider raises rather than rendering on CPU — that is intentional, not a bug
  to patch. Fix the MPS environment instead.
- **Gate B health probe non-200.** The sidecar isn't up or the health route
  differs. Confirm `curl <endpoint-without-/synthesize>/health` returns 200 and
  that `YTVIDEO_VOICEBOX_ENDPOINT` points at the `/synthesize` route.
- **NOT_CONFIGURED (exit 2).** A required env var is unset (weights path / torch
  not installed for Gate A; endpoint empty for Gate B). Complete the
  corresponding step above; exit 2 never counts as a pass.
- **Over budget (Gate A FAIL with `--max-seconds`).** Confirm you're on the 2B
  *distilled* weights and that the device really resolved to `mps`, not a larger
  variant or an accidental CPU path.
