import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { graphFixture } from "../../test/graphFixtures";
import { FunctionTrace } from "./FunctionTrace";

describe("FunctionTrace", () => {
  it("runs a bounded trace, exposes labeled controls, and focuses the exact discovered node", () => {
    const onSelectNode = vi.fn();
    render(<FunctionTrace graph={graphFixture} selectedNodeId="symbol:one" onSelectNode={onSelectNode} />);
    expect(screen.getByLabelText("Direction")).toBeTruthy();
    expect(screen.getByLabelText("Maximum depth")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Direction"), { target: { value: "BOTH" } });
    fireEvent.click(screen.getByRole("button", { name: "Run trace" }));
    expect(screen.getByText(/Current result: both/)).toBeTruthy();
    expect(screen.getByText(/3 individual CALLS relationships/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Focus & use as root" }));
    expect(onSelectNode).toHaveBeenCalledWith("symbol:two");
  });

  it("disables tracing for non-function roots and reports a valid empty result", () => {
    const onSelectNode = vi.fn();
    const { rerender } = render(<FunctionTrace graph={graphFixture} selectedNodeId="file:src/a.py" onSelectNode={onSelectNode} />);
    expect(screen.getByText("Files cannot be trace roots. Select a function or method.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Run trace" }).hasAttribute("disabled")).toBe(true);
    rerender(<FunctionTrace graph={{ ...graphFixture, edges: [] }} selectedNodeId="symbol:one" onSelectNode={onSelectNode} />);
    fireEvent.click(screen.getByRole("button", { name: "Run trace" }));
    expect(screen.getByText("No call relationships are reachable in this direction and depth. This is a valid empty trace.")).toBeTruthy();
  });
});
