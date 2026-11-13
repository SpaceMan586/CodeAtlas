import { ApiRequestError, requestJson } from "./api";
import type { AnalysisInfo, DeleteAnalysisResponse } from "../types/analysis";
import type { GraphData } from "../types/graph";
import type { InspectorParameter, InspectorSourceFile, InspectorSourceLocation, InspectorSymbol, SymbolInspectorResponse, SymbolSourceResponse } from "../types/inspector";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function requireRecord(value: unknown, message: string): Record<string, unknown> {
  if (!isRecord(value)) throw new ApiRequestError(message, "invalid-response");
  return value;
}

function requireNumber(record: Record<string, unknown>, key: string): number {
  const value = record[key];
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new ApiRequestError(`Flask returned an invalid ${key} value.`, "invalid-response");
  }
  return value;
}

function nullableString(value: unknown, field: string): string | null {
  if (value === null) return null;
  if (typeof value !== "string") throw new ApiRequestError(`Flask returned an invalid ${field} value.`, "invalid-response");
  return value;
}

function parseInspectorSymbol(value: unknown): InspectorSymbol {
  const symbol = requireRecord(value, "Flask returned invalid symbol data.");
  if (
    typeof symbol.symbol_id !== "string" || typeof symbol.language !== "string" ||
    typeof symbol.symbol_kind !== "string" || typeof symbol.file_path !== "string" ||
    !Array.isArray(symbol.parameters) || !Array.isArray(symbol.decorators) ||
    !Array.isArray(symbol.lexical_scope)
  ) throw new ApiRequestError("Flask returned invalid symbol data.", "invalid-response");
  let location: InspectorSourceLocation | null = null;
  if (symbol.source_location !== null) {
    const raw = requireRecord(symbol.source_location, "Flask returned invalid source location data.");
    const numberOrNull = (field: string): number | null => {
      const item = raw[field];
      if (item === null) return null;
      if (typeof item !== "number" || !Number.isInteger(item)) throw new ApiRequestError("Flask returned invalid source location data.", "invalid-response");
      return item;
    };
    location = { start_line: numberOrNull("start_line"), end_line: numberOrNull("end_line"), start_column: numberOrNull("start_column"), end_column: numberOrNull("end_column") };
  }
  const parameters: InspectorParameter[] = symbol.parameters.map((item): InspectorParameter => {
    const parameter = requireRecord(item, "Flask returned invalid parameter data.");
    if (typeof parameter.position !== "number" || !Number.isInteger(parameter.position) || (parameter.has_default !== null && typeof parameter.has_default !== "boolean")) throw new ApiRequestError("Flask returned invalid parameter data.", "invalid-response");
    const metadata = parameter.metadata === null ? null : requireRecord(parameter.metadata, "Flask returned invalid parameter metadata.");
    return {
      position: parameter.position,
      name: nullableString(parameter.name, "parameter name"),
      kind: nullableString(parameter.kind, "parameter kind"),
      type_annotation: nullableString(parameter.type_annotation, "parameter annotation"),
      default_value: nullableString(parameter.default_value, "parameter default"),
      has_default: parameter.has_default,
      metadata,
    };
  });
  if (symbol.decorators.some((item) => typeof item !== "string") || symbol.lexical_scope.some((item) => typeof item !== "string")) throw new ApiRequestError("Flask returned invalid symbol data.", "invalid-response");
  const metadata = symbol.metadata === null ? null : requireRecord(symbol.metadata, "Flask returned invalid symbol metadata.");
  return {
    symbol_id: symbol.symbol_id,
    language: symbol.language,
    original_name: nullableString(symbol.original_name, "symbol name"),
    qualified_name: nullableString(symbol.qualified_name, "qualified name"),
    symbol_kind: symbol.symbol_kind,
    file_path: symbol.file_path,
    source_location: location,
    lexical_scope: symbol.lexical_scope,
    parent_symbol_id: nullableString(symbol.parent_symbol_id, "parent symbol ID"),
    parameters,
    return_type: nullableString(symbol.return_type, "return annotation"),
    decorators: symbol.decorators,
    documentation: nullableString(symbol.documentation, "documentation"),
    metadata,
  };
}

