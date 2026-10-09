"""Settings module shape: one pydantic-settings class, every field documented."""

from __future__ import annotations

import ast
import re
from pathlib import Path

from pydantic_settings import BaseSettings

from app import settings as settings_module
from app.settings import Settings, settings

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ENV_EXAMPLE = _REPO_ROOT / ".env.example"


def test_settings_is_the_pydantic_settings_class() -> None:
    assert issubclass(Settings, BaseSettings)
    assert isinstance(settings, Settings)


def test_settings_module_defines_a_single_settings_class() -> None:
    """A second (fallback) class would duplicate every default and drift."""
    tree = ast.parse(Path(settings_module.__file__).read_text(encoding="utf-8"))
    classes = [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == "Settings"
    ]
    assert classes == ["Settings"]


def test_every_setting_is_documented_in_env_example() -> None:
    documented = set(
        re.findall(r"YTVIDEO_([A-Z0-9_]+)=", _ENV_EXAMPLE.read_text(encoding="utf-8"))
    )
    fields = {name.upper() for name in Settings.model_fields}

    assert fields - documented == set()
