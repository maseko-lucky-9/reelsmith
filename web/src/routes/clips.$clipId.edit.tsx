import { createRoute, useNavigate } from "@tanstack/react-router";
import { useQuery, useMutation } from "@tanstack/react-query";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import {
  AlignLeft,
  ArrowLeft,
  Download,
  Mic,
  RefreshCw,
  Scissors,
  Type,
  type LucideIcon,
} from "lucide-react";
import { toast } from "sonner";
import { rootRoute } from "./root";
import { api } from "@/api/client";
import type { TimelinePayload, VoiceoverRequest } from "@/api/client";
import { apiErrorDetail, apiErrorStatus } from "@/lib/apiErrorDetail";
import type { SourceWindow } from "@/lib/timelineEdit";
import { BrollCredits } from "@/components/broll-credits";
import {
  CaptionsPanel,
  TextPanel,
  TrimPanel,
  VoiceoverPanel,
} from "@/components/editor/EditorToolPanels";
import "@/editor/editor.css";
import {
  cutOf,
  renderedCut,
  splitGate,
  type PreviewView,
  type SplitGate,
} from "@/editor/renderedCut";
import { useClipEdit } from "@/editor/useClipEdit";
import { PreviewPanel } from "@/editor/opencut/components/preview-panel";
import { PropertiesPanel } from "@/editor/opencut/components/properties-panel";
import { Timeline } from "@/editor/opencut/components/timeline";
import {
  SPLIT_OPEN,
  toastRefusal,
} from "@/editor/opencut/hooks/use-playback-controls";
import { usePlaybackStore } from "@/editor/opencut/stores/playback-store";
import { useTimelineStore } from "@/editor/opencut/stores/timeline-store";

export const clipEditRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/clips/$clipId/edit",
  component: ClipEditorPage,
  // Another clip is another editor: the page remounts (TanStack Router keeps a
  // component across a param change), so no route state (the event cut, the
  // re-render lock, the video version, the view, a pending mutation) carries over.
  remountDeps: ({ params }) => params,
});

type ToolId = "captions" | "text" | "voiceover" | "trim";

// Only tools the render applies (precedent T013: the editor does not
// advertise features that do nothing). Reframe is the server's
// YTVIDEO_REFRAME_PROVIDER, not a per-clip setting, so it has no tool here.
const RIGHT_TOOLS: Array<{ id: ToolId; label: string; icon: LucideIcon }> = [
  { id: "captions", label: "Captions", icon: AlignLeft },
  { id: "text", label: "Text", icon: Type },
  { id: "voiceover", label: "Voice-over", icon: Mic },
  { id: "trim", label: "Trim", icon: Scissors },
];

type Word = { word: string; start: number; end: number };

function formatVideoTime(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  const cs = Math.floor((seconds % 1) * 100);
  return `${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}.${cs.toString().padStart(2, "0")}`;
}

/** Shown when the server had no Piper voice and wrote the stub's silent track. */
const STUB_VOICEOVER_WARNING =
  "Voice-over added as a silent placeholder: no Piper voice is installed on the server. " +
  "Run `python -m scripts.fetch_piper_voice` there, then generate it again.";

/** The job records no source path: retention removed it (spec 001 FR-013), so GET /jobs/{id}/source is a 404. */
const SOURCE_NOT_KEPT = "The source video is no longer kept on the server.";

/** A voice-over request's failure as shown in its panel. */
function voiceoverErrorMessage(err: unknown): string {
  const status = apiErrorStatus(err);
  const detail = apiErrorDetail(err);
  const reason = detail ? `: ${detail}` : "";
  // The server re-checks the edit's version after synthesis (lost update).
  if (status === 409 && detail?.startsWith("the clip's edit changed"))
    return "The edit changed while the voice-over was being generated, so it was not added. Reload the page and try again (409).";
  if (status === 409) return `Cannot add a voice-over (409)${reason}`;
  if (status === 503 && detail?.startsWith("voice-over busy"))
    return "The server is busy making other voice-overs. Try again in a moment (503).";
  if (status === 503)
    return `Voice-over is unavailable on the server (503)${reason}`;
  if (status === 502) return `The voice engine failed (502)${reason}`;
  if (status !== null) return `Voice-over failed (${status})${reason}`;
  return `Voice-over failed: ${String(err)}`;
}

