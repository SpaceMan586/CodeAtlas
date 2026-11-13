import { useRef, useState } from "react";
import { ApiRequestError } from "../../services/api";
import { createAnalysis, deleteAnalysis, getAnalysisGraph } from "../../services/analysisApi";
import type { AnalysisInfo } from "../../types/analysis";
import type { GraphData } from "../../types/graph";

export type AnalysisPhase = "IDLE" | "SUBMITTING" | "LOADING_GRAPH" | "CLEANING_UP" | "READY" | "ERROR" | "DELETING";

export interface ActiveAnalysis {
  analysis: AnalysisInfo;
  graph: GraphData;
}

export interface AnalysisSessionState {
  phase: AnalysisPhase;
  active: ActiveAnalysis | null;
  error: string | null;
}

const initialState: AnalysisSessionState = { phase: "IDLE", active: null, error: null };

function errorMessage(error: unknown): string {
  if (error instanceof ApiRequestError) return error.message;
  return "The analysis request failed unexpectedly. Please try again.";
}

export function useAnalysisSession() {
  const [state, setState] = useState<AnalysisSessionState>(initialState);
  const busy = useRef(false);

  async function analyze(projectPath: string): Promise<void> {
    if (busy.current) return;
    busy.current = true;
    const previous = state.active;
    setState({ phase: "SUBMITTING", active: previous, error: null });
    let createdId: string | null = null;
    try {
      const created = await createAnalysis(projectPath);
      createdId = created.analysis_id;
      setState({ phase: "LOADING_GRAPH", active: previous, error: null });
      const graph = await getAnalysisGraph(createdId);
      if (graph.analysis_id !== createdId) {
        throw new ApiRequestError("Flask returned graph data for a different analysis.", "invalid-response");
      }
      const completed = { analysis: created, graph };
      const shouldCleanPrevious = previous !== null && previous.analysis.analysis_id !== createdId;
      setState({ phase: shouldCleanPrevious ? "CLEANING_UP" : "READY", active: completed, error: null });
      if (shouldCleanPrevious && previous) {
        try {
          await deleteAnalysis(previous.analysis.analysis_id);
          setState({ phase: "READY", active: completed, error: null });
        } catch {
          setState({
            phase: "READY",
            active: completed,
            error: "The new analysis is ready, but the previous session could not be removed from the backend.",
          });
        }
      }
    } catch (error) {
      let cleanupFailed = false;
      if (createdId && createdId !== previous?.analysis.analysis_id) {
        try {
          await deleteAnalysis(createdId);
        } catch {
          cleanupFailed = true;
        }
      }
      setState({
        phase: "ERROR",
        active: previous,
        error: `${errorMessage(error)}${cleanupFailed ? " The incomplete session could not be removed." : ""}`,
      });
    } finally {
      busy.current = false;
    }
  }

  async function removeActive(): Promise<void> {
    if (busy.current || !state.active) return;
    busy.current = true;
    const active = state.active;
    setState({ phase: "DELETING", active, error: null });
    try {
      await deleteAnalysis(active.analysis.analysis_id);
      setState(initialState);
    } catch (error) {
      setState({ phase: "ERROR", active, error: errorMessage(error) });
    } finally {
      busy.current = false;
    }
  }

  return { state, analyze, removeActive };
}
