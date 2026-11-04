/**
 * useClipEdit: load, save and adopt on the timeline store (spec 017 T145;
 * FR-005, FR-012, FR-019, FR-020). The first two cases are the save-in-flight
 * cases of spec 016's editor state hook (removed by T156), ported.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createElement, type ReactNode } from "react";
import type { ClipEditState, TimelinePayload } from "@/api/client";
import { upgradeTimeline, type SourceWindow } from "@/lib/timelineEdit";
import {
  serverDefault,
  v1Foreign,
  WINDOW,
} from "@/editor/__fixtures__/timelines";

vi.mock("@/api/client", () => ({
  api: {
    getClipEdit: vi.fn(),
    upsertClipEdit: vi.fn(),
  },
}));

import { api } from "@/api/client";
import { useTimelineStore } from "@/editor/opencut/stores/timeline-store";
import { canonicalJson, useClipEdit } from "./useClipEdit";

const tl = (text: string): TimelinePayload => ({
  schema_version: 2,
  tracks: [
    { kind: "video", items: [{ start: 100, end: 110, src: "main" }] },
    { kind: "caption", items: [{ start: 0, end: 1, text }] },
  ],
});

const edit = (timeline: TimelinePayload, version: number): ClipEditState => ({
  clip_id: "clip-1",
  timeline,
  version,
  created_at: "x",
  updated_at: "x",
});

function controlled<T>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

function setup(sourceWindow: SourceWindow | null = WINDOW) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const wrapper = ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client: qc }, children);
  return renderHook(
    ({ window }) => useClipEdit("clip-1", { sourceWindow: window }),
    {
      wrapper,
      initialProps: { window: sourceWindow },
    },
  );
}

const captionText = (t: TimelinePayload) =>
  t.tracks.find((k) => k.kind === "caption")?.items[0]?.text;
const putBody = (call = 0) => vi.mocked(api.upsertClipEdit).mock.calls[call][1];

beforeEach(() => {
  vi.mocked(api.getClipEdit)
    .mockReset()
    .mockResolvedValue(edit(tl("loaded"), 1));
  vi.mocked(api.upsertClipEdit).mockReset();
});

describe("useClipEdit — save in flight (ported from spec 016)", () => {
  it("keeps edits made while the save was in flight, still dirty, on the new version", async () => {
    const pending = controlled<ClipEditState>();
    vi.mocked(api.upsertClipEdit).mockReturnValue(pending.promise);
    const { result } = setup();
    await waitFor(() =>
      expect(captionText(result.current.timeline)).toBe("loaded"),
    );

    act(() => {
      result.current.setTimeline(tl("first"));
    });
    let saved!: Promise<void>;
    act(() => {
      saved = result.current.save();
    });
    await waitFor(() => expect(api.upsertClipEdit).toHaveBeenCalledTimes(1));
    expect(captionText(putBody().timeline)).toBe("first");

    // Edited after the save started.
    act(() => {
      result.current.setTimeline(tl("second"));
    });
    await act(async () => {
      pending.resolve(edit(tl("first"), 2));
      await saved;
    });

    expect(captionText(result.current.timeline)).toBe("second");
    expect(result.current.version).toBe(2);
    expect(result.current.isDirty).toBe(true);
    // Undo lands on what was saved, which now reads as saved.
    act(() => result.current.undo());
    expect(captionText(result.current.timeline)).toBe("first");
    expect(result.current.isDirty).toBe(false);
  });

  it("adopts the server's copy when nothing changed during the save", async () => {
    const pending = controlled<ClipEditState>();
    vi.mocked(api.upsertClipEdit).mockReturnValue(pending.promise);
    const { result } = setup();
    await waitFor(() =>
      expect(captionText(result.current.timeline)).toBe("loaded"),
    );

    act(() => {
      result.current.setTimeline(tl("first"));
    });
    let saved!: Promise<void>;
    act(() => {
      saved = result.current.save();
    });
    await waitFor(() => expect(api.upsertClipEdit).toHaveBeenCalledTimes(1));
    await act(async () => {
      // The server normalises the edit (e.g. rounds a time).
      pending.resolve(edit(tl("first (server)"), 2));
      await saved;
    });

    expect(captionText(result.current.timeline)).toBe("first (server)");
    expect(result.current.version).toBe(2);
    expect(result.current.isDirty).toBe(false);
  });
});

describe("useClipEdit — loading", () => {
  it("opens a saved v1 edit converted to v2, not dirty, with savedIsV1", async () => {
    vi.mocked(api.getClipEdit).mockResolvedValue(edit(serverDefault, 3));
    const { result } = setup();
    await waitFor(() => expect(result.current.isLoaded).toBe(true));
    expect(result.current.timeline).toStrictEqual(
      upgradeTimeline(serverDefault, WINDOW),
    );
    expect(result.current.isDirty).toBe(false);
    expect(result.current.savedIsV1).toBe(true);
    expect(result.current.version).toBe(3);
    expect(result.current.loadedTimeline).toBe(serverDefault);

    // Saved as v2: no longer v1.
    vi.mocked(api.upsertClipEdit).mockImplementation(async (_id, body) =>
      edit(body.timeline, 4),
    );
    await act(async () => {
      await result.current.save();
    });
    expect(putBody().timeline.schema_version).toBe(2);
    expect(result.current.savedIsV1).toBe(false);
    expect(result.current.isDirty).toBe(false);
  });

  it("opens a v1 edit it cannot convert read-only, with the reason, not dirty", async () => {
    vi.mocked(api.getClipEdit).mockResolvedValue(edit(v1Foreign, 2));
    const { result } = setup();
    await waitFor(() => expect(result.current.isLoaded).toBe(true));
    expect(result.current.readOnly).toMatch(/another file/i);
    expect(result.current.isDirty).toBe(false);
    expect(result.current.timeline).toBe(v1Foreign);
  });

  it("opens the clip's window as one segment when there is no edit (404), version 0, not dirty", async () => {
    vi.mocked(api.getClipEdit).mockRejectedValue(
      new Error('404 {"detail":"no edit state for clip"}'),
    );
    const { result } = setup();
    await waitFor(() => expect(result.current.isLoaded).toBe(true));
    expect(result.current.version).toBe(0);
    expect(result.current.isDirty).toBe(false);
    expect(result.current.timeline.tracks[0].items).toEqual([
      { start: 100, end: 110, src: "main" },
    ]);
  });

  it("converts a v1 edit loaded before the clip window is known once the window arrives", async () => {
    vi.mocked(api.getClipEdit).mockResolvedValue(edit(serverDefault, 1));
    const { result, rerender } = setup(null);
    await waitFor(() => expect(result.current.isLoaded).toBe(true));
    expect(result.current.readOnly).toMatch(/unknown/i);

    rerender({ window: WINDOW });
    await waitFor(() => expect(result.current.readOnly).toBeNull());
    expect(result.current.timeline).toStrictEqual(
      upgradeTimeline(serverDefault, WINDOW),
    );
    expect(result.current.isDirty).toBe(false);
  });

  it("is neither loaded nor dirty before the edit arrives", async () => {
    const pending = controlled<ClipEditState>();
    vi.mocked(api.getClipEdit).mockReturnValue(pending.promise);
    const { result } = setup();
    expect(result.current.isLoaded).toBe(false);
    expect(result.current.isDirty).toBe(false);
    await act(async () => {
      pending.resolve(edit(tl("loaded"), 1));
    });
    await waitFor(() => expect(result.current.isLoaded).toBe(true));
  });
});

describe("useClipEdit — history and the saved copy", () => {
  it("reads as saved again after undoing back to the saved snapshot", async () => {
    const { result } = setup();
    await waitFor(() => expect(result.current.isLoaded).toBe(true));
    act(() => {
      result.current.setTimeline(tl("changed"));
    });
    expect(result.current.isDirty).toBe(true);
    expect(result.current.canUndo).toBe(true);
    act(() => result.current.undo());
    expect(result.current.isDirty).toBe(false);
    expect(result.current.canRedo).toBe(true);
  });

  it("applies the server's edit (a voice-over) as one undoable step that reads as saved", async () => {
    const { result } = setup();
    await waitFor(() => expect(result.current.isLoaded).toBe(true));
    const withVoiceover: TimelinePayload = {
      ...tl("loaded"),
      tracks: [
        ...tl("loaded").tracks,
        {
          kind: "audio",
          items: [{ start: 0, end: 2, src: "vo.wav", gain: 1 }],
        },
      ],
    };
    act(() => result.current.applyServerState(edit(withVoiceover, 5)));

    expect(result.current.timeline).toStrictEqual(withVoiceover);
    expect(result.current.version).toBe(5);
    expect(result.current.isDirty).toBe(false);
    expect(useTimelineStore.getState().history).toHaveLength(1);

    act(() => result.current.undo());
    expect(result.current.timeline.tracks.some((t) => t.kind === "audio")).toBe(
      false,
    );
    expect(result.current.isDirty).toBe(true);
  });

  it("sends schema_version 2 and the version it was based on", async () => {
    vi.mocked(api.upsertClipEdit).mockImplementation(async (_id, body) =>
      edit(body.timeline, 2),
    );
    const { result } = setup();
    await waitFor(() => expect(result.current.isLoaded).toBe(true));
    act(() => {
      result.current.setTimeline(tl("changed"));
    });
    await act(async () => {
      await result.current.save();
    });
    expect(putBody()).toMatchObject({ version: 1 });
    expect(putBody().timeline.schema_version).toBe(2);
  });

  it("sends no version for the default timeline (version 0)", async () => {
    vi.mocked(api.getClipEdit).mockRejectedValue(
      new Error('404 {"detail":"no edit state for clip"}'),
    );
    vi.mocked(api.upsertClipEdit).mockImplementation(async (_id, body) =>
      edit(body.timeline, 1),
    );
    const { result } = setup();
    await waitFor(() => expect(result.current.isLoaded).toBe(true));
    act(() => {
      result.current.setTimeline(tl("changed"));
    });
    await act(async () => {
      await result.current.save();
    });
    expect("version" in putBody()).toBe(false);
    expect(putBody().timeline.schema_version).toBe(2);
    expect(result.current.version).toBe(1);
  });
});

describe("useClipEdit — reopening the editor", () => {
  it("loads the saved edit again, not the closed editor's unsaved changes", async () => {
    // Within staleTime the edit is cached: the new editor has it at mount.
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: 10_000 } },
    });
    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(QueryClientProvider, { client: qc }, children);
    const open = () =>
      renderHook(() => useClipEdit("clip-1", { sourceWindow: WINDOW }), {
        wrapper,
      });

    const first = open();
    await waitFor(() => expect(first.result.current.isLoaded).toBe(true));
    act(() => {
      first.result.current.setTimeline(tl("unsaved"));
    });
    expect(first.result.current.isDirty).toBe(true);
    first.unmount();

    const second = open();
    await waitFor(() => expect(second.result.current.isLoaded).toBe(true));
    expect(captionText(second.result.current.timeline)).toBe("loaded");
    expect(second.result.current.isDirty).toBe(false);
    expect(second.result.current.canUndo).toBe(false);
    expect(api.getClipEdit).toHaveBeenCalledTimes(1);
  });

  it("clears the timeline store when the editor closes, so the next editor never shows this edit", async () => {
    const first = setup();
    await waitFor(() => expect(first.result.current.isLoaded).toBe(true));
    act(() => {
      first.result.current.setTimeline(tl("unsaved"));
    });
    first.unmount();

    const store = useTimelineStore.getState();
    expect(
      store._tracks.flatMap((t) => t.elements).map((el) => el.name),
    ).toEqual([]);
    expect(store.history).toEqual([]);
    expect(store.selectedElements).toEqual([]);
  });
});

describe("useClipEdit — the source's length", () => {
  it("keeps the preview's probed length when the job's recorded one answers later (211.98 s, not 212 s)", async () => {
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(QueryClientProvider, { client: qc }, children);
    const pending = controlled<ClipEditState>();
    vi.mocked(api.getClipEdit).mockReturnValue(pending.promise);
    const { result, rerender } = renderHook(
      ({ duration }: { duration: number | null }) =>
        useClipEdit("clip-1", {
          sourceWindow: WINDOW,
          sourceDuration: duration,
        }),
      { wrapper, initialProps: { duration: null as number | null } },
    );

    // The source's metadata loads before the edit does.
    act(() => useTimelineStore.getState().setSourceDuration(211.98, "probe"));
    await act(async () => {
      pending.resolve(edit(tl("loaded"), 1));
    });
    await waitFor(() => expect(result.current.isLoaded).toBe(true));
    // Then the job (yt-dlp's metadata) answers.
    rerender({ duration: 212 });

    const store = useTimelineStore.getState();
    expect(store.sourceDuration).toBe(211.98);
    const segment = store._tracks.find((t) => t.role === "segments")!
      .elements[0];
    act(() => {
      expect(store.trimSegment(segment.id, { end: 500 }).ok).toBe(true);
    });
    expect(result.current.timeline.tracks[0].items).toEqual([
      { start: 100, end: 211.98, src: "main" },
    ]);
  });
});

describe("canonicalJson", () => {
  it("ignores key order and drops undefined like JSON", () => {
    expect(
      canonicalJson({ b: 1, a: { d: [1, { y: 2, x: 1 }], c: undefined } }),
    ).toBe(canonicalJson({ a: { d: [1, { x: 1, y: 2 }] }, b: 1 }));
    expect(canonicalJson([2, 1])).not.toBe(canonicalJson([1, 2]));
  });
});