/** A fresh, stopped playhead: the playback store is shared (one editor at a time). */
function resetPlayback() {
  const playback = usePlaybackStore.getState();
  playback.pause();
  playback.setCurrentTime(0);
}

/** Renders its children with the playhead, so only they follow every frame of playback, not the page. */
function WithPlayhead({
  children,
}: {
  children: (playhead: number) => ReactNode;
}) {
  const playhead = usePlaybackStore((s) => s.currentTime);
  return <>{children(playhead)}</>;
}

function CaptionRow({
  index,
  start,
  end,
  text,
  active,
  onSeek,
  onCommit,
}: {
  index: number;
  start: number;
  end: number;
  text: string;
  active: boolean;
  onSeek: () => void;
  onCommit: (text: string) => void;
}) {
  return (
    <li
      className={`flex items-start gap-2 rounded px-1 py-0.5 ${active ? "bg-white/10" : ""}`}
    >
      <button
        type="button"
        onClick={onSeek}
        title={`Jump to ${formatVideoTime(start)}`}
        className="mt-1 font-mono text-[10px] text-zinc-500 hover:text-white"
      >
        {formatVideoTime(start)}
      </button>
      <input
        key={text}
        aria-label={`Caption ${index + 1}`}
        title={`${formatVideoTime(start)}–${formatVideoTime(end)}`}
        defaultValue={text}
        maxLength={500}
        onBlur={(e) => {
          if (e.target.value !== text) onCommit(e.target.value);
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter") e.currentTarget.blur();
        }}
        className="flex-1 bg-transparent text-sm text-zinc-200 border-b border-transparent hover:border-white/15 focus:border-white/40 focus:outline-none"
      />
    </li>
  );
}

interface CaptionLine {
  trackId: string;
  id: string;
  /** Reel seconds. */
  start: number;
  end: number;
  text: string;
}

/**
 * The transcript: the edit's captions (text edited through the timeline
 * store, seek on the time), else the clip's words. A caption is highlighted
 * only while the playhead is a time on the edited reel (FR-017).
 */
function TranscriptPanel({
  words,
  playheadOnEditClock,
}: {
  words: Word[];
  playheadOnEditClock: boolean;
}) {
  const tracks = useTimelineStore((s) => s._tracks);
  const captions = useMemo<CaptionLine[]>(
    () =>
      tracks
        .filter((t) => t.role === "captions")
        .flatMap((t) =>
          t.elements.flatMap((el) =>
            el.type === "text"
              ? [
                  {
                    trackId: t.id,
                    id: el.id,
                    start: el.startTime,
                    end: el.startTime + el.duration - el.trimStart - el.trimEnd,
                    text: el.content,
                  },
                ]
              : [],
          ),
        ),
    [tracks],
  );
  // Indexes, not the time: the list re-renders when the active row changes, not every frame.
  const activeCaption = usePlaybackStore((s) =>
    playheadOnEditClock
      ? captions.findIndex(
          (c) => s.currentTime >= c.start && s.currentTime < c.end,
        )
      : -1,
  );
  const activeWord = usePlaybackStore((s) =>
    captions.length > 0
      ? -1
      : words.findIndex(
          (w) => s.currentTime >= w.start && s.currentTime < w.end,
        ),
  );
  const seek = (t: number) => usePlaybackStore.getState().seek(t);

  if (captions.length > 0) {
    return (
      <ol aria-label="Captions" className="space-y-1">
        {captions.map((c, i) => (
          <CaptionRow
            key={c.id}
            index={i}
            start={c.start}
            end={c.end}
            text={c.text}
            active={i === activeCaption}
            onSeek={() => seek(c.start)}
            onCommit={(text) =>
              toastRefusal(
                useTimelineStore
                  .getState()
                  .updateTextElement(c.trackId, c.id, { content: text }),
              )
            }
          />
        ))}
      </ol>
    );
  }
  if (words.length > 0) {
    return (
      <div className="text-sm leading-relaxed">
        {words.map((w, i) => (
          <span
            key={i}
            onClick={() => seek(w.start)}
            className={`cursor-pointer rounded px-0.5 transition-colors ${
              activeWord === i
                ? "text-black font-medium"
                : "text-zinc-300 hover:text-white"
            }`}
            style={
              activeWord === i
                ? { backgroundColor: "var(--score-green)" }
                : undefined
            }
          >
            {w.word}{" "}
          </span>
        ))}
      </div>
    );
  }
  return <p className="text-zinc-500 text-xs">No transcript available.</p>;
}