function parseInspectorSourceFile(value: unknown): InspectorSourceFile | null {
  if (value === null) return null;
  const file = requireRecord(value, "Flask returned invalid source file data.");
  const metadata = file.metadata === null ? null : requireRecord(file.metadata, "Flask returned invalid source file metadata.");
  if (
    typeof file.file_path !== "string" || typeof file.file_name !== "string" ||
    typeof file.file_extension !== "string" || typeof file.language !== "string" ||
    typeof file.parsing_status !== "string" ||
    (file.category !== null && typeof file.category !== "string") ||
    (file.source_line_count !== null && typeof file.source_line_count !== "number")
  ) throw new ApiRequestError("Flask returned invalid source file data.", "invalid-response");
  return {
    file_path: file.file_path,
    file_name: file.file_name,
    file_extension: file.file_extension,
    language: file.language,
    size_bytes: requireNumber(file, "size_bytes"),
    parsing_status: file.parsing_status,
    source_line_count: file.source_line_count,
    category: file.category,
    metadata,
  };
}

function parseAnalysis(value: unknown): AnalysisInfo {
  const record = requireRecord(value, "Flask returned an unexpected analysis response.");
  const summary = requireRecord(record.summary, "Flask returned invalid analysis summary data.");
  const diagnostics = requireRecord(record.diagnostics, "Flask returned invalid diagnostic data.");
  if (
    typeof record.analysis_id !== "string" ||
    typeof record.project_display_path !== "string" ||
    typeof record.status !== "string" ||
    typeof record.completed_at !== "string" ||
    typeof record.project_ir_schema_version !== "number"
  ) {
    throw new ApiRequestError("Flask returned an unexpected analysis response.", "invalid-response");
  }
  const summaryFields = ["scanned_files", "parsed_files", "parse_total_files", "symbols", "call_sites", "call_dependencies", "import_dependencies", "graph_nodes", "graph_edges"] as const;
  const diagnosticsFields = ["total", "project", "call_resolution", "imports", "cross_file_calls", "call_dependencies"] as const;
  const parsedSummary = Object.fromEntries(summaryFields.map((key) => [key, requireNumber(summary, key)])) as unknown as AnalysisInfo["summary"];
  const parsedDiagnostics = Object.fromEntries(diagnosticsFields.map((key) => [key, requireNumber(diagnostics, key)])) as unknown as AnalysisInfo["diagnostics"];
  return {
    analysis_id: record.analysis_id,
    project_display_path: record.project_display_path,
    status: record.status,
    completed_at: record.completed_at,
    project_ir_schema_version: record.project_ir_schema_version,
    summary: parsedSummary,
    diagnostics: parsedDiagnostics,
  };
}

function parseGraph(value: unknown): GraphData {
  const record = requireRecord(value, "Flask returned an unexpected graph response.");
  const stats = requireRecord(record.statistics, "Flask returned invalid graph statistics.");
  if (typeof record.analysis_id !== "string" || !Array.isArray(record.nodes) || !Array.isArray(record.edges)) {
    throw new ApiRequestError("Flask returned an unexpected graph response.", "invalid-response");
  }
  const nodes = record.nodes.map((value): GraphData["nodes"][number] => {
    const node = requireRecord(value, "Flask returned an invalid graph node.");
    const data = requireRecord(node.data, "Flask returned invalid graph node metadata.");
    if (typeof node.id !== "string" || typeof data.node_type !== "string") {
      throw new ApiRequestError("Flask returned an invalid graph node.", "invalid-response");
    }
    return { id: node.id, data: data as GraphData["nodes"][number]["data"] };
  });
  const edges = record.edges.map((value): GraphData["edges"][number] => {
    const edge = requireRecord(value, "Flask returned an invalid graph edge.");
    const data = requireRecord(edge.data, "Flask returned invalid graph edge metadata.");
    if (
      typeof edge.id !== "string" || typeof edge.source !== "string" ||
      typeof edge.target !== "string" || typeof edge.key !== "string" ||
      typeof data.relationship_type !== "string" || typeof data.edge_type !== "string"
    ) {
      throw new ApiRequestError("Flask returned an invalid graph edge.", "invalid-response");
    }
    return { id: edge.id, source: edge.source, target: edge.target, key: edge.key, data: data as GraphData["edges"][number]["data"] };
  });
  const nodesByCategory = requireRecord(stats.nodes_by_category, "Flask returned invalid node statistics.");
  const edgesByType = requireRecord(stats.edges_by_relationship_type, "Flask returned invalid relationship statistics.");
  const toCounts = (value: Record<string, unknown>): Record<string, number> => Object.fromEntries(
    Object.entries(value).map(([key, count]) => {
      if (typeof count !== "number" || !Number.isFinite(count)) throw new ApiRequestError("Flask returned invalid graph statistics.", "invalid-response");
      return [key, count];
    }),
  );
  if (!isRecord(record.metadata)) throw new ApiRequestError("Flask returned invalid graph metadata.", "invalid-response");
  return {
    analysis_id: record.analysis_id,
    metadata: record.metadata,
    nodes,
    edges,
    statistics: {
      node_count: requireNumber(stats, "node_count"),
      edge_count: requireNumber(stats, "edge_count"),
      total_nodes: requireNumber(stats, "total_nodes"),
      total_edges: requireNumber(stats, "total_edges"),
      nodes_by_category: toCounts(nodesByCategory),
      edges_by_relationship_type: toCounts(edgesByType),
    },
  };
}

