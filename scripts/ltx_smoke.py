#!/usr/bin/env python
"""Gate A — LTX-Video (subprocess) smoke test for generate:// mode.

Runs the *real* ``ltx`` provider once and verifies the produced shot is a
playable, correctly-sized, non-black clip. The real provider shells out to the
LTX fork's ``inference.py`` running in the fork's own venv. This is operator
tooling: it does NOT change runtime behavior and is never exercised by CI
(which has neither the LTX venv nor weights).

Usage
-----
    python -m scripts.ltx_smoke [--ltx-python PATH] [--inference-script PATH]
                                [--pipeline-config PATH] [--seconds N]
                                [--width W] [--height H] [--frame-rate FPS]
                                [--max-seconds BUDGET] [--prompt TEXT]
                                [--out PATH]

Exit codes (shared legend with Gate B / the preflight orchestrator):
    0  PASS            — clip generated, dims/duration correct, not all-black
    1  FAIL            — black frames, wrong dims, zero duration, or over budget
    2  NOT_CONFIGURED  — fork venv/script/config not set up; nothing was run

The MoviePy / settings imports are deferred into ``main`` so that importing
this module (e.g. from a unit test) stays cheap and torch-free.
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

# Default reel dimensions (portrait 9:16-ish; height > width) when settings
# don't specify. These are rounded to the nearest multiple of 32 before use,
# since LTX requires frame dims divisible by 32.
DEFAULT_WIDTH = 704
DEFAULT_HEIGHT = 1216
DEFAULT_SECONDS = 3.0
DEFAULT_FRAME_RATE = 24
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
        description="Gate A — LTX-Video subprocess smoke test for generate:// mode.",
    )
    p.add_argument(
        "--ltx-python",
        default=None,
        help="Override YTVIDEO_LTX_PYTHON (interpreter inside the fork's venv).",
    )
    p.add_argument(
        "--inference-script",
        default=None,
        help="Override YTVIDEO_LTX_INFERENCE_SCRIPT (path to the fork's inference.py).",
    )
    p.add_argument(
        "--pipeline-config",
        default=None,
        help="Override YTVIDEO_LTX_PIPELINE_CONFIG (path to the pipeline yaml).",
    )
    p.add_argument(
        "--seconds", type=float, default=None,
        help=f"Clip length in seconds (default {DEFAULT_SECONDS}).",
    )
    p.add_argument("--width", type=int, default=None, help="Requested frame width.")
    p.add_argument("--height", type=int, default=None, help="Requested frame height.")
    p.add_argument(
        "--frame-rate", type=int, default=None,
        help=f"Frame rate (default {DEFAULT_FRAME_RATE}).",
    )
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

    ltx_python = (
        args.ltx_python if args.ltx_python is not None else settings.ltx_python
    )
    inference_script = (
        args.inference_script if args.inference_script is not None
        else settings.ltx_inference_script
    )
    pipeline_config = (
        args.pipeline_config if args.pipeline_config is not None
        else settings.ltx_pipeline_config
    )
    seconds = args.seconds if args.seconds is not None else DEFAULT_SECONDS
    width = (
        args.width if args.width is not None
        else getattr(settings, "ltx_width", DEFAULT_WIDTH)
    )
    height = (
        args.height if args.height is not None
        else getattr(settings, "ltx_height", DEFAULT_HEIGHT)
    )
    frame_rate = (
        args.frame_rate if args.frame_rate is not None
        else getattr(settings, "ltx_frame_rate", DEFAULT_FRAME_RATE)
    )
    return ltx_python, inference_script, pipeline_config, seconds, width, height, frame_rate


def main(argv: Sequence[str] | None = None) -> int:
    _bootstrap_path()
    args = _build_parser().parse_args(argv)

    (
        ltx_python,
        inference_script,
        pipeline_config,
        seconds,
        width,
        height,
        frame_rate,
    ) = _resolve_config(args)

    # ── Pre-check: is the fork venv + script + config configured? ─────────────
    required = (
        ("YTVIDEO_LTX_PYTHON", ltx_python),
        ("YTVIDEO_LTX_INFERENCE_SCRIPT", inference_script),
        ("YTVIDEO_LTX_PIPELINE_CONFIG", pipeline_config),
    )
    reasons = []
    for env_name, value in required:
        if not value:
            reasons.append(f"{env_name} is empty")
        elif not Path(value).exists():
            reasons.append(f"{env_name} path does not exist: {value}")
    if reasons:
        print("Gate A: NOT_CONFIGURED — LTX fork subprocess not configured.")
        for r in reasons:
            print(f"  - {r}")
        print(
            "  Fix: set YTVIDEO_LTX_PYTHON to the fork venv interpreter, "
            "YTVIDEO_LTX_INFERENCE_SCRIPT to its inference.py, and "
            "YTVIDEO_LTX_PIPELINE_CONFIG to the pipeline yaml."
        )
        return EXIT_NOT_CONFIGURED

    # ── Run the real model. Defer compat+moviepy+producer imports to here. ────
    import app.compat  # noqa: F401  # PIL.Image.ANTIALIAS shim before MoviePy
    from app.services import ltx_producer

    # The clip is sized to the ÷32-rounded request — assert against THESE, not
    # hardcoded reel dims, since LTX only emits multiples of 32.
    expected_width = ltx_producer.round_to_multiple_of_32(width)
    expected_height = ltx_producer.round_to_multiple_of_32(height)

    if args.out:
        out_path = args.out
    else:
        import tempfile

        out_path = str(Path(tempfile.gettempdir()) / "ltx_smoke_shot.mp4")
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)

    print("Gate A: running LTX smoke...")
    print(f"  ltx_python : {ltx_python}")
    print(f"  script     : {inference_script}")
    print(f"  config     : {pipeline_config}")
    print(f"  dims       : {expected_width}x{expected_height} "
          f"(requested {width}x{height})")
    print(f"  seconds    : {seconds}  frame_rate: {frame_rate}")

    t0 = time.perf_counter()
    try:
        ltx_producer.generate_shot(
            args.prompt,
            seconds,
            out_path,
            provider="ltx",
            ltx_python=ltx_python,
            ltx_inference_script=inference_script,
            ltx_pipeline_config=pipeline_config,
            ltx_height=height,
            ltx_width=width,
            ltx_frame_rate=frame_rate,
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

        if size != [expected_width, expected_height]:
            failures.append(
                f"dims {size} != expected [{expected_width}, {expected_height}]"
            )
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
    print(f"  dims         : {size[0]}x{size[1]} "
          f"(expected {expected_width}x{expected_height})")
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
