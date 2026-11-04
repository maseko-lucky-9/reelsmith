/**
 * The clip editor's two previews (spec 017 T155; FR-013, FR-016..FR-019):
 * the clean source played through the edit (the playhead is the edit's own
 * clock, so Split works right after a trim) and the rendered clip (the 016
 * split gate on the toolbar, the S key and the context menu), the fallback
 * to the rendered clip when the source cannot play, the result view's clock,
 * a refused re-render and the voice-over on the audio track. Real
 * useClipEdit, timeline and preview against a mocked API.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  render,
  screen,
  fireEvent,
  waitFor,
  act,
  within,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createElement, type ComponentType } from "react";
import type {
  ClipEditState,
  JobState,
  TimelinePayload,
  VoiceoverResponse,
} from "@/api/client";
import { STALE_PLAYHEAD_REASON } from "@/editor/renderedCut";
import { usePlaybackStore } from "@/editor/opencut/stores/playback-store";
import { useTimelineStore } from "@/editor/opencut/stores/timeline-store";
import { segmentsOf } from "@/lib/timelineEdit";

vi.mock("@tanstack/react-router", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("@tanstack/react-router")>();
  return { ...actual, useNavigate: () => vi.fn() };
});

vi.mock("@/api/client", () => ({
  api: {
    listClips: vi.fn(),
    getJob: vi.fn(),
    rerenderClip: vi.fn(),
    getClipEdit: vi.fn(),
    upsertClipEdit: vi.fn(),
    createVoiceover: vi.fn(),
  },
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}));

import { api } from "@/api/client";
import { toast } from "sonner";
import { clipEditRoute } from "./clips.$clipId.edit";

/** A saved v2 edit of a clip at 100-110 s of its source. */
const SAVED: TimelinePayload = {
  schema_version: 2,
  tracks: [
    { kind: "video", items: [{ start: 100, end: 110, src: "main" }] },
    {
      kind: "caption",
      items: [
        { start: 1, end: 2, text: "hello there", words: [] },
        { start: 5, end: 6, text: "later on", words: [] },
      ],
    },
    { kind: "text-overlay", items: [] },
  ],
};

const edit = (timeline: TimelinePayload, version: number): ClipEditState => ({
  clip_id: "clip-1",
  timeline,
  version,
  created_at: "x",
  updated_at: "x",
});

/** jsdom has no EventSource: records the editor's live stream. */
class FakeEventSource {
  url: string;
  constructor(url: string) {
    this.url = url;
  }
  addEventListener() {}
  close() {}
}

async function renderPage(job: Partial<JobState> = {}) {
  vi.mocked(api.listClips).mockResolvedValue([
    {
      clip_id: "clip-1",
      job_id: "job-1",
      title: "Intro",
      output_path: "/data/clip-1.mp4",
      start: 100,
      end: 110,
      ai_hook_text: "Wait for the twist",
    } as never,
  ]);
  vi.mocked(api.getJob).mockResolvedValue({
    job_id: "job-1",
    video_path: "/data/downloads/source.mp4",
    duration: 600,
    ...job,
  } as never);
  vi.spyOn(clipEditRoute, "useParams").mockReturnValue({
    clipId: "clip-1",
  } as never);
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const Page = clipEditRoute.options.component as ComponentType;
  const view = render(
    createElement(QueryClientProvider, { client: qc }, createElement(Page)),
  );
  await screen.findByRole("textbox", { name: "Caption 1" });
  await waitFor(() => expect(api.getJob).toHaveBeenCalled());
  return view;
}

const button = (name: string | RegExp) =>
  screen.getByRole("button", { name }) as HTMLButtonElement;
const toolbar = () => screen.getByRole("toolbar", { name: "Timeline" });
const toolbarButton = (name: string) =>
  within(toolbar()).getByRole("button", { name }) as HTMLButtonElement;
const sourceVideo = () =>
  screen.queryByLabelText("Edit preview") as HTMLVideoElement | null;
const resultVideo = () =>
  screen.queryByLabelText("Rendered clip") as HTMLVideoElement | null;
const cut = () =>
  segmentsOf(useTimelineStore.getState().toTimeline()).map((s) => [
    s.start,
    s.end,
  ]);
const captionRow = (n: number) =>
  screen
    .getByRole("textbox", { name: `Caption ${n}` })
    .closest("li") as HTMLElement;

function trimStart(value: string) {
  fireEvent.click(button("Trim"));
  const start = screen.getByRole("spinbutton", { name: "Segment 1 start" });
  fireEvent.change(start, { target: { value } });
  fireEvent.blur(start);
}

function seek(seconds: number) {
  act(() => usePlaybackStore.getState().seek(seconds));
}

