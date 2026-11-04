/**
 * Moving from one clip's editor to another's (PR #97 review, blocker A).
 * The timeline store and the saved-edit store are singletons, and a
 * mutation's callbacks still run after its editor unmounted: an answer for
 * clip A (a voice-over, up to 120 s of Piper, or a slow save) that arrives
 * once clip B is open must not land in B's editor, flip B's Saved state or
 * B's version, nor toast. Real useClipEdit and route against a mocked API.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  render,
  screen,
  fireEvent,
  waitFor,
  act,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
} from "@tanstack/react-router";
import { createElement, useState, type ComponentType } from "react";
import type {
  ClipEditState,
  TimelinePayload,
  VoiceoverResponse,
} from "@/api/client";
import { segmentsOf } from "@/lib/timelineEdit";
import { useTimelineStore } from "@/editor/opencut/stores/timeline-store";

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

const tl = (start: number, end: number, caption: string): TimelinePayload => ({
  schema_version: 2,
  tracks: [
    { kind: "video", items: [{ start, end, src: "main" }] },
    {
      kind: "caption",
      items: [{ start: 1, end: 2, text: caption, words: [] }],
    },
    { kind: "text-overlay", items: [] },
  ],
});
const edit = (
  clipId: string,
  timeline: TimelinePayload,
  version: number,
): ClipEditState => ({
  clip_id: clipId,
  timeline,
  version,
  created_at: "x",
  updated_at: "x",
});

/** Clip A's voice-over answer: its edit with an audio item, version 8. */
function voiceoverForA(): VoiceoverResponse {
  const timeline = tl(100, 110, "clip a caption");
  timeline.tracks.push({
    kind: "audio",
    items: [{ start: 0, end: 1, src: "vo.wav", gain: 1 }],
  });
  return {
    edit: edit("A", timeline, 8),
    item: { start: 0, end: 1, src: "vo.wav", gain: 1 },
    provider: "piper",
  };
}

class FakeEventSource {
  static urls: string[] = [];
  constructor(url: string) {
    FakeEventSource.urls.push(url);
  }
  addEventListener() {}
  close() {}
}

function controlled<T>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

let qc: QueryClient;
const Page = clipEditRoute.options.component as ComponentType;

/** Opens the editor of `clipId` (the route component, as the router mounts it). */
async function open(clipId: "A" | "B") {
  vi.spyOn(clipEditRoute, "useParams").mockReturnValue({ clipId } as never);
  const view = render(
    createElement(QueryClientProvider, { client: qc }, createElement(Page)),
  );
  await screen.findByDisplayValue(
    clipId === "A" ? "clip a caption" : "clip b caption",
  );
  return view;
}

const saveButton = () =>
  screen.getByRole("button", { name: /^(Save changes|Saved|Saving…)$/ });
const cut = () => segmentsOf(useTimelineStore.getState().toTimeline());

function editCaption(value: string) {
  const box = screen.getByRole("textbox", { name: "Caption 1" });
  fireEvent.change(box, { target: { value } });
  fireEvent.blur(box);
}

