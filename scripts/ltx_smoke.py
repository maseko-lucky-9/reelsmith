#!/usr/bin/env python
"""Gate A — LTX-Video 2B (MPS) smoke test for generate:// mode.

Runs the *real* ``ltx`` provider once and verifies the produced shot is a
playable, correctly-sized, non-black clip. This is operator tooling: it does
NOT change runtime behavior and is never exercised by CI (which has no torch
and no LTX weights).

Usage
-----
    python -m scripts.ltx_smoke [--model-path PATH] [--seconds N]
                                [--width W] [--height H]
                                [--max-seconds BUDGET] [--prompt TEXT]
                                [--out PATH]

Exit codes (shared legend with Gate B / the preflight orchestrator):
    0  PASS            — clip generated, dims/duration correct, not all-black
    1  FAIL            — black frames, wrong dims, zero duration, or over budget
    2  NOT_CONFIGURED  — weights/torch not set up; nothing was run

The torch / MoviePy / settings imports are deferred into ``main`` so that
importing this module (e.g. from a unit test) never pulls in torch and never
trips the LTX provider's import guard.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Sequence

# Exit-code legend (shared across the Stage 2 gates).
EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_NOT_CONFIGURED = 2

# Default reel dimensions (9:16) when settings don't specify.
DEFAULT_WIDTH = 1080
DEFAULT_HEIGHT = 1920
DEFAULT_SECONDS = 3.0
DEFAULT_PROMPT = "a calm sunrise over a quiet city skyline, slow drift"

# Black-frame threshold: mean luma (0-255). Below this a frame is "near black".
BLACK_LUMA_THRESHOLD = 8.0


def _bootstrap_path() -> None:
    """Ensure the repo root is importable when run as ``python scripts/x.py``."""
    repo_root = Path(__file__).resolve().parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))


def is_mostly_black(frames: Sequence, threshold: float = BLACK_LUMA_THRESHOLD) -> bool:
    """Return True if *every* sampled frame is near-black.

    A pure helper so the black-frame verdict is unit-testable without a model.
    ``frames`` is a sequence of HxWxC (or HxW) numpy arrays as returned by
    ``moviepy`` ``clip.get_frame(t)``. Luma is approximated as the per-frame
    mean over all channels. The verdict is "mostly black" only when ALL
    sampled frames fall below ``threshold`` — a single bright frame clears it.
    """
    import numpy as np

    if not len(frames):  # type: ignore[arg-type]
        # No frames sampled → treat as black (suspicious, fail-closed).
        return True
    for frame in frames:
        arr = np.asarray(frame, dtype="float64")
        if arr.size == 0:
            continue
        if float(arr.mean()) >= threshold:
            return False
    return True


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ltx_smoke",
        description="Gate A — LTX-Video MPS smoke test for generate:// mode.",
    )
    p.add_argument(
        "--model-path",
        default=None,
        help="Override YTVIDEO_LTX_MODEL_PATH (path to the LTX 2B weights).",
    )
    p.add_argument(
        "--seconds", type=float, default=None,
        help=f"Clip length in seconds (default {DEFAULT_SECONDS}).",
    )
    p.add_argument("--width", type=int, default=None, help="Expected frame width.")
    p.add_argument("--height", type=int, default=None, help="Expected frame height.")
    p.add_argument(
        "--max-seconds", type=float, default=None,
        help="Wall-clock budget; exceeding it FAILS the gate (exit 1).",
    )
    p.add_argument("--prompt", default=DEFAULT_PROMPT, help="Prompt to render.")
    p.add_argument(
        "--out", default=None,
        help="Output mp4 path (default: a temp file under the system tmp dir).",
    )
    return p


def _resolve_config(args: argparse.Namespace):
    """Resolve effective config from CLI overrides then settings."""
    from app.settings import settings

    model_path = args.model_path if args.model_path is not None else settings.ltx_model_path
    seconds = args.seconds if args.seconds is not None else DEFAULT_SECONDS
    width = args.width if args.width is not None else DEFAULT_WIDTH
    height = args.height if args.height is not None else DEFAULT_HEIGHT
    use_mps = bool(getattr(settings, "ltx_use_mps", True))
    return model_path, seconds, width, height, use_mps


def _torch_importable() -> bool:
    import importlib.util

    return importlib.util.find_spec("torch") is not None


def main(argv: Sequence[str] | None = None) -> int:
    _bootstrap_path()
    args = _build_parser().parse_args(argv)

    model_path, seconds, width, height, use_mps = _resolve_config(args)

    # ── Pre-check: are we configured to run the real model at all? ────────────
    weights_present = bool(model_path) and Path(model_path).exists()
    if not model_path or not _torch_importable() or not weights_present:
        reasons = []
        if not model_path:
            reasons.append("YTVIDEO_LTX_MODEL_PATH is empty")
        elif not weights_present:
            reasons.append(f"weights path does not exist: {model_path}")
        if not _torch_importable():
            reasons.append("torch is not importable")
        print("Gate A: NOT_CONFIGURED — LTX weights not configured.")
        for r in reasons:
            print(f"  - {r}")
        print(
            "  Fix: set YTVIDEO_LTX_MODEL_PATH to the LTX 2B weights dir and "
            "run 'pip install -r requirements-generate.txt'."
        )
        return EXIT_NOT_CONFIGURED

    # ── Run the real model. Defer compat+moviepy+producer imports to here. ────
    import app.compat  # noqa: F401  # PIL.Image.ANTIALIAS shim before MoviePy
    from app.services import ltx_producer

    if args.out:
        out_path = args.out
    else:
        import tempfile

        out_path = str(Path(tempfile.gettempdir()) / "ltx_smoke_shot.mp4")
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)

    device = "mps" if use_mps else "cpu"
    print("Gate A: running LTX smoke...")
    print(f"  model_path : {model_path}")
    print(f"  device     : {device}")
    print(f"  dims       : {width}x{height}")
    print(f"  seconds    : {seconds}")

    t0 = time.perf_counter()
    try:
        ltx_producer.generate_shot(
            args.prompt,
            seconds,
            out_path,
            provider="ltx",
            model_path=model_path,
            use_mps=use_mps,
        )
    except Exception as e:  # noqa: BLE001
        wall = time.perf_counter() - t0
        print(f"Gate A: FAIL — generation raised after {wall:.1f}s: {e}")
        return EXIT_FAIL
    wall = time.perf_counter() - t0

    # ── Verify the output via MoviePy (no system ffprobe in CI). ──────────────
    from moviepy.editor import VideoFileClip

    # Initialise report names up front so a mid-inspection raise (malformed /
    # zero-byte mp4) still lands a clean EXIT_FAIL report, never a NameError.
    failures: list[str] = []
    size: list[int] = [0, 0]
    duration: float = 0.0
    all_black: bool = True

    clip = None
    try:
        clip = VideoFileClip(out_path)
        size = list(clip.size)  # MoviePy returns [w, h]
        duration = float(clip.duration or 0.0)

        if size != [width, height]:
            failures.append(f"dims {size} != expected [{width}, {height}]")
        if duration <= 0:
            failures.append(f"duration {duration} is not > 0")

        # Sample ~5 evenly-spaced frames for the black-frame check.
        frames = []
        if duration > 0:
            n = 5
            for i in range(n):
                t = duration * (i + 0.5) / n
                frames.append(clip.get_frame(min(t, max(0.0, duration - 1e-3))))
        all_black = is_mostly_black(frames)
        if all_black:
            failures.append("all sampled frames are near-black")
    except Exception as e:  # noqa: BLE001
        failures.append(f"could not inspect output: {e}")
    finally:
        if clip is not None:
            clip.close()

    if args.max_seconds is not None and wall > args.max_seconds:
        failures.append(
            f"wall-clock {wall:.1f}s exceeds budget {args.max_seconds:.1f}s"
        )

    # ── Structured report ─────────────────────────────────────────────────────
    verdict = "PASS" if not failures else "FAIL"
    print("── Gate A report ──────────────────────────────")
    print(f"  device       : {device}")
    print(f"  dims         : {size[0]}x{size[1]} (expected {width}x{height})")
    print(f"  duration     : {duration:.2f}s")
    print(f"  wall-clock   : {wall:.1f}s")
    print(f"  black-frames : {'ALL BLACK' if all_black else 'ok'}")
    print(f"  output       : {out_path}")
    print(f"  verdict      : {verdict}")
    if failures:
        for f in failures:
            print(f"    ! {f}")
    print("───────────────────────────────────────────────")

    return EXIT_PASS if not failures else EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
