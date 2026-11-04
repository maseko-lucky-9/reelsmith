// Adapted from OpenCut (MIT) apps/web/src/components/editor/preview-panel.tsx @ 3037a7e
// with the playback sync of apps/web/src/components/ui/video-player.tsx @ 3037a7e. Local changes: one
// <video> of the job's source (GET /jobs/{id}/source) played through the edit's segments instead of
// one player per media element (reel time to source time via previewClock, a seek at each segment
// boundary, OpenCut's 0.5 s drift correction measured on the reel clock); the reel's 9:16 frame laid out
// as the render's reel_geometry (previewLayout); captions in the render's font and caption looks
// (caption-looks.json) with the render's active word; overlays at their x/y fractions; a result view of
// the rendered clip on its own clock, reporting its length (onResultDuration) when its metadata loads;
// no media, editor or project store, blur background, aspect-ratio
// menu, audio player, rotation, opacity or font classes. Licence: ../../LICENSE-OpenCut.
import "@/editor/editor.css";
import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties } from "react";
import { Pause, Play } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { Segment } from "@/lib/timelineEdit";
import { OVERLAY_CANVAS } from "@/editor/adapter";
import looks from "@/editor/caption-looks.json";
import {
  activeWordIndex,
  captionWords,
  CLOCK_EPS,
  continueAt,
  reelTimeAt,
  sourceTimeAt,
} from "@/editor/previewClock";
import {
  fontScale,
  reelGeometry,
  type ReelGeometry,
} from "@/editor/previewLayout";
import type { EditorElement, EditorTrack } from "@/editor/types";
import { formatTimeCode } from "@/editor/opencut/lib/time";
import { usePlaybackStore } from "@/editor/opencut/stores/playback-store";
import { useTimelineStore } from "@/editor/opencut/stores/timeline-store";

export interface PreviewPanelProps {
  /** `source`: the job's clean source through the edit, captions and text drawn on top; `result`: the rendered clip. */
  view: "source" | "result";
  jobId: string;
  clipId: string;
  /** Bumped on each ClipRendered for this clip: the result view asks for `?v={videoVersion}`, never a cached file. */
  videoVersion: number;
  /** The source cannot be played (not kept, refused, fails to load); the reason says why. */
  onUnavailable: (reason: string) => void;
  /** The rendered clip's length once its metadata loads (the playhead's clock in the result view). */
  onResultDuration?: (seconds: number) => void;
}

/** The render's default caption font (editor.css, a copy of app/assets/fonts/Anton-Regular.ttf). */
const CAPTION_FONT = '"Reelsmith Caption", Impact, sans-serif';
/** OpenCut video-player: the video is re-seeked when it is more than this far off the playhead. */
const DRIFT_SECONDS = 0.5;
/** Until the source's metadata arrives, lay the reel out for the usual 16:9 source. */
const DEFAULT_SOURCE_SIZE: [number, number] = [1920, 1080];
const FPS = 30;
/** subtitle_image_service: stroke 4 px, shadow offset (4, 6) blurred 4 px, black at alpha 200. */
const CAPTION_STROKE = 4;
const SHADOW = { x: 4, y: 6, blur: 4, color: "rgba(0, 0, 0, 0.784)" } as const;

type Look = (typeof looks)["default"];
type Rgba = Look["fill"];

/** A preset's look, the default look for null, an absent key or a name the render would not know. */
function lookOf(style: string | null | undefined): Look {
  return style && Object.hasOwn(looks, style)
    ? looks[style as keyof typeof looks]
    : looks.default;
}

const rgba = ([r, g, b, a]: Rgba) => `rgba(${r}, ${g}, ${b}, ${a / 255})`;
/** A percentage with at most four decimals (0.3 * 100 is 30.000000000000004). */
const pct = (fraction: number) => `${Math.round(fraction * 1e6) / 1e4}%`;
const px = (n: number) => `${Math.round(n * 100) / 100}px`;
const clamp01 = (n: number) => Math.min(1, Math.max(0, n));
const reelEnd = (el: EditorElement) =>
  el.startTime + el.duration - el.trimStart - el.trimEnd;
const isActiveAt = (el: EditorElement, t: number) =>
  t >= el.startTime && t < reelEnd(el);

/** The edit's segments in source seconds (a segment element is a window of the whole source). */
function segmentsOf(tracks: EditorTrack[]): Segment[] {
  const track = tracks.find((t) => t.role === "segments");
  return (track?.elements ?? []).map((el) => ({
    start: el.trimStart,
    end: el.duration - el.trimEnd,
  }));
}

