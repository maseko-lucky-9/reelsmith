"""Download a Piper voice for the voice-over feature (spec 016).

Usage::

    python -m scripts.fetch_piper_voice                      # en_US-lessac-medium
    python -m scripts.fetch_piper_voice en_US-amy-medium     # another voice
    python -m scripts.fetch_piper_voice --dest /path/to/models

Fetches ``<voice>.onnx`` and ``<voice>.onnx.json`` from the official
``rhasspy/piper-voices`` Hugging Face repository into the voice folder
(``YTVIDEO_VOICEOVER_MODEL_DIR``, default ``data/models``). Each file is
written to a ``.part`` file and renamed once it is complete and not empty.
Nothing downloaded is executed or imported here. Existing files are kept
unless ``--force`` is given. The app never downloads voices itself: with no
voice, ``YTVIDEO_VOICEOVER_PROVIDER=auto`` falls back to the stub.
"""

from __future__ import annotations

import argparse
import re
import sys
import urllib.request
from pathlib import Path

BASE_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main"
DEFAULT_VOICE = "en_US-lessac-medium"
# <lang>_<REGION>-<name>-<quality>, e.g. en_US-lessac-medium.
VOICE_RE = re.compile(
    r"^(?P<family>[a-z]{2,3})_(?P<region>[A-Z]{2})-(?P<name>[A-Za-z0-9_]+)-(?P<quality>x_low|low|medium|high)$"
)
MAX_BYTES = 200 * 1024 * 1024
TIMEOUT_SECONDS = 60


def voice_urls(voice: str) -> dict[str, str]:
    """``{file name: url}`` for the voice's model and config."""
    match = VOICE_RE.fullmatch(voice)
    if match is None:
        raise ValueError(f"not a Piper voice name: {voice!r} (expected e.g. {DEFAULT_VOICE})")
    family, region, name, quality = match.group("family", "region", "name", "quality")
    folder = f"{BASE_URL}/{family}/{family}_{region}/{name}/{quality}"
    return {f"{voice}{ext}": f"{folder}/{voice}{ext}?download=true" for ext in (".onnx", ".onnx.json")}


def _download(url: str, dest: Path) -> int:
    part = dest.with_name(dest.name + ".part")
    size = 0
    with urllib.request.urlopen(url, timeout=TIMEOUT_SECONDS) as response, part.open("wb") as out:  # noqa: S310 - fixed https host
        while chunk := response.read(1 << 20):
            size += len(chunk)
            if size > MAX_BYTES:
                raise RuntimeError(f"{url}: larger than {MAX_BYTES} bytes")
            out.write(chunk)
    if size == 0:
        raise RuntimeError(f"{url}: empty download")
    part.replace(dest)
    return size


def main(argv: list[str] | None = None) -> int:
    from app.settings import settings

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("voice", nargs="?", default=DEFAULT_VOICE)
    parser.add_argument("--dest", default=settings.voiceover_model_dir, help="voice folder")
    parser.add_argument("--force", action="store_true", help="download even if the files exist")
    args = parser.parse_args(argv)

    try:
        urls = voice_urls(args.voice)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    dest = Path(args.dest)
    dest.mkdir(parents=True, exist_ok=True)
    for name, url in urls.items():
        target = dest / name
        if target.is_file() and target.stat().st_size > 0 and not args.force:
            print(f"kept {target}")
            continue
        print(f"downloading {url}")
        size = _download(url, target)
        print(f"wrote {target} ({size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
