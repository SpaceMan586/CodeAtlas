import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { getSymbolInspector, getSymbolSource } from "../../services/analysisApi";
import { graphFixture } from "../../test/graphFixtures";
import type { InspectorSymbol, SymbolInspectorResponse, SymbolSourceResponse } from "../../types/inspector";
import { FunctionInspector } from "./FunctionInspector";

vi.mock("../../services/analysisApi", () => ({ getSymbolInspector: vi.fn(), getSymbolSource: vi.fn() }));

const detailsMock = vi.mocked(getSymbolInspector);
const sourceMock = vi.mocked(getSymbolSource);

function symbol(symbolId: string, name: string): InspectorSymbol {
  return {
    symbol_id: symbolId, language: "python", original_name: name, qualified_name: `pkg.${name}`,
    symbol_kind: "function", file_path: "src/module.py",
    source_location: { start_line: 2, end_line: 4, start_column: 0, end_column: 10 },
    lexical_scope: [], parent_symbol_id: null,
    parameters: [{ position: 0, name: "value", kind: "POSITIONAL_OR_KEYWORD", type_annotation: "str", default_value: "'x'", has_default: true, metadata: null }],
    return_type: "bool", decorators: ["cached"], documentation: "Useful docs.", metadata: { python: { is_async: true } },
  };
}

function details(analysisId: string, symbolId: string, name: string): SymbolInspectorResponse {
  return {
    analysis_id: analysisId, symbol: symbol(symbolId, name), parent_symbol: null,
    source_file: { file_path: "src/module.py", file_name: "module.py", file_extension: ".py", language: "python", size_bytes: 42, parsing_status: "parsed", source_line_count: 9, category: "source", metadata: null },
  };
}

const availableSource: SymbolSourceResponse = {
  analysis_id: "analysis-1", symbol_id: "one", file_path: "src/module.py", status: "available", reason: null,
  code: "def run(value):\n    return True\n", start_line: 2, end_line: 3, truncated: true,
  changed_after_analysis: null, freshness: "unverified",
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => { resolve = res; });
  return { promise, resolve };
}

describe("FunctionInspector", () => {
  beforeEach(() => { detailsMock.mockReset(); sourceMock.mockReset(); });

  it("maps exact symbol identity and displays metadata, source, and truncation state", async () => {
    detailsMock.mockResolvedValue(details("analysis-1", "one", "run"));
    sourceMock.mockResolvedValue(availableSource);
    render(<FunctionInspector analysisId="analysis-1" graph={graphFixture} selectedNodeId="symbol:one" />);
    expect(await screen.findByText("Useful docs.")).toBeTruthy();
    expect(screen.getByText("value")).toBeTruthy();
    expect(screen.getByText("@cached")).toBeTruthy();
    expect(screen.getByText("Async function")).toBeTruthy();
    expect(await screen.findByText("The source snippet was truncated at the display limit.")).toBeTruthy();
    expect(detailsMock).toHaveBeenCalledWith("analysis-1", "one", expect.any(AbortSignal));
    expect(sourceMock).toHaveBeenCalledWith("analysis-1", "one", expect.any(AbortSignal));
  });

  it("prevents a late response for the previous selection from replacing the current symbol", async () => {
    const first = deferred<SymbolInspectorResponse>();
    const second = deferred<SymbolInspectorResponse>();
    detailsMock.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    sourceMock.mockResolvedValue({ ...availableSource, symbol_id: "two", code: null, status: "unavailable", reason: "Source unavailable.", truncated: false });
    const { rerender } = render(<FunctionInspector analysisId="analysis-1" graph={graphFixture} selectedNodeId="symbol:one" />);
    rerender(<FunctionInspector analysisId="analysis-1" graph={graphFixture} selectedNodeId="symbol:two" />);
    second.resolve(details("analysis-1", "two", "second"));
    expect(await screen.findByText("Useful docs.")).toBeTruthy();
    expect(screen.getByText("pkg.second")).toBeTruthy();
    first.resolve(details("analysis-1", "one", "first"));
    await waitFor(() => expect(screen.queryByText("pkg.first")).toBeNull());
    expect(await screen.findByText("Source unavailable.")).toBeTruthy();
  });

  it("ignores late source responses after the selected symbol changes", async () => {
    const firstSource = deferred<SymbolSourceResponse>();
    const secondSource = deferred<SymbolSourceResponse>();
    detailsMock.mockResolvedValueOnce(details("analysis-1", "one", "first")).mockResolvedValueOnce(details("analysis-1", "two", "second"));
    sourceMock.mockReturnValueOnce(firstSource.promise).mockReturnValueOnce(secondSource.promise);
    const { rerender } = render(<FunctionInspector analysisId="analysis-1" graph={graphFixture} selectedNodeId="symbol:one" />);
    await waitFor(() => expect(sourceMock).toHaveBeenCalledWith("analysis-1", "one", expect.any(AbortSignal)));
    rerender(<FunctionInspector analysisId="analysis-1" graph={graphFixture} selectedNodeId="symbol:two" />);
    await waitFor(() => expect(sourceMock).toHaveBeenLastCalledWith("analysis-1", "two", expect.any(AbortSignal)));
    secondSource.resolve({ ...availableSource, symbol_id: "two", code: "SECOND_SOURCE", truncated: false });
    expect(await screen.findByText("SECOND_SOURCE")).toBeTruthy();
    firstSource.resolve({ ...availableSource, symbol_id: "one", code: "STALE_SOURCE", truncated: false });
    await waitFor(() => expect(screen.queryByText("STALE_SOURCE")).toBeNull());
    expect(screen.getByText("SECOND_SOURCE")).toBeTruthy();
  });

  it("shows empty, file, and unsupported states without requesting symbol data", () => {
    const { rerender } = render(<FunctionInspector analysisId="analysis-1" graph={graphFixture} selectedNodeId={null} />);
    expect(screen.getByText("Select a function, method, or class in the graph or from search/trace to inspect it.")).toBeTruthy();
    rerender(<FunctionInspector analysisId="analysis-1" graph={graphFixture} selectedNodeId="file:src/a.py" />);
    expect(screen.getByText("A file is selected. File-level source browsing is not available in this Inspector.")).toBeTruthy();
    rerender(<FunctionInspector analysisId="analysis-1" graph={graphFixture} selectedNodeId="symbol:unknown" />);
    expect(screen.getByText("The selected node is no longer present in the active analysis graph.")).toBeTruthy();
    expect(detailsMock).not.toHaveBeenCalled();
  });
});
