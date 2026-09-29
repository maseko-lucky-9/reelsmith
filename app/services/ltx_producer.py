"""AI b-roll shot producer for generate:// mode (Stage 2).

Provider-pluggable. The ``stub`` provider renders a solid-colour 1080x1920
clip via MoviePy so the generate pipeline produces a real, decodable mp4 on
hosts without a GPU or the LTX model.

Behind ``YTVIDEO_LTX_PROVIDER``:
    stub | ltx  (default ``stub``)

The ``ltx`` provider shells out to the LTX-Video fork's ``inference.py`` CLI
running in the fork's OWN virtualenv. The fork's dependency tree conflicts
with reelsmith's, so it is NEVER imported in-process — the real path is a
``subprocess.run`` against a separate interpreter. As a result this module is
torch-free at import time (there is a test asserting exactly that), and CI
(which has neither torch nor the LTX venv) is unaffected.

The fork ships ``inference.py`` built on ``HfArgumentParser(InferenceConfig)``,
so each dataclass field maps to a ``--<field>`` flag. It writes a uniquely
named ``*.mp4`` into the ``--output_path`` FOLDER; we copy the newest one to
``out_path``.
"""
from __future__ import annotations

import glob
import logging
import shutil
import subprocess
from pathlib import Path

# Must run before any MoviePy import to restore PIL.Image.ANTIALIAS.
import app.compat  # noqa: F401

log = logging.getLogger(__name__)

# Generous wall-clock budget for a single LTX shot on MPS/CPU. The fork loads
# the model on every invocation (cold subprocess), so the first frames are slow.
_LTX_TIMEOUT_SECONDS = 900


class LtxError(RuntimeError):
    pass


def round_to_multiple_of_32(value: int) -> int:
    """Round ``value`` to the nearest multiple of 32, floored at 32.

    LTX requires frame dims divisible by 32 (the fork pads up internally, but
    we round here so the produced clip's dims are deterministic and match what
    the smoke test asserts). Pure + unit-testable.
    """
    if value <= 0:
        return 32
    rounded = int(round(value / 32.0)) * 32
    return max(32, rounded)


def nearest_valid_num_frames(target: int) -> int:
    """Round ``target`` to the nearest valid LTX frame count of the form 8n+1.

    LTX requires ``num_frames`` to satisfy ``num_frames % 8 == 1`` and be ≥ 9
    (n ≥ 1). We pick the nearest such value to ``target`` (ties round up). Pure
    + unit-testable.
    """
    if target <= 9:
        return 9
    # n such that 8n + 1 is closest to target.
    n = int(round((target - 1) / 8.0))
    n = max(1, n)
    return 8 * n + 1


