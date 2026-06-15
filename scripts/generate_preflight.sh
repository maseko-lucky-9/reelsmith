#!/usr/bin/env bash
# generate:// Stage 2 preflight — run the gates and print a GO / NO-GO summary.
#
# Runs Gate A (LTX-Video MPS smoke) then Gate B (Voicebox sidecar smoke),
# captures their exit codes, and prints a summary table. Gate C (brand-voice
# recording) is a manual step and is reported as a reminder only.
#
# Exit-code legend (per gate):
#   0  PASS            — gate green
#   1  FAIL            — gate red (e.g. black frames, bad WAV, health down)
#   2  NOT_CONFIGURED  — gate skipped; setup still needed
#
# This script exits 0 ONLY when both Gate A and Gate B return 0.
set -eu

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

PY="${PYTHON:-python}"

echo "==> generate:// Stage 2 preflight"
echo "    repo: $REPO_ROOT"
echo

# ── Gate A — LTX-Video MPS ────────────────────────────────────────────────────
echo "==> Gate A — LTX-Video (MPS) smoke"
set +e
"$PY" scripts/ltx_smoke.py
GATE_A=$?
set -e
echo

# ── Gate B — Voicebox sidecar ─────────────────────────────────────────────────
echo "==> Gate B — Voicebox sidecar smoke"
set +e
"$PY" scripts/voicebox_smoke.py
GATE_B=$?
set -e
echo

# ── Summarise an exit code into a human label ─────────────────────────────────
label_for() {
  case "$1" in
    0) echo "PASS" ;;
    2) echo "SKIPPED/SETUP-NEEDED" ;;
    *) echo "FAIL" ;;
  esac
}

LABEL_A="$(label_for "$GATE_A")"
LABEL_B="$(label_for "$GATE_B")"

echo "================ PREFLIGHT SUMMARY ================"
printf "  %-8s %-22s (exit %s)\n" "Gate A" "$LABEL_A" "$GATE_A"
printf "  %-8s %-22s (exit %s)\n" "Gate B" "$LABEL_B" "$GATE_B"
printf "  %-8s %-22s\n" "Gate C" "MANUAL"
echo "    Gate C = record ~30s clean brand-voice reference, create the"
echo "    Voicebox profile, and set YTVIDEO_GENERATE_VOICE_PROFILE."
echo "=================================================="

if [ "$GATE_A" -eq 0 ] && [ "$GATE_B" -eq 0 ]; then
  echo "RESULT: GO — Gates A & B green. Complete Gate C, then flip the .env"
  echo "        providers (ltx / voicebox) and YTVIDEO_GENERATE_ENABLED=true."
  exit 0
fi

echo "RESULT: NO-GO — at least one gate is not green (see above)."
echo "        exit 2 means 'not configured yet', not a pass."
exit 1