function sourceErrorReason(error: MediaError | null): string {
  switch (error?.code) {
    case 2: // MEDIA_ERR_NETWORK
      return "The source video stopped loading (network error).";
    case 3: // MEDIA_ERR_DECODE
      return "The source video could not be decoded in this browser.";
    case 4: // MEDIA_ERR_SRC_NOT_SUPPORTED: a 404 or 409 answer lands here too
      return "The source video is not available: it is no longer kept, the server refused it, or this browser cannot play its format.";
    default:
      return "The source video could not be played.";
  }
}

/** Shows the frame under reel time `t`; past the reel, the last segment's last frame. */
function showReelTime(video: HTMLVideoElement, segments: Segment[], t: number) {
  const target = sourceTimeAt(segments, t);
  if (target !== null) video.currentTime = target;
  else if (segments.length > 0 && t > 0)
    video.currentTime = segments[segments.length - 1].end;
}

/** The fitted size of a `ratio` frame inside `el` (OpenCut's preview sizing), tracked as it resizes. */
function useFittedSize(
  ref: React.RefObject<HTMLDivElement | null>,
  ratio: number,
) {
  const [size, setSize] = useState({ width: 0, height: 0 });
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => {
      const { width, height } = el.getBoundingClientRect();
      if (!(width > 0 && height > 0)) return setSize({ width: 0, height: 0 });
      // Container wider than the frame: constrain by height, else by width.
      setSize(
        width / height > ratio
          ? { width: height * ratio, height }
          : { width, height: width / ratio },
      );
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, [ref, ratio]);
  return size;
}

export function PreviewPanel({
  view,
  jobId,
  clipId,
  videoVersion,
  onUnavailable,
  onResultDuration,
}: PreviewPanelProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [sourceSize, setSourceSize] = useState(DEFAULT_SOURCE_SIZE);
  const [resultDuration, setResultDuration] = useState(0);
  // A value key, so the video is re-seeked only when the cut changes, not on every caption edit.
  const segmentsKey = useTimelineStore((s) =>
    JSON.stringify(segmentsOf(s.tracks)),
  );
  const segments = useMemo<Segment[]>(
    () => JSON.parse(segmentsKey),
    [segmentsKey],
  );
  // To the millisecond, the edit's precision: the elements' duration - trimEnd arithmetic is not
  // exact (a 30 s cut sums to 29.999999999999993, which the floored time code reads as 29:29).
  const reelLength =
    Math.round(
      segments.reduce((sum, s) => sum + Math.max(0, s.end - s.start), 0) * 1000,
    ) / 1000;

  const geometry = reelGeometry(...sourceSize);
  const [canvasW, canvasH] = geometry.canvasSize;
  const frame = useFittedSize(containerRef, canvasW / canvasH);

  return (
    <div className="flex h-full min-h-0 w-full min-w-0 flex-col items-center gap-2 p-3">
      <div
        ref={containerRef}
        className="flex min-h-0 w-full min-w-0 flex-1 items-center justify-center"
      >
        <div
          className="relative overflow-hidden rounded-sm border bg-black"
          style={{ width: frame.width, height: frame.height }}
        >
          {view === "source" ? (
            <SourceView
              jobId={jobId}
              segments={segments}
              geometry={geometry}
              scale={fontScale(frame.width, geometry)}
              onSourceSize={setSourceSize}
              onUnavailable={onUnavailable}
            />
          ) : (
            <ResultView
              clipId={clipId}
              videoVersion={videoVersion}
              onDuration={(seconds) => {
                setResultDuration(seconds);
                onResultDuration?.(seconds);
              }}
            />
          )}
        </div>
      </div>
      <PreviewToolbar
        duration={view === "source" ? reelLength : resultDuration}
        disabled={view === "source" && segments.length === 0}
      />
    </div>
  );
}

