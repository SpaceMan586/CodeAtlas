import { lazy, Suspense, useEffect, useState, type FormEvent } from "react";
import { StatusBadge } from "../../components/common/StatusBadge";
import { useBackendHealth } from "../../hooks/useBackendHealth";
import { useAnalysisSession } from "../analysis/useAnalysisSession";
import { GraphVisualizationBoundary } from "../graph/GraphVisualizationBoundary";
import { FunctionSearch } from "../search/FunctionSearch";
import { FunctionTrace } from "../trace/FunctionTrace";
import { FunctionInspector } from "../inspector/FunctionInspector";
import type { AnalysisInfo } from "../../types/analysis";

const GraphVisualization = lazy(() => import("../graph/GraphVisualization").then((module) => ({ default: module.GraphVisualization })));

const summaryMetrics: Array<[string, keyof AnalysisInfo["summary"]]> = [
  ["Scanned files", "scanned_files"],
  ["Parsed files", "parsed_files"],
  ["Symbols", "symbols"],
  ["Call sites", "call_sites"],
  ["Call dependencies", "call_dependencies"],
  ["Import dependencies", "import_dependencies"],
];

const diagnosticMetrics: Array<[string, keyof AnalysisInfo["diagnostics"]]> = [
  ["Total", "total"],
  ["Project", "project"],
  ["Call resolution", "call_resolution"],
  ["Imports", "imports"],
  ["Cross-file calls", "cross_file_calls"],
  ["Call dependencies", "call_dependencies"],
];

function MetricGrid({ values }: { values: Array<[string, number]> }) {
  return (
    <dl className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-3">
      {values.map(([label, value]) => (
        <div className="rounded-md border border-atlas-line bg-atlas-raised p-3" key={label}>
          <dt className="text-xs text-atlas-muted">{label}</dt>
          <dd className="mt-1 text-lg font-semibold tabular-nums text-white">{value.toLocaleString()}</dd>
        </div>
      ))}
    </dl>
  );
}