function ViewButton({
  pressed,
  disabled = false,
  title,
  onClick,
  children,
}: {
  pressed: boolean;
  disabled?: boolean;
  title: string;
  onClick: () => void;
  children: string;
}) {
  return (
    <button
      type="button"
      aria-pressed={pressed}
      disabled={disabled}
      title={title}
      onClick={onClick}
      className={`rounded px-2 py-1 text-xs transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
        pressed ? "bg-white/15 text-white" : "text-zinc-400 hover:text-white"
      }`}
    >
      {children}
    </button>
  );
}

export function ClipEditorPage() {
  const { clipId } = clipEditRoute.useParams();
  const navigate = useNavigate();

  const [transcriptOnly, setTranscriptOnly] = useState(false);
  const [regenerateCopy, setRegenerateCopy] = useState(true);
  const [activeTool, setActiveTool] = useState<ToolId | null>(null);
  // The preview's clock: the clean source played through the edit (the
  // playhead is a time on the edited reel), or the rendered clip.
  const [view, setView] = useState<PreviewView>("source");
  // Why this clip's source cannot be played (the preview failed to load it).
  const [sourceError, setSourceError] = useState<{
    clipId: string;
    reason: string;
  } | null>(null);
  // The rendered clip's length: the playhead's clock in the result view.
  const [resultDuration, setResultDuration] = useState<number | null>(null);

  const clipsQuery = useQuery({
    queryKey: ["clips"],
    queryFn: () => api.listClips(),
  });
  const clip = (clipsQuery.data ?? []).find((c) => c.clip_id === clipId);
  const clipJobId = clip?.job_id;

  // The job's recorded duration bounds a segment extension until the
  // source's metadata loads; no recorded source path means it is gone.
  const jobQuery = useQuery({
    queryKey: ["job", clipJobId],
    queryFn: () => api.getJob(clipJobId ?? ""),
    enabled: Boolean(clipJobId),
  });
  const job = jobQuery.data;

  const clipStart = clip?.start;
  const clipEnd = clip?.end;
  const sourceWindow = useMemo<SourceWindow | null>(
    () =>
      typeof clipStart === "number" &&
      typeof clipEnd === "number" &&
      clipEnd > clipStart &&
      clipStart >= 0
        ? { start: clipStart, end: clipEnd }
        : null,
    [clipStart, clipEnd],
  );
  const editor = useClipEdit(clipId, {
    sourceWindow,
    sourceDuration: job?.duration ?? null,
  });

  useEffect(() => {
    resetPlayback();
    return resetPlayback;
  }, [clipId]);

  // The cut the clip's file was rendered from: the server's
  // (`clip.rendered_segments`; the clip's own window when none), replaced by
  // the one a ClipRendered event of this clip carries.
  const [eventCut, setEventCut] = useState<{
    segments: ReadonlyArray<readonly [number, number]> | null;
  } | null>(null);
  // UX only (correctness is the cut comparison): true from a 202 until this
  // clip's ClipRendered / ClipRerenderFailed; the controls that would save or
  // queue another change wait.
  const [rerenderInFlight, setRerenderInFlight] = useState(false);
  // Bumped when the clip's file is re-rendered: busts the rendered clip's cache.
  const [videoVersion, setVideoVersion] = useState(0);
  const [rerenderError, setRerenderError] = useState<string | null>(null);

  // A re-render of a completed job is reported on its live event stream
  // (no replay: the job's old events are not for this page).
  useEffect(() => {
    if (!clipJobId || typeof EventSource === "undefined") return;
    const source = new EventSource(
      `/api/jobs/${encodeURIComponent(clipJobId)}/events?live=true`,
    );
    const payloadOf = (e: Event): Record<string, unknown> | null => {
      try {
        const parsed = JSON.parse((e as MessageEvent).data as string) as {
          payload?: Record<string, unknown>;
        };
        return parsed.payload && parsed.payload.clip_id === clipId
          ? parsed.payload
          : null;
      } catch {
        return null;
      }
    };
    const onRendered = (e: Event) => {
      const payload = payloadOf(e);
      if (!payload) return;
      const segs = payload.segments;
      setEventCut({
        segments: Array.isArray(segs)
          ? (segs as Array<[number, number]>)
          : null,
      });
      setRerenderInFlight(false);
      setRerenderError(null);
      setVideoVersion((v) => v + 1);
      toast.success("Re-render finished");
    };
    const onFailed = (e: Event) => {
      const payload = payloadOf(e);
      if (!payload) return;
      setRerenderInFlight(false);
      const error =
        typeof payload.error === "string" && payload.error
          ? payload.error
          : "unknown error";
      setRerenderError(`Re-render failed: ${error}`);
      toast.error(`Re-render failed: ${error}`);
    };
    source.addEventListener("ClipRendered", onRendered);
    source.addEventListener("ClipRerenderFailed", onFailed);
    return () => source.close();
  }, [clipJobId, clipId]);

  // The clean-source view needs the job's source; without it the preview
  // falls back to the rendered clip and says why (FR-013).
  const sourceFailed =
    sourceError?.clipId === clipId ? sourceError.reason : null;
  const sourceUnavailable =
    sourceFailed ?? (job && !job.video_path ? SOURCE_NOT_KEPT : null);
  const previewView: PreviewView = sourceUnavailable ? "result" : view;

  // Split at the playhead (toolbar, S key, context menu, Trim panel) and the
  // transcript highlight wait while the playhead is the rendered file's clock
  // and the edit's segments are not the cut that file was rendered from
  // (FR-017; data-model §2 playheadOnEditClock). The clean-source view's
  // playhead is the edit's own clock.
  const loadedTimeline = editor.loadedTimeline ?? null;
  const rendered = eventCut
    ? renderedCut(eventCut.segments, sourceWindow)
    : clip
      ? renderedCut(clip.rendered_segments, sourceWindow)
      : null;
  const baseline =
    rendered ?? (loadedTimeline ? cutOf(loadedTimeline, sourceWindow) : null);
  const gateNow = splitGate({
    view: previewView,
    edit: cutOf(editor.timeline, sourceWindow),
    baseline,
    rerenderInFlight,
  });
  const gateReason = gateNow.ok ? null : gateNow.reason;
  // A stable gate while its outcome holds (the keyboard map re-binds on a new one).
  const gate = useMemo<SplitGate>(
    () =>
      gateReason === null ? SPLIT_OPEN : { ok: false, reason: gateReason },
    [gateReason],
  );

  // A selected caption or text overlay opens its properties in place of the tool panel.
  const hasSelection = useTimelineStore((s) => s.selectedElements.length > 0);
  // The source's length when known (data-model §3): bounds a typed segment end.
  const probedDuration = useTimelineStore((s) => s.probedDuration);
  const knownSourceDuration = probedDuration ?? job?.duration ?? null;

  const rerenderMutation = useMutation({
    mutationFn: () =>
      api.rerenderClip(clipId, {
        reframe_provider: "letterbox",
        regenerate_copy: regenerateCopy,
      }),
    // 202 only means queued: the rendered clip stays the old cut until this
    // clip's ClipRendered event arrives (above), so the rendered cut is not advanced here.
    onSuccess: () => {
      setRerenderInFlight(true);
      setRerenderError(null);
      toast.success("Re-render queued");
    },
    onError: (err) => {
      const detail = apiErrorDetail(err);
      toast.error(
        detail ? `Cannot re-render: ${detail}` : "Failed to queue re-render",
      );
    },
  });

  // The render reads the saved edit, so pending changes are saved first.
  const rerender = async () => {
    if (editor.isDirty) {
      try {
        await editor.save();
      } catch (e) {
        toast.error(`Save failed: ${apiErrorDetail(e) ?? String(e)}`);
        return;
      }
    }
    rerenderMutation.mutate();
  };

  const voiceoverMutation = useMutation({
    mutationFn: async (body: VoiceoverRequest) => {
      // The server appends to the saved edit and refuses a saved v1 one:
      // save local changes (as v2) first. The editor's edit is always v2,
      // so whether the saved row is v1 comes from the hook.
      if (editor.isDirty || editor.savedIsV1) await editor.save();
      return api.createVoiceover(clipId, body);
    },
    onSuccess: (res) => {
      // The answer can come after this editor closed (Piper takes up to
      // 120 s): it is then this clip's saved edit, not news for the next page.
      if (!editor.applyServerState(res.edit)) return;
      if (res.provider === "stub") toast.warning(STUB_VOICEOVER_WARNING);
      else toast.success("Voice-over added");
    },
  });

  const transcriptWords =
    clip?.transcript &&
    Array.isArray((clip.transcript as Record<string, unknown>).words)
      ? ((clip.transcript as Record<string, unknown>).words as Word[])
      : null;
  const editorWords = editor.words;
  const words: Word[] = useMemo(
    () =>
      editorWords && editorWords.length > 0
        ? editorWords
        : (transcriptWords ?? []),
    [editorWords, transcriptWords],
  );

  if (clipsQuery.isLoading) {
    return (
      <div className="flex items-center justify-center h-full text-zinc-400">
        Loading…
      </div>
    );
  }
  if (!clip) {
    return (
      <div className="flex items-center justify-center h-full text-red-400">
        Clip not found.
      </div>
    );
  }

  // A whole v2 timeline from a 016 tool panel: one undoable step (a
  // read-only edit refuses it with the reason).
  const applyTimeline = (next: TimelinePayload) => {
    toastRefusal(editor.setTimeline(next));
  };

  // A voice-over saves first and the server appends to the saved edit: a save
  // or re-render meanwhile would race it.
  const voiceoverPending = voiceoverMutation.isPending;
  const busy =
    editor.isSaving ||
    rerenderMutation.isPending ||
    voiceoverPending ||
    rerenderInFlight;

  return (
    <div
      className="flex flex-col h-full -mx-6 -my-6 overflow-hidden"
      style={{ height: "calc(100vh - 48px - 32px)" }}
    >
      {/* Editor top bar (undo/redo live in the timeline's toolbar) */}
      <div
        className="flex items-center gap-3 px-4 py-2 border-b border-white/8 flex-shrink-0"
        style={{ background: "var(--sidebar-bg)" }}
      >
        <button
          onClick={() =>
            void navigate({
              to: "/jobs/$jobId",
              params: { jobId: clip.job_id },
            })
          }
          aria-label="Back to the job"
          className="p-1.5 rounded-md text-zinc-400 hover:text-white hover:bg-white/10 transition-colors"
        >
          <ArrowLeft className="w-4 h-4" />
        </button>

        <span className="text-sm text-white font-medium truncate max-w-[200px]">
          {clip.title ?? "Untitled clip"}
        </span>

        <div className="flex-1" />

        <button
          type="button"
          disabled={
            !editor.isDirty ||
            editor.isSaving ||
            voiceoverPending ||
            rerenderInFlight
          }
          title={
            voiceoverPending
              ? "Wait for the voice-over to finish"
              : rerenderInFlight
                ? "Wait for the re-render to finish"
                : undefined
          }
          onClick={() =>
            editor.save().then(
              () => toast.success("Saved"),
              (e) =>
                toast.error(`Save failed: ${apiErrorDetail(e) ?? String(e)}`),
            )
          }
          className="px-3 py-1.5 rounded-lg border border-white/20 text-sm text-zinc-300 hover:text-white hover:border-white/40 transition-colors disabled:opacity-60 disabled:cursor-not-allowed"
        >
          {editor.isSaving
            ? "Saving…"
            : editor.isDirty
              ? "Save changes"
              : "Saved"}
        </button>

        <button
          type="button"
          disabled={busy}
          onClick={() => void rerender()}
          title="Save the edit and render the clip with it"
          className="px-3 py-1.5 rounded-lg border border-white/20 text-sm text-zinc-300 hover:text-white hover:border-white/40 transition-colors disabled:opacity-60 disabled:cursor-not-allowed flex items-center gap-1.5"
        >
          <RefreshCw className="w-3.5 h-3.5" />
          Re-render
        </button>
        {rerenderInFlight ? (
          <span role="status" className="text-xs text-zinc-400">
            Re-render in progress
          </span>
        ) : null}

        <a
          href={`/api/clips/${clip.clip_id}/video`}
          download
          className="px-3 py-1.5 rounded-lg bg-white text-black text-sm font-semibold hover:bg-zinc-100 transition-colors flex items-center gap-1.5"
        >
          <Download className="w-3.5 h-3.5" />
          Export
        </a>
      </div>

      {/* Main editor area */}
      <div className="flex flex-1 min-h-0 overflow-hidden">
        {/* Left transcript panel */}
        <div
          className="w-80 flex-shrink-0 overflow-y-auto border-r border-white/8 p-4 space-y-4"
          style={{ background: "var(--sidebar-bg)" }}
        >
          <label className="flex items-center gap-2 text-sm text-zinc-300 cursor-pointer">
            <input
              type="checkbox"
              checked={transcriptOnly}
              onChange={(e) => setTranscriptOnly(e.target.checked)}
              className="accent-white"
            />
            Transcript only
          </label>

          <TranscriptPanel words={words} playheadOnEditClock={gate.ok} />

          <label className="flex items-center gap-2 text-xs text-zinc-300 cursor-pointer">
            <input
              type="checkbox"
              checked={regenerateCopy}
              onChange={(e) => setRegenerateCopy(e.target.checked)}
              className="accent-white"
            />
            Regenerate title, summary and hashtags
          </label>

          <button
            onClick={() => void rerender()}
            disabled={busy}
            className="text-xs text-zinc-400 border border-white/10 px-3 py-1.5 rounded-md hover:text-white hover:border-white/30 transition-colors disabled:opacity-60"
          >
            {rerenderMutation.isPending ? "Queuing…" : "+ Regenerate captions"}
          </button>

          <BrollCredits
            assets={clip.broll_assets}
            className="pt-4 border-t border-border"
          />
        </div>

        {/* Center preview: the edit on the clean source, or the rendered clip */}
        <div className="flex min-w-0 flex-1 flex-col overflow-hidden bg-black">
          <div className="flex items-center gap-2 px-3 pt-2">
            <div
              role="group"
              aria-label="Preview"
              className="flex gap-0.5 rounded-md border border-white/15 p-0.5"
            >
              <ViewButton
                pressed={previewView === "source"}
                disabled={sourceUnavailable !== null}
                title={
                  sourceUnavailable
                    ? sourceUnavailable
                    : "The source played through the edit, captions and text drawn on top"
                }
                onClick={() => setView("source")}
              >
                Edit preview
              </ViewButton>
              <ViewButton
                pressed={previewView === "result"}
                title="The clip as it was last rendered"
                onClick={() => setView("result")}
              >
                Rendered clip
              </ViewButton>
            </div>
          </div>
          {sourceUnavailable ? (
            <p
              role="note"
              className="mx-3 mt-2 rounded border border-amber-500/40 bg-amber-950/80 px-2 py-1 text-xs text-amber-100"
            >
              Showing the rendered clip. {sourceUnavailable}
              {sourceFailed && !(job && !job.video_path) ? (
                <>
                  {" "}
                  {/* A failed load (network, a refused request) may pass on a second try. */}
                  <button
                    type="button"
                    onClick={() => {
                      setSourceError(null);
                      setView("source");
                    }}
                    className="underline underline-offset-2 hover:text-white"
                  >
                    Try the source again
                  </button>
                </>
              ) : null}
            </p>
          ) : null}
          <div className="relative min-h-0 flex-1">
            {previewView === "result" && !clip.output_path ? (
              <div className="flex h-full items-center justify-center">
                <p className="text-zinc-500 text-sm">Video not available</p>
              </div>
            ) : (
              <PreviewPanel
                view={previewView}
                jobId={clip.job_id}
                clipId={clip.clip_id}
                videoVersion={videoVersion}
                onUnavailable={(reason) => setSourceError({ clipId, reason })}
                onResultDuration={setResultDuration}
              />
            )}
            {rerenderError ? (
              <p
                role="alert"
                className="absolute bottom-3 left-3 right-3 rounded bg-red-950/90 px-2 py-1 text-xs text-red-200 border border-red-500/40"
              >
                {rerenderError}
              </p>
            ) : null}
          </div>
        </div>

        {/* Properties of the selection, else the active tool panel */}
        {hasSelection ? (
          <div
            className="w-72 flex-shrink-0 overflow-hidden border-l border-white/8"
            style={{ background: "var(--sidebar-bg)" }}
          >
            <PropertiesPanel />
          </div>
        ) : activeTool ? (
          <div
            key={activeTool}
            className="w-72 flex-shrink-0 overflow-y-auto border-l border-white/8 p-4"
            style={{ background: "var(--sidebar-bg)" }}
          >
            {activeTool === "captions" ? (
              <CaptionsPanel
                timeline={editor.timeline}
                onChange={applyTimeline}
              />
            ) : (
              <WithPlayhead>
                {(playhead) =>
                  activeTool === "text" ? (
                    <TextPanel
                      timeline={editor.timeline}
                      onChange={applyTimeline}
                      playhead={playhead}
                    />
                  ) : activeTool === "voiceover" ? (
                    <VoiceoverPanel
                      timeline={editor.timeline}
                      onChange={applyTimeline}
                      playhead={playhead}
                      defaultText={clip.ai_hook_text ?? ""}
                      pending={voiceoverMutation.isPending}
                      locked={rerenderInFlight}
                      error={
                        voiceoverMutation.error
                          ? voiceoverErrorMessage(voiceoverMutation.error)
                          : null
                      }
                      onGenerate={(req) => voiceoverMutation.mutate(req)}
                    />
                  ) : (
                    <TrimPanel
                      timeline={editor.timeline}
                      onChange={applyTimeline}
                      playhead={playhead}
                      sourceWindow={sourceWindow}
                      sourceDuration={knownSourceDuration}
                      playheadStale={!gate.ok}
                      playheadStaleReason={gate.ok ? undefined : gate.reason}
                    />
                  )
                }
              </WithPlayhead>
            )}
          </div>
        ) : null}

        {/* Right tools sidebar */}
        <div
          className="w-16 flex-shrink-0 flex flex-col items-center gap-3 py-4 border-l border-white/8"
          style={{ background: "var(--sidebar-bg)" }}
          role="toolbar"
          aria-label="Editor tools"
          aria-orientation="vertical"
        >
          {RIGHT_TOOLS.map(({ id, label, icon: Icon }) => {
            const pressed = !hasSelection && activeTool === id;
            return (
              <button
                key={id}
                type="button"
                title={label}
                aria-label={label}
                aria-pressed={pressed}
                onClick={() => {
                  // A tool replaces the properties of the selection.
                  if (hasSelection) {
                    useTimelineStore.getState().clearSelectedElements();
                    setActiveTool(id);
                  } else {
                    setActiveTool((cur) => (cur === id ? null : id));
                  }
                }}
                className={`p-2 rounded-lg transition-colors ${
                  pressed
                    ? "text-white bg-white/15"
                    : "text-zinc-400 hover:text-white hover:bg-white/10"
                }`}
              >
                <Icon className="w-5 h-5" />
              </button>
            );
          })}
        </div>
      </div>

      {/* Bottom timeline (OpenCut): needs a parent with a set height */}
      <div
        className="h-64 flex-shrink-0 border-t border-white/8"
        style={{ background: "var(--sidebar-bg)" }}
      >
        <Timeline
          splitGate={gate}
          keyboard
          playbackDuration={
            previewView === "result" && resultDuration !== null
              ? resultDuration
              : undefined
          }
        />
      </div>
    </div>
  );
}
