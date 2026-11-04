/**
 * The server's checks of a schema v2 timeline, ported for tests: what
 * `validate_timeline(timeline, source_range=(0, source))` refuses
 * (app/services/timeline_render_service.py `_parse`, `_segments`, `_times`,
 * `_caption_words`), on raw floats as Python computes them. Written apart from
 * the editor's own rules so a test compares the two. Not ported: shapes the
 * editor never writes (non-object items, unknown keys' types), style names
 * and the audio file lookup.
 *
 * Note the server's order of work: a segment within 1 ms past the source is
 * accepted, then clamped to it, and only then held to 0.05 s; the edited
 * reel's end (which captions, overlays and audio may not pass by more than
 * 1 ms) is the sum of the clamped segments.
 */
import type { TimelineItem, TimelinePayload } from "@/api/client";

const TIME_TOLERANCE = 1e-3;
const MAX_ITEMS: Record<string, number> = {
  video: 100,
  caption: 2000,
  "text-overlay": 100,
  audio: 16,
};

/** Why the server would refuse `timeline` (v2) with the source `[0, source]`, else null. */
export function serverRefusal(
  timeline: TimelinePayload,
  source: number | null,
): string | null {
  if (timeline.tracks.length > 16) return "more than 16 tracks";
  const grouped: Record<string, TimelineItem[]> = {
    video: [],
    caption: [],
    "text-overlay": [],
    audio: [],
  };
  for (const track of timeline.tracks) {
    if (!(track.kind in grouped)) return `unknown track kind ${track.kind}`;
    grouped[track.kind].push(...track.items);
    if (grouped[track.kind].length > MAX_ITEMS[track.kind])
      return `too many ${track.kind} items`;
  }

  const times = (it: TimelineItem, name: string): [number, number] | string => {
    const start = Number(it.start ?? 0);
    const end = Number(it.end ?? 0);
    if (!Number.isFinite(start) || !Number.isFinite(end))
      return `${name}: not a number`;
    if (start < 0 || end < 0) return `${name}: times must not be negative`;
    if (!(end > start)) return `${name}: end must be > start`;
    return [start, end];
  };

  // _segments
  if (grouped.video.length === 0) return "a v2 timeline needs a segment";
  const segments: Array<{ start: number; end: number }> = [];
  let total = 0;
  for (const [i, it] of grouped.video.entries()) {
    const name = `video[${i}]`;
    if (it.src != null && it.src !== "main") return `${name}.src`;
    const t = times(it, name);
    if (typeof t === "string") return t;
    let [start, end] = t;
    if (source !== null) {
      if (start < 0 - TIME_TOLERANCE || end > source + TIME_TOLERANCE)
        return `${name}: [${start}, ${end}] is outside the source [0, ${source}]`;
      start = Math.max(start, 0);
      end = Math.min(end, source);
    }
    if (end - start < 0.05) return `${name}: shorter than 0.05s`;
    const prev = segments[segments.length - 1];
    if (prev && start < prev.end - TIME_TOLERANCE)
      return `${name}: segments must be in source order and must not overlap`;
    if (prev) start = Math.max(start, prev.end);
    total += end - start;
    if (total > 20 * 60) return "the edited reel is longer than 1200s";
    segments.push({ start, end });
  }
  let cursor = 0;
  for (const s of segments) cursor += s.end - s.start;
  const reelEnd = cursor;

  const placed = (
    it: TimelineItem,
    name: string,
  ): [number, number] | string => {
    const t = times(it, name);
    if (typeof t === "string") return t;
    if (t[1] > reelEnd + TIME_TOLERANCE)
      return `${name}: ends at ${t[1]}, past the edited reel's end (${reelEnd.toFixed(3)})`;
    return t;
  };
  const chars = (text: string) => [...text].length;
  const tokens = (text: string) =>
    text.split(/\s+/).filter((w) => w.length > 0).length;

  let captionWords = 0;
  for (const [i, it] of grouped.caption.entries()) {
    const name = `captions[${i}]`;
    const t = placed(it, name);
    if (typeof t === "string") return t;
    const text = it.text == null ? "" : String(it.text);
    if (chars(text) > 500) return `${name}: text longer than 500 characters`;
    captionWords += tokens(text);
    if (captionWords > 5000) return "the captions hold more than 5000 words";
    if (Array.isArray(it.words) && it.words.length > 100)
      return `${name}.words: more than 100 words`;
    if (tokens(text) > 100) return `${name}.text: more than 100 words`;
  }

  let overlayChars = 0;
  for (const [i, it] of grouped["text-overlay"].entries()) {
    const name = `overlays[${i}]`;
    const t = placed(it, name);
    if (typeof t === "string") return t;
    const fontSize = Math.trunc(Number(it.font_size ?? 36));
    if (!(fontSize >= 6 && fontSize <= 400)) return `${name}.font_size`;
    const text = it.text == null ? "" : String(it.text);
    if (chars(text) > 500) return `${name}: text longer than 500 characters`;
    if ((text.match(/\n/g) ?? []).length > 10) return `${name}: line breaks`;
    overlayChars += chars(text);
    if (overlayChars > 5000)
      return "the text overlays hold more than 5000 characters";
  }

  for (const [i, it] of grouped.audio.entries()) {
    const name = `audio[${i}]`;
    const t = placed(it, name);
    if (typeof t === "string") return t;
    const gain = Number(it.gain ?? 1);
    if (!(gain >= 0 && gain <= 4)) return `${name}.gain`;
  }
  return null;
}
