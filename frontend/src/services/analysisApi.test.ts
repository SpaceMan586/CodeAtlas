import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiRequestError } from "./api";
import { createAnalysis, deleteAnalysis, getAnalysis, getAnalysisGraph, getSymbolInspector, getSymbolSource } from "./analysisApi";
import { graphFixture } from "../test/graphFixtures";
import { jsonResponse } from "../test/responses";

const analysis = {
  analysis_id: "id/with space", project_display_path: "sample", status: "completed",
  completed_at: "2026-11-11T00:00:00+00:00", project_ir_schema_version: 3,
  summary: { scanned_files: 1, parsed_files: 1, parse_total_files: 1, symbols: 2, call_sites: 1, call_dependencies: 1, import_dependencies: 0, graph_nodes: 3, graph_edges: 2 },
  diagnostics: { total: 0, project: 0, call_resolution: 0, imports: 0, cross_file_calls: 0, call_dependencies: 0 },
};

const symbol = {
  symbol_id: "symbol/id + value", language: "python", original_name: "run", qualified_name: "Worker.run", symbol_kind: "method", file_path: "src/a.py",
  source_location: { start_line: 2, end_line: 3, start_column: 0, end_column: 12 }, lexical_scope: ["Worker"], parent_symbol_id: "class-id",
  parameters: [{ position: 0, name: "self", kind: "POSITIONAL_OR_KEYWORD", type_annotation: null, default_value: null, has_default: false, metadata: null }],
  return_type: "str", decorators: ["property"], documentation: "Runs it.", metadata: { python: { is_async: false } },
};

describe("analysisApi", () => {
  const fetchMock = vi.fn<typeof fetch>();
  beforeEach(() => { fetchMock.mockReset(); vi.stubGlobal("fetch", fetchMock); });

  it("creates, reads, and deletes an analysis with the backend HTTP contract", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(analysis, 201));
    await expect(createAnalysis("C:\\projects\\sample")).resolves.toMatchObject({ analysis_id: analysis.analysis_id });
    const [createUrl, createInit] = fetchMock.mock.calls[0]!;
    expect(createUrl).toBe("/api/analysis");
    expect(createInit?.method).toBe("POST");
    expect(JSON.parse(String(createInit?.body))).toEqual({ project_path: "C:\\projects\\sample" });
    expect((createInit?.headers as Headers).get("Content-Type")).toContain("application/json");

    fetchMock.mockResolvedValueOnce(jsonResponse(analysis));
    await getAnalysis("id/with space");
    expect(fetchMock.mock.calls[1]?.[0]).toBe("/api/analysis/id%2Fwith%20space");
    fetchMock.mockResolvedValueOnce(jsonResponse({ analysis_id: analysis.analysis_id, deleted: true }));
    await expect(deleteAnalysis(analysis.analysis_id)).resolves.toEqual({ analysis_id: analysis.analysis_id, deleted: true });
    expect(fetchMock.mock.calls[2]?.[1]?.method).toBe("DELETE");
  });

  it("preserves graph IDs, keys, metadata, and parallel relationships", async () => {
    fetchMock.mockResolvedValue(jsonResponse(graphFixture));
    const graph = await getAnalysisGraph("analysis-1");
    expect(graph.edges).toEqual(graphFixture.edges);
    expect(graph.edges.filter((edge) => edge.source === "symbol:one" && edge.target === "symbol:two")).toHaveLength(2);
  });

  it("encodes Inspector IDs, propagates signals, and validates selection identity", async () => {
    const controller = new AbortController();
    fetchMock.mockResolvedValueOnce(jsonResponse({ analysis_id: "analysis-1", symbol, parent_symbol: null, source_file: null }));
    const details = await getSymbolInspector("analysis-1", symbol.symbol_id, controller.signal);
    expect(details.symbol.parameters[0]?.name).toBe("self");
    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/analysis/analysis-1/symbols/lookup?symbol_id=symbol%2Fid+%2B+value");
    expect(fetchMock.mock.calls[0]?.[1]?.signal).toBeInstanceOf(AbortSignal);
    fetchMock.mockResolvedValueOnce(jsonResponse({
      analysis_id: "analysis-1", symbol_id: symbol.symbol_id, file_path: "src/a.py", status: "unavailable",
      reason: "missing", code: null, start_line: null, end_line: null, truncated: true,
      changed_after_analysis: null, freshness: "unverified",
    }));
    await expect(getSymbolSource("analysis-1", symbol.symbol_id)).resolves.toMatchObject({ status: "unavailable", truncated: true, code: null });

    fetchMock.mockResolvedValueOnce(jsonResponse({
      analysis_id: "analysis-1", symbol_id: symbol.symbol_id, file_path: "src/a.py", status: "available",
      reason: null, code: "def run(): ...", start_line: 1, end_line: 1, truncated: true,
      changed_after_analysis: true, freshness: "changed",
    }));
    await expect(getSymbolSource("analysis-1", symbol.symbol_id)).resolves.toMatchObject({ code: "def run(): ...", truncated: true, changed_after_analysis: true, freshness: "changed" });

    fetchMock.mockResolvedValueOnce(jsonResponse({ analysis_id: "wrong", symbol, parent_symbol: null, source_file: null }));
    await expect(getSymbolInspector("analysis-1", symbol.symbol_id)).rejects.toMatchObject({ kind: "invalid-response" });
  });

  it("propagates backend errors and rejects malformed response shapes", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ error: { status: 404, message: "Analysis was not found." } }, 404));
    await expect(getAnalysis("unknown")).rejects.toMatchObject({ name: "ApiRequestError", kind: "http", message: "Analysis was not found." });
    fetchMock.mockResolvedValueOnce(jsonResponse({ analysis_id: "bad" }));
    await expect(getAnalysisGraph("bad")).rejects.toBeInstanceOf(ApiRequestError);
    fetchMock.mockResolvedValueOnce(jsonResponse({ analysis_id: "other", deleted: true }));
    await expect(deleteAnalysis("wanted")).rejects.toMatchObject({ kind: "invalid-response" });
  });
});