beforeEach(() => {
  vi.stubGlobal("EventSource", FakeEventSource);
  vi.mocked(api.getClipEdit)
    .mockReset()
    .mockResolvedValue(edit(structuredClone(SAVED), 1));
  vi.mocked(api.upsertClipEdit)
    .mockReset()
    .mockImplementation(async (_id, body) =>
      edit(body.timeline, (body.version ?? 0) + 1),
    );
  vi.mocked(api.rerenderClip)
    .mockReset()
    .mockResolvedValue({ status: "queued", clip_id: "clip-1" });
  vi.mocked(api.createVoiceover).mockReset();
  vi.mocked(api.getJob).mockReset();
  vi.mocked(toast.success).mockReset();
  vi.mocked(toast.warning).mockReset();
  vi.mocked(toast.error).mockReset();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("clean-source view", () => {
  it("opens on the job source, played through the edit", async () => {
    await renderPage();

    expect(sourceVideo()?.getAttribute("src")).toBe("/api/jobs/job-1/source");
    expect(resultVideo()).toBeNull();
    expect(button("Edit preview").getAttribute("aria-pressed")).toBe("true");
    expect(button("Rendered clip").getAttribute("aria-pressed")).toBe("false");
  });

  it("splits at the playhead right after a trim, with no re-render: the playhead is the edit clock", async () => {
    await renderPage();
    trimStart("101.5"); // reel 8.5 s; caption 2 now at 3.5-4.5
    seek(4);

    expect(toolbarButton("Split").disabled).toBe(false);
    expect(toolbarButton("Split").title).toBe("");
    expect(button("Split at playhead").disabled).toBe(false);
    expect(captionRow(2).className).toContain("bg-white/10");

    fireEvent.click(toolbarButton("Split"));

    expect(cut()).toEqual([
      [101.5, 105.5],
      [105.5, 110],
    ]);
    expect(api.rerenderClip).not.toHaveBeenCalled();
  });
});

describe("rendered-clip view", () => {
  async function staleResultView() {
    await renderPage();
    fireEvent.click(button("Rendered clip"));
    const video = resultVideo() as HTMLVideoElement;
    Object.defineProperty(video, "currentTime", {
      configurable: true,
      value: 4,
      writable: true,
    });
    fireEvent(video, new Event("timeupdate"));
    trimStart("101.5"); // the edit is no longer the rendered file's cut
  }

  it("plays the rendered clip on its own clock", async () => {
    await renderPage();
    fireEvent.click(button("Rendered clip"));

    expect(resultVideo()?.getAttribute("src")).toBe("/api/clips/clip-1/video");
    expect(sourceVideo()).toBeNull();
    expect(usePlaybackStore.getState().duration).toBe(10);

    const video = resultVideo() as HTMLVideoElement;
    Object.defineProperty(video, "duration", {
      configurable: true,
      value: 12.5,
    });
    fireEvent.loadedMetadata(video);
    expect(usePlaybackStore.getState().duration).toBe(12.5);

    fireEvent.click(button("Edit preview"));
    expect(usePlaybackStore.getState().duration).toBe(10);
  });

  it("disables Split on the toolbar with the 016 reason while the cut is stale", async () => {
    await staleResultView();

    for (const name of ["Split", "Split keep left", "Split keep right"]) {
      expect(toolbarButton(name).disabled).toBe(true);
      expect(toolbarButton(name).title).toBe(STALE_PLAYHEAD_REASON);
    }
    expect(button("Split at playhead").disabled).toBe(true);
    expect(button("Split at playhead").title).toBe(STALE_PLAYHEAD_REASON);
    expect(captionRow(2).className).not.toContain("bg-white/10");
  });

  it("refuses the S key with the reason and changes nothing", async () => {
    await staleResultView();

    fireEvent.keyDown(document.body, { key: "s" });

    expect(toast.error).toHaveBeenCalledWith(STALE_PLAYHEAD_REASON);
    expect(cut()).toEqual([[101.5, 110]]);
  });

  it("disables the segment's split items in its context menu, with the reason", async () => {
    await staleResultView();

    fireEvent.contextMenu(
      screen.getByRole("button", { name: /^Segment 1, source/ }),
    );
    const menu = await screen.findByRole("menu");

    expect(within(menu).getByText(STALE_PLAYHEAD_REASON)).toBeTruthy();
    for (const name of ["Split", "Split keep left", "Split keep right"]) {
      expect(
        within(menu)
          .getByRole("menuitem", { name })
          .getAttribute("aria-disabled"),
      ).toBe("true");
    }
    fireEvent.click(within(menu).getByRole("menuitem", { name: "Split" }));
    expect(cut()).toEqual([[101.5, 110]]);
  });
});

describe("fallback to the rendered clip", () => {
  it("switches to the rendered clip, says why and keeps the gate when the source fails to load", async () => {
    await renderPage();
    const video = sourceVideo() as HTMLVideoElement;
    Object.defineProperty(video, "error", {
      configurable: true,
      value: { code: 4 },
    });
    fireEvent.error(video);

    expect(resultVideo()?.getAttribute("src")).toBe("/api/clips/clip-1/video");
    expect(sourceVideo()).toBeNull();
    expect(screen.getByRole("note").textContent).toMatch(
      /^Showing the rendered clip\. The source video is not available/,
    );
    expect(button("Edit preview").disabled).toBe(true);
    expect(button("Rendered clip").getAttribute("aria-pressed")).toBe("true");

    trimStart("101.5");
    expect(toolbarButton("Split").disabled).toBe(true);
    expect(toolbarButton("Split").title).toBe(STALE_PLAYHEAD_REASON);
  });

  it("offers to try the source again after it failed to load, and plays it in the edit preview again", async () => {
    await renderPage();
    const failed = sourceVideo() as HTMLVideoElement;
    Object.defineProperty(failed, "error", {
      configurable: true,
      value: { code: 2 },
    });
    fireEvent.error(failed);
    expect(sourceVideo()).toBeNull();

    fireEvent.click(button("Try the source again"));

    const again = sourceVideo();
    expect(again).not.toBeNull();
    expect(again).not.toBe(failed);
    expect(again?.getAttribute("src")).toBe("/api/jobs/job-1/source");
    expect(screen.queryByRole("note")).toBeNull();
    expect(button("Edit preview").disabled).toBe(false);
    expect(button("Edit preview").getAttribute("aria-pressed")).toBe("true");
  });

  it("opens on the rendered clip when the job no longer keeps its source", async () => {
    await renderPage({ video_path: null });

    await waitFor(() => expect(resultVideo()).not.toBeNull());
    expect(sourceVideo()).toBeNull();
    expect(screen.getByRole("note").textContent).toBe(
      "Showing the rendered clip. The source video is no longer kept on the server.",
    );
    // Nothing to try again: the server no longer has it.
    expect(
      screen.queryByRole("button", { name: "Try the source again" }),
    ).toBeNull();
  });
});

describe("re-render and voice-over on the new editor", () => {
  it("shows the server's reason for a refused re-render (409) and leaves Re-render enabled", async () => {
    vi.mocked(api.rerenderClip).mockRejectedValue(
      new Error(
        '409 {"detail":"a re-render of this clip is already in progress"}',
      ),
    );
    await renderPage();

    fireEvent.click(button("Re-render"));

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        "Cannot re-render: a re-render of this clip is already in progress",
      ),
    );
    expect(button("Re-render").disabled).toBe(false);
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("puts a placeholder voice-over on the audio track with the warning, and Undo removes it", async () => {
    const withAudio: TimelinePayload = {
      ...structuredClone(SAVED),
      tracks: [
        ...structuredClone(SAVED).tracks,
        {
          kind: "audio",
          items: [{ start: 0, end: 1, src: "vo.wav", gain: 1 }],
        },
      ],
    };
    const response: VoiceoverResponse = {
      edit: edit(withAudio, 2),
      item: { start: 0, end: 1, src: "vo.wav", gain: 1 },
      provider: "stub",
    };
    vi.mocked(api.createVoiceover).mockResolvedValue(response);
    const view = await renderPage();

    fireEvent.click(button("Voice-over"));
    fireEvent.click(button("Generate voice-over"));

    const audioTrack = () =>
      view.container.querySelector(
        '[data-track-role="voiceovers"]',
      ) as HTMLElement | null;
    await waitFor(() => expect(audioTrack()).not.toBeNull());
    expect(
      within(audioTrack() as HTMLElement).getByRole("button", {
        name: "Voice-over: vo.wav",
      }),
    ).toBeTruthy();
    expect(toast.warning).toHaveBeenCalledWith(
      expect.stringMatching(/silent placeholder/),
    );
    expect(button("Saved")).toBeTruthy();

    fireEvent.click(toolbarButton("Undo"));

    expect(
      screen.queryByRole("button", { name: "Voice-over: vo.wav" }),
    ).toBeNull();
    expect(button("Save changes")).toBeTruthy();
  });
});

describe("properties of the selection", () => {
  it("opens a caption selected on the timeline in the properties panel; a tool replaces it", async () => {
    await renderPage();
    fireEvent.click(button("Text"));
    expect(screen.getByRole("region", { name: "Text" })).toBeTruthy();

    fireEvent.click(
      screen.getByRole("button", { name: "Caption: hello there" }),
    );

    const properties = screen.getByRole("region", { name: "Properties" });
    expect(screen.queryByRole("region", { name: "Text" })).toBeNull();
    expect(button("Text").getAttribute("aria-pressed")).toBe("false");
    const text = within(properties).getByRole("textbox", {
      name: "Caption text",
    });
    fireEvent.change(text, { target: { value: "hi there" } });
    fireEvent.blur(text);
    // One edit, shared with the transcript.
    expect(
      (screen.getByRole("textbox", { name: "Caption 1" }) as HTMLInputElement)
        .value,
    ).toBe("hi there");

    fireEvent.click(button("Trim"));

    expect(screen.queryByRole("region", { name: "Properties" })).toBeNull();
    expect(screen.getByRole("region", { name: "Trim" })).toBeTruthy();
    expect(button("Trim").getAttribute("aria-pressed")).toBe("true");
  });
});
