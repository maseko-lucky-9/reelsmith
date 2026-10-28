#!/usr/bin/env python
"""Benchmark the local scene-search stack (ADR-013). Runs under ``.venv-mlx``.

    scene_bench.py embed  --models M1 M2 ... [--work-dir DIR]
    scene_bench.py judge  --embed-model M --judge-models gemma4:latest qwen2.5vl:7b [--work-dir DIR]

``embed``: for every model, index each labelled source with ``scripts/scene_index.py``
(one subprocess per source, so peak RSS is that of one real index job), embed the labelled
prompts, and report index seconds per source minute, projected minutes per source hour,
peak RSS, top-5 recall (pooled over all sources, and per source with its chance level) and
the cosine sanity check (two different chunks must be < 0.99).

``judge``: send 4 frames of every top-5 candidate to an Ollama vision model and compare
its ``match`` verdict with the hand labels (precision, recall, latency).

Labels: ``scripts/scene_bench_labels.json``. Sources are read from ``data/downloads``
and never modified. Results are written to ``<work-dir>/results.json``.
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DEFAULT_LABELS = REPO_ROOT / "scripts" / "scene_bench_labels.json"
DEFAULT_WORK = Path("/tmp/reelsmith-scene-bench")
DOWNLOADS = "data/downloads"
TOP_K = 5
MIN_OVERLAP_S = 2.0
COSINE_LIMIT = 0.99
OLLAMA_URL = "http://localhost:11434"
JUDGE_FIELDS = {
    "match": {"type": "boolean"},
    "confidence": {"type": "number"},
    "title": {"type": "string"},
    "reason": {"type": "string"},
    "emotion": {"type": "string"},
}
# Ollama generates structured output in schema order, so putting "reason" first makes the model
# describe what it sees before it commits to "match". "spec" is the order the plan lists.
JUDGE_ORDERS = {
    "spec": ["match", "confidence", "title", "reason", "emotion"],
    "reason_first": ["reason", "match", "confidence", "title", "emotion"],
}
JUDGE_PROMPT = (
    "These are 4 frames sampled in order from one short video clip. Decide whether the clip "
    "matches this search query: {query!r}\n"
    "Set match=true only if what the query describes (the subject, the action, the mood, or "
    "the words) is actually visible in the frames or written on screen; otherwise false. "
    "confidence is 0 to 1. title is a 5 word title for the clip, reason is one short sentence, "
    "emotion is the dominant emotion shown by any person ('none' if no person)."
)
JUDGE_PROMPT_STRICT = (
    "These are 4 frames sampled in order from one short video clip. Search query: {query!r}\n"
    "First say in `reason` what the frames actually show (people, objects, setting, on-screen text). "
    "Then set match=true ONLY if that description contains the specific subject, action, mood or "
    "words of the query. A similar-looking or merely related scene is NOT a match: if a key part of "
    "the query is absent, match=false. confidence is 0 to 1 (use low values when unsure). title is a "
    "5 word title for the clip. emotion is the dominant emotion shown by any person ('none' if no person)."
)
JUDGE_VARIANTS = {
    "spec": ("spec", JUDGE_PROMPT),
    "reason_first": ("reason_first", JUDGE_PROMPT),
    "strict": ("reason_first", JUDGE_PROMPT_STRICT),
}


def judge_schema(order: str) -> dict:
    keys = JUDGE_ORDERS[order]
    return {"type": "object", "properties": {k: JUDGE_FIELDS[k] for k in keys}, "required": keys}


# --- pure helpers (unit-tested) ---------------------------------------------


def is_hit(chunk: tuple[float, float], spans: list[list[float]]) -> bool:
    """A chunk hits a label when it overlaps a span by >= min(2 s, half that span)."""
    start, end = chunk
    for s0, s1 in spans:
        needed = min(MIN_OVERLAP_S, (s1 - s0) / 2)
        if min(end, s1) - max(start, s0) >= needed:
            return True
    return False


def chance_top_k(total: int, hits: int, k: int = TOP_K) -> float:
    """P(at least one of *hits* chunks lands in k random picks out of *total*)."""
    if hits <= 0:
        return 0.0
    if total <= k or hits >= total:
        return 1.0
    return 1.0 - math.comb(total - hits, k) / math.comb(total, k)


def percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile (no interpolation); 0.0 for an empty list."""
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, math.ceil(pct / 100 * len(ordered)) - 1))]


