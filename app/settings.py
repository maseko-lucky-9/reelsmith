from __future__ import annotations

from pathlib import Path

try:
    from pydantic_settings import BaseSettings, SettingsConfigDict
    _HAS_PYDANTIC_SETTINGS = True
except ImportError:  # pragma: no cover - resolved in Phase 8 once dep is added
    _HAS_PYDANTIC_SETTINGS = False


_DEFAULT_FONT_CANDIDATES = (
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
)


def _default_font_path() -> str | None:
    for candidate in _DEFAULT_FONT_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    return None


if _HAS_PYDANTIC_SETTINGS:

    class Settings(BaseSettings):
        model_config = SettingsConfigDict(env_prefix="YTVIDEO_", extra="ignore")

        default_download_path: str = "/tmp/yt"
        default_caption_format: str = "srt"
        default_target_aspect_ratio: float = 9 / 16
        max_parallel_chapters: int = 1
        max_thread_workers: int = 4
        font_path: str | None = _default_font_path()
        download_timeout_seconds: int = 600
        transcription_timeout_seconds: int = 120
        render_timeout_seconds: int = 3600
        transcription_provider: str = "google"  # "google" | "stub"

else:  # Fallback so Phase 2 doesn't require pydantic-settings to be installed yet.

    class Settings:
        default_download_path = "/tmp/yt"
        default_caption_format = "srt"
        default_target_aspect_ratio = 9 / 16
        max_parallel_chapters = 1
        max_thread_workers = 4
        font_path = _default_font_path()
        download_timeout_seconds = 600
        transcription_timeout_seconds = 120
        render_timeout_seconds = 300
        transcription_provider = "google"


settings = Settings()
