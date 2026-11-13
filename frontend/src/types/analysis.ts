export interface AnalysisSummary {
  scanned_files: number;
  parsed_files: number;
  parse_total_files: number;
  symbols: number;
  call_sites: number;
  call_dependencies: number;
  import_dependencies: number;
  graph_nodes: number;
  graph_edges: number;
}

export interface AnalysisDiagnostics {
  total: number;
  project: number;
  call_resolution: number;
  imports: number;
  cross_file_calls: number;
  call_dependencies: number;
}

export interface AnalysisInfo {
  analysis_id: string;
  project_display_path: string;
  status: "completed" | "completed_with_warnings" | string;
  completed_at: string;
  project_ir_schema_version: number;
  summary: AnalysisSummary;
  diagnostics: AnalysisDiagnostics;
}

export interface DeleteAnalysisResponse {
  analysis_id: string;
  deleted: true;
}
