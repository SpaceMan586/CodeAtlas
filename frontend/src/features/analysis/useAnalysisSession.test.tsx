import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { createAnalysis, deleteAnalysis, getAnalysisGraph } from "../../services/analysisApi";
import type { AnalysisInfo } from "../../types/analysis";
import type { GraphData } from "../../types/graph";
import { graphFixture } from "../../test/graphFixtures";
import { useAnalysisSession } from "./useAnalysisSession";

vi.mock("../../services/analysisApi", () => ({ createAnalysis: vi.fn(), deleteAnalysis: vi.fn(), getAnalysisGraph: vi.fn() }));

const createMock = vi.mocked(createAnalysis);
const deleteMock = vi.mocked(deleteAnalysis);
const graphMock = vi.mocked(getAnalysisGraph);

function analysis(id: string): AnalysisInfo {
  return {
    analysis_id: id, project_display_path: id, status: "completed", completed_at: "2026-11-11T00:00:00Z", project_ir_schema_version: 3,
    summary: { scanned_files: 0, parsed_files: 0, parse_total_files: 0, symbols: 0, call_sites: 0, call_dependencies: 0, import_dependencies: 0, graph_nodes: 0, graph_edges: 0 },
    diagnostics: { total: 0, project: 0, call_resolution: 0, imports: 0, cross_file_calls: 0, call_dependencies: 0 },
  };
}

function graph(id: string): GraphData { return { ...structuredClone(graphFixture), analysis_id: id }; }
function deferred<T>() { let resolve!: (value: T) => void; let reject!: (reason?: unknown) => void; const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; }); return { promise, resolve, reject }; }

describe("useAnalysisSession", () => {
  beforeEach(() => { createMock.mockReset(); deleteMock.mockReset(); graphMock.mockReset(); });

  it("starts empty, creates once, reuses the POST result, then loads exactly one graph", async () => {
    createMock.mockResolvedValue(analysis("new"));
    graphMock.mockResolvedValue(graph("new"));
    const { result } = renderHook(() => useAnalysisSession());
    expect(result.current.state).toEqual({ phase: "IDLE", active: null, error: null });
    await act(async () => { await result.current.analyze("project path"); });
    expect(createMock).toHaveBeenCalledTimes(1);
    expect(graphMock).toHaveBeenCalledExactlyOnceWith("new");
    expect(result.current.state).toMatchObject({ phase: "READY", active: { analysis: { analysis_id: "new" }, graph: { analysis_id: "new" } } });
    expect(deleteMock).not.toHaveBeenCalled();
  });

  it("prevents duplicate submissions while analysis creation is in flight", async () => {
    const pending = deferred<AnalysisInfo>();
    createMock.mockReturnValue(pending.promise);
    graphMock.mockResolvedValue(graph("only"));
    const { result } = renderHook(() => useAnalysisSession());
    let first!: Promise<void>;
    act(() => {
      first = result.current.analyze("project");
      void result.current.analyze("duplicate");
    });
    pending.resolve(analysis("only"));
    await act(async () => { await first; });
    expect(createMock).toHaveBeenCalledExactlyOnceWith("project");
    expect(result.current.state.active?.analysis.analysis_id).toBe("only");
  });

  it("keeps the previous session active while replacement graph loads, then cleans it up", async () => {
    createMock.mockResolvedValueOnce(analysis("old")).mockResolvedValueOnce(analysis("next"));
    graphMock.mockResolvedValueOnce(graph("old"));
    const pendingGraph = deferred<GraphData>();
    graphMock.mockReturnValueOnce(pendingGraph.promise);
    deleteMock.mockResolvedValue({ analysis_id: "old", deleted: true });
    const { result } = renderHook(() => useAnalysisSession());
    await act(async () => { await result.current.analyze("old"); });
    let replacement!: Promise<void>;
    act(() => { replacement = result.current.analyze("next"); });
    await waitFor(() => expect(result.current.state.phase).toBe("LOADING_GRAPH"));
    expect(result.current.state.active?.analysis.analysis_id).toBe("old");
    await act(async () => { pendingGraph.resolve(graph("next")); await replacement; });
    expect(result.current.state).toMatchObject({ phase: "READY", active: { analysis: { analysis_id: "next" } }, error: null });
    expect(deleteMock).toHaveBeenCalledExactlyOnceWith("old");
  });

  it("retains the old session after failed replacement and reports failed cleanup of incomplete work", async () => {
    createMock.mockResolvedValueOnce(analysis("old")).mockResolvedValueOnce(analysis("broken"));
    graphMock.mockResolvedValueOnce(graph("old")).mockRejectedValueOnce(new Error("graph failed"));
    deleteMock.mockRejectedValueOnce(new Error("cleanup failed"));
    const { result } = renderHook(() => useAnalysisSession());
    await act(async () => { await result.current.analyze("old"); });
    await act(async () => { await result.current.analyze("new"); });
    expect(result.current.state.active?.analysis.analysis_id).toBe("old");
    expect(result.current.state.phase).toBe("ERROR");
    expect(result.current.state.error).toContain("incomplete session could not be removed");
  });

  it("keeps a successfully loaded replacement when old-session cleanup fails; failed deletion keeps active state", async () => {
    createMock.mockResolvedValueOnce(analysis("old")).mockResolvedValueOnce(analysis("new"));
    graphMock.mockResolvedValueOnce(graph("old")).mockResolvedValueOnce(graph("new"));
    deleteMock.mockRejectedValueOnce(new Error("old session unavailable"));
    const { result } = renderHook(() => useAnalysisSession());
    await act(async () => { await result.current.analyze("old"); });
    await act(async () => { await result.current.analyze("new"); });
    expect(result.current.state).toMatchObject({ phase: "READY", active: { analysis: { analysis_id: "new" } }, error: expect.stringContaining("previous session") });
    deleteMock.mockRejectedValueOnce(new Error("delete failed"));
    await act(async () => { await result.current.removeActive(); });
    expect(result.current.state).toMatchObject({ phase: "ERROR", active: { analysis: { analysis_id: "new" } }, error: expect.any(String) });
  });

  it("rejects graph/session mismatches and deletes the incomplete created session", async () => {
    createMock.mockResolvedValue(analysis("new"));
    graphMock.mockResolvedValue(graph("different"));
    deleteMock.mockResolvedValue({ analysis_id: "new", deleted: true });
    const { result } = renderHook(() => useAnalysisSession());
    await act(async () => { await result.current.analyze("project"); });
    expect(result.current.state.phase).toBe("ERROR");
    expect(result.current.state.error).toContain("different analysis");
    expect(deleteMock).toHaveBeenCalledWith("new");
  });
});
