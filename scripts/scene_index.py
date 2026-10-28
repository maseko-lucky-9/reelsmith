#!/usr/bin/env python
"""Scene-embedding sidecar CLI. Runs under ``.venv-mlx`` (mlx + mlx-vlm), never the API venv.

    scene_index.py index <video> <out_dir> [--chunk 8] [--overlap 0] [--model HF_ID]
    scene_index.py query <text> [--model HF_ID]

``index`` cuts the video into fixed spans (480p / 5 fps, one ffmpeg pass each),
embeds every span with Qwen3-VL-Embedding on MLX and writes, atomically:

    <out_dir>/embeddings.npy   float32, n x 768, rows L2-normalised
    <out_dir>/chunks.json      [{"start": s, "end": e, "skipped_still": bool}, ...]

Progress goes to stdout as one JSON object per line (``{"done": i, "total": n}``)
so a parent process can stream it; logs and a final ``stats`` line go to stderr.
``query`` prints one JSON line: the 768-float normalised text vector.

The model is ``--model``, else ``$YTVIDEO_SCENE_MODEL``, else ``DEFAULT_MODEL``.
Exit status: 0 ok, 1 failure (message on stderr), 2 bad arguments.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import resource
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterator
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DEFAULT_MODEL = "mlx-community/Qwen3-VL-Embedding-2B-8bit"
MODEL_ENV = "YTVIDEO_SCENE_MODEL"
EMBED_DIMS = 768
MIN_CHUNK_SECONDS = 1.0  # a shorter tail cannot hold 2 sampled frames


class SceneIndexError(RuntimeError):
    """A failure with a message fit to show the operator."""


def resolve_model(cli_value: str | None) -> str:
    """``--model`` wins, then ``$YTVIDEO_SCENE_MODEL``, then the default."""
    return cli_value or os.environ.get(MODEL_ENV) or DEFAULT_MODEL


def plan_spans(duration: float, chunk: int, overlap: int) -> list[tuple[float, float]]:
    """Spans to embed: upstream's spans minus a sub-second tail (kept if it is the only one)."""
    from app.vendor.sentrysearch.chunker import expected_chunk_spans

    spans = expected_chunk_spans(duration, chunk, overlap)
    kept = [s for s in spans if s[1] - s[0] >= MIN_CHUNK_SECONDS]
    return kept or spans


def peak_rss_mb() -> float:
    """Peak resident set size of this process in MB (macOS reports bytes)."""
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e6


@contextlib.contextmanager
def _stdout_to_stderr() -> Iterator[None]:
    """Keep third-party prints off stdout, which is reserved for JSON lines."""
    with contextlib.redirect_stdout(sys.stderr):
        yield


def load_embedder(model: str, fps: float = 1.0):
    """Build the vendored MLX embedder (model loads lazily on first use)."""
    from app.vendor.sentrysearch.mlx_embedder import MLXEmbedder

    return MLXEmbedder(model_name=model, dimensions=EMBED_DIMS, fps=fps)


def index_video(
    video: Path,
    out_dir: Path,
    *,
    chunk: int,
    overlap: int,
    model: str,
    fps: float = 1.0,
    skip_still: bool = False,
    emit: Callable[[dict], None],
) -> dict:
    """Embed *video* chunk by chunk and write the index. Returns the stats dict."""
    import numpy as np

    from app.vendor.sentrysearch import chunker

    if not video.is_file():
        raise SceneIndexError(f"video not found: {video}")
    try:
        duration = chunker.get_video_duration(str(video))
    except (RuntimeError, OSError) as exc:
        raise SceneIndexError(f"cannot read {video.name}: {exc}") from exc
    if duration <= 0:
        raise SceneIndexError(f"{video.name} has zero duration")
    try:
        spans = plan_spans(duration, chunk, overlap)
    except ValueError as exc:
        raise SceneIndexError(str(exc)) from exc

    embedder = load_embedder(model, fps)
    load_started = time.perf_counter()
    embedder._load_model()  # noqa: SLF001 - eager load so load time is reported apart from embedding
    load_seconds = time.perf_counter() - load_started
    started = time.perf_counter()
    rows = np.zeros((len(spans), EMBED_DIMS), dtype=np.float32)
    meta: list[dict] = []
    emit({"done": 0, "total": len(spans)})
    with tempfile.TemporaryDirectory(prefix="scene_index_") as tmp:
        for i, (start, end) in enumerate(spans):
            try:
                path = chunker.render_chunk(str(video), start, end, os.path.join(tmp, f"c{i:05d}.mp4"))
            except subprocess.CalledProcessError as exc:
                tail = (exc.stderr or b"").decode("utf-8", "replace").strip().splitlines()[-1:]
                raise SceneIndexError(f"ffmpeg failed on {start:.1f}-{end:.1f}s: {''.join(tail)}") from exc
            still = skip_still and chunker.is_still_frame_chunk(path)
            if not still:
                with _stdout_to_stderr():
                    rows[i] = embedder.embed_video_chunk(path)
            os.unlink(path)
            meta.append({"start": round(start, 3), "end": round(end, 3), "skipped_still": bool(still)})
            emit({"done": i + 1, "total": len(spans)})

    write_index(out_dir, rows, meta)
    elapsed = time.perf_counter() - started
    return {
        "model": model,
        "chunks": len(spans),
        "source_seconds": round(duration, 2),
        "load_seconds": round(load_seconds, 2),
        "index_seconds": round(elapsed, 2),
        "seconds_per_source_minute": round(elapsed / (duration / 60), 2),
        "peak_rss_mb": round(peak_rss_mb(), 1),
        "mlx_peak_gb": round(_mlx_peak_gb(), 2),
    }


