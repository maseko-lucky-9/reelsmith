"""scripts/scene_index.py end to end under .venv-mlx (real model, real ffmpeg).

Skipped unless the sidecar venv exists. It needs the scene model in the Hugging Face
cache (the first run downloads ~2 GB) and the Apple GPU, so it is opt-in
(``-m integration``). The cosine assertion is the silent-video-drop check from ADR-013:
if mlx-vlm drops the video pixels, every chunk embeds to nearly the same vector.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[2]


def _scene_python() -> Path | None:
    """The sidecar interpreter: $YTVIDEO_SCENE_PYTHON, else the first .venv-mlx up the tree
    (a git worktree sits under the main checkout, which owns the venv)."""
    override = os.environ.get("YTVIDEO_SCENE_PYTHON")
    if override:
        return Path(override) if Path(override).exists() else None
    for base in (REPO, *REPO.parents):
        candidate = base / ".venv-mlx" / "bin" / "python"
        if candidate.exists():
            return candidate
    return None


SCENE_PYTHON = _scene_python()

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(SCENE_PYTHON is None, reason=".venv-mlx not found (run scripts/setup_scene_venv.sh)"),
    pytest.mark.skipif(sys.platform != "darwin", reason="MLX needs Apple Silicon"),
]


def _bundled_ffmpeg() -> str:
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


@pytest.fixture
def two_scene_clip(tmp_path: Path) -> Path:
    """16 s clip: 8 s of a test pattern, then 8 s of a mandelbrot zoom."""
    out = tmp_path / "two_scenes.mp4"
    subprocess.run(
        [
            _bundled_ffmpeg(), "-y",
            "-f", "lavfi", "-i", "testsrc2=s=320x240:r=10:d=8",
            "-f", "lavfi", "-i", "mandelbrot=s=320x240:r=10",
            "-filter_complex", "[1:v]trim=duration=8,setpts=PTS-STARTPTS[b];[0:v][b]concat=n=2:v=1:a=0[v]",
            "-map", "[v]", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            str(out),
        ],
        check=True, capture_output=True, timeout=120,
    )  # fmt: skip
    return out


def test_index_writes_normalised_distinct_embeddings_and_streams_progress(two_scene_clip, tmp_path):
    out_dir = tmp_path / "idx"
    scratch = tmp_path / "tmp"
    scratch.mkdir()
    script = REPO / "scripts" / "scene_index.py"
    env = {**os.environ, "TMPDIR": str(scratch)}
    done = subprocess.run(
        [
            str(SCENE_PYTHON), str(script), "index", "--chunk", "8", "--overlap", "0",
            "--", str(two_scene_clip), str(out_dir),
        ],
        capture_output=True, text=True, timeout=900, env=env,
    )  # fmt: skip
    assert done.returncode == 0, done.stderr

    progress = [json.loads(line) for line in done.stdout.splitlines()]
    assert progress[0] == {"done": 0, "total": 2}
    assert progress[-1] == {"done": 2, "total": 2}

    emb = np.load(out_dir / "embeddings.npy")
    assert emb.shape == (2, 768) and emb.dtype == np.float32
    assert np.linalg.norm(emb, axis=1) == pytest.approx(1.0, abs=1e-3)
    chunks = json.loads((out_dir / "chunks.json").read_text())
    assert [c["start"] for c in chunks] == [0.0, 8.0]
    assert chunks[1]["end"] == pytest.approx(16.0, abs=0.3)  # the concat is a frame or two short

    cosine = float(emb[0] @ emb[1])
    assert cosine < 0.99, f"chunks of different scenes embed alike (cosine {cosine:.4f}): video pixels dropped?"
    assert list(scratch.iterdir()) == [], "temp chunk files left behind"

    # A missing source fails fast with exit 1 and a clear message, and writes nothing.
    missing = subprocess.run(
        [str(SCENE_PYTHON), str(script), "index", "--", str(tmp_path / "nope.mp4"), str(tmp_path / "idx2")],
        capture_output=True, text=True, timeout=120, env=env,
    )  # fmt: skip
    assert missing.returncode == 1 and "video not found" in missing.stderr
    assert not (tmp_path / "idx2").exists()


def test_dash_prefixed_query_round_trips_through_double_dash(tmp_path):
    script = REPO / "scripts" / "scene_index.py"
    done = subprocess.run(
        [str(SCENE_PYTHON), str(script), "query", "--", "--help me find a dog"],
        capture_output=True, text=True, timeout=300,
    )  # fmt: skip
    assert done.returncode == 0, done.stderr
    lines = done.stdout.splitlines()
    assert len(lines) == 1 and "usage" not in lines[0]
    vector = np.array(json.loads(lines[0]), dtype=np.float32)
    assert vector.shape == (768,) and float(np.linalg.norm(vector)) == pytest.approx(1.0, abs=1e-3)


def test_sigterm_mid_index_exits_143_and_leaves_no_temp_dir_or_ffmpeg(tmp_path):
    """Phase 2 cancels a job by SIGTERM-ing this process: it must clean up after itself."""
    clip = tmp_path / "long.mp4"
    subprocess.run(
        [
            _bundled_ffmpeg(), "-y", "-f", "lavfi", "-i", "testsrc2=s=320x240:r=10:d=96",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip),
        ],
        check=True, capture_output=True, timeout=120,
    )  # fmt: skip
    scratch = tmp_path / "tmp"
    scratch.mkdir()
    proc = subprocess.Popen(
        [str(SCENE_PYTHON), str(REPO / "scripts" / "scene_index.py"), "index", "--", str(clip), str(tmp_path / "idx")],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env={**os.environ, "TMPDIR": str(scratch)},
    )  # fmt: skip
    try:
        assert json.loads(proc.stdout.readline())["done"] == 0
        assert json.loads(proc.stdout.readline())["done"] == 1  # a chunk is embedded, 11 remain
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=60) == 143, proc.stderr.read()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    assert list(scratch.iterdir()) == [], "temp chunk dir left behind"
    assert not (tmp_path / "idx").exists(), "a cancelled index must not publish files"
    survivors = subprocess.run(["pgrep", "-f", str(clip)], capture_output=True, text=True).stdout.split()
    assert survivors == [], f"ffmpeg or indexer still running: {survivors}"
