from __future__ import annotations

import logging
import shutil
from pathlib import Path

log = logging.getLogger(__name__)


def export_clips(output_paths: list[str | None], export_dir: str) -> list[str]:
    Path(export_dir).mkdir(parents=True, exist_ok=True)
    exported: list[str] = []
    for src in output_paths:
        if not src:
            continue
        dst = str(Path(export_dir) / Path(src).name)
        shutil.copy2(src, dst)
        exported.append(dst)
        log.info("Exported %s → %s", src, dst)
    return exported
