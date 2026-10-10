"""Application settings: every field reads a ``YTVIDEO_``-prefixed env var or ``.env``."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ANTON = Path(__file__).resolve().parent / "assets" / "fonts" / "Anton-Regular.ttf"
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


# Anchor .env to the project root (app/settings.py → app/ → project root),
# so the path is CWD-independent regardless of where uvicorn is launched from.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ENV_FILE = str(_PROJECT_ROOT / ".env")


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
    # Skip ``alembic upgrade head`` at API start-up (sql store only).
    skip_alembic: bool = False

    # ── Logging ───────────────────────────────────────────────────────────
    # Root log level name (DEBUG | INFO | WARNING | ERROR); unknown → INFO.
    log_level: str = "INFO"

    # ── Jobs & concurrency ────────────────────────────────────────────────
    max_concurrent_jobs: int = 1
    max_parallel_chapters: int = 1
    max_thread_workers: int = 4

    # ── Pipeline defaults ─────────────────────────────────────────────────
    # Downloaded and uploaded source videos (kept for re-render) and job
    # output folders. Not under /tmp: macOS clears it. Gitignored; created on
    # first use.
    default_download_path: str = str(_PROJECT_ROOT / "data" / "downloads")
    default_caption_format: str = "srt"
    default_target_aspect_ratio: float = 9 / 16
    default_transcription_language: str = "en-US"
    font_path: str | None = _default_font_path()

    # ── Transcription ─────────────────────────────────────────────────────
    transcription_provider: str = "whisper"  # "whisper" | "stub"
    whisper_model: str = "base"
    # Decode-time budget per chapter: max(this, chapter seconds), counted
    # from when decoding starts (model loaded, decode slot held).
    transcription_timeout_seconds: int = 120
    # Decode settings, chosen by scripts/bench_whisper.py (P3, Apple M5 Pro,
    # base int8, 77 s speech): beam 1 + VAD is ~2x faster than beam 5 and
    # matched the reference transcript better (VAD stops Whisper skipping
    # the speech that follows a long silence). 8 CPU threads beat 4/5/10/15
    # and the CTranslate2 default (0 = 4); use 0 on hosts with < 8 cores.
    whisper_beam_size: int = 1
    whisper_vad_filter: bool = True
    whisper_cpu_threads: int = 8
    # Load the model in the background at API start-up (whisper provider
    # only) so the first job doesn't pay for it. Tests turn this off.
    whisper_warmup: bool = True

    # ── Segment scoring ───────────────────────────────────────────────────
    # "local_heuristic" | "chapter" | "stub"
    segment_provider: str = "chapter"
    target_clip_seconds_min: int = 20
    target_clip_seconds_max: int = 60
    score_weights: str = (
        '{"hook":0.30,"value":0.25,"emotion":0.15,"audio":0.15,"trend":0.15}'
    )

    # ── Reframe ───────────────────────────────────────────────────────────
    # "letterbox" (default: scaled inset over the blurred background) |
    # "face_track" (9:16 window pans with the speaker's face; YuNet on
    # onnxruntime). Any other value is treated as "letterbox". Only applies
    # when a job's ``reframe`` option is on.
    reframe_provider: str = "letterbox"
    # Where face_track downloads its face model on first use (232 KB,
    # SHA-256 pinned in app/services/face_detector.py). Gitignored.
    reframe_model_dir: str = str(_PROJECT_ROOT / "data" / "models")

    # ── B-Roll ────────────────────────────────────────────────────────────
    # Fills a reel's B-roll inserts when the job's ``broll`` option is on:
    # "none" (default: no B-roll) | "local" (broll_library_dir) | "pexels"
    # (Pexels video search; needs pexels_api_key).
    broll_provider: str = "none"
    # The local provider's library: *.mp4 files named by keyword
    # (``ocean_waves.mp4`` answers "ocean" and "waves").
    broll_library_dir: str = str(_PROJECT_ROOT / "data" / "broll")

    # ── Media & retention ─────────────────────────────────────────────────
    max_upload_mb: int = 500
    retention_days: int = 30
    retention_sweep_minutes: int = 60
    # Files of retired clips still on disk (e.g. the clips a reprompt
    # replaced) are deleted once the file is older than this (T033).
    retired_files_grace_hours: int = 24

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

    # ── Share links (W3.4) ────────────────────────────────────────────────
    # HMAC secret for share-link tokens. Unset → a random per-process
    # secret, so links stop verifying after a restart.
    share_link_secret: str | None = None

    # ── Social adapter selection (W1.5) ───────────────────────────────────
    # Global provider: "stub" | "real". A non-empty per-platform value wins
    # over it (TikTok: "cookie" | "n8n"; YouTube: "real").
    social_provider: str = "stub"
    social_provider_youtube: str = ""
    social_provider_tiktok: str = ""
    social_provider_instagram: str = ""
    social_provider_linkedin: str = ""
    social_provider_x: str = ""

    # ── TikTok cookie adapter ─────────────────────────────────────────────
    # Set YTVIDEO_SOCIAL_PROVIDER_TIKTOK=cookie to activate.
    # YTVIDEO_OAUTH_ENCRYPT_KEY must be a stable Fernet key or cookies
    # die on process restart (generate: python -c "from cryptography.fernet
    # import Fernet; print(Fernet.generate_key().decode())").
    tiktok_cookies_dir: str = "data/tiktok-cookies"
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
    # Sent only as the Authorization header of the search request; never
    # logged. Downloads are cached in broll_cache_dir by Pexels video id.
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

    # ── Voice-over (W2.3) ─────────────────────────────────────────────────
    # Path to the .onnx model for the ``piper`` voice-over provider.
    piper_model: str = ""

    # ── Bulk export (W3.7) ────────────────────────────────────────────────
    bulk_export_max_clips: int = 200

    # ── Long-stage hardening (W2.10) ──────────────────────────────────────
    # Seconds between keep-alive pings on the job SSE stream (sse-starlette
    # sends a ': ping' comment frame the client ignores). 0 disables.
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


settings = Settings()
