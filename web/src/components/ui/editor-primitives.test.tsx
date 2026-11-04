/**
 * Base UI wrappers the OpenCut timeline port uses (spec 017 T146, FR-010;
 * research R13): context menu, menu, tooltip, slider, textarea, scroll area.
 * Each case drives the wrapper the way the timeline does and checks what the
 * user gets, not how Base UI is wired.
 */
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuTrigger,
} from "./context-menu";
import { Menu, MenuContent, MenuItem, MenuTrigger } from "./menu";
import { ScrollArea } from "./scroll-area";
import { Slider } from "./slider";
import { Textarea } from "./textarea";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "./tooltip";

describe("ContextMenu", () => {
  it("opens on right click; an item's onClick fires and closes it", async () => {
    const onSplit = vi.fn();
    render(
      <ContextMenu>
        <ContextMenuTrigger>segment</ContextMenuTrigger>
        <ContextMenuContent>
          <ContextMenuItem onClick={onSplit}>Split</ContextMenuItem>
        </ContextMenuContent>
      </ContextMenu>,
    );
    expect(screen.queryByRole("menu")).toBeNull();

    fireEvent.contextMenu(screen.getByText("segment"));
    const item = await screen.findByRole("menuitem", { name: "Split" });
    fireEvent.click(item);

    expect(onSplit).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(screen.queryByRole("menu")).toBeNull());
  });
});

describe("Menu", () => {
  it("opens from its trigger and lists its items", async () => {
    const onDelete = vi.fn();
    render(
      <Menu>
        <MenuTrigger aria-label="More actions">⋮</MenuTrigger>
        <MenuContent>
          <MenuItem onClick={onDelete}>Delete</MenuItem>
        </MenuContent>
      </Menu>,
    );
    expect(screen.queryByRole("menuitem")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "More actions" }));
    fireEvent.click(await screen.findByRole("menuitem", { name: "Delete" }));

    expect(onDelete).toHaveBeenCalledTimes(1);
  });
});

describe("Tooltip", () => {
  it("shows its content when the trigger takes focus", async () => {
    render(
      <TooltipProvider delay={0}>
        <Tooltip>
          <TooltipTrigger render={<button type="button">Split</button>} />
          <TooltipContent>Split at the playhead (S)</TooltipContent>
        </Tooltip>
      </TooltipProvider>,
    );
    expect(screen.queryByText("Split at the playhead (S)")).toBeNull();

    fireEvent.focus(screen.getByRole("button", { name: "Split" }));

    expect(
      await screen.findByText("Split at the playhead (S)"),
    ).toBeInTheDocument();
  });
});

describe("Slider", () => {
  it("changes its value from the keyboard", () => {
    function FontSize() {
      const [value, setValue] = useState(36);
      return (
        <>
          <Slider
            aria-label="Font size"
            value={value}
            min={6}
            max={400}
            onValueChange={(v) => setValue(Array.isArray(v) ? v[0] : v)}
          />
          <output>{value}</output>
        </>
      );
    }
    render(<FontSize />);
    // Base UI keeps the thumb visibility:hidden until it measures layout, which
    // jsdom never does, so query by label (no visibility filter) instead of role.
    const slider = screen.getByLabelText("Font size");
    expect(slider).toHaveAttribute("type", "range");

    fireEvent.keyDown(slider, { key: "ArrowRight" });

    expect(screen.getByRole("status")).toHaveTextContent("37");
    expect(slider).toHaveAttribute("aria-valuenow", "37");
  });
});

describe("Textarea", () => {
  it("forwards maxLength and its label", () => {
    render(<Textarea aria-label="Text" maxLength={500} defaultValue="hi" />);
    const box = screen.getByRole("textbox", { name: "Text" });
    expect(box).toHaveAttribute("maxlength", "500");
    expect(box).toHaveValue("hi");
  });
});

describe("ScrollArea", () => {
  it("renders its children inside a scrollable viewport", () => {
    render(
      <ScrollArea data-testid="area">
        <p>tracks</p>
      </ScrollArea>,
    );
    const viewport = screen
      .getByTestId("area")
      .querySelector('[data-slot="scroll-area-viewport"]');
    expect(viewport).not.toBeNull();
    expect(viewport).toContainElement(screen.getByText("tracks"));
  });
});
