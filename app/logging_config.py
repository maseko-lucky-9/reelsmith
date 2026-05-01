from __future__ import annotations

import logging
import os
import sys
import time
from contextlib import contextmanager
from typing import Generator

_CONFIGURED = False


def configure_logging(level: int | None = None) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    if level is None:
        raw = os.environ.get("YTVIDEO_LOG_LEVEL", "INFO").upper()
        level = getattr(logging, raw, logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s.%(msecs)03d %(levelname)-8s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(handler)
    _CONFIGURED = True


@contextmanager
def timed(log: logging.Logger, label: str) -> Generator[None, None, None]:
    """Log entry + exit with elapsed time for a pipeline step."""
    log.debug("%-30s starting", label)
    t0 = time.perf_counter()
    try:
        yield
    finally:
        elapsed = time.perf_counter() - t0
        log.info("%-30s done  (%.2fs)", label, elapsed)


configure_logging()
