import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { graphFixture } from "../../test/graphFixtures";
import { GraphVisualization } from "./GraphVisualization";

const { cytoscapeMock, targetMock, coreMock, collectionMock, otherSelectedMock } = vi.hoisted(() => {
  const otherSelectedMock = { unselect: vi.fn() };
  const target = { empty: vi.fn(() => false), isNode: vi.fn(() => true), selected: vi.fn(() => false), select: vi.fn(), id: vi.fn(() => "symbol:two") };
  const collection = { remove: vi.fn(), unselect: vi.fn(), not: vi.fn(() => otherSelectedMock), length: 0, 0: target };
  const core = {
    elements: vi.fn(() => collection), add: vi.fn(), layout: vi.fn(() => ({ run: vi.fn() })),
    on: vi.fn(), destroy: vi.fn(), $: vi.fn(() => collection), getElementById: vi.fn(() => target),
    center: vi.fn(), fit: vi.fn(), resize: vi.fn(), width: vi.fn(() => 100), height: vi.fn(() => 80),
    zoom: vi.fn((value?: unknown) => value ? undefined : 1),
  };
  const cytoscape = vi.fn(() => core);
  return { cytoscapeMock: cytoscape, targetMock: target, coreMock: core, collectionMock: collection, otherSelectedMock };
});

vi.mock("cytoscape", () => ({ default: cytoscapeMock }));

describe("GraphVisualization", () => {
  beforeEach(() => {
    cytoscapeMock.mockClear();
    for (const value of Object.values(coreMock)) if (typeof value === "function") value.mockClear();
    targetMock.select.mockClear();
  });

  it("initializes one Cytoscape instance, loads parallel edges, focuses exact IDs, and cleans up", () => {
    const onNodeSelect = vi.fn();
    const { unmount, rerender } = render(<GraphVisualization graph={graphFixture} onNodeSelect={onNodeSelect} focusNodeId="symbol:two" focusRequest={1} />);
    expect(cytoscapeMock).toHaveBeenCalledTimes(1);
    expect(coreMock.add).toHaveBeenCalledTimes(1);
    expect(coreMock.add.mock.calls[0]?.[0]).toHaveLength(graphFixture.nodes.length + graphFixture.edges.length);
    expect(coreMock.center).toHaveBeenCalledWith(targetMock);
    expect(collectionMock.not).toHaveBeenCalledWith(targetMock);
    expect(otherSelectedMock.unselect).toHaveBeenCalled();
    expect(targetMock.select).toHaveBeenCalled();

    const selectRegistration = coreMock.on.mock.calls.find(([event, selector]) => event === "select" && selector === "node");
    const handler = selectRegistration?.[2] as ((event: { target: { isNode: () => boolean; id: () => string } }) => void) | undefined;
    handler?.({ target: { isNode: () => true, id: () => "symbol:two" } });
    expect(onNodeSelect).toHaveBeenCalledWith("symbol:two");

    fireEvent.change(screen.getByLabelText("Graph layout"), { target: { value: "circle" } });
    expect(coreMock.layout).toHaveBeenLastCalledWith(expect.objectContaining({ name: "circle" }));
    fireEvent.click(screen.getByRole("button", { name: "Zoom in" }));
    expect(coreMock.zoom).toHaveBeenLastCalledWith(expect.objectContaining({ level: 1.2 }));
    window.dispatchEvent(new Event("resize"));
    expect(coreMock.resize).toHaveBeenCalled();

    rerender(<GraphVisualization graph={{ ...graphFixture, nodes: [...graphFixture.nodes] }} onNodeSelect={onNodeSelect} />);
    expect(cytoscapeMock).toHaveBeenCalledTimes(1);
    expect(coreMock.add).toHaveBeenCalledTimes(2);
    unmount();
    expect(coreMock.destroy).toHaveBeenCalledTimes(1);
    expect(onNodeSelect).toHaveBeenLastCalledWith(null);
  });

  it("shows empty-graph state and accessible controls, and displays malformed-data errors", () => {
    const empty = { ...graphFixture, nodes: [], edges: [], statistics: { ...graphFixture.statistics, node_count: 0, edge_count: 0, total_nodes: 0, total_edges: 0, nodes_by_category: {}, edges_by_relationship_type: {} } };
    const { rerender } = render(<GraphVisualization graph={empty} />);
    expect(screen.getByRole("status").textContent).toContain("contains no graph nodes");
    expect(screen.getByRole("button", { name: "Zoom in" }).hasAttribute("disabled")).toBe(true);
    rerender(<GraphVisualization graph={{ ...graphFixture, statistics: { ...graphFixture.statistics, total_edges: 0 } }} />);
    expect(screen.getByRole("alert").textContent).toContain("Graph counts do not match");
  });
});