export function OverviewPage() {
  const { state: backend, retry } = useBackendHealth();
  const { state: session, analyze, removeActive } = useAnalysisSession();
  const [projectPath, setProjectPath] = useState("");
  const [selection, setSelection] = useState<{ analysisId: string; nodeId: string } | null>(null);
  const [focusRequest, setFocusRequest] = useState(0);
  const activeAnalysisId = session.active?.analysis.analysis_id ?? null;
  const selectedNodeId = selection?.analysisId === activeAnalysisId ? selection.nodeId : null;
  const isBusy = ["SUBMITTING", "LOADING_GRAPH", "CLEANING_UP", "DELETING"].includes(session.phase);

  useEffect(() => {
    if (!selection) return;
    if (selection.analysisId !== activeAnalysisId) {
      setSelection(null);
      return;
    }
    if (!session.active?.graph.nodes.some((node) => node.id === selection.nodeId)) {
      setSelection(null);
    }
  }, [activeAnalysisId, selection, session.active?.graph]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!projectPath.trim() || isBusy) return;
    await analyze(projectPath.trim());
  }

  function selectNode(nodeId: string | null) {
    if (!nodeId || !activeAnalysisId) {
      setSelection(null);
      return;
    }
    setSelection({ analysisId: activeAnalysisId, nodeId });
    setFocusRequest((request) => request + 1);
  }

  return (
    <section aria-labelledby="overview-heading" className="space-y-7" id="overview">
      <div className="max-w-3xl">
        <p className="flex items-center gap-2 text-[10px] font-semibold tracking-[0.2em] text-atlas-accent">
          <span aria-hidden="true" className="h-px w-5 bg-atlas-accent" />
          SOFTWARE ARCHITECTURE EXPLORER
        </p>
        <h2 className="mt-4 text-3xl font-semibold leading-tight tracking-tight text-white sm:text-4xl" id="overview-heading">
          Understand how your codebase connects.
        </h2>
        <p className="mt-4 max-w-2xl text-sm leading-7 text-atlas-muted sm:text-base">
          Analyze a Python project that is available to the backend, then review its summary and graph data.
        </p>
      </div>

      <section aria-labelledby="analyze-heading" className="rounded-lg border border-atlas-line bg-atlas-surface p-5 sm:p-7">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="text-[10px] font-semibold tracking-[0.18em] text-atlas-muted">PROJECT ANALYSIS</p>
            <h3 className="mt-2 text-lg font-semibold text-white" id="analyze-heading">Choose a project directory</h3>
          </div>
          <StatusBadge>Python analysis</StatusBadge>
        </div>
        <form className="mt-5" onSubmit={submit}>
          <label className="mb-2 block text-sm font-medium text-atlas-copy" htmlFor="project-path">Project path accessible to Flask</label>
          <div className="flex flex-col gap-3 sm:flex-row">
            <input
              autoComplete="off"
              className="min-h-11 min-w-0 flex-1 rounded-md border border-atlas-line bg-atlas-raised px-3 text-sm text-white placeholder:text-atlas-dim focus:border-atlas-accent"
              id="project-path"
              onChange={(event) => setProjectPath(event.target.value)}
              placeholder={'C:\\Projects\\sample-python'}
              spellCheck={false}
              value={projectPath}
            />
            <button
              className="min-h-11 rounded-md bg-atlas-accent px-5 text-sm font-semibold text-atlas-bg transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
              disabled={!projectPath.trim() || isBusy}
              type="submit"
            >
              {session.phase === "SUBMITTING" ? "Analyzing…" : "Analyze project"}
            </button>
          </div>
          <p className="mt-2 text-xs text-atlas-muted">Enter a local directory path. The browser does not upload or modify project files.</p>
        </form>
        <div aria-live="polite" className="mt-4" role="status">
          {(session.phase === "SUBMITTING" || session.phase === "LOADING_GRAPH" || session.phase === "CLEANING_UP") && (
            <p className="text-sm text-atlas-accent">
              {session.phase === "SUBMITTING" && "Analysis is running. This may take a while."}
              {session.phase === "LOADING_GRAPH" && "Loading graph data…"}
              {session.phase === "CLEANING_UP" && "The new analysis is ready. Removing the previous session…"}
            </p>
          )}
          {session.phase === "DELETING" && <p className="text-sm text-atlas-accent">Deleting analysis session…</p>}
          {session.error && <p className="text-sm leading-6 text-rose-300" role="alert">{session.error}</p>}
        </div>
      </section>

      {session.active ? (
        <div className="space-y-4">
          <section aria-labelledby="analysis-summary-heading" className="rounded-lg border border-atlas-line bg-atlas-surface p-5 sm:p-7">
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div className="min-w-0">
                <p className="text-[10px] font-semibold tracking-[0.18em] text-atlas-muted">ANALYSIS SUMMARY</p>
                <h3 className="mt-2 break-words text-xl font-semibold text-white" id="analysis-summary-heading">{session.active.analysis.project_display_path}</h3>
                <p className="mt-1 break-all font-mono text-xs text-atlas-muted">{session.active.analysis.analysis_id}</p>
              </div>
              <span className="rounded border border-atlas-accent/25 bg-atlas-accent/10 px-2.5 py-1.5 text-xs font-medium text-atlas-accent">
                {session.active.analysis.status === "completed_with_warnings" ? "Completed with warnings" : session.active.analysis.status}
              </span>
            </div>
            <MetricGrid values={summaryMetrics.map(([label, key]) => [label, session.active!.analysis.summary[key]])} />
            <div className="mt-5 border-t border-atlas-line pt-4">
              <h4 className="text-sm font-medium text-atlas-copy">Diagnostics</h4>
              <MetricGrid values={diagnosticMetrics.map(([label, key]) => [label, session.active!.analysis.diagnostics[key]])} />
            </div>
            <p className="mt-4 text-xs text-atlas-muted">Completed {new Date(session.active.analysis.completed_at).toLocaleString()} · IR schema v{session.active.analysis.project_ir_schema_version}</p>
          </section>

          <section aria-labelledby="graph-summary-heading" className="rounded-lg border border-atlas-line bg-atlas-surface p-5 sm:p-7">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <p className="text-[10px] font-semibold tracking-[0.18em] text-atlas-muted">GRAPH DATA</p>
                <h3 className="mt-2 text-lg font-semibold text-white" id="graph-summary-heading">Graph loaded</h3>
              </div>
              <StatusBadge>{session.active.graph.nodes.length} nodes · {session.active.graph.edges.length} edges</StatusBadge>
            </div>
            <MetricGrid values={[
              ["Total nodes", session.active.graph.statistics.total_nodes],
              ["Total edges", session.active.graph.statistics.total_edges],
              ["File nodes", session.active.graph.statistics.nodes_by_category.file ?? 0],
              ["Symbol nodes", session.active.graph.statistics.nodes_by_category.symbol ?? 0],
              ["CONTAINS", session.active.graph.statistics.edges_by_relationship_type.CONTAINS ?? 0],
              ["CALLS", session.active.graph.statistics.edges_by_relationship_type.CALLS ?? 0],
              ["IMPORTS", session.active.graph.statistics.edges_by_relationship_type.IMPORTS ?? 0],
            ]} />
            {session.active.graph.nodes.length === 0 && (
              <p className="mt-4 text-sm text-atlas-muted">This analysis returned an empty graph.</p>
            )}
            <p className="mt-4 text-xs text-atlas-muted">Graph data retains original node IDs, edge keys, relationships, and metadata.</p>
            <div className="mt-5 grid items-start gap-4 xl:grid-cols-[minmax(260px,0.34fr)_minmax(0,0.66fr)]">
              <div className="min-w-0 space-y-4 xl:sticky xl:top-4 xl:max-h-[calc(100vh-2rem)] xl:overflow-y-auto xl:pr-2">
                <FunctionSearch
                  graph={session.active.graph}
                  key={`search:${session.active.analysis.analysis_id}`}
                  onSelectNode={(nodeId) => selectNode(nodeId)}
                  selectedNodeId={selectedNodeId}
                />
                <FunctionTrace
                  graph={session.active.graph}
                  key={`trace:${session.active.analysis.analysis_id}`}
                  onSelectNode={selectNode}
                  selectedNodeId={selectedNodeId}
                />
                <FunctionInspector
                  analysisId={session.active.analysis.analysis_id}
                  graph={session.active.graph}
                  key={`inspector:${session.active.analysis.analysis_id}`}
                  selectedNodeId={selectedNodeId}
                />
              </div>
              <div className="min-w-0">
                <GraphVisualizationBoundary key={session.active.analysis.analysis_id}>
                  <Suspense fallback={<div className="flex h-[620px] items-center justify-center rounded-lg border border-atlas-line bg-atlas-bg/70 text-sm text-atlas-muted" role="status">Loading graph renderer…</div>}>
                    <GraphVisualization graph={session.active.graph} onNodeSelect={selectNode} focusNodeId={selectedNodeId} focusRequest={focusRequest} />
                  </Suspense>
                </GraphVisualizationBoundary>
                <p aria-live="polite" className="mt-3 min-h-5 text-xs text-atlas-muted" role="status">
                  {selectedNodeId ? <>Selected node ID: <span className="break-all font-mono text-atlas-copy">{selectedNodeId}</span></> : "Select a graph node to see its ID here."}
                </p>
              </div>
            </div>
            <button
              className="mt-5 min-h-10 rounded-md border border-rose-400/30 px-4 text-sm font-medium text-rose-200 hover:bg-rose-400/10 disabled:cursor-not-allowed disabled:opacity-50"
              disabled={isBusy}
              onClick={() => void removeActive()}
              type="button"
            >
              Delete analysis session
            </button>
          </section>
        </div>
      ) : (
        session.phase === "IDLE" && (
          <section aria-label="Empty analysis workspace" className="rounded-lg border border-dashed border-atlas-line bg-atlas-surface/50 p-8 text-center">
            <p className="text-sm font-medium text-white">No project analyzed</p>
            <p className="mt-2 text-sm text-atlas-muted">Enter a backend-accessible path above to start an analysis.</p>
          </section>
        )
      )}

      <section aria-labelledby="backend-status-heading" className="rounded-lg border border-atlas-line bg-atlas-surface p-5">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <p className="text-[10px] font-semibold tracking-[0.18em] text-atlas-muted">BACKEND CONNECTION</p>
            <h3 className="mt-2 text-sm font-semibold text-white" id="backend-status-heading">
              {backend.status === "CHECKING" && "Checking Flask connection…"}
              {backend.status === "CONNECTED" && `${backend.health.application} · v${backend.health.version}`}
              {backend.status === "DISCONNECTED" && "Backend unavailable"}
            </h3>
            {backend.status === "DISCONNECTED" && <p className="mt-1 text-sm text-atlas-muted">{backend.error}</p>}
          </div>
          {backend.status === "DISCONNECTED" && (
            <button className="min-h-9 rounded-md border border-atlas-line px-3 text-xs font-medium text-atlas-copy hover:border-atlas-accent/50" onClick={retry} type="button">Retry connection</button>
          )}
        </div>
      </section>
    </section>
  );
}