function SourceView({
  jobId,
  segments,
  geometry,
  scale,
  onSourceSize,
  onUnavailable,
}: {
  jobId: string;
  segments: Segment[];
  geometry: ReelGeometry;
  scale: number;
  onSourceSize: (size: [number, number]) => void;
  onUnavailable: (reason: string) => void;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const { isPlaying, currentTime, volume, muted, speed } = usePlaybackStore();
  const muteSource = useTimelineStore(
    (s) => s.tracks.find((t) => t.role === "segments")?.muted === true,
  );
  const setSourceDuration = useTimelineStore((s) => s.setSourceDuration);
  const onReel = sourceTimeAt(segments, currentTime) !== null;

  // Store to video (OpenCut video-player): a seek shows its reel time at once; while playing,
  // the video is corrected when it drifts more than 0.5 s off the reel clock or sits in a cut part.
  // A new cut re-shows the frame under the playhead (a trim or split is visible immediately).
  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    showReelTime(video, segments, usePlaybackStore.getState().currentTime);

    const handleSeek = (e: Event) =>
      showReelTime(
        video,
        segments,
        (e as CustomEvent<{ time: number }>).detail.time,
      );
    const handleUpdate = (e: Event) => {
      const t = (e as CustomEvent<{ time: number }>).detail.time;
      const target = sourceTimeAt(segments, t);
      if (target === null) return video.pause();
      // Where the video continues (itself, or the next segment's start once it ran out of its
      // segment) and the reel time that is: far from the playhead, jump to the playhead; near it,
      // only leave a cut part forward, never back into the segment it just finished.
      const next = continueAt(segments, video.currentTime);
      const shown = next === null ? null : reelTimeAt(segments, next);
      if (
        next === null ||
        shown === null ||
        Math.abs(shown - t) > DRIFT_SECONDS
      )
        video.currentTime = target;
      else if (Math.abs(next - video.currentTime) > CLOCK_EPS)
        video.currentTime = next;
    };
    const handleSpeed = (e: Event) => {
      video.playbackRate = (e as CustomEvent<{ speed: number }>).detail.speed;
    };
    window.addEventListener("playback-seek", handleSeek);
    window.addEventListener("playback-update", handleUpdate);
    window.addEventListener("playback-speed", handleSpeed);
    return () => {
      window.removeEventListener("playback-seek", handleSeek);
      window.removeEventListener("playback-update", handleUpdate);
      window.removeEventListener("playback-speed", handleSpeed);
    };
  }, [segments]);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    if (isPlaying && onReel) void Promise.resolve(video.play()).catch(() => {});
    else video.pause();
  }, [isPlaying, onReel]);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    video.volume = volume;
    video.muted = muteSource || muted;
    video.playbackRate = speed;
  }, [volume, muted, muteSource, speed]);

  // At a segment's end the video jumps to the next segment's start, never showing the cut part;
  // where a split's halves meet it plays straight through (a seek to the same time still stalls).
  const handleTimeUpdate = () => {
    const video = videoRef.current;
    if (!video) return;
    const next = continueAt(segments, video.currentTime);
    if (next === null) video.pause();
    else if (Math.abs(next - video.currentTime) > CLOCK_EPS)
      video.currentTime = next;
  };

  const handleLoadedMetadata = () => {
    const video = videoRef.current;
    if (!video) return;
    if (video.videoWidth > 0 && video.videoHeight > 0)
      onSourceSize([video.videoWidth, video.videoHeight]);
    if (Number.isFinite(video.duration)) setSourceDuration(video.duration);
    showReelTime(video, segments, usePlaybackStore.getState().currentTime);
  };

  const canvasH = geometry.canvasSize[1];
  return (
    <>
      <video
        ref={videoRef}
        src={`/api/jobs/${encodeURIComponent(jobId)}/source`}
        preload="metadata"
        aria-label="Edit preview"
        className="absolute left-0 w-full object-fill"
        style={{
          top: pct(geometry.insetTop / canvasH),
          height: pct(geometry.insetHeight / canvasH),
        }}
        playsInline
        controls={false}
        disablePictureInPicture
        disableRemotePlayback
        onContextMenu={(e) => e.preventDefault()}
        onTimeUpdate={handleTimeUpdate}
        onLoadedMetadata={handleLoadedMetadata}
        onError={() =>
          onUnavailable(sourceErrorReason(videoRef.current?.error ?? null))
        }
      />
      <TextLayers t={currentTime} geometry={geometry} scale={scale} />
    </>
  );
}

