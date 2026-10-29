"""Scene search end to end with the real model (spec 014): opt-in, ``-m integration``.

Indexes a generated 16 s clip (8 s of a TV test pattern, then 8 s of a fractal zoom)
with the real sidecar (``.venv-mlx``, Qwen3-VL-Embedding on MLX) and checks that a
visual prompt finds the right chunk. The judge test also needs Ollama with the
vision model. No database fixture is used.
"""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest

from app.services import scene_search, segment_rerank
from tests.integration.test_scene_index_cli import SCENE_PYTHON, _bundled_ffmpeg

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(SCENE_PYTHON is None, reason=".venv-mlx not found (run scripts/setup_scene_venv.sh)"),
    pytest.mark.skipif(sys.platform != "darwin", reason="MLX needs Apple Silicon"),
]


def _ollama_has(model: str) -> bool:
    try:
        with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=3) as response:  # noqa: S310
            names = {m["name"] for m in json.load(response)["models"]}
    except OSError:
        return False
    return model in names


@pytest.fixture
def two_scene_clip(tmp_path: Path) -> Path:
    out = tmp_path / "job" / "two_scenes.mp4"
    out.parent.mkdir()
    subprocess.run(
        [
            _bundled_ffmpeg(), "-y",
            "-f", "lavfi", "-i", "testsrc2=s=640x360:r=10:d=8",
            "-f", "lavfi", "-i", "mandelbrot=s=640x360:r=10",
            "-filter_complex", "[1:v]trim=duration=8,setpts=PTS-STARTPTS[b];[0:v][b]concat=n=2:v=1:a=0[v]",
            "-map", "[v]", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out),
        ],
        check=True, capture_output=True, timeout=120,
    )  # fmt: skip
    return out


@pytest.fixture
def real_sidecar(monkeypatch):
    monkeypatch.setattr(scene_search.settings, "scene_python", str(SCENE_PYTHON))
    monkeypatch.setattr(scene_search.settings, "scene_model", "mlx-community/Qwen3-VL-Embedding-2B-8bit")


async def _search(clip: Path, prompt: str):
    index_dir = scene_search.scenes_dir(clip.parent)
    index, _ = await scene_search.ensure_index(clip, index_dir)
    prompt_vec, _ = await scene_search.prompt_and_sentence_vectors(index_dir, prompt, [])
    return scene_search.search(index, prompt_vec, min_s=1, max_s=8, duration=16.0)


async def test_a_visual_prompt_finds_its_chunk_and_the_index_is_reused(two_scene_clip, real_sidecar):
    fractal = await _search(two_scene_clip, "a colorful swirling fractal pattern")
    bars = await _search(two_scene_clip, "a television test card with colour bars")

    assert fractal[0].focus == (8.0, pytest.approx(16.0, abs=0.3))
    assert bars[0].focus == (0.0, 8.0)
    _, reused = await scene_search.ensure_index(two_scene_clip, scene_search.scenes_dir(two_scene_clip.parent))
    assert reused is True


async def test_the_vision_judge_confirms_the_fractal_and_rejects_the_test_card(
    two_scene_clip, real_sidecar, monkeypatch
):
    # Probed here, not in a skipif: collecting the default run makes no network call.
    if not _ollama_has("qwen2.5vl:7b"):
        pytest.skip("Ollama with qwen2.5vl:7b not available")
    monkeypatch.setattr(segment_rerank.settings, "ollama_enabled", True)
    monkeypatch.setattr(segment_rerank.settings, "ollama_vision_model", "qwen2.5vl:7b")
    (fractal, *_) = await _search(two_scene_clip, "a colorful swirling fractal pattern")
    (test_card, *_) = await _search(two_scene_clip, "a television test card with colour bars")
    assert (fractal.focus[0], test_card.focus[0]) == (8.0, 0.0)

    # The test-card chunk as a fractal candidate: the judge must turn it down.
    judged = await segment_rerank.judge_scenes(
        [fractal, test_card], prompt="a colorful swirling fractal pattern", video_path=str(two_scene_clip)
    )

    assert [c.focus[0] for c in judged] == [8.0]
    assert judged[0].title and judged[0].score_breakdown["judge"] > 0.5
