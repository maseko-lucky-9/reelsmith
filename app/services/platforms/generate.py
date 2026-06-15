"""Generate adapter — synthesises a video from a text brief (Stage 1).

Handles the ``generate://`` URL scheme. Instead of downloading, it produces
a real mp4 from a stored brief: TTS voice-over + AI b-roll shots, assembled
with MoviePy. The result feeds ReelSmith's existing pipeline unchanged
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
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlparse

# Must run before any MoviePy import to restore PIL.Image.ANTIALIAS.
import app.compat  # noqa: F401

from app.services import ltx_producer, tts_service
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
    """Return media duration in seconds, CI-safe.

    Prefers MoviePy (which uses ``imageio-ffmpeg``'s bundled ffmpeg and works in
    CI where a system ``ffprobe`` is absent). Handles both video containers and
    audio-only files (e.g. wav voice-over). Only falls back to the ``ffprobe``
    subprocess when MoviePy can't open the file AND a real ``ffprobe`` is on
    PATH. Never silently returns ``0.0`` when a real duration is obtainable.
    """
    moviepy_err: Exception | None = None
    try:
        from moviepy.editor import AudioFileClip, VideoFileClip

        try:
            clip = VideoFileClip(path)
        except Exception:
            # Audio-only container (wav VO) — VideoFileClip can't open it.
            clip = AudioFileClip(path)
        try:
            return float(clip.duration)
        finally:
            try:
                clip.close()
            except Exception:  # noqa: BLE001
                pass
    except Exception as e:  # noqa: BLE001
        moviepy_err = e

    # MoviePy could not read the file. Only now consider ffprobe, and only if a
    # real one is installed (imageio-ffmpeg ships ffmpeg, NOT ffprobe).
    if shutil.which("ffprobe") is not None:
        try:
            result = subprocess.run(
                [
                    "ffprobe", "-v", "quiet", "-print_format", "json",
                    "-show_format", path,
                ],
                capture_output=True, text=True, timeout=15,
            )
            info = json.loads(result.stdout)
            return float(info.get("format", {}).get("duration", 0))
        except Exception:  # noqa: BLE001
            pass

    raise RuntimeError(
        f"could not determine duration of {path!r}: {moviepy_err}"
    )


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
        """Concatenate shots, attach the VO, pad the tail, write libx264/aac."""
        from moviepy.editor import (
            AudioFileClip,
            ColorClip,
            VideoFileClip,
            concatenate_videoclips,
        )

        video_clips = [VideoFileClip(p) for p in shot_paths]
        audio_clip = AudioFileClip(vo_wav)
        opened = [*video_clips, audio_clip]
        try:
            if video_clips:
                base = concatenate_videoclips(video_clips, method="compose")
            else:
                # No shots: fall back to a black clip matching the VO length.
                base = ColorClip(
                    size=(1080, 1920), color=(0, 0, 0),
                    duration=max(0.1, audio_clip.duration),
                ).set_fps(24)
                opened.append(base)

            # The downstream pipeline runs probe_safe_end → min(v,a) - epsilon.
            # When the VO is longer than the concatenated b-roll, the video must
            # extend STRICTLY past the audio by at least epsilon + margin so the
            # safe_end clamp can never truncate spoken content. Pad the VIDEO tail
            # to audio_duration + _AUDIO_TAIL_HEADROOM_SECONDS; never shorten audio.
            min_video_for_audio = audio_clip.duration + _AUDIO_TAIL_HEADROOM_SECONDS
            target = max(
                base.duration + _TRAILING_PAD_SECONDS,
                min_video_for_audio,
            )

            # Pad video to target by holding a trailing black frame.
            pad_tail = ColorClip(
                size=base.size, color=(0, 0, 0),
                duration=max(0.0, target - base.duration),
            ).set_fps(24)
            opened.append(pad_tail)
            video = concatenate_videoclips([base, pad_tail], method="compose")
            opened.append(video)

            # Attach the VO without truncating speech (audio is never shortened;
            # the video was extended above to keep it strictly longer than audio).
            video = video.set_audio(audio_clip)
            video.write_videofile(
                out_path,
                codec="libx264",
                audio_codec="aac",
                fps=24,
                preset="ultrafast",
                logger=None,
            )
            # Derive duration from the in-memory clip rather than re-opening the
            # written file with ffprobe — MoviePy's clip duration is authoritative
            # and works in CI (no system ffprobe required).
            result_duration = float(video.duration)
        finally:
            for c in opened:
                try:
                    c.close()
                except Exception:  # noqa: BLE001
                    pass

        return result_duration

    def extract_chapters(self, info: dict) -> list[Chapter]:
        return []