function analysisPath(analysisId: string): string {
  return `/api/analysis/${encodeURIComponent(analysisId)}`;
}

export async function createAnalysis(projectPath: string): Promise<AnalysisInfo> {
  return parseAnalysis(await requestJson("/api/analysis", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ project_path: projectPath }),
    timeoutMs: 120_000,
  }));
}

export async function getAnalysis(analysisId: string): Promise<AnalysisInfo> {
  return parseAnalysis(await requestJson(analysisPath(analysisId)));
}

export async function getAnalysisGraph(analysisId: string): Promise<GraphData> {
  return parseGraph(await requestJson(`${analysisPath(analysisId)}/graph`));
}

export async function deleteAnalysis(analysisId: string): Promise<DeleteAnalysisResponse> {
  const value = requireRecord(await requestJson(analysisPath(analysisId), { method: "DELETE" }), "Flask returned an unexpected deletion response.");
  if (value.analysis_id !== analysisId || value.deleted !== true) {
    throw new ApiRequestError("Flask returned an unexpected deletion response.", "invalid-response");
  }
  return { analysis_id: analysisId, deleted: true };
}

export async function getSymbolInspector(
  analysisId: string,
  symbolId: string,
  signal?: AbortSignal,
): Promise<SymbolInspectorResponse> {
  const query = new URLSearchParams({ symbol_id: symbolId });
  const value = requireRecord(
    await requestJson(`${analysisPath(analysisId)}/symbols/lookup?${query}`, { signal }),
    "Flask returned an unexpected symbol response.",
  );
  const symbol = parseInspectorSymbol(value.symbol);
  if (value.analysis_id !== analysisId || symbol.symbol_id !== symbolId) {
    throw new ApiRequestError("Flask returned symbol data for a different selection.", "invalid-response");
  }
  return {
    analysis_id: analysisId,
    symbol,
    parent_symbol: value.parent_symbol === null ? null : parseInspectorSymbol(value.parent_symbol),
    source_file: parseInspectorSourceFile(value.source_file),
  };
}

export async function getSymbolSource(
  analysisId: string,
  symbolId: string,
  signal?: AbortSignal,
): Promise<SymbolSourceResponse> {
  const query = new URLSearchParams({ symbol_id: symbolId });
  const value = requireRecord(
    await requestJson(`${analysisPath(analysisId)}/symbols/source?${query}`, { signal }),
    "Flask returned an unexpected source response.",
  );
  if (
    value.analysis_id !== analysisId || value.symbol_id !== symbolId ||
    (value.status !== "available" && value.status !== "unavailable") ||
    typeof value.truncated !== "boolean" || typeof value.file_path !== "string" ||
    (value.reason !== null && typeof value.reason !== "string") ||
    (value.code !== null && typeof value.code !== "string") ||
    (value.start_line !== null && typeof value.start_line !== "number") ||
    (value.end_line !== null && typeof value.end_line !== "number") ||
    (value.changed_after_analysis !== null && typeof value.changed_after_analysis !== "boolean") ||
    (value.freshness !== "changed" && value.freshness !== "unverified")
  ) {
    throw new ApiRequestError("Flask returned invalid source data.", "invalid-response");
  }
  return {
    analysis_id: analysisId,
    symbol_id: symbolId,
    file_path: value.file_path,
    status: value.status,
    reason: value.reason,
    code: value.code,
    start_line: value.start_line,
    end_line: value.end_line,
    truncated: value.truncated,
    changed_after_analysis: value.changed_after_analysis,
    freshness: value.freshness,
  };
}