def judge_scores(verdicts: list[tuple[bool, bool]]) -> dict[str, float]:
    """(judge_said_match, truly_matches) pairs -> precision, recall, accuracy."""
    tp = sum(1 for said, truth in verdicts if said and truth)
    fp = sum(1 for said, truth in verdicts if said and not truth)
    fn = sum(1 for said, truth in verdicts if not said and truth)
    correct = sum(1 for said, truth in verdicts if said == truth)
    return {
        "precision": tp / (tp + fp) if tp + fp else 0.0,
        "recall": tp / (tp + fn) if tp + fn else 0.0,
        "accuracy": correct / len(verdicts) if verdicts else 0.0,
        "n": float(len(verdicts)),
    }


# --- embedding benchmark ----------------------------------------------------


def load_labels(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def source_path(rel: str) -> Path:
    return downloads_root() / rel


def downloads_root() -> Path:
    """data/downloads, found by walking up: a linked worktree has no data/, the main checkout does."""
    for base in (REPO_ROOT, *REPO_ROOT.parents):
        if (base / DOWNLOADS).is_dir():
            return base / DOWNLOADS
    raise SystemExit(f"scene_bench: {DOWNLOADS} not found above {REPO_ROOT}")


def run_index(video: Path, out_dir: Path, model: str) -> dict:
    """Run scene_index.py as a child and return its stderr ``stats`` payload."""
    started = time.perf_counter()
    done = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "scene_index.py"),
            "index",
            "--model",
            model,
            "--",
            str(video),
            str(out_dir),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode != 0:
        raise SystemExit(f"scene_bench: index failed for {video.name} ({model}):\n{done.stderr[-2000:]}")
    stats_lines = [ln for ln in done.stderr.splitlines() if ln.startswith("stats ")]
    stats = json.loads(stats_lines[-1][len("stats ") :])
    stats["wall_seconds"] = round(time.perf_counter() - started, 2)
    return stats


def _row_hits(row: dict, prompt: dict) -> bool:
    return row["video"] == prompt["video"] and is_hit((row["start"], row["end"]), prompt["spans"])


def score_prompt(prompt: dict, sims, pool_meta: list[dict]) -> dict:
    """Rank the pooled chunks by cosine *sims* and score the prompt against its labels."""
    import numpy as np

    order = [int(i) for i in np.argsort(-sims)]
    top = [
        {**pool_meta[i], "score": round(float(sims[i]), 4), "hit": _row_hits(pool_meta[i], prompt)}
        for i in order[:TOP_K]
    ]
    own = [i for i in order if pool_meta[i]["video"] == prompt["video"]]
    top_video = [
        {**pool_meta[i], "score": round(float(sims[i]), 4), "hit": _row_hits(pool_meta[i], prompt)} for i in own[:TOP_K]
    ]
    n_hit_chunks = sum(_row_hits(pool_meta[i], prompt) for i in own)
    return {
        "id": prompt["id"],
        "category": prompt["category"],
        "on_screen_text": prompt.get("on_screen_text"),
        "pooled_top5_hit": any(t["hit"] for t in top),
        "pooled_top1_hit": top[0]["hit"],
        "video_top5_hit": any(_row_hits(pool_meta[i], prompt) for i in own[:TOP_K]),
        "video_chance": chance_top_k(len(own), n_hit_chunks),
        "top": top,
        "top_video": top_video,
    }


