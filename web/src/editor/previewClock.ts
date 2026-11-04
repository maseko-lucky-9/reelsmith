/**
 * The clean-source preview's clock (spec 017 FR-013, plan §7): the edited
 * reel plays the source's segments back to back, so a reel time maps to a
 * source time through them, and playback seeks the source at each boundary.
 *
 * Reel to source is 016's `timelineEdit.sourceAt` (a boundary within 1 ms
 * belongs to the segment it starts; the reel's last instant shows the last
 * segment's end; past the reel is null); the other helpers use the same rule.
 * The caption helpers mirror what the render draws (timeline_render_service
 * `_caption_words`, render_service `plan_caption_entries`).
 */
import type { TimelinePayload } from "@/api/client";
import { sourceAt, type Segment } from "@/lib/timelineEdit";

/** Two times this close are one instant (timelineEdit's EPS, the server's 1 ms). */
export const CLOCK_EPS = 1e-3;

/** A segment placed on the reel: it plays from reel time `reel` for `len` seconds. */
interface Piece {
  index: number;
  reel: number;
  start: number;
  end: number;
  len: number;
}

function piecesOf(segments: readonly Segment[]): Piece[] {
  let reel = 0;
  return segments.map((s, index) => {
    const len = Math.max(0, s.end - s.start);
    const piece = { index, reel, start: s.start, end: s.end, len };
    reel += len;
    return piece;
  });
}

/** timelineEdit's `pieceAtReel`: the piece playing at `t`, else the one ending at `t`. */
function pieceAtReel(pieces: Piece[], t: number): Piece | null {
  return (
    pieces.find(
      (p) => t >= p.reel - CLOCK_EPS && t < p.reel + p.len - CLOCK_EPS,
    ) ??
    pieces.find((p) => Math.abs(t - (p.reel + p.len)) <= CLOCK_EPS) ??
    null
  );
}

/** The source time under reel time `t`; null past the reel (or before it, or with no segment). */
export function sourceTimeAt(
  segments: readonly Segment[],
  t: number,
): number | null {
  const timeline: TimelinePayload = {
    schema_version: 2,
    tracks: [
      {
        kind: "video",
        items: segments.map((s) => ({ start: s.start, end: s.end })),
      },
    ],
  };
  return sourceAt(timeline, t);
}

/** The index of the segment playing at reel time `t`; null outside the reel. */
export function segmentIndexAt(
  segments: readonly Segment[],
  t: number,
): number | null {
  return pieceAtReel(piecesOf(segments), t)?.index ?? null;
}

export interface Boundary {
  /** The segment playing at the reel time asked about. */
  index: number;
  /** Where that segment ends on the reel. */
  reel: number;
  /** Where it ends in the source. */
  sourceEnd: number;
  /** The source time playback continues at: the next segment's start, null at the reel's end. */
  continueAt: number | null;
}

/** The end of the segment playing at reel time `t` and where playback goes next; null outside the reel. */
export function nextBoundary(
  segments: readonly Segment[],
  t: number,
): Boundary | null {
  const pieces = piecesOf(segments);
  const p = pieceAtReel(pieces, t);
  if (!p) return null;
  const next = pieces[p.index + 1];
  return {
    index: p.index,
    reel: p.reel + p.len,
    sourceEnd: p.end,
    continueAt: next ? next.start : null,
  };
}

/** The reel time showing source time `s`; null when `s` is outside every segment (a cut part). */
export function reelTimeAt(
  segments: readonly Segment[],
  s: number,
): number | null {
  const pieces = piecesOf(segments);
  const p =
    pieces.find((q) => s >= q.start - CLOCK_EPS && s < q.end - CLOCK_EPS) ??
    pieces.find((q) => Math.abs(s - q.end) <= CLOCK_EPS);
  return p ? p.reel + Math.min(Math.max(s - p.start, 0), p.len) : null;
}

/**
 * Where a video at source time `s` should be for the edit: `s` itself inside
 * a segment (also where a split's two halves meet); the next segment's start
 * once it reached a segment's end before a cut part, or sits in a cut part
 * (before the first segment, between two); null past the last segment.
 */
export function continueAt(
  segments: readonly Segment[],
  s: number,
): number | null {
  const reel = reelTimeAt(segments, s);
  if (reel !== null) return sourceTimeAt(segments, reel);
  return segments.find((seg) => seg.start > s)?.start ?? null;
}

export interface CaptionWord {
  text: string;
  start: number;
  end: number;
}

const finite = (value: unknown, fallback: number): number =>
  typeof value === "number" && Number.isFinite(value) ? value : fallback;

/**
 * The words the render draws for a caption (`_caption_words`): the text's
 * words; when there are as many as `rawWords`, each keeps its timing clamped
 * to the caption, otherwise they are spread evenly over it.
 */
export function captionWords(
  text: string,
  rawWords: unknown,
  start: number,
  end: number,
): CaptionWord[] {
  const tokens = text.split(/\s+/).filter(Boolean);
  if (tokens.length === 0) return [];
  const raw = Array.isArray(rawWords) ? rawWords : [];
  if (tokens.length === raw.length) {
    return tokens.map((token, i) => {
      const w = (raw[i] ?? {}) as { start?: unknown; end?: unknown };
      const ws = finite(w.start, start);
      const we = finite(w.end, ws);
      const s = Math.min(Math.max(ws, start), end);
      return { text: token, start: s, end: Math.min(Math.max(we, s), end) };
    });
  }
  const step = (end - start) / tokens.length;
  return tokens.map((token, i) => ({
    text: token,
    start: start + i * step,
    end: start + (i + 1) * step,
  }));
}

/**
 * The highlighted word at reel time `t` (`plan_caption_entries`): each word
 * from its start until the next word's start, the last until the caption's
 * end; none before the first word, and none for a look that never highlights.
 */
export function activeWordIndex(
  words: readonly CaptionWord[],
  captionEnd: number,
  t: number,
  highlights: boolean,
): number | null {
  if (!highlights) return null;
  for (let j = 0; j < words.length; j++) {
    const until = j + 1 < words.length ? words[j + 1].start : captionEnd;
    if (until > words[j].start && t >= words[j].start && t < until) return j;
  }
  return null;
}