def _stub_shot(prompt: str, seconds: float, out_path: str) -> str:
    """Render a solid-colour 1080x1920 clip of ``seconds`` duration as mp4."""
    from moviepy.editor import ColorClip

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    # Deterministic colour derived from the prompt so successive shots differ.
    seed = sum(ord(c) for c in prompt) if prompt else 0
    colour = (40 + seed % 160, 40 + (seed // 3) % 160, 40 + (seed // 7) % 160)
    clip = ColorClip(size=(1080, 1920), color=colour, duration=max(0.1, seconds))
    clip = clip.set_fps(24)
    try:
        clip.write_videofile(
            out_path,
            codec="libx264",
            audio=False,
            fps=24,
            preset="ultrafast",
            logger=None,
        )
    finally:
        clip.close()
    return out_path


def _ltx_shot(
    prompt: str,
    seconds: float,
    out_path: str,
    *,
    ltx_python: str,
    ltx_inference_script: str,
    ltx_pipeline_config: str,
    height: int,
    width: int,
    frame_rate: int,
    seed: int | None,
) -> str:
    """Run the LTX fork's inference.py CLI in its own venv and copy the result.

    Raises ``LtxError`` for NOT_CONFIGURED, a non-zero return code, a timeout,
    or a missing output clip.
    """
    # ── NOT_CONFIGURED guard ─────────────────────────────────────────────────
    missing = [
        name
        for name, value in (
            ("ltx_python", ltx_python),
            ("ltx_inference_script", ltx_inference_script),
            ("ltx_pipeline_config", ltx_pipeline_config),
        )
        if not value or not Path(value).exists()
    ]
    if missing:
        raise LtxError(
            "ltx not configured: set YTVIDEO_LTX_PYTHON / "
            "YTVIDEO_LTX_INFERENCE_SCRIPT / YTVIDEO_LTX_PIPELINE_CONFIG "
            f"(unset or not found: {', '.join(missing)})"
        )

    # ── Resolve LTX-valid dimensions and frame count ─────────────────────────
    h = round_to_multiple_of_32(height)
    w = round_to_multiple_of_32(width)
    nf = nearest_valid_num_frames(round(seconds * frame_rate))

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    # The fork writes a uniquely-named mp4 into the output FOLDER; give it a
    # private temp dir adjacent to the output so the newest-mp4 scan is clean.
    import tempfile

    tmp_dir = tempfile.mkdtemp(prefix="ltx_out_")
    try:
        cmd = [
            ltx_python,
            ltx_inference_script,
            "--prompt",
            prompt,
            "--pipeline_config",
            ltx_pipeline_config,
            "--output_path",
            tmp_dir,
            "--height",
            str(h),
            "--width",
            str(w),
            "--num_frames",
            str(nf),
            "--frame_rate",
            str(frame_rate),
            "--seed",
            str(seed if seed is not None else 0),
        ]
        log.info("ltx subprocess: %s", " ".join(cmd))
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=_LTX_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as e:
            raise LtxError(
                f"ltx generation timed out after {_LTX_TIMEOUT_SECONDS}s"
            ) from e

        if proc.returncode != 0:
            stderr_tail = (proc.stderr or "")[-1500:]
            raise LtxError(
                f"ltx inference failed (returncode={proc.returncode}): "
                f"{stderr_tail}"
            )

        # The fork names the file ``video_output_*.mp4``; grab the newest mp4.
        mp4s = sorted(
            glob.glob(str(Path(tmp_dir) / "**" / "*.mp4"), recursive=True),
            key=lambda p: Path(p).stat().st_mtime,
        )
        if not mp4s:
            stdout_tail = (proc.stdout or "")[-800:]
            raise LtxError(
                "ltx inference produced no mp4 in the output folder "
                f"(stdout tail: {stdout_tail})"
            )
        newest = mp4s[-1]
        shutil.copyfile(newest, out_path)
        return out_path
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def generate_shot(
    prompt: str,
    seconds: float,
    out_path: str,
    *,
    provider: str = "stub",
    # ── legacy / stub knobs (kept for call-site & smoke back-compat) ──────────
    model_path: str | None = None,
    use_mps: bool = True,
    seed: int | None = None,
    # ── ltx subprocess knobs ──────────────────────────────────────────────────
    ltx_python: str | None = None,
    ltx_inference_script: str | None = None,
    ltx_pipeline_config: str | None = None,
    ltx_height: int = 1216,
    ltx_width: int = 704,
    ltx_frame_rate: int = 24,
) -> str:
    """Generate a b-roll shot for ``prompt`` of ``seconds`` length at ``out_path``.

    ``stub`` — renders a solid-colour 1080x1920 libx264 mp4 via MoviePy.
    ``ltx``  — shells out to the LTX fork's ``inference.py`` CLI (running in the
               fork's own venv via ``ltx_python``), then copies the produced
               mp4 to ``out_path``. NEVER imports torch in-process.

    Raises ``LtxError`` on failure (including NOT_CONFIGURED).
    """
    if provider == "stub":
        try:
            return _stub_shot(prompt, seconds, out_path)
        except Exception as e:  # noqa: BLE001
            raise LtxError(f"stub shot render failed: {e}") from e

    if provider == "ltx":
        return _ltx_shot(
            prompt,
            seconds,
            out_path,
            ltx_python=ltx_python or "",
            ltx_inference_script=ltx_inference_script or "",
            ltx_pipeline_config=ltx_pipeline_config or "",
            height=ltx_height,
            width=ltx_width,
            frame_rate=ltx_frame_rate,
            seed=seed,
        )

    raise LtxError(f"unknown ltx provider: {provider!r}")
