#!/usr/bin/env python
"""Whisper decode-settings benchmark: beam size x VAD x cpu_threads.

Picks the ``whisper_beam_size`` / ``whisper_vad_filter`` / ``whisper_cpu_threads``
defaults. For every config it transcribes one speech file with the same model
(``base``, int8, CPU), word timestamps on, and records:

* median wall clock over ``--repeats`` runs (after one untimed warm-up run) and
  the real-time factor (wall / audio duration);
* word agreement with the reference config (beam 5, VAD off, cpu_threads 0):
  ``difflib.SequenceMatcher`` ratio over normalised word lists. Gate: >= 0.97;
* word-start drift against the reference over matched words (median / max).
  Report only, never a gate: Whisper timestamps legitimately move several
  hundred ms between decode settings. Caption/audio sync is proven on the
  rendered output by ``tests/sync_checker.py``.

Usage
-----
    .venv-314/bin/python -m scripts.bench_whisper --audio speech.wav \
        [--threads 0 4 8] [--repeats 3] [--json out.json]

Use 60-90 s of real speech with pauses: shorter clips don't exercise the
30 s windowing, and silence-free audio hides what VAD does.
"""

from __future__ import annotations

import argparse
import difflib
import itertools
import json
import re
import statistics
import subprocess
import time
from dataclasses import asdict, dataclass

REFERENCE = (5, False, 0)
AGREEMENT_GATE = 0.97


@dataclass(frozen=True, slots=True)
class Result:
    beam_size: int
    vad_filter: bool
    cpu_threads: int
    wall_s: float
    rtf: float
    words: int
    agreement: float = 1.0
    drift_median_ms: float = 0.0
    drift_max_ms: float = 0.0


def _norm(word: str) -> str:
    return re.sub(r"[^a-z0-9']", "", word.lower())


def _transcribe(model, audio: str, beam: int, vad: bool) -> list[tuple[str, float]]:
    segments, _info = model.transcribe(
        audio, language="en", word_timestamps=True, beam_size=beam, vad_filter=vad
    )
    return [(_norm(w.word), w.start) for s in segments for w in (s.words or [])]


def _compare(ref: list[tuple[str, float]], got: list[tuple[str, float]]):
    matcher = difflib.SequenceMatcher(
        a=[w for w, _ in ref], b=[w for w, _ in got], autojunk=False
    )
    drifts = [
        abs(ref[block.a + k][1] - got[block.b + k][1]) * 1000
        for block in matcher.get_matching_blocks()
        for k in range(block.size)
    ]
    return (
        matcher.ratio(),
        statistics.median(drifts) if drifts else 0.0,
        max(drifts, default=0.0),
    )


def _perf_cores() -> int | None:
    try:
        out = subprocess.run(
            ["sysctl", "-n", "hw.perflevel0.physicalcpu"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return int(out.stdout.strip())


def main() -> None:
    from faster_whisper import WhisperModel
    from faster_whisper.audio import decode_audio

    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--audio", required=True)
    parser.add_argument("--model", default="base")
    parser.add_argument("--threads", type=int, nargs="*")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--json")
    args = parser.parse_args()

    threads = args.threads or sorted({0, 4, 8, *filter(None, [_perf_cores()])})
    duration = len(decode_audio(args.audio)) / 16000
    print(
        f"audio {args.audio}  {duration:.2f}s  model {args.model} int8  threads {threads}"
    )

    transcripts: dict[tuple[int, bool, int], list[tuple[str, float]]] = {}
    results: list[Result] = []
    for n_threads in threads:
        model = WhisperModel(args.model, compute_type="int8", cpu_threads=n_threads)
        for beam, vad in itertools.product((5, 1), (False, True)):
            _transcribe(model, args.audio, beam, vad)  # warm-up, untimed
            walls = []
            for _ in range(args.repeats):
                t0 = time.perf_counter()
                words = _transcribe(model, args.audio, beam, vad)
                walls.append(time.perf_counter() - t0)
            transcripts[(beam, vad, n_threads)] = words
            wall = statistics.median(walls)
            results.append(
                Result(beam, vad, n_threads, wall, wall / duration, len(words))
            )
            print(f"  beam={beam} vad={vad!s:5} threads={n_threads:2}  {wall:6.2f}s")
        del model

    ref = transcripts[REFERENCE]
    final = []
    for r in results:
        agreement, med, mx = _compare(
            ref, transcripts[(r.beam_size, r.vad_filter, r.cpu_threads)]
        )
        final.append(
            Result(
                **{
                    **asdict(r),
                    "agreement": agreement,
                    "drift_median_ms": med,
                    "drift_max_ms": mx,
                }
            )
        )

    print(
        "\n| beam | vad | threads | wall s | RTF | words | agreement | drift med/max ms |"
    )
    print("|---|---|---|---|---|---|---|---|")
    for r in sorted(final, key=lambda r: r.wall_s):
        gate = "" if r.agreement >= AGREEMENT_GATE else " FAIL"
        print(
            f"| {r.beam_size} | {'on' if r.vad_filter else 'off'} | {r.cpu_threads} "
            f"| {r.wall_s:.2f} | {r.rtf:.3f} | {r.words} | {r.agreement:.4f}{gate} "
            f"| {r.drift_median_ms:.0f} / {r.drift_max_ms:.0f} |"
        )
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "audio": args.audio,
                    "duration_s": duration,
                    "results": [asdict(r) for r in final],
                    "transcripts": {str(k): v for k, v in transcripts.items()},
                },
                fh,
                indent=1,
            )


if __name__ == "__main__":
    main()
