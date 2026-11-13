import { useEffect, useMemo, useState } from "react";
import { ApiRequestError } from "../../services/api";
import { getSymbolInspector, getSymbolSource } from "../../services/analysisApi";
import type { GraphData, GraphNode } from "../../types/graph";
import type { InspectorSymbol, SymbolInspectorResponse, SymbolSourceResponse } from "../../types/inspector";

interface FunctionInspectorProps {
  analysisId: string;
  graph: GraphData;
  selectedNodeId: string | null;
}

type Keyed<T> = { key: string; value: T };
type LoadState<T> = Keyed<T> | { key: string; error: string } | { key: string; loading: true } | null;

function isInspectable(node: GraphNode | undefined): boolean {
  if (!node || node.data.node_type !== "symbol") return false;
  const kind = node.data.symbol_kind;
  return typeof kind === "string" && ["function", "method", "class"].includes(kind.toLowerCase());
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiRequestError) return error.message;
  return "The Inspector request failed unexpectedly.";
}

function metadataSection(symbol: InspectorSymbol): Record<string, unknown> | null {
  const metadata = symbol.metadata;
  if (!metadata) return null;
  const python = metadata.python;
  if (typeof python === "object" && python !== null && !Array.isArray(python)) {
    return python as Record<string, unknown>;
  }
  return metadata;
}

function DependencySummary({ graph, nodeId }: { graph: GraphData; nodeId: string }) {
  const counts = useMemo(() => {
    let outgoing = 0;
    let incoming = 0;
    const callees = new Set<string>();
    const callers = new Set<string>();
    for (const edge of graph.edges) {
      if (edge.data.relationship_type !== "CALLS" && edge.data.edge_type !== "CALLS") continue;
      if (edge.source === nodeId) {
        outgoing += 1;
        callees.add(edge.target);
      }
      if (edge.target === nodeId) {
        incoming += 1;
        callers.add(edge.source);
      }
    }
    return { outgoing, incoming, calleeCount: callees.size, callerCount: callers.size };
  }, [graph.edges, nodeId]);
  return (
    <section aria-label="Direct call dependencies" className="border-t border-atlas-line pt-3">
      <h4 className="text-xs font-semibold uppercase tracking-wide text-atlas-accent">Direct CALLS relationships</h4>
      <dl className="mt-2 grid grid-cols-2 gap-2 text-xs">
        <div><dt className="text-atlas-muted">Outgoing edges</dt><dd className="mt-0.5 text-white">{counts.outgoing}</dd></div>
        <div><dt className="text-atlas-muted">Distinct callees</dt><dd className="mt-0.5 text-white">{counts.calleeCount}</dd></div>
        <div><dt className="text-atlas-muted">Incoming edges</dt><dd className="mt-0.5 text-white">{counts.incoming}</dd></div>
        <div><dt className="text-atlas-muted">Distinct callers</dt><dd className="mt-0.5 text-white">{counts.callerCount}</dd></div>
      </dl>
      <p className="mt-2 text-[10px] leading-4 text-atlas-dim">Counts use the currently loaded graph and retain parallel CALLS edges.</p>
    </section>
  );
}

function SourceViewer({ source }: { source: SymbolSourceResponse }) {
  if (source.status !== "available" || source.code === null) {
    return <p className="mt-2 text-xs leading-5 text-atlas-muted">{source.reason ?? "Source is unavailable."}</p>;
  }
  const lines = source.code.split(/\r\n|\n|\r/);
  if (lines.length > 1 && lines[lines.length - 1] === "") lines.pop();
  return (
    <>
      {source.changed_after_analysis === true && <p className="mt-2 text-xs leading-5 text-amber-200">This file’s size changed after analysis. The snippet shows its current contents.</p>}
      {source.freshness === "unverified" && source.changed_after_analysis !== true && <p className="mt-2 text-xs leading-5 text-atlas-muted">Current file contents are shown; exact snapshot freshness could not be verified.</p>}
      {source.truncated && <p className="mt-2 text-xs leading-5 text-amber-200">The source snippet was truncated at the display limit.</p>}
      <div aria-label="Source code" className="mt-2 max-h-[520px] overflow-auto rounded-md border border-atlas-line bg-[#080c10]">
        <pre className="min-w-max p-3 font-mono text-xs leading-5 text-atlas-copy"><code>{lines.map((line, index) => (
          <span className="block min-h-5" key={`${(source.start_line ?? 1) + index}:${index}`}><span aria-hidden="true" className="mr-4 inline-block w-10 select-none text-right text-atlas-dim">{(source.start_line ?? 1) + index}</span>{line}</span>
        ))}</code></pre>
      </div>
    </>
  );
}

