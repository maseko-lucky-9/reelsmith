#!/usr/bin/env bash
# Recreate .venv-mlx, the sidecar venv for AI scene search (ADR-013).
#
# Separate from the API venv on purpose: mlx-vlm pulls its own fastapi/uvicorn/starlette
# and relies on mlx-vlm internals (see app/vendor/sentrysearch/mlx_embedder.py).
# Apple Silicon only. Usage:
#
#   scripts/setup_scene_venv.sh            # create/refresh .venv-mlx from requirements-scene.txt
#   PYTHON=/opt/homebrew/bin/python3.14 scripts/setup_scene_venv.sh
#
# Model weights are NOT installed here; scripts/scene_index.py downloads the chosen
# Hugging Face model into ~/.cache/huggingface on first use.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${PYTHON:-/opt/homebrew/bin/python3.14}"
VENV="${SCENE_VENV:-$REPO/.venv-mlx}"

if [ "$(uname -s)" != "Darwin" ] || [ "$(uname -m)" != "arm64" ]; then
  echo "setup_scene_venv: MLX needs Apple Silicon (Darwin/arm64)." >&2
  exit 1
fi
if [ ! -x "$PYTHON" ]; then
  echo "setup_scene_venv: interpreter not found: $PYTHON (set PYTHON=...)" >&2
  exit 1
fi

"$PYTHON" -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -r "$REPO/requirements-scene.txt"
"$VENV/bin/python" -c "import mlx.core, mlx_vlm, cv2, numpy, imageio_ffmpeg; print('imports ok')"
echo "setup_scene_venv: $VENV ready"
