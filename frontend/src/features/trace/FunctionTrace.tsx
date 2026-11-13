import { useEffect, useMemo, useState } from "react";
import type { GraphData, GraphNode } from "../../types/graph";
import {
  CallTraceError,
  createCallTraceIndex,
  runCallTrace,
  type CallTraceResult,
  type TraceDirection,
} from "./callTrace";

interface FunctionTraceProps {
  graph: GraphData;
  selectedNodeId: string | null;
  onSelectNode: (nodeId: string) => void;
}

function optionalString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function sourceLine(node: GraphNode | undefined): number | null {
  const location = node?.data.source_location;
  if (typeof location !== "object" || location === null || Array.isArray(location)) return null;
  const line = (location as Record<string, unknown>).start_line;
  return typeof line === "number" && Number.isInteger(line) && line > 0 ? line : null;
}

function label(node: GraphNode | undefined, nodeId: string): string {
  return optionalString(node?.data.name) || optionalString(node?.data.qualified_name) || nodeId;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function relationshipEvidence(data: GraphData["edges"][number]["data"]): Array<{ filePath: string; line: number | null; occurrence: number | null; provenance: string }> {
  const metadata = isRecord(data.metadata) ? data.metadata : {};
  const evidenceItems = Array.isArray(data.evidence) ? data.evidence : [];
  const parsed = evidenceItems.flatMap((item) => {
    if (!isRecord(item)) return [];
    const reference = isRecord(item.call_site_reference) ? item.call_site_reference : {};
    const location = isRecord(reference.source_location) ? reference.source_location : {};
    const line = typeof location.start_line === "number" && Number.isInteger(location.start_line) ? location.start_line : null;
    const occurrence = typeof reference.occurrence_order === "number" && Number.isInteger(reference.occurrence_order) ? reference.occurrence_order : null;
    const filePath = typeof reference.file_path === "string" ? reference.file_path : "";
    const provenance = typeof item.provenance === "string"
      ? item.provenance
      : typeof metadata.resolution_provenance === "string" ? metadata.resolution_provenance : "";
    return [{ filePath, line, occurrence, provenance }];
  });
  if (parsed.length > 0) return parsed;
  const sourceLocation = isRecord(data.source_location) ? data.source_location : {};
  return [{
    filePath: "",
    line: typeof sourceLocation.start_line === "number" ? sourceLocation.start_line : null,
    occurrence: null,
    provenance: typeof metadata.resolution_provenance === "string" ? metadata.resolution_provenance : "",
  }];
}

export function FunctionTrace({ graph, selectedNodeId, onSelectNode }: FunctionTraceProps) {
  const [direction, setDirection] = useState<TraceDirection>("OUTGOING");
  const [depth, setDepth] = useState(3);
  const [result, setResult] = useState<CallTraceResult | null>(null);
  const [resultGraph, setResultGraph] = useState<GraphData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [indexError, setIndexError] = useState<string | null>(null);

  const indexState = useMemo(() => {
    try {
      return { index: createCallTraceIndex(graph), error: null };
    } catch (caught) {
      return {
        index: null,
        error: caught instanceof Error ? caught.message : "The graph call relationships are invalid.",
      };
    }
  }, [graph]);
  const selectedRoot = selectedNodeId ? graph.nodes.find((node) => node.id === selectedNodeId) : undefined;
  const selectedKind = optionalString(selectedRoot?.data.symbol_kind).toLowerCase();
  const rootIsValid = selectedRoot?.data.node_type === "symbol" && (selectedKind === "function" || selectedKind === "method");
  const rootName = selectedRoot ? label(selectedRoot, selectedRoot.id) : "";
  const rootLine = sourceLine(selectedRoot);

  useEffect(() => {
    setResult(null);
    setResultGraph(null);
    setError(null);
  }, [graph, selectedNodeId]);

  useEffect(() => {
    setIndexError(indexState.error);
    if (indexState.error) {
      setResult(null);
      setResultGraph(null);
    }
  }, [indexState]);

  const nodeById = useMemo(() => new Map(graph.nodes.map((node) => [node.id, node])), [graph]);
  const relationshipsByDepth = useMemo(() => {
    const groups = new Map<number, CallTraceResult["relationships"]>();
    for (const relationship of result?.relationships ?? []) {
      const group = groups.get(relationship.depth) ?? [];
      group.push(relationship);
      groups.set(relationship.depth, group);
    }
    return [...groups.entries()].sort(([a], [b]) => a - b);
  }, [result]);

  function executeTrace() {
    if (!selectedNodeId || !indexState.index) return;
    try {
      setResult(runCallTrace(indexState.index, selectedNodeId, direction, depth));
      setResultGraph(graph);
      setError(null);
    } catch (caught) {
      setResult(null);
      setResultGraph(null);
      setError(caught instanceof CallTraceError ? caught.message : "The function trace could not be calculated.");
    }
  }

  return (
    <section aria-labelledby="function-trace-heading" className="rounded-lg border border-atlas-line bg-atlas-surface p-4 sm:p-5">
      <div>
        <p className="text-[10px] font-semibold tracking-[0.18em] text-atlas-muted">CALL DEPENDENCIES</p>
        <h3 className="mt-1 text-base font-semibold text-white" id="function-trace-heading">Function Trace</h3>
        <p className="mt-1 text-xs leading-5 text-atlas-muted">Follow statically resolved CALLS relationships from the selected function or method.</p>
      </div>

      {rootIsValid && selectedRoot ? (
        <div className="mt-4 rounded-md border border-atlas-accent/20 bg-atlas-accent/5 p-3">
          <p className="text-[10px] font-semibold uppercase tracking-wide text-atlas-accent">Trace root · {selectedKind}</p>
          <p className="mt-1 break-words text-sm font-semibold text-white">{rootName}</p>
          {optionalString(selectedRoot.data.qualified_name) && <p className="mt-1 break-all text-xs text-atlas-muted">{optionalString(selectedRoot.data.qualified_name)}</p>}
          {optionalString(selectedRoot.data.file_path) && <p className="mt-1 break-all text-xs text-atlas-muted">{optionalString(selectedRoot.data.file_path)}{rootLine ? ` · line ${rootLine}` : ""}</p>}
        </div>
      ) : (
        <div className="mt-4 rounded-md border border-atlas-line bg-atlas-raised p-3 text-xs leading-5 text-atlas-muted" role="status">
          {!selectedNodeId && "Select a function or method through Function Search or the graph to choose a trace root."}
          {selectedNodeId && !selectedRoot && "The selected node is not present in this graph. Choose a current graph node."}
          {selectedRoot && selectedRoot.data.node_type === "file" && "Files cannot be trace roots. Select a function or method."}
          {selectedRoot && selectedRoot.data.node_type === "symbol" && !rootIsValid && "This symbol is not a function or method. Select a function or method."}
        </div>
      )}

      <div className="mt-4 grid grid-cols-2 gap-2">
        <div>
          <label className="mb-1 block text-xs text-atlas-muted" htmlFor="trace-direction">Direction</label>
          <select className="min-h-9 w-full rounded-md border border-atlas-line bg-atlas-raised px-2 text-xs text-atlas-copy focus:border-atlas-accent" id="trace-direction" onChange={(event) => setDirection(event.target.value as TraceDirection)} value={direction}>
            <option value="OUTGOING">Outgoing</option>
            <option value="INCOMING">Incoming</option>
            <option value="BOTH">Both directions</option>
          </select>
        </div>
        <div>
          <label className="mb-1 block text-xs text-atlas-muted" htmlFor="trace-depth">Maximum depth</label>
          <select className="min-h-9 w-full rounded-md border border-atlas-line bg-atlas-raised px-2 text-xs text-atlas-copy focus:border-atlas-accent" id="trace-depth" onChange={(event) => setDepth(Number(event.target.value))} value={depth}>
            {Array.from({ length: 10 }, (_, index) => index + 1).map((value) => <option key={value} value={value}>{value}</option>)}
          </select>
        </div>
      </div>
      <p className="mt-2 text-[10px] text-atlas-muted">Root is depth 0; direct calls are depth 1. Maximum depth is 10.</p>
      <button
        className="mt-3 min-h-10 w-full rounded-md bg-atlas-accent px-3 text-sm font-semibold text-atlas-bg transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
        disabled={!rootIsValid || !indexState.index}
        onClick={executeTrace}
        type="button"
      >
        Run trace
      </button>

      {(error || indexError) && <p className="mt-3 text-xs leading-5 text-rose-300" role="alert">{error ?? indexError}</p>}

      {result && resultGraph === graph && result.rootNodeId === selectedNodeId && (
        <div className="mt-4 border-t border-atlas-line pt-3">
          <p className="text-xs font-medium text-atlas-copy">Current result: {result.direction.toLowerCase()} · depth {result.maxDepth}</p>
          <p className="mt-1 text-[11px] text-atlas-muted">{result.nodeCount} nodes · {result.relationshipCount} individual CALLS relationships · reached depth {result.maximumDepthReached}</p>
          {result.depthBoundaryReached && <p className="mt-2 rounded-md border border-amber-300/20 bg-amber-300/5 p-2 text-[11px] leading-5 text-amber-200">The depth limit blocked additional reachable nodes. Increase depth to continue.</p>}
          {result.relationshipCount === 0 && <p className="mt-3 text-xs text-atlas-muted">No call relationships are reachable in this direction and depth. This is a valid empty trace.</p>}

          {result.nodesByDepth.map((nodes, level) => nodes.length > 0 && (
            <section aria-label={`Trace nodes at depth ${level}`} className="mt-4" key={level}>
              <h4 className="text-xs font-semibold uppercase tracking-wide text-atlas-accent">Depth {level}</h4>
              <ul className="mt-2 space-y-2">
                {nodes.map((node) => {
                  const graphNode = nodeById.get(node.id);
                  const kind = optionalString(graphNode?.data.symbol_kind);
                  const nodeName = label(graphNode, node.id);
                  const line = sourceLine(graphNode);
                  return (
                    <li className="rounded-md border border-atlas-line bg-atlas-raised p-2.5" key={node.id}>
                      <p className="break-words text-xs font-semibold text-white">{nodeName}{kind === "class" && <span className="ml-1.5 font-normal text-atlas-muted">constructor target</span>}</p>
                      {optionalString(graphNode?.data.qualified_name) && <p className="mt-1 break-all text-[11px] text-atlas-accent">{optionalString(graphNode?.data.qualified_name)}</p>}
                      <p className="mt-1 break-all text-[11px] text-atlas-muted">{kind}{optionalString(graphNode?.data.file_path) && ` · ${optionalString(graphNode?.data.file_path)}${line ? `:${line}` : ""}`}</p>
                      <button className="graph-control mt-2 min-h-8 px-2 text-[11px]" onClick={() => onSelectNode(node.id)} type="button">{kind === "class" ? "Focus constructor target" : node.id === result.rootNodeId ? "Focus root" : "Focus & use as root"}</button>
                    </li>
                  );
                })}
              </ul>
            </section>
          ))}

          {relationshipsByDepth.map(([relationshipDepth, relationships]) => (
            <section aria-label={`Trace call relationships at depth ${relationshipDepth}`} className="mt-4" key={`relationships-${relationshipDepth}`}>
              <h4 className="text-xs font-semibold uppercase tracking-wide text-atlas-accent">Relationships · depth {relationshipDepth}</h4>
              <ul className="mt-2 max-h-72 space-y-2 overflow-y-auto pr-1">
                {relationships.map((relationship) => {
                  const caller = nodeById.get(relationship.source);
                  const callee = nodeById.get(relationship.target);
                  return (
                    <li className="rounded-md border border-atlas-line bg-atlas-raised p-2.5" key={relationship.id}>
                      <p className="mt-1 break-words text-xs text-white"><span className="text-atlas-accent">Caller:</span> {label(caller, relationship.source)}</p>
                      <p className="mt-0.5 break-words text-xs text-white"><span className="text-atlas-accent">Callee:</span> {label(callee, relationship.target)}</p>
                      {callee && sourceLine(callee) && <p className="mt-1 break-all text-[10px] text-atlas-muted">Target declaration: {optionalString(callee.data.file_path)}{optionalString(callee.data.file_path) ? ":" : "line "}{sourceLine(callee)}</p>}
                      <p className="mt-1 break-all font-mono text-[10px] text-atlas-muted">Edge ID: {relationship.id}</p>
                      <p className="mt-1 break-all font-mono text-[10px] text-atlas-muted">Edge key: {relationship.key}</p>
                      {relationshipEvidence(relationship.data).map((item, evidenceIndex) => (
                        <p className="mt-1 break-all text-[10px] text-atlas-muted" key={`${relationship.id}:${evidenceIndex}`}>
                          {item.filePath && `Call site: ${item.filePath}`}{item.line && `:${item.line}`}{item.occurrence !== null && ` · occurrence ${item.occurrence}`}{item.provenance && ` · ${item.provenance}`}
                          {!item.filePath && !item.line && !item.occurrence && !item.provenance && "Call-site details unavailable"}
                        </p>
                      ))}
                    </li>
                  );
                })}
              </ul>
            </section>
          ))}
        </div>
      )}
    </section>
  );
}