export function FunctionInspector({ analysisId, graph, selectedNodeId }: FunctionInspectorProps) {
  const selectedNode = selectedNodeId ? graph.nodes.find((node) => node.id === selectedNodeId) : undefined;
  const hasInspectableKind = isInspectable(selectedNode);
  const selectedSymbolId = typeof selectedNode?.data.symbol_id === "string" ? selectedNode.data.symbol_id : null;
  const canInspect = hasInspectableKind && selectedSymbolId !== null;
  const selectionKey = canInspect && selectedNodeId && selectedSymbolId
    ? `${analysisId}\u0000${selectedNodeId}\u0000${selectedSymbolId}`
    : null;
  const [detailsState, setDetailsState] = useState<LoadState<SymbolInspectorResponse>>(null);
  const [sourceState, setSourceState] = useState<LoadState<SymbolSourceResponse>>(null);
  const details = detailsState && "value" in detailsState && detailsState.key === selectionKey ? detailsState.value : null;
  const source = sourceState && "value" in sourceState && sourceState.key === selectionKey ? sourceState.value : null;
  const detailsLoading = selectionKey !== null && (!detailsState || detailsState.key !== selectionKey || "loading" in detailsState);
  const detailsError = detailsState && "error" in detailsState && detailsState.key === selectionKey ? detailsState.error : null;
  const sourceLoading = Boolean(details) && (!sourceState || sourceState.key !== selectionKey || "loading" in sourceState);
  const sourceError = sourceState && "error" in sourceState && sourceState.key === selectionKey ? sourceState.error : null;

  useEffect(() => {
    if (!selectionKey || !selectedSymbolId) {
      setDetailsState(null);
      setSourceState(null);
      return;
    }
    const controller = new AbortController();
    let active = true;
    setDetailsState({ key: selectionKey, loading: true });
    setSourceState(null);
    void getSymbolInspector(analysisId, selectedSymbolId, controller.signal).then(
      (value) => { if (active) setDetailsState({ key: selectionKey, value }); },
      (error: unknown) => {
        if (active && !(error instanceof ApiRequestError && error.kind === "aborted")) {
          setDetailsState({ key: selectionKey, error: errorMessage(error) });
        }
      },
    );
    return () => { active = false; controller.abort(); };
  }, [analysisId, selectedNodeId, selectedSymbolId, selectionKey]);

  useEffect(() => {
    if (!details || !selectionKey || !selectedSymbolId) return;
    const controller = new AbortController();
    let active = true;
    setSourceState({ key: selectionKey, loading: true });
    void getSymbolSource(analysisId, selectedSymbolId, controller.signal).then(
      (value) => { if (active) setSourceState({ key: selectionKey, value }); },
      (error: unknown) => {
        if (active && !(error instanceof ApiRequestError && error.kind === "aborted")) {
          setSourceState({ key: selectionKey, error: errorMessage(error) });
        }
      },
    );
    return () => { active = false; controller.abort(); };
  }, [analysisId, details, selectedNodeId, selectedSymbolId, selectionKey]);

  const symbol = details?.symbol;
  const location = symbol?.source_location;
  const parameterized = symbol && ["function", "method"].includes(symbol.symbol_kind.toLowerCase());
  const metadata = symbol ? metadataSection(symbol) : null;

  return (
    <section aria-labelledby="function-inspector-heading" className="rounded-lg border border-atlas-line bg-atlas-surface p-4 sm:p-5">
      <div>
        <p className="text-[10px] font-semibold tracking-[0.18em] text-atlas-muted">SELECTED SYMBOL</p>
        <h3 className="mt-1 text-base font-semibold text-white" id="function-inspector-heading">Function Inspector</h3>
        <p className="mt-1 text-xs leading-5 text-atlas-muted">Inspect stored symbol metadata and its source range.</p>
      </div>

      {!selectedNodeId && <p className="mt-4 text-xs leading-5 text-atlas-muted" role="status">Select a function, method, or class in the graph or from search/trace to inspect it.</p>}
      {selectedNodeId && !selectedNode && <p className="mt-4 text-xs leading-5 text-atlas-muted" role="status">The selected node is no longer present in the active analysis graph.</p>}
      {selectedNodeId && selectedNode?.data.node_type === "file" && <p className="mt-4 text-xs leading-5 text-atlas-muted" role="status">A file is selected. File-level source browsing is not available in this Inspector.</p>}
      {selectedNodeId && selectedNode && selectedNode.data.node_type !== "file" && !hasInspectableKind && <p className="mt-4 text-xs leading-5 text-atlas-muted" role="status">This selected graph node is not a supported function, method, or class symbol.</p>}
      {selectedNodeId && hasInspectableKind && !selectedSymbolId && <p className="mt-4 text-xs leading-5 text-rose-300" role="alert">This graph node does not include its SymbolIR identity, so the Inspector cannot look it up safely.</p>}
      {detailsLoading && <p className="mt-4 text-xs text-atlas-accent" role="status">Loading symbol metadata…</p>}
      {detailsError && <p className="mt-4 text-xs leading-5 text-rose-300" role="alert">{detailsError}</p>}

      {symbol && selectionKey && (
        <div className="mt-4 space-y-4 border-t border-atlas-line pt-4">
          <section aria-label="Symbol identity">
            <h4 className="break-words text-lg font-semibold text-white">{symbol.original_name ?? symbol.qualified_name ?? symbol.symbol_id}</h4>
            {symbol.qualified_name && <p className="mt-1 break-all text-xs text-atlas-accent">{symbol.qualified_name}</p>}
            <p className="mt-2 break-all text-xs text-atlas-muted">{symbol.symbol_kind} · {symbol.language}</p>
            <p className="mt-1 break-all font-mono text-[11px] text-atlas-muted">{symbol.file_path}</p>
            <p className="mt-1 break-all font-mono text-[10px] text-atlas-dim">Symbol ID: {symbol.symbol_id}</p>
            <p className="mt-2 text-xs text-atlas-copy">{location?.start_line && location.end_line ? `Lines ${location.start_line}–${location.end_line}` : "Source location unavailable"}{location?.start_column !== null && location?.start_column !== undefined ? ` · column ${location.start_column}` : ""}{location?.end_column !== null && location?.end_column !== undefined ? `–${location.end_column}` : ""}</p>
          </section>

          {parameterized && (
            <section className="border-t border-atlas-line pt-3">
              <h4 className="text-xs font-semibold uppercase tracking-wide text-atlas-accent">Parameters and return</h4>
              {symbol.parameters.length > 0 ? <ul className="mt-2 space-y-2">{symbol.parameters.map((parameter) => (
                <li className="rounded-md bg-atlas-raised p-2 text-xs" key={`${parameter.position}:${parameter.name ?? "parameter"}`}>
                  <p className="break-words font-mono text-atlas-copy">{parameter.name ?? `Parameter ${parameter.position + 1}`}</p>
                  <p className="mt-1 text-atlas-muted">{parameter.kind ?? "Parameter kind unavailable"}{parameter.type_annotation ? ` · ${parameter.type_annotation}` : " · No type annotation"}</p>
                  {(parameter.has_default === true || parameter.default_value !== null) && <p className="mt-1 break-words text-atlas-muted">Default: {parameter.default_value ?? "(value unavailable)"}</p>}
                </li>
              ))}</ul> : <p className="mt-2 text-xs text-atlas-muted">No parameters were recorded.</p>}
              <p className="mt-2 break-words text-xs text-atlas-muted">Return: {symbol.return_type ?? "No return annotation"}</p>
              {typeof metadata?.is_async === "boolean" && <p className="mt-2 text-xs text-atlas-muted">{metadata.is_async ? "Async function" : "Synchronous function"}</p>}
            </section>
          )}

          {symbol.decorators.length > 0 && <section className="border-t border-atlas-line pt-3"><h4 className="text-xs font-semibold uppercase tracking-wide text-atlas-accent">Decorators</h4><ul className="mt-2 space-y-1">{symbol.decorators.map((decorator, index) => <li className="break-words font-mono text-xs text-atlas-copy" key={`${index}:${decorator}`}>@{decorator}</li>)}</ul></section>}
          {symbol.documentation && <section className="border-t border-atlas-line pt-3"><h4 className="text-xs font-semibold uppercase tracking-wide text-atlas-accent">Documentation</h4><p className="mt-2 whitespace-pre-wrap break-words text-xs leading-5 text-atlas-copy">{symbol.documentation}</p></section>}
          {details.parent_symbol && <section className="border-t border-atlas-line pt-3"><h4 className="text-xs font-semibold uppercase tracking-wide text-atlas-accent">Parent symbol</h4><p className="mt-2 break-words text-xs text-white">{details.parent_symbol.original_name ?? details.parent_symbol.qualified_name ?? details.parent_symbol.symbol_id}</p>{details.parent_symbol.qualified_name && <p className="mt-1 break-all text-[11px] text-atlas-muted">{details.parent_symbol.qualified_name}</p>}<p className="mt-1 text-[11px] text-atlas-muted">{details.parent_symbol.symbol_kind} · {details.parent_symbol.language}</p></section>}
          {details.source_file && <section className="border-t border-atlas-line pt-3"><h4 className="text-xs font-semibold uppercase tracking-wide text-atlas-accent">Source file metadata</h4><dl className="mt-2 grid grid-cols-2 gap-2 text-[11px]"><div><dt className="text-atlas-muted">Parsing</dt><dd className="mt-0.5 text-atlas-copy">{details.source_file.parsing_status}</dd></div><div><dt className="text-atlas-muted">Size</dt><dd className="mt-0.5 text-atlas-copy">{details.source_file.size_bytes.toLocaleString()} bytes</dd></div>{details.source_file.source_line_count !== null && <div><dt className="text-atlas-muted">Lines at analysis</dt><dd className="mt-0.5 text-atlas-copy">{details.source_file.source_line_count.toLocaleString()}</dd></div>}{details.source_file.category && <div><dt className="text-atlas-muted">Category</dt><dd className="mt-0.5 text-atlas-copy">{details.source_file.category}</dd></div>}</dl>{details.source_file.metadata && <details className="mt-2"><summary className="cursor-pointer text-[11px] text-atlas-muted">Additional source-file metadata</summary><pre className="mt-1 max-h-40 overflow-auto rounded bg-atlas-raised p-2 font-mono text-[10px] leading-4 text-atlas-muted">{JSON.stringify(details.source_file.metadata, null, 2)}</pre></details>}</section>}
          {metadata && <details className="border-t border-atlas-line pt-3"><summary className="cursor-pointer text-xs font-semibold text-atlas-accent">Language metadata</summary><pre className="mt-2 max-h-56 overflow-auto rounded bg-atlas-raised p-2 font-mono text-[10px] leading-4 text-atlas-muted">{JSON.stringify(metadata, null, 2)}</pre></details>}

          <section className="border-t border-atlas-line pt-3">
            <h4 className="text-xs font-semibold uppercase tracking-wide text-atlas-accent">Source code</h4>
            {sourceLoading && <p className="mt-2 text-xs text-atlas-accent" role="status">Loading source range…</p>}
            {sourceError && <p className="mt-2 text-xs leading-5 text-rose-300" role="alert">{sourceError}</p>}
            {source && <SourceViewer source={source} />}
          </section>

          {selectedNodeId && <DependencySummary graph={graph} nodeId={selectedNodeId} />}
        </div>
      )}
    </section>
  );
}
