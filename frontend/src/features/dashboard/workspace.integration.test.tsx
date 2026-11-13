import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import App from "../../app/App";
import { createAnalysis, deleteAnalysis, getAnalysisGraph, getSymbolInspector, getSymbolSource } from "../../services/analysisApi";
import { getBackendHealth } from "../../services/healthService";
import { graphFixture } from "../../test/graphFixtures";
import type { AnalysisInfo } from "../../types/analysis";
import type { SymbolInspectorResponse, SymbolSourceResponse } from "../../types/inspector";

vi.mock("../../services/healthService", () => ({ getBackendHealth: vi.fn() }));
vi.mock("../../services/analysisApi", () => ({
  createAnalysis: vi.fn(), deleteAnalysis: vi.fn(), getAnalysisGraph: vi.fn(),
  getSymbolInspector: vi.fn(), getSymbolSource: vi.fn(),
}));
vi.mock("../graph/GraphVisualization", () => ({
  GraphVisualization: ({ onNodeSelect, focusNodeId }: { onNodeSelect?: (id: string) => void; focusNodeId?: string | null }) => (
    <div><button type="button" onClick={() => onNodeSelect?.("symbol:two")}>Mock graph select</button><output aria-label="Focused graph node">{focusNodeId}</output></div>
  ),
}));

const analysisMock = vi.mocked(createAnalysis);
const graphMock = vi.mocked(getAnalysisGraph);
const healthMock = vi.mocked(getBackendHealth);
const inspectorMock = vi.mocked(getSymbolInspector);
const sourceMock = vi.mocked(getSymbolSource);

const analysis: AnalysisInfo = {
  analysis_id: "analysis-1", project_display_path: "sample", status: "completed", completed_at: "2026-11-11T00:00:00Z", project_ir_schema_version: 3,
  summary: { scanned_files: 2, parsed_files: 2, parse_total_files: 2, symbols: 4, call_sites: 3, call_dependencies: 3, import_dependencies: 1, graph_nodes: 6, graph_edges: 5 },
  diagnostics: { total: 0, project: 0, call_resolution: 0, imports: 0, cross_file_calls: 0, call_dependencies: 0 },
};

function inspectorResponse(symbolId: string, name: string): SymbolInspectorResponse {
  return {
    analysis_id: "analysis-1",
    symbol: { symbol_id: symbolId, language: "python", original_name: name, qualified_name: `pkg.${name}`, symbol_kind: "function", file_path: "src/module.py", source_location: { start_line: 1, end_line: 2, start_column: 0, end_column: 4 }, lexical_scope: [], parent_symbol_id: null, parameters: [], return_type: null, decorators: [], documentation: `Selected ${name}`, metadata: null },
    parent_symbol: null, source_file: null,
  };
}

const sourceResponse: SymbolSourceResponse = {
  analysis_id: "analysis-1", symbol_id: "one", file_path: "src/module.py", status: "unavailable", reason: "No source range.", code: null,
  start_line: null, end_line: null, truncated: false, changed_after_analysis: null, freshness: "unverified",
};

describe("analysis workspace integration", () => {
  beforeEach(() => {
    analysisMock.mockReset().mockResolvedValue(analysis);
    graphMock.mockReset().mockResolvedValue(graphFixture);
    healthMock.mockReset().mockResolvedValue({ status: "ok", application: "CodeAtlas", version: "0.1.0" });
    inspectorMock.mockReset().mockImplementation(async (_analysisId, symbolId) => inspectorResponse(symbolId, symbolId === "one" ? "run" : "run-two"));
    sourceMock.mockReset().mockImplementation(async (_analysisId, symbolId) => ({ ...sourceResponse, symbol_id: symbolId }));
    vi.mocked(deleteAnalysis).mockReset().mockResolvedValue({ analysis_id: "analysis-1", deleted: true });
  });

  it("shares exact selection between search, graph focus, trace navigation, and Inspector", async () => {
    render(<App />);
    fireEvent.change(screen.getByRole("textbox", { name: "Project path accessible to Flask" }), { target: { value: "C:/sample" } });
    fireEvent.click(screen.getByRole("button", { name: "Analyze project" }));
    expect(await screen.findByText("Graph loaded")).toBeTruthy();
    expect(graphMock).toHaveBeenCalledExactlyOnceWith("analysis-1");

    fireEvent.change(screen.getByRole("searchbox", { name: "Name or qualified name" }), { target: { value: "alpha.run" } });
    const searchResult = await screen.findByRole("button", { name: /alpha.run/ });
    fireEvent.click(searchResult);
    await waitFor(() => expect(screen.getByLabelText("Focused graph node").textContent).toBe("symbol:one"));
    expect(await screen.findByText("Selected run")).toBeTruthy();
    expect(screen.getByLabelText("Focused graph node").textContent).toBe("symbol:one");

    fireEvent.click(screen.getByRole("button", { name: "Run trace" }));
    fireEvent.click(screen.getByRole("button", { name: "Focus & use as root" }));
    await waitFor(() => expect(screen.getByLabelText("Focused graph node").textContent).toBe("symbol:two"));
    expect(screen.getByLabelText("Focused graph node").textContent).toBe("symbol:two");
    await waitFor(() => expect(inspectorMock).toHaveBeenLastCalledWith("analysis-1", "two", expect.any(AbortSignal)));

    fireEvent.click(screen.getByRole("button", { name: "Mock graph select" }));
    await waitFor(() => expect(screen.getByLabelText("Focused graph node").textContent).toBe("symbol:two"));
    expect(screen.getByText("Selected run-two")).toBeTruthy();
  });
});
