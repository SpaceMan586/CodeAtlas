import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { FunctionSearch } from "./FunctionSearch";
import { graphFixture } from "../../test/graphFixtures";

describe("FunctionSearch", () => {
  it("searches names and qualified names without collapsing duplicate IDs or requesting the API", async () => {
    const onSelectNode = vi.fn();
    const graph = structuredClone(graphFixture);
    graph.nodes.push({ id: "symbol:unsupported", data: { node_type: "symbol", symbol_id: "unsupported", symbol_kind: "constant", name: "run" } });
    graph.nodes.push({ id: "file:run.py", data: { node_type: "file", file_path: "run.py" } });
    render(<FunctionSearch graph={graph} selectedNodeId="symbol:two" onSelectNode={onSelectNode} />);

    fireEvent.change(screen.getByRole("searchbox", { name: "Name or qualified name" }), { target: { value: "BETA.RUN" } });
    await screen.findByRole("list", { name: "Function search results" });
    await waitFor(() => expect(screen.getByRole("button", { name: /run function/ })).toBeTruthy());
    expect(screen.getByText("src/b.py · line 8")).toBeTruthy();

    fireEvent.change(screen.getByRole("searchbox", { name: "Name or qualified name" }), { target: { value: "run" } });
    await waitFor(() => expect(screen.getAllByRole("button", { name: /run (function|method)/ })).toHaveLength(2));
    const resultButtons = screen.getAllByRole("button", { name: /run (function|method)/ });
    expect(resultButtons[1]?.getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(resultButtons[0]!);
    expect(onSelectNode).toHaveBeenCalledWith("symbol:one");
    expect(screen.queryByText("unsupported")).toBeNull();
  });

  it("applies kind and path filters and clears query and filters", async () => {
    render(<FunctionSearch graph={graphFixture} selectedNodeId={null} onSelectNode={vi.fn()} />);
    fireEvent.change(screen.getByRole("searchbox", { name: "Name or qualified name" }), { target: { value: "run" } });
    await waitFor(() => expect(screen.getAllByRole("button", { name: /run (function|method)/ })).toHaveLength(2));
    fireEvent.change(screen.getByLabelText("Kind"), { target: { value: "function" } });
    await waitFor(() => expect(screen.getAllByRole("button", { name: /run function/ })).toHaveLength(2));
    fireEvent.change(screen.getByLabelText("File path contains"), { target: { value: "src/b" } });
    await waitFor(() => expect(screen.getAllByRole("button", { name: /run function/ })).toHaveLength(1));
    fireEvent.click(screen.getByRole("button", { name: "Clear search and filters" }));
    expect(screen.getByText("Enter a name to search. Results are limited to functions and methods in the active analysis.")).toBeTruthy();
  });
});
