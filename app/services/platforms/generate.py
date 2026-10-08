"""Generate adapter — synthesises a video from a text brief (Stage 1).

Handles the ``generate://`` URL scheme. Instead of downloading, it produces
a real mp4 from a stored brief: TTS voice-over + AI b-roll shots, assembled
with one ffmpeg pass (concat filter + tpad). The result feeds ReelSmith's existing pipeline unchanged
(empty chapters → full-video pseudo-chapter → transcribe → caption → export).

STAGE 1: both producers default to STUB and emit real, decodable artifacts
(valid wav/mp4) so the pipeline and ffprobe work without a GPU or model.

Security: the ``brief_id`` is validated against ``^[A-Za-z0-9_-]+$`` and the
resolved brief path is confirmed to stay inside the configured brief
directory, preventing path traversal / arbitrary file read.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlparse

from app.services import ffmpeg_tools, ltx_producer, tts_service
from app.services.platforms.base import Chapter, DownloadResult
from app.settings import settings

_BRIEF_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")

# Trailing pad so the assembled video extends past the spoken content; without
# this, AUDIO_TAIL_EPSILON_SECONDS clamping in clip_service drops trailing words.
_TRAILING_PAD_SECONDS = 1.5

# When the VO is the longer track, the video tail must clear the audio by at
# least AUDIO_TAIL_EPSILON_SECONDS (1.0s in clip_service) plus a safety margin so
# probe_safe_end → min(v,a) - epsilon never clamps real speech. 2.0s = epsilon + 1.0s.
_AUDIO_TAIL_HEADROOM_SECONDS = 2.0


def _probe_duration(path: str) -> float:
    """Return media duration in seconds via PyAV (video or audio-only, e.g. wav).

    CI-safe: no system ``ffprobe`` needed. Never silently returns ``0.0`` —
    raises ``RuntimeError`` when the duration cannot be read.
    """
    try:
        return float(ffmpeg_tools.duration(path))
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"could not determine duration of {path!r}: {e}") from e


def _probe_duration_video(path: str) -> float:
    """Duration of the first video stream (falls back to the container)."""
    try:
        value = ffmpeg_tools.duration(path, "video")
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"could not determine duration of {path!r}: {e}") from e
    if value is None:
        raise RuntimeError(f"no video stream in {path!r}")
    return float(value)


# Output frame rate of the assembled video (and of the no-shot black fallback).
_ASSEMBLE_FPS = 24
_FALLBACK_SIZE = (1080, 1920)


class GenerateAdapter:
    """Synthesises a video from a stored text brief as if it were downloaded."""

    platform_id = "generate"

    @classmethod
    def matches(cls, url: str) -> bool:
        return isinstance(url, str) and url.startswith("generate://")

    def download(self, url: str, destination_folder: str) -> DownloadResult:
        if not settings.generate_enabled:
            raise RuntimeError(
                "generate mode disabled: set YTVIDEO_GENERATE_ENABLED=true"
            )

        # Parse generate://<brief_id>. urlparse puts the id in netloc for this
        # scheme; fall back to stripping the scheme prefix for robustness.
        parsed = urlparse(url)
        brief_id = parsed.netloc or url[len("generate://"):]
        brief_id = brief_id.strip("/")
        if not _BRIEF_ID_RE.match(brief_id):
            raise ValueError(f"invalid brief id: {brief_id!r}")

        brief_root = Path(settings.generate_brief_dir).resolve()
        candidate = (brief_root / f"{brief_id}.json").resolve()
        try:
            candidate.relative_to(brief_root)
        except ValueError:
            raise PermissionError(
                f"brief path escapes brief directory: {brief_id!r}"
            )
        if not candidate.is_file():
            raise FileNotFoundError(f"brief not found: {candidate}")

        brief = json.loads(candidate.read_text(encoding="utf-8"))
        title = brief.get("title", "Generated Video")
        script = brief.get("script", "")
        shots = brief.get("shots") or []
        voice_profile = brief.get("voice_profile") or settings.generate_voice_profile

        dest = Path(destination_folder)
        dest.mkdir(parents=True, exist_ok=True)

        # ── Voice-over (TTS) ─────────────────────────────────────────────────
        vo_wav = str(dest / "vo.wav")
        tts_service.synthesize(
            script,
            vo_wav,
            provider=settings.generate_tts_provider,
            endpoint=settings.voicebox_endpoint or None,
            api_key=settings.voicebox_api_key,
            voice_profile=voice_profile,
            engine=settings.voicebox_engine,
        )

        # ── B-roll shots ─────────────────────────────────────────────────────
        shot_paths: list[str] = []
        for i, shot in enumerate(shots):
            shot_path = str(dest / f"shot_{i:03d}.mp4")
            # Defensive clamp — the router validates shot.seconds to [0.1, 30.0],
            # but briefs can be written out-of-band, so bound it here too.
            seconds = max(0.1, min(30.0, float(shot.get("seconds", 2.0))))
            ltx_producer.generate_shot(
                shot.get("prompt", ""),
                seconds,
                shot_path,
                provider=settings.ltx_provider,
                seed=shot.get("seed"),
                ltx_python=settings.ltx_python or None,
                ltx_inference_script=settings.ltx_inference_script or None,
                ltx_pipeline_config=settings.ltx_pipeline_config or None,
                ltx_height=settings.ltx_height,
                ltx_width=settings.ltx_width,
                ltx_frame_rate=settings.ltx_frame_rate,
            )
            shot_paths.append(shot_path)

        # ── Assemble ─────────────────────────────────────────────────────────
        out_path = str(dest / "generated.mp4")
        dur = self._assemble(shot_paths, vo_wav, out_path)

        return DownloadResult(
            video_path=out_path,
            info={
                "title": title,
                "duration": dur,
                "chapters": [],
                "upload_date": None,
                "description": brief.get("script", ""),
                "tags": [],
            },
            title=title,
            duration=dur,
            source=self.platform_id,
        )

    def _assemble(self, shot_paths: list[str], vo_wav: str, out_path: str) -> float:
        """Concatenate shots, attach the VO, pad the tail, write libx264/aac.

        One ffmpeg pass: each shot is centred on the largest shot's canvas
        (MoviePy ``method="compose"``), normalised to 24 fps, concatenated,
        then black-padded (``tpad``) to the target length; ``-t`` is explicit.
        Returns the written video's duration.
        """
        audio_duration = _probe_duration(vo_wav)
        if shot_paths:
            sizes = [ffmpeg_tools.video_size(p) for p in shot_paths]
            width = max(w for w, _ in sizes)
            height = max(h for _, h in sizes)
            base_duration = sum(
                ffmpeg_tools.duration(p, "video") or _probe_duration(p)
                for p in shot_paths
            )
            inputs: list[str] = []
            for p in shot_paths:
                inputs += ["-i", p]
        else:
            # No shots: a black clip matching the VO length.
            width, height = _FALLBACK_SIZE
            base_duration = max(0.1, audio_duration)
            inputs = [
                "-f", "lavfi",
                "-i", f"color=c=black:s={width}x{height}:r={_ASSEMBLE_FPS}:d={base_duration:.6f}",
            ]  # fmt: skip
        n_video = max(1, len(shot_paths))

        # The downstream pipeline runs probe_safe_end → min(v,a) - epsilon.
        # When the VO is longer than the concatenated b-roll, the video must
        # extend STRICTLY past the audio by at least epsilon + margin so the
        # safe_end clamp can never truncate spoken content. Pad the VIDEO tail
        # to audio_duration + _AUDIO_TAIL_HEADROOM_SECONDS; never shorten audio.
        target = max(
            base_duration + _TRAILING_PAD_SECONDS,
            audio_duration + _AUDIO_TAIL_HEADROOM_SECONDS,
        )
        pad = max(0.0, target - base_duration)

        graph = [
            f"[{i}:v]pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
            f"setsar=1,fps={_ASSEMBLE_FPS},format=yuv420p[s{i}]"
            for i in range(n_video)
        ]
        graph.append(
            "".join(f"[s{i}]" for i in range(n_video))
            + f"concat=n={n_video}:v=1:a=0,"
            + f"tpad=stop_mode=add:stop_duration={pad:.6f}:color=black[v]"
        )
        ffmpeg_tools.run(
            [
                "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
                *inputs,
                "-i", vo_wav,
                "-filter_complex", ";".join(graph),
                "-map", "[v]", "-map", f"{n_video}:a:0",
                "-t", f"{target:.6f}", "-r", str(_ASSEMBLE_FPS),
                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                "-c:a", "aac",
                out_path,
            ]
        )  # fmt: skip
        return _probe_duration_video(out_path)

    def extract_chapters(self, info: dict) -> list[Chapter]:
        return []
