
from __future__ import annotations

import json
from pathlib import Path

try:
    from pydantic_settings import BaseSettings, SettingsConfigDict
    _HAS_PYDANTIC_SETTINGS = True
except ImportError:  # pragma: no cover
    _HAS_PYDANTIC_SETTINGS = False


_REPO_ANTON = (
    Path(__file__).resolve().parent / "assets" / "fonts" / "Anton-Regular.ttf"
)
_DEFAULT_FONT_CANDIDATES = (
    str(_REPO_ANTON),
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

    # Anchor .env to the project root (app/settings.py → app/ → project root),
    # so the path is CWD-independent regardless of where uvicorn is launched from.
    _ENV_FILE = str(Path(__file__).resolve().parent.parent / ".env")

    class Settings(BaseSettings):
        model_config = SettingsConfigDict(
            env_prefix="YTVIDEO_",
            extra="ignore",
            env_file=_ENV_FILE,
            env_file_encoding="utf-8",
        )

        # ── Database ──────────────────────────────────────────────────────────
        db_url: str = "sqlite+aiosqlite:///./reelsmith.db"
        # "sql" | "memory"
        job_store: str = "sql"

        # ── Jobs & concurrency ────────────────────────────────────────────────
        max_concurrent_jobs: int = 1
        max_parallel_chapters: int = 1
        max_thread_workers: int = 4

        # ── Pipeline defaults ─────────────────────────────────────────────────
        default_download_path: str = "/tmp/yt"
        default_caption_format: str = "srt"
        default_target_aspect_ratio: float = 9 / 16
        default_transcription_language: str = "en-US"
        font_path: str | None = _default_font_path()

        # ── Transcription ─────────────────────────────────────────────────────
        transcription_provider: str = "whisper"  # "whisper" | "stub"
        whisper_model: str = "base"
        transcription_timeout_seconds: int = 120

        # ── Segment scoring ───────────────────────────────────────────────────
        # "local_heuristic" | "chapter" | "stub"
        segment_provider: str = "chapter"
        target_clip_seconds_min: int = 20
        target_clip_seconds_max: int = 60
        score_weights: str = '{"hook":0.30,"value":0.25,"emotion":0.15,"audio":0.15,"trend":0.15}'

        # ── Reframe ───────────────────────────────────────────────────────────
        # "letterbox" | "face_track" | "stub"
        reframe_provider: str = "letterbox"

        # ── B-Roll ────────────────────────────────────────────────────────────
        # "local" | "none"
        broll_provider: str = "none"

        # ── Media & retention ─────────────────────────────────────────────────
        max_upload_mb: int = 500
        retention_days: int = 30
        retention_sweep_minutes: int = 60

        # ── Frontend ──────────────────────────────────────────────────────────
        serve_frontend: bool = False

        # ── Auth ──────────────────────────────────────────────────────────────
        require_auth: bool = False
        api_key: str | None = None

        # ── Multi-user auth (W3.8) ────────────────────────────────────────────
        # When False (default), current_user_id() returns 'local' and the
        # API token resolver is bypassed. Flip to True after issuing the
        # first API token.
        auth_enabled: bool = False

        # ── OAuth at-rest encryption (W1.3) ───────────────────────────────────
        # Fernet key (URL-safe base64-encoded 32 bytes). When unset, the
        # token vault falls back to an ephemeral in-process key — tokens
        # cannot survive process restarts in that mode.
        oauth_encrypt_key: str | None = None

        # ── Publish scheduler (W1.4) ──────────────────────────────────────────
        scheduler_enabled: bool = False
        scheduler_poll_seconds: int = 30
        scheduler_max_concurrent: int = 3

        # ── TikTok cookie adapter ─────────────────────────────────────────────
        # Set YTVIDEO_SOCIAL_PROVIDER_TIKTOK=cookie to activate.
        # YTVIDEO_OAUTH_ENCRYPT_KEY must be a stable Fernet key or cookies
        # die on process restart (generate: python -c "from cryptography.fernet
        # import Fernet; print(Fernet.generate_key().decode())").
        tiktok_cookies_dir: str = "data/tiktok-cookies"
        tiktok_profile_url_base: str = "https://www.tiktok.com/@"
        tiktok_node_bin: str = "node"
        tiktok_session_ttl_days: int = 21
        # ── TikTok n8n sidecar (interchangeable path) ─────────────────────────
        # Set YTVIDEO_SOCIAL_PROVIDER_TIKTOK=n8n to activate.
        # e.g. http://tiktok-sidecar.n8n-live.svc.cluster.local:8000/api/upload
        tiktok_sidecar_url: str | None = None

        # ── AI Hook (W1.7) ────────────────────────────────────────────────────
        ai_hook_enabled: bool = True
        ai_hook_max_chars: int = 80

        # ── Speech enhancement (W1.8) ─────────────────────────────────────────
        # "loudnorm" | "rnnoise" | "passthrough"
        audio_enhance_provider: str = "loudnorm"
        audio_enhance_rnnoise_model: str | None = None

        # ── B-Roll Pexels (W1.9) ──────────────────────────────────────────────
        pexels_api_key: str | None = None
        broll_cache_dir: str = "data/broll-cache"

        # ── Generate mode (Stage 1) ───────────────────────────────────────────
        # Text brief → AI b-roll + TTS voice-over → assembled mp4 → existing
        # pipeline. Defaults keep the feature OFF and both producers on STUB so
        # importing settings and running CI is unchanged. Real providers
        # (ltx / voicebox) need requirements-generate.txt and a GPU/MPS host.
        generate_enabled: bool = False
        generate_brief_dir: str = "data/generate-briefs"
        ltx_provider: str = "stub"  # "stub" | "ltx"
        # Legacy in-process knobs — retained for back-compat with smoke tooling
        # and tests; the real ``ltx`` path is now a subprocess (see below) and
        # does not use these.
        ltx_model_path: str = ""
        ltx_use_mps: bool = True
        ltx_num_frames: int = 121
        # ── LTX subprocess path (real provider) ───────────────────────────────
        # The LTX fork runs in its OWN venv (deps conflict with reelsmith's), so
        # the ``ltx`` provider shells out to the fork's inference.py CLI rather
        # than importing ltx_video in-process. All three must be set + exist on
        # disk for the provider to run; otherwise it raises NOT_CONFIGURED.
        ltx_python: str = ""  # interpreter inside the fork's venv
        ltx_inference_script: str = ""  # path to the fork's inference.py
        ltx_pipeline_config: str = ""  # path to the pipeline yaml
        # Portrait reel defaults: height > width. Rounded to ÷32 at call time.
        ltx_height: int = 1216
        ltx_width: int = 704
        ltx_frame_rate: int = 24
        generate_tts_provider: str = "stub"  # "stub" | "voicebox"
        voicebox_endpoint: str = ""
        voicebox_api_key: str | None = None
        voicebox_engine: str = "kokoro"
        generate_voice_profile: str = ""

        # ── Bulk export (W3.7) ────────────────────────────────────────────────
        bulk_export_max_clips: int = 200

        # ── Long-stage hardening (W2.10) ──────────────────────────────────────
        # Per-stage soft timeout in seconds. Workers respect this to abort
        # runaway voice-over / demucs / animated-caption renders.
        stage_timeout_seconds: int = 1800
        # SSE keep-alive heartbeat interval. Sent as ': ping\n\n' so it's a
        # comment frame the client ignores. 0 disables.
        sse_keepalive_seconds: int = 15
        # Connection pool recycle (Postgres only).
        db_pool_recycle_seconds: int = 1800

        # ── CORS ──────────────────────────────────────────────────────────────
        cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

        # ── Ollama ────────────────────────────────────────────────────────────
        ollama_base_url: str = "http://localhost:11434"
        ollama_model: str = "mistral"
        ollama_enabled: bool = True
        ollama_timeout_seconds: int = 60

        # ── Export ────────────────────────────────────────────────────────────
        export_base_folder: str = ""

        # ── Rendering ─────────────────────────────────────────────────────────
        caption_words_per_segment: int = 3
        download_timeout_seconds: int = 600
        render_timeout_seconds: int = 3600

        def cors_origins_list(self) -> list[str]:
            return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

        def score_weights_dict(self) -> dict[str, float]:
            return json.loads(self.score_weights)

else:  # Fallback: pydantic-settings not yet installed

    class Settings:  # type: ignore[no-redef]
        db_url = "sqlite+aiosqlite:///./reelsmith.db"
        job_store = "sql"
        max_concurrent_jobs = 1
        max_parallel_chapters = 1
        max_thread_workers = 4
        default_download_path = "/tmp/yt"
        default_caption_format = "srt"
        default_target_aspect_ratio = 9 / 16
        default_transcription_language = "en-US"
        font_path = _default_font_path()
        transcription_provider = "whisper"
        whisper_model = "base"
        transcription_timeout_seconds = 120
        segment_provider = "chapter"
        target_clip_seconds_min = 20
        target_clip_seconds_max = 60
        score_weights = '{"hook":0.30,"value":0.25,"emotion":0.15,"audio":0.15,"trend":0.15}'
        reframe_provider = "letterbox"
        broll_provider = "none"
        # ── Generate mode (Stage 1) ───────────────────────────────────────────
        generate_enabled = False
        generate_brief_dir = "data/generate-briefs"
        ltx_provider = "stub"
        ltx_model_path = ""
        ltx_use_mps = True
        ltx_num_frames = 121
        # ── LTX subprocess path (real provider) — see pydantic class above ────
        ltx_python = ""
        ltx_inference_script = ""
        ltx_pipeline_config = ""
        ltx_height = 1216
        ltx_width = 704
        ltx_frame_rate = 24
        generate_tts_provider = "stub"
        voicebox_endpoint = ""
        voicebox_api_key = None
        voicebox_engine = "kokoro"
        generate_voice_profile = ""
        max_upload_mb = 500
        retention_days = 30
        retention_sweep_minutes = 60
        serve_frontend = False
        require_auth = False
        api_key = None
        cors_origins = "http://localhost:5173,http://127.0.0.1:5173"
        ollama_base_url = "http://localhost:11434"
        ollama_model = "mistral"
        ollama_enabled = True
        ollama_timeout_seconds = 60
        export_base_folder = ""
        caption_words_per_segment = 3
        download_timeout_seconds = 600
        render_timeout_seconds = 3600

        def cors_origins_list(self) -> list[str]:
            return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

        def score_weights_dict(self) -> dict[str, float]:
            return json.loads(self.score_weights)


settings = Settings()
