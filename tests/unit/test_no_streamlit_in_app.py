"""Architecture invariant: app/ must not import streamlit anywhere.

Streamlit is allowed only under ui/. This test fails if any module
under app/ acquires a hard dependency on the UI framework.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_DIR = REPO_ROOT / "app"

_STREAMLIT_RE = re.compile(r"^\s*(import\s+streamlit|from\s+streamlit)", re.MULTILINE)


def test_app_does_not_import_streamlit():
    offenders = []
    for path in APP_DIR.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if _STREAMLIT_RE.search(text):
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert not offenders, f"streamlit imports found in app/: {offenders}"