beforeEach(() => {
  vi.stubGlobal("EventSource", FakeEventSource);
  qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  vi.mocked(api.listClips).mockResolvedValue([
    {
      clip_id: "A",
      job_id: "job-1",
      title: "A",
      output_path: "/x/a.mp4",
      start: 100,
      end: 110,
      ai_hook_text: "hook",
    } as never,
    {
      clip_id: "B",
      job_id: "job-1",
      title: "B",
      output_path: "/x/b.mp4",
      start: 300,
      end: 320,
      ai_hook_text: "hook b",
    } as never,
  ]);
  vi.mocked(api.getJob).mockResolvedValue({
    job_id: "job-1",
    video_path: "/d/s.mp4",
    duration: 600,
  } as never);
  vi.mocked(api.getClipEdit).mockImplementation(async (id: string) =>
    id === "A"
      ? edit("A", tl(100, 110, "clip a caption"), 7)
      : edit("B", tl(300, 320, "clip b caption"), 3),
  );
  vi.mocked(api.upsertClipEdit).mockImplementation(async (id, body) =>
    edit(id, body.timeline, (body.version ?? 0) + 1),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.mocked(toast.success).mockClear();
  vi.mocked(toast.warning).mockClear();
  vi.mocked(toast.error).mockClear();
});

describe("an answer for clip A arriving after clip B's editor opened", () => {
  it("a voice-over: B keeps its own edit, reads as saved, saves its own version, and nothing is toasted", async () => {
    const voiceover = controlled<VoiceoverResponse>();
    vi.mocked(api.createVoiceover).mockReturnValue(voiceover.promise);
    const a = await open("A");
    fireEvent.click(screen.getByRole("button", { name: "Voice-over" }));
    fireEvent.click(
      screen.getByRole("button", { name: "Generate voice-over" }),
    );
    await waitFor(() => expect(api.createVoiceover).toHaveBeenCalled());

    a.unmount();
    await open("B");
    await act(async () => {
      voiceover.resolve(voiceoverForA());
    });

    expect(cut()).toEqual([{ start: 300, end: 320 }]);
    expect(screen.getByRole("textbox", { name: "Caption 1" })).toHaveValue(
      "clip b caption",
    );
    expect(
      useTimelineStore.getState().tracks.some((t) => t.role === "voiceovers"),
    ).toBe(false);
    expect(saveButton()).toHaveTextContent("Saved");
    expect(toast.success).not.toHaveBeenCalledWith("Voice-over added");
    expect(toast.warning).not.toHaveBeenCalled();
    // A's cache has A's answer, for when A is opened again.
    expect(qc.getQueryData<ClipEditState>(["clip-edit", "A"])?.version).toBe(8);

    // No stuck lock: B's own voice-over is not pending, and B saves its own edit and version.
    fireEvent.click(screen.getByRole("button", { name: "Voice-over" }));
    expect(
      screen.getByRole("button", { name: "Generate voice-over" }),
    ).toBeEnabled();
    editCaption("edited in B");
    expect(saveButton()).toBeEnabled();
    fireEvent.click(saveButton());
    await waitFor(() => expect(api.upsertClipEdit).toHaveBeenCalledTimes(1));
    const [id, body] = vi.mocked(api.upsertClipEdit).mock.calls[0];
    expect(id).toBe("B");
    expect(body.version).toBe(3);
    expect(segmentsOf(body.timeline)).toEqual([{ start: 300, end: 320 }]);
  });

  it("a save: B stays Saved and its next save sends B's version, not A's", async () => {
    const save = controlled<ClipEditState>();
    vi.mocked(api.upsertClipEdit).mockReturnValueOnce(save.promise);
    const a = await open("A");
    editCaption("a edited");
    fireEvent.click(saveButton());
    await waitFor(() => expect(api.upsertClipEdit).toHaveBeenCalledTimes(1));

    a.unmount();
    await open("B");
    await act(async () => {
      save.resolve(edit("A", tl(100, 110, "a edited"), 8));
    });

    expect(saveButton()).toHaveTextContent("Saved");
    expect(cut()).toEqual([{ start: 300, end: 320 }]);
    expect(qc.getQueryData<ClipEditState>(["clip-edit", "A"])?.version).toBe(8);
    // A's "Saved" is not news on B's page.
    expect(toast.success).not.toHaveBeenCalled();
    editCaption("b edited");
    fireEvent.click(saveButton());
    await waitFor(() => expect(api.upsertClipEdit).toHaveBeenCalledTimes(2));
    const [id, body] = vi.mocked(api.upsertClipEdit).mock.calls[1];
    expect(id).toBe("B");
    expect(body.version).toBe(3);
  });
});

describe("toasts after the page moved to another clip", () => {
  it("A's re-render queued is not toasted on B's page", async () => {
    const queued = controlled<{ status: string; clip_id: string }>();
    vi.mocked(api.rerenderClip).mockReturnValue(queued.promise as never);
    const a = await open("A");
    fireEvent.click(screen.getByRole("button", { name: /^Re-render$/ }));
    await waitFor(() => expect(api.rerenderClip).toHaveBeenCalled());

    a.unmount();
    await open("B");
    await act(async () => {
      queued.resolve({ status: "queued", clip_id: "A" });
    });

    expect(toast.success).not.toHaveBeenCalled();
  });

  it("A's failed voice-over is toasted naming A, tagged with its id", async () => {
    let fail!: (e: Error) => void;
    vi.mocked(api.createVoiceover).mockReturnValue(
      new Promise<VoiceoverResponse>((_resolve, reject) => {
        fail = reject;
      }),
    );
    const a = await open("A");
    fireEvent.click(screen.getByRole("button", { name: "Voice-over" }));
    fireEvent.click(
      screen.getByRole("button", { name: "Generate voice-over" }),
    );
    await waitFor(() => expect(api.createVoiceover).toHaveBeenCalled());

    a.unmount();
    await open("B");
    await act(async () => {
      fail(new Error('502 {"detail":"piper crashed"}'));
    });

    expect(toast.error).toHaveBeenCalledWith(
      'Voice-over for "A" failed: The voice engine failed (502): piper crashed',
      { id: "voiceover:A" },
    );
  });

  it("A's failed save is toasted on B's page naming A, tagged with its id", async () => {
    let fail!: (e: Error) => void;
    vi.mocked(api.upsertClipEdit).mockReturnValueOnce(
      new Promise<ClipEditState>((_resolve, reject) => {
        fail = reject;
      }),
    );
    const a = await open("A");
    editCaption("a edited");
    fireEvent.click(saveButton());
    await waitFor(() => expect(api.upsertClipEdit).toHaveBeenCalledTimes(1));

    a.unmount();
    await open("B");
    await act(async () => {
      fail(new Error("boom"));
    });
    expect(toast.error).toHaveBeenCalledWith('"A": Save failed: Error: boom', {
      id: "save:A",
    });
  });

  it("a failed voice-over on the open clip is shown in its panel, not toasted", async () => {
    vi.mocked(api.createVoiceover).mockRejectedValue(
      new Error('502 {"detail":"piper crashed"}'),
    );
    await open("A");
    fireEvent.click(screen.getByRole("button", { name: "Voice-over" }));
    fireEvent.click(
      screen.getByRole("button", { name: "Generate voice-over" }),
    );
    expect(
      await screen.findByText(/The voice engine failed \(502\): piper crashed/),
    ).toBeInTheDocument();
    expect(toast.error).not.toHaveBeenCalled();
  });
});

describe("the same clip opened again while its old editor's save was in flight (A, B, A)", () => {
  it("the old editor's answer does not decide for the reopened one: it stays Saved on the version it loaded", async () => {
    const save = controlled<ClipEditState>();
    vi.mocked(api.upsertClipEdit).mockReturnValueOnce(save.promise);
    const first = await open("A");
    editCaption("a edited");
    fireEvent.click(saveButton());
    await waitFor(() => expect(api.upsertClipEdit).toHaveBeenCalledTimes(1));

    first.unmount();
    const b = await open("B");
    b.unmount();
    await open("A");
    await act(async () => {
      save.resolve(edit("A", tl(100, 110, "a edited"), 8));
    });

    // The reopened editor shows what it loaded and reads as saved on it.
    expect(screen.getByRole("textbox", { name: "Caption 1" })).toHaveValue(
      "clip a caption",
    );
    expect(saveButton()).toHaveTextContent("Saved");
    expect(qc.getQueryData<ClipEditState>(["clip-edit", "A"])?.version).toBe(8);
    editCaption("reopened edit");
    fireEvent.click(saveButton());
    await waitFor(() => expect(api.upsertClipEdit).toHaveBeenCalledTimes(2));
    // Its own base version (the server answers 409 if A moved on: no lost update).
    expect(vi.mocked(api.upsertClipEdit).mock.calls[1][1].version).toBe(7);
  });
});

describe("the editor route", () => {
  it("asks for the job's live events with the job id encoded", async () => {
    vi.mocked(api.listClips).mockResolvedValue([
      {
        clip_id: "A",
        job_id: "job/1 ?",
        title: "A",
        output_path: "/x/a.mp4",
        start: 100,
        end: 110,
      } as never,
    ]);
    FakeEventSource.urls = [];
    await open("A");
    expect(FakeEventSource.urls).toEqual([
      "/api/jobs/job%2F1%20%3F/events?live=true",
    ]);
  });

  it("remounts its page for another clip, so no route state carries over (TanStack Router keeps it on a param change)", async () => {
    // The route's own remountDeps on a router of its own: a page that counts its mounts.
    let mounts = 0;
    function Probe() {
      const [n] = useState(() => ++mounts);
      return createElement("p", null, `mount ${n}`);
    }
    const root = createRootRoute({ component: Outlet });
    const route = createRoute({
      getParentRoute: () => root,
      path: "/clips/$clipId/edit",
      component: Probe,
      remountDeps: clipEditRoute.options.remountDeps,
    });
    const router = createRouter({
      routeTree: root.addChildren([route]),
      history: createMemoryHistory({ initialEntries: ["/clips/A/edit"] }),
    });
    render(createElement(RouterProvider, { router }));
    await screen.findByText("mount 1");

    await act(async () => {
      await router.navigate({
        to: "/clips/$clipId/edit",
        params: { clipId: "B" },
      });
    });

    expect(await screen.findByText("mount 2")).toBeInTheDocument();
  });
});
