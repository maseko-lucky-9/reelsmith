"""Env vars formerly read straight from ``os.environ`` go through Settings (T002, E2).

Two kinds of test per variable:

* the ``Settings`` field reads its ``YTVIDEO_`` env var (a fresh instance);
* the call site reads ``settings.<field>`` at call time, not the raw
  environment: each test sets the setting to one value and the env var to a
  different one, so a call site that still reads ``os.environ`` fails.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from app import logging_config
from app.services import share_link_service, token_vault, voiceover_service
from app.services.social import get_adapter, registry
from app.services.social.stub import StubAdapter
from app.settings import Settings, settings

PLATFORMS = ("youtube", "tiktok", "instagram", "linkedin", "x")


def _fresh_settings() -> Settings:
    """A Settings built from the process environment only (no .env file)."""
    return Settings(_env_file=None)


# ── Settings fields ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "env_name,field,value",
    [
        ("YTVIDEO_LOG_LEVEL", "log_level", "DEBUG"),
        ("YTVIDEO_PIPER_MODEL", "piper_model", "/models/voice.onnx"),
        ("YTVIDEO_SHARE_LINK_SECRET", "share_link_secret", "share-secret"),
        ("YTVIDEO_OAUTH_ENCRYPT_KEY", "oauth_encrypt_key", "fernet-key"),
        ("YTVIDEO_SOCIAL_PROVIDER", "social_provider", "real"),
        *[
            (f"YTVIDEO_SOCIAL_PROVIDER_{p.upper()}", f"social_provider_{p}", "real")
            for p in PLATFORMS
        ],
    ],
)
def test_settings_field_reads_its_prefixed_env_var(monkeypatch, env_name, field, value):
    monkeypatch.setenv(env_name, value)

    assert getattr(_fresh_settings(), field) == value


def test_settings_defaults_for_the_new_fields(monkeypatch):
    for name in (
        "YTVIDEO_LOG_LEVEL",
        "YTVIDEO_PIPER_MODEL",
        "YTVIDEO_SHARE_LINK_SECRET",
        "YTVIDEO_SOCIAL_PROVIDER",
        *(f"YTVIDEO_SOCIAL_PROVIDER_{p.upper()}" for p in PLATFORMS),
    ):
        monkeypatch.delenv(name, raising=False)

    fresh = _fresh_settings()

    assert fresh.log_level == "INFO"
    assert fresh.piper_model == ""
    assert fresh.share_link_secret is None
    assert fresh.social_provider == "stub"
    assert all(getattr(fresh, f"social_provider_{p}") == "" for p in PLATFORMS)


def test_social_provider_tiktok_env_name_still_works(monkeypatch):
    """The documented YTVIDEO_SOCIAL_PROVIDER_TIKTOK name keeps selecting cookie."""
    monkeypatch.setenv("YTVIDEO_SOCIAL_PROVIDER_TIKTOK", "cookie")
    fresh = _fresh_settings()
    monkeypatch.setattr(registry, "settings", fresh)

    assert fresh.social_provider_tiktok == "cookie"
    assert type(get_adapter("tiktok")).__name__ == "TikTokCookieAdapter"


def test_social_provider_tiktok_from_env_file_is_honoured(monkeypatch, tmp_path: Path):
    """Behaviour change: a .env value now selects the adapter (it was ignored)."""
    monkeypatch.delenv("YTVIDEO_SOCIAL_PROVIDER_TIKTOK", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("YTVIDEO_SOCIAL_PROVIDER_TIKTOK=cookie\n", encoding="utf-8")
    fresh = Settings(_env_file=str(env_file))
    monkeypatch.setattr(registry, "settings", fresh)

    assert type(get_adapter("tiktok")).__name__ == "TikTokCookieAdapter"


# ── Call sites read settings, not os.environ ────────────────────────────────


def test_configure_logging_uses_settings_log_level(monkeypatch):
    root = logging.getLogger()
    saved_level, saved_handlers = root.level, list(root.handlers)
    monkeypatch.setattr(settings, "log_level", "debug")
    monkeypatch.setenv("YTVIDEO_LOG_LEVEL", "ERROR")
    monkeypatch.setattr(logging_config, "_CONFIGURED", False)
    try:
        logging_config.configure_logging()

        assert root.level == logging.DEBUG
    finally:
        root.setLevel(saved_level)
        root.handlers[:] = saved_handlers


def test_piper_reads_model_from_settings(monkeypatch, tmp_path: Path):
    out = tmp_path / "vo.wav"
    argv_seen: list[list[str]] = []
    monkeypatch.setattr(settings, "piper_model", "/models/from-settings.onnx")
    monkeypatch.setenv("YTVIDEO_PIPER_MODEL", "/models/from-env.onnx")
    monkeypatch.setattr(
        voiceover_service.shutil, "which", lambda _name: "/usr/bin/piper"
    )

    def fake(argv, stdin_text=""):
        argv_seen.append(list(argv))
        out.write_bytes(b"fake")

    voiceover_service.synthesize("hi", str(out), provider="piper", invoker=fake)

    argv = argv_seen[0]
    assert argv[argv.index("--model") + 1] == "/models/from-settings.onnx"


def test_share_link_secret_comes_from_settings(monkeypatch):
    monkeypatch.setattr(settings, "share_link_secret", "from-settings")
    monkeypatch.setenv("YTVIDEO_SHARE_LINK_SECRET", "from-env")

    assert share_link_service._resolve_secret() == "from-settings"


def test_token_vault_key_comes_from_settings(monkeypatch):
    settings_key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "oauth_encrypt_key", settings_key)
    monkeypatch.setenv("YTVIDEO_OAUTH_ENCRYPT_KEY", Fernet.generate_key().decode())
    token_vault.reset_for_tests()
    try:
        ciphertext = token_vault.encrypt("hunter2")

        assert Fernet(settings_key.encode()).decrypt(ciphertext) == b"hunter2"
    finally:
        token_vault.reset_for_tests()


def test_token_vault_has_no_stable_key_when_settings_has_none(monkeypatch):
    monkeypatch.setattr(settings, "oauth_encrypt_key", None)
    monkeypatch.setenv("YTVIDEO_OAUTH_ENCRYPT_KEY", Fernet.generate_key().decode())

    assert token_vault.has_stable_key() is False


@pytest.mark.parametrize("platform", PLATFORMS)
def test_registry_per_platform_provider_comes_from_settings(monkeypatch, platform):
    monkeypatch.setattr(settings, "social_provider", "stub")
    monkeypatch.setattr(settings, f"social_provider_{platform}", "real")
    monkeypatch.setenv(f"YTVIDEO_SOCIAL_PROVIDER_{platform.upper()}", "stub")

    assert registry._provider_for(platform) == "real"


def test_registry_global_provider_comes_from_settings(monkeypatch):
    monkeypatch.setattr(settings, "social_provider", "real")
    monkeypatch.setattr(settings, "social_provider_youtube", "")
    monkeypatch.setenv("YTVIDEO_SOCIAL_PROVIDER", "stub")
    monkeypatch.delenv("YTVIDEO_SOCIAL_PROVIDER_YOUTUBE", raising=False)

    assert type(get_adapter("youtube")).__name__ == "YouTubeAdapter"


def test_registry_defaults_to_stub(monkeypatch):
    monkeypatch.setattr(settings, "social_provider", "stub")
    for platform in PLATFORMS:
        monkeypatch.setattr(settings, f"social_provider_{platform}", "")

    assert all(isinstance(get_adapter(p), StubAdapter) for p in PLATFORMS)
