"""AI b-roll shot producer for generate:// mode (Stage 1).

Provider-pluggable. The ``stub`` provider renders a solid-colour 1080x1920
clip via MoviePy so the generate pipeline produces a real, decodable mp4 on
hosts without a GPU or the LTX model.

Behind ``YTVIDEO_LTX_PROVIDER``:
    stub | ltx  (default ``stub``)

The ``ltx`` provider lazy-imports torch and the LTX fork *only inside its own
branch* — importing this module never pulls in torch, so CI and the default
install are unaffected. The ``ltx`` path requires Apple MPS when
``use_mps`` is set and raises rather than silently falling back to CPU.
"""
from __future__ import annotations

import logging

# Must run before any MoviePy import to restore PIL.Image.ANTIALIAS.
import app.compat  # noqa: F401

log = logging.getLogger(__name__)


class LtxError(RuntimeError):
    pass


def _stub_shot(prompt: str, seconds: float, out_path: str) -> str:
    """Render a solid-colour 1080x1920 clip of ``seconds`` duration as mp4."""
    from pathlib import Path

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


def generate_shot(
    prompt: str,
    seconds: float,
    out_path: str,
    *,
    provider: str = "stub",
    model_path: str | None = None,
    use_mps: bool = True,
    seed: int | None = None,
) -> str:
    """Generate a b-roll shot for ``prompt`` of ``seconds`` length at ``out_path``.

    ``stub`` — renders a solid-colour 1080x1920 libx264 mp4 via MoviePy.
    ``ltx``  — lazy-imports torch + the LTX fork, resolves the device (MPS
               required when ``use_mps``), and runs the real model.

    Raises ``LtxError`` on failure.
    """
    if provider == "stub":
        try:
            return _stub_shot(prompt, seconds, out_path)
        except Exception as e:  # noqa: BLE001
            raise LtxError(f"stub shot render failed: {e}") from e

    if provider == "ltx":
        # Lazy-import heavy deps ONLY here so module import stays cheap and
        # CI never needs torch/diffusers installed.
        try:
            import torch  # noqa: PLC0415
        except ImportError as e:  # pragma: no cover - real-provider path
            raise LtxError(
                "ltx provider requires torch — install requirements-generate.txt"
            ) from e

        if use_mps:
            if not torch.backends.mps.is_available():
                # No silent CPU fallback — the real path is MPS-only by design.
                raise RuntimeError(
                    "ltx provider requested MPS but torch.backends.mps is unavailable"
                )
            device = "mps"
        else:
            device = "cpu"

        if not model_path:
            raise LtxError("ltx provider requires model_path (YTVIDEO_LTX_MODEL_PATH)")

        try:  # pragma: no cover - real-provider path, not exercised in CI
            from ltx_video.inference import generate as ltx_generate  # type: ignore

            ltx_generate(
                prompt=prompt,
                num_seconds=seconds,
                out_path=out_path,
                model_path=model_path,
                device=device,
                seed=seed,
            )
        except Exception as e:  # noqa: BLE001
            raise LtxError(f"ltx generation failed: {e}") from e
        return out_path

    raise LtxError(f"unknown ltx provider: {provider!r}")
