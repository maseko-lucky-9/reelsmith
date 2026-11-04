"""The root conftest keeps every test's exports out of a developer's real export folder."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from app.settings import Settings, settings

REPO_ROOT = Path(__file__).resolve().parents[2]
EXPORTING_TEST = "tests/e2e/test_happy_path.py"


def test_the_export_base_folder_is_an_empty_tmp_dir_of_this_test(tmp_path_factory):
    export_base = Path(settings.export_base_folder)

    assert export_base.is_dir()
    assert list(export_base.iterdir()) == []
    assert tmp_path_factory.getbasetemp() in export_base.parents


def test_a_fresh_settings_reads_the_same_folder():
    assert Settings().export_base_folder == settings.export_base_folder
    assert os.environ["YTVIDEO_EXPORT_BASE_FOLDER"] == settings.export_base_folder


def test_an_exporting_test_run_leaves_a_pre_set_share_empty(tmp_path):
    """A shell that exports the real folder must not receive a test's job dirs."""
    share = tmp_path / "share"
    share.mkdir()

    result = subprocess.run(
        [sys.executable, "-m", "pytest", EXPORTING_TEST, "-q", "-p", "no:cacheprovider"],
        cwd=REPO_ROOT,
        env={**os.environ, "YTVIDEO_EXPORT_BASE_FOLDER": str(share)},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert list(share.iterdir()) == []