def embed_benchmark(model: str, labels: dict, work: Path) -> dict:
    import numpy as np

    from scripts.scene_index import embed_queries

    model_dir = work / model.replace("/", "__")
    index_stats: dict[str, dict] = {}
    pool_vecs, pool_meta = [], []
    for key, rel in labels["videos"].items():
        out_dir = model_dir / key
        index_stats[key] = run_index(source_path(rel), out_dir, model)
        vecs = np.load(out_dir / "embeddings.npy")
        chunks = json.loads((out_dir / "chunks.json").read_text())
        for vec, ch in zip(vecs, chunks, strict=True):
            pool_vecs.append(vec)
            pool_meta.append({"video": key, "start": ch["start"], "end": ch["end"]})
    pool = np.stack(pool_vecs)

    prompts = labels["prompts"]
    qvecs = np.array(embed_queries([p["text"] for p in prompts], model), dtype=np.float32)
    results = [score_prompt(prompt, pool @ qvec, pool_meta) for prompt, qvec in zip(prompts, qvecs, strict=True)]

    summary = summarise(results)
    summary["cosine_sanity"] = cosine_sanity(pool, pool_meta)
    total_src = sum(s["source_seconds"] for s in index_stats.values())
    loop = sum(s["index_seconds"] for s in index_stats.values())
    summary["index_seconds_per_source_minute"] = round(loop / (total_src / 60), 2)
    # seconds per source-minute equals minutes per source-hour (the 60s cancel)
    summary["projected_minutes_per_source_hour"] = summary["index_seconds_per_source_minute"]
    summary["model_load_seconds"] = round(statistics.mean(s["load_seconds"] for s in index_stats.values()), 2)
    summary["peak_rss_mb"] = max(s["peak_rss_mb"] for s in index_stats.values())
    summary["mlx_peak_gb"] = max(s["mlx_peak_gb"] for s in index_stats.values())
    summary["source_minutes"] = round(total_src / 60, 2)
    summary["chunks"] = len(pool_meta)
    return {"model": model, "summary": summary, "index": index_stats, "results": results}


def summarise(results: list[dict]) -> dict:
    def rate(rows: list[dict], key: str) -> float:
        return round(sum(r[key] for r in rows) / len(rows), 3) if rows else 0.0

    def subset(pred) -> list[dict]:
        return [r for r in results if pred(r)]

    sees = subset(lambda r: r["category"] != "speech" or r["on_screen_text"])
    out = {
        "n_prompts": len(results),
        "recall5_pooled_all": rate(results, "pooled_top5_hit"),
        "recall5_pooled_visible": rate(sees, "pooled_top5_hit"),
        "recall1_pooled_all": rate(results, "pooled_top1_hit"),
        "recall5_in_video_all": rate(results, "video_top5_hit"),
        "chance5_in_video_all": rate(results, "video_chance"),
        "by_category": {},
    }
    for cat in sorted({r["category"] for r in results}):
        rows = subset(lambda r, c=cat: r["category"] == c)
        out["by_category"][cat] = {
            "n": len(rows),
            "recall5_pooled": rate(rows, "pooled_top5_hit"),
            "recall5_in_video": rate(rows, "video_top5_hit"),
        }
    audio_only = subset(lambda r: r["category"] == "speech" and not r["on_screen_text"])
    out["speech_audio_only"] = {"n": len(audio_only), "recall5_pooled": rate(audio_only, "pooled_top5_hit")}
    return out


def cosine_sanity(pool, meta: list[dict]) -> dict:
    """Highest and lowest cosine between the first chunks of different sources."""
    first = {}
    for i, m in enumerate(meta):
        first.setdefault(m["video"], i)
    keys = list(first)
    pairs = [float(pool[first[a]] @ pool[first[b]]) for ai, a in enumerate(keys) for b in keys[ai + 1 :]]
    return {
        "max_cosine_between_first_chunks_of_different_sources": round(max(pairs), 4),
        "min_cosine_between_first_chunks_of_different_sources": round(min(pairs), 4),
        "pass": max(pairs) < COSINE_LIMIT,
    }


# --- judge benchmark --------------------------------------------------------


def grab_frames(video: Path, start: float, end: float, count: int = 4, max_side: int = 512) -> list[str]:
    """*count* evenly spaced JPEG frames from [start, end) as base64 strings."""
    import cv2

    cap = cv2.VideoCapture(str(video))
    frames = []
    try:
        for k in range(count):
            t = start + (end - start) * (k + 0.5) / count
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok, frame = cap.read()
            if not ok:
                continue
            h, w = frame.shape[:2]
            scale = max_side / max(h, w)
            if scale < 1:
                frame = cv2.resize(frame, (int(w * scale), int(h * scale)))
            ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
            if ok:
                frames.append(base64.b64encode(buf.tobytes()).decode("ascii"))
    finally:
        cap.release()
    return frames