def _mlx_peak_gb() -> float:
    import mlx.core as mx

    return mx.get_peak_memory() / 1e9


def write_index(out_dir: Path, rows, meta: list[dict]) -> None:
    """Write embeddings.npy and chunks.json via temp files + rename (never half-written)."""
    import numpy as np

    out_dir.mkdir(parents=True, exist_ok=True)
    npy_tmp = out_dir / "embeddings.npy.tmp"
    json_tmp = out_dir / "chunks.json.tmp"
    try:
        with npy_tmp.open("wb") as handle:
            np.save(handle, rows.astype(np.float32))
        json_tmp.write_text(json.dumps(meta), encoding="utf-8")
        os.replace(npy_tmp, out_dir / "embeddings.npy")
        os.replace(json_tmp, out_dir / "chunks.json")
    finally:
        npy_tmp.unlink(missing_ok=True)
        json_tmp.unlink(missing_ok=True)


def embed_query(text: str, model: str) -> list[float]:
    """One normalised 768-float vector for *text*."""
    if not text.strip():
        raise SceneIndexError("query text is empty")
    embedder = load_embedder(model)
    with _stdout_to_stderr():
        return embedder.embed_query(text)


def embed_queries(texts: list[str], model: str) -> list[list[float]]:
    """Vectors for several texts with one model load (used by scripts/scene_bench.py)."""
    embedder = load_embedder(model)
    with _stdout_to_stderr():
        return [embedder.embed_query(text) for text in texts]


def _emit_json_line(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scene_index.py", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    idx = sub.add_parser("index", help="embed a video into <out_dir>")
    idx.add_argument("video", type=Path)
    idx.add_argument("out_dir", type=Path)
    idx.add_argument("--chunk", type=int, default=8, help="chunk length in seconds (default 8)")
    idx.add_argument("--overlap", type=int, default=0, help="overlap in seconds (default 0)")
    idx.add_argument("--model", default=None, help=f"HF id or local dir (default ${MODEL_ENV} or built-in)")
    idx.add_argument("--fps", type=float, default=1.0, help="frames/s sampled inside a chunk (default 1.0)")
    idx.add_argument(
        "--skip-still",
        action="store_true",
        help="do not embed near-static chunks (row stays zero, skipped_still=true)",
    )

    qry = sub.add_parser("query", help="print the vector for a text query")
    qry.add_argument("text")
    qry.add_argument("--model", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    model = resolve_model(args.model)
    try:
        if args.command == "index":
            stats = index_video(
                args.video,
                args.out_dir,
                chunk=args.chunk,
                overlap=args.overlap,
                model=model,
                fps=args.fps,
                skip_still=args.skip_still,
                emit=_emit_json_line,
            )
            print("stats " + json.dumps(stats), file=sys.stderr)
        else:
            _emit_json_line_vector(embed_query(args.text, model))
    except SceneIndexError as exc:
        print(f"scene_index: error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # boundary: any model/ffmpeg failure becomes a clear exit 1
        print(f"scene_index: error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


def _emit_json_line_vector(vector: list[float]) -> None:
    sys.stdout.write(json.dumps(vector) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    sys.exit(main())