function TextLayers({
  t,
  geometry,
  scale,
}: {
  t: number;
  geometry: ReelGeometry;
  scale: number;
}) {
  const tracks = useTimelineStore((s) => s.tracks);
  const look = lookOf(useTimelineStore((s) => s.captionStyle));
  const active = (role: EditorTrack["role"]) =>
    tracks
      .filter((track) => track.role === role)
      .flatMap((track) => track.elements.filter((el) => isActiveAt(el, t)));

  const captionStyle: CSSProperties = {
    top: pct(geometry.bandAnchorY / geometry.canvasSize[1]),
    // One unwrapped line centred on the frame (the render's x_start = (W - w) / 2): a line wider
    // than the frame is cut evenly on both sides, not only on the right.
    left: "50%",
    width: "max-content",
    transform: "translate(-50%, -50%)",
    fontFamily: CAPTION_FONT,
    fontSize: px(look.font_size * scale),
    WebkitTextStroke: `${px(2 * CAPTION_STROKE * scale)} ${rgba(look.stroke_fill)}`,
    paintOrder: "stroke fill",
    textShadow: `${px(SHADOW.x * scale)} ${px(SHADOW.y * scale)} ${px(SHADOW.blur * scale)} ${SHADOW.color}`,
  };

  return (
    // Text is React text nodes only: caption and overlay text never becomes markup (FR-014, threat T4).
    <div className="pointer-events-none absolute inset-0 text-white">
      {active("overlays").map((el) => {
        if (el.type !== "text") return null;
        const stroke = Math.max(1, Math.floor(el.fontSize / 24));
        return (
          <div
            key={el.id}
            data-testid="preview-overlay"
            className="absolute max-w-full whitespace-pre-wrap leading-tight"
            style={{
              left: pct(clamp01(el.x / OVERLAY_CANVAS.width + 0.5)),
              top: pct(clamp01(el.y / OVERLAY_CANVAS.height + 0.5)),
              transform: "translate(-50%, -50%)",
              fontFamily: CAPTION_FONT,
              fontSize: px(el.fontSize * scale),
              color: el.color,
              WebkitTextStroke: `${px(2 * stroke * scale)} #000`,
              paintOrder: "stroke fill",
            }}
          >
            {el.content}
          </div>
        );
      })}
      {active("captions").map((el) => {
        if (el.type !== "text") return null;
        const end = reelEnd(el);
        const words = captionWords(
          el.content,
          el.origin.words,
          el.startTime,
          end,
        );
        // The render draws nothing for a caption without words or time.
        if (words.length === 0 || !(end > el.startTime)) return null;
        const highlighted = activeWordIndex(words, end, t, look.highlights);
        return (
          <div
            key={el.id}
            data-testid="preview-caption"
            className="absolute whitespace-nowrap text-center uppercase leading-none"
            style={captionStyle}
          >
            {words.map((word, i) => (
              <Fragment key={i}>
                {i > 0 ? " " : null}
                <span
                  style={{
                    color: rgba(
                      i === highlighted ? look.highlight_fill : look.fill,
                    ),
                  }}
                >
                  {word.text}
                </span>
              </Fragment>
            ))}
          </div>
        );
      })}
    </div>
  );
}

function ResultView({
  clipId,
  videoVersion,
  onDuration,
}: {
  clipId: string;
  videoVersion: number;
  onDuration: (duration: number) => void;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const { isPlaying, volume, muted, speed } = usePlaybackStore();
  const reportDuration = () => {
    const d = videoRef.current?.duration;
    if (d !== undefined && Number.isFinite(d) && d > 0) onDuration(d);
  };

  // The rendered file is its own clock: a store seek moves it, and its time moves the playhead.
  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    video.currentTime = usePlaybackStore.getState().currentTime;
    const handleSeek = (e: Event) => {
      video.currentTime = (e as CustomEvent<{ time: number }>).detail.time;
    };
    window.addEventListener("playback-seek", handleSeek);
    return () => window.removeEventListener("playback-seek", handleSeek);
  }, []);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    if (isPlaying) void Promise.resolve(video.play()).catch(() => {});
    else video.pause();
  }, [isPlaying]);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    video.volume = volume;
    video.muted = muted;
    video.playbackRate = speed;
  }, [volume, muted, speed]);

  return (
    <video
      ref={videoRef}
      src={`/api/clips/${encodeURIComponent(clipId)}/video${videoVersion > 0 ? `?v=${videoVersion}` : ""}`}
      preload="metadata"
      aria-label="Rendered clip"
      className="absolute inset-0 h-full w-full object-contain"
      playsInline
      controls={false}
      disablePictureInPicture
      disableRemotePlayback
      onContextMenu={(e) => e.preventDefault()}
      onTimeUpdate={() => {
        const video = videoRef.current;
        if (video)
          usePlaybackStore.getState().setCurrentTime(video.currentTime);
      }}
      onEnded={() => usePlaybackStore.getState().pause()}
      onLoadedMetadata={reportDuration}
      onDurationChange={reportDuration}
    />
  );
}

function PreviewToolbar({
  duration,
  disabled,
}: {
  duration: number;
  disabled: boolean;
}) {
  const isPlaying = usePlaybackStore((s) => s.isPlaying);
  const currentTime = usePlaybackStore((s) => s.currentTime);
  const toggle = usePlaybackStore((s) => s.toggle);
  return (
    <div
      data-toolbar
      className="flex w-full items-center justify-between gap-2 px-1 text-xs text-muted-foreground"
    >
      <p className="flex items-center gap-1 tabular-nums">
        <span className="text-foreground">
          {formatTimeCode(currentTime, "HH:MM:SS:FF", FPS)}
        </span>
        <span className="opacity-50">/</span>
        <span>{formatTimeCode(duration, "HH:MM:SS:FF", FPS)}</span>
      </p>
      <Button
        variant="ghost"
        size="icon-sm"
        onClick={toggle}
        disabled={disabled}
        aria-label={isPlaying ? "Pause" : "Play"}
      >
        {isPlaying ? <Pause /> : <Play />}
      </Button>
    </div>
  );
}