def ollama_judge(
    model: str, query: str, frames: list[str], url: str = OLLAMA_URL, variant: str = "spec"
) -> tuple[dict | None, float]:
    """One /api/generate call. Returns (parsed verdict or None, seconds)."""
    order, prompt = JUDGE_VARIANTS[variant]
    body = json.dumps(
        {
            "model": model,
            "prompt": prompt.format(query=query),
            "images": frames,
            "format": judge_schema(order),
            "stream": False,
            "think": False,
            "keep_alive": "60s",
            "options": {"temperature": 0},
        }
    ).encode()
    request = urllib.request.Request(f"{url}/api/generate", data=body, headers={"Content-Type": "application/json"})
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=300) as resp:  # noqa: S310 - fixed localhost URL
            payload = json.loads(resp.read())
        verdict = json.loads(payload["response"])
    except urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError:
        verdict = None
    return verdict, time.perf_counter() - started


def judge_benchmark(
    embed_result: dict,
    labels: dict,
    judge_model: str,
    url: str = OLLAMA_URL,
    scope: str = "video",
    variant: str = "spec",
) -> dict:
    """Judge every top-5 candidate. scope "video": candidates ranked inside the labelled source
    (the product searches one video); "pooled": ranked across all sources (harder)."""
    prompts = {p["id"]: p for p in labels["prompts"]}
    rows, verdicts, latencies, failures = [], [], [], 0
    for res in embed_result["results"]:
        prompt = prompts[res["id"]]
        for cand in res["top_video" if scope == "video" else "top"]:
            video = source_path(labels["videos"][cand["video"]])
            frames = grab_frames(video, cand["start"], cand["end"])
            verdict, seconds = ollama_judge(judge_model, prompt["text"], frames, url, variant)
            if verdict is None or not isinstance(verdict.get("match"), bool):
                failures += 1
                continue
            latencies.append(seconds)
            verdicts.append((verdict["match"], bool(cand["hit"])))
            rows.append(
                {
                    "id": res["id"],
                    "chunk": [cand["video"], cand["start"], cand["end"]],
                    "truth": cand["hit"],
                    **verdict,
                    "seconds": round(seconds, 2),
                }
            )
    scores = judge_scores(verdicts)
    return {
        "judge_model": judge_model,
        "scope": scope,
        "variant": variant,
        "embed_model": embed_result["model"],
        **{k: round(v, 3) for k, v in scores.items()},
        "latency_mean_s": round(statistics.mean(latencies), 2) if latencies else 0.0,
        "latency_p95_s": round(percentile(latencies, 95), 2),
        "failures": failures,
        "rows": rows,
    }


def unload_ollama(model: str, url: str = OLLAMA_URL) -> None:
    body = json.dumps({"model": model, "keep_alive": 0}).encode()
    request = urllib.request.Request(f"{url}/api/generate", data=body, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(request, timeout=30).read()  # noqa: S310
    except urllib.error.URLError, TimeoutError:
        pass


# --- CLI --------------------------------------------------------------------


def _load_results(work: Path) -> dict:
    path = work / "results.json"
    return json.loads(path.read_text()) if path.exists() else {"embed": {}, "judge": {}}


def _save_results(work: Path, data: dict) -> None:
    work.mkdir(parents=True, exist_ok=True)
    (work / "results.json").write_text(json.dumps(data, indent=1), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK)
    sub = parser.add_subparsers(dest="command", required=True)
    emb = sub.add_parser("embed")
    emb.add_argument("--models", nargs="+", required=True)
    jdg = sub.add_parser("judge")
    jdg.add_argument("--embed-model", required=True)
    jdg.add_argument("--judge-models", nargs="+", default=["gemma4:latest"])
    jdg.add_argument("--ollama-url", default=OLLAMA_URL)
    jdg.add_argument("--scope", choices=["video", "pooled"], default="video")
    jdg.add_argument("--variant", choices=list(JUDGE_VARIANTS), default="spec")
    args = parser.parse_args(argv)

    labels = load_labels(args.labels)
    data = _load_results(args.work_dir)
    if args.command == "embed":
        for model in args.models:
            data["embed"][model] = embed_benchmark(model, labels, args.work_dir)
            _save_results(args.work_dir, data)
            print(json.dumps({"model": model, **data["embed"][model]["summary"]}, indent=1))
    else:
        embed_result = data["embed"][args.embed_model]
        for judge_model in args.judge_models:
            result = judge_benchmark(embed_result, labels, judge_model, args.ollama_url, args.scope, args.variant)
            data["judge"][f"{args.embed_model}|{judge_model}|{args.scope}|{args.variant}"] = result
            _save_results(args.work_dir, data)
            unload_ollama(judge_model, args.ollama_url)
            print(json.dumps({k: v for k, v in result.items() if k != "rows"}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
