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


# Settings removed by T014 because no code read them. Re-adding one means it
# needs a reader in app/ (see the test below) or an allowlist entry.
_DELETED_SETTINGS = (
    "scheduler_enabled",
    "scheduler_poll_seconds",
    "scheduler_max_concurrent",
    "tiktok_profile_url_base",
    "tiktok_node_bin",
    "ltx_model_path",
    "ltx_use_mps",
    "ltx_num_frames",
    "stage_timeout_seconds",
)

# Fields read without their literal name at the call site: the value is the
# accessor that must appear in app/ for the field to count as read.
_INDIRECT_READS = {
    "score_weights": "score_weights_dict(",
    "cors_origins": "cors_origins_list(",
    **{
        f"social_provider_{platform}": 'f"social_provider_{platform}"'
        for platform in ("youtube", "tiktok", "instagram", "linkedin", "x")
    },
}

# Declared on purpose with no reader yet. Keep this list short and give a reason.
# (Empty since the B-roll wiring, T012, gave pexels_api_key and
# broll_cache_dir their reader in app/services/broll_pexels_service.py.)
_FORWARD_LOOKING: set[str] = set()


def _app_source_outside_settings() -> str:
    settings_file = Path(settings_module.__file__).resolve()
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((_REPO_ROOT / "app").rglob("*.py"))
        if path.resolve() != settings_file
    )


def _is_read(name: str, source: str) -> bool:
    if re.search(rf"\b{re.escape(name)}\b", source):
        return True
    accessor = _INDIRECT_READS.get(name)
    return accessor is not None and accessor in source


def test_deleted_settings_are_not_declared() -> None:
    assert set(_DELETED_SETTINGS) & set(Settings.model_fields) == set()


def test_every_setting_is_read_by_app_code() -> None:
    source = _app_source_outside_settings()
    unread = {
        name
        for name in Settings.model_fields
        if name not in _FORWARD_LOOKING and not _is_read(name, source)
    }

    assert unread == set()


def test_forward_looking_allowlist_is_still_unread() -> None:
    """Once a forward-looking setting gets a reader, drop it from the allowlist."""
    source = _app_source_outside_settings()

    assert _FORWARD_LOOKING <= set(Settings.model_fields)
    assert {name for name in _FORWARD_LOOKING if _is_read(name, source)} == set()
