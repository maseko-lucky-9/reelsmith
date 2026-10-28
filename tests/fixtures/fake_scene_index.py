"""A stand-in for ``scripts/scene_index.py`` (spec 014 tests): same argv, same files.

    fake_scene_index.py index --model M -- <video> <out_dir>
    fake_scene_index.py embed-texts --model M -- <texts.json> <out.npy>

Driven by the JSON file named by ``$FAKE_SCENE_SPEC``:

    {"chunks": [[start, end, a], ...],    # chunk vector: a*e0 + sqrt(1-a^2)*e_(1+i)
     "texts": {"some text": a, ...},      # text vector likewise (unknown text: a=0)
     "prompt": "the prompt",              # embeds to e0 exactly
     "hang_after": 1,                     # index: block after this many chunks
     "fail": "index"}                     # exit 1 on this command

Every call appends ``<command>`` to ``$FAKE_SCENE_SPEC.calls``; ``index`` writes its
pid to ``$FAKE_SCENE_SPEC.pid`` and, on SIGTERM, ``$FAKE_SCENE_SPEC.sigterm``
before it exits 143 without publishing anything (the real CLI's contract).
"""

from __future__ import annotations

import json
import math
import os
import signal
import sys
import time
from pathlib import Path

import numpy as np

DIMS = 768


def _vector(a: float, slot: int) -> np.ndarray:
    v = np.zeros(DIMS, dtype=np.float32)
    v[0] = a
    v[1 + slot % (DIMS - 1)] = math.sqrt(max(0.0, 1.0 - a * a))
    return v


def main(argv: list[str]) -> int:
    spec_path = Path(os.environ["FAKE_SCENE_SPEC"])
    spec = json.loads(spec_path.read_text())
    command = argv[0]
    with open(f"{spec_path}.calls", "a") as fh:
        fh.write(command + "\n")
    if spec.get("fail") == command:
        print("fake failure", file=sys.stderr)
        return 1
    positionals = argv[argv.index("--") + 1 :]

    if command == "index":
        Path(f"{spec_path}.pid").write_text(str(os.getpid()))

        def _term(signum: int, frame: object) -> None:
            Path(f"{spec_path}.sigterm").write_text("1")
            sys.exit(143)

        signal.signal(signal.SIGTERM, _term)
        _video, out_dir = positionals
        chunks = spec["chunks"]
        print(json.dumps({"done": 0, "total": len(chunks)}), flush=True)
        rows = []
        for i, (start, end, a) in enumerate(chunks):
            rows.append(_vector(a, i))
            print(json.dumps({"done": i + 1, "total": len(chunks)}), flush=True)
            if spec.get("hang_after") == i + 1:
                time.sleep(3600)
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / "chunks.json").write_text(
            json.dumps([{"start": s, "end": e, "skipped_still": False} for s, e, _ in chunks])
        )
        np.save(out / "embeddings.npy", np.stack(rows))
        return 0

    if command == "embed-texts":
        texts_path, out = positionals
        texts = json.loads(Path(texts_path).read_text())
        known = spec.get("texts", {})
        rows = []
        for i, text in enumerate(texts):
            if text == spec.get("prompt"):
                rows.append(_vector(1.0, 0))
            else:
                rows.append(_vector(float(known.get(text, 0.0)), 400 + i))
        np.save(out, np.stack(rows))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
