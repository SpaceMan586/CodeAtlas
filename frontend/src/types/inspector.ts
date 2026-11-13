export interface InspectorSourceLocation {
  start_line: number | null;
  end_line: number | null;
  start_column: number | null;
  end_column: number | null;
}

export interface InspectorParameter {
  position: number;
  name: string | null;
  kind: string | null;
  type_annotation: string | null;
  default_value: string | null;
  has_default: boolean | null;
  metadata: Record<string, unknown> | null;
}

export interface InspectorSymbol {
  symbol_id: string;
  language: string;
  original_name: string | null;
  qualified_name: string | null;
  symbol_kind: string;
  file_path: string;
  source_location: InspectorSourceLocation | null;
  lexical_scope: string[];
  parent_symbol_id: string | null;
  parameters: InspectorParameter[];
  return_type: string | null;
  decorators: string[];
  documentation: string | null;
  metadata: Record<string, unknown> | null;
}

export interface InspectorSourceFile {
  file_path: string;
  file_name: string;
  file_extension: string;
  language: string;
  size_bytes: number;
  parsing_status: string;
  source_line_count: number | null;
  category: string | null;
  metadata: Record<string, unknown> | null;
}

export interface SymbolInspectorResponse {
  analysis_id: string;
  symbol: InspectorSymbol;
  parent_symbol: InspectorSymbol | null;
  source_file: InspectorSourceFile | null;
}

export interface SymbolSourceResponse {
  analysis_id: string;
  symbol_id: string;
  file_path: string;
  status: "available" | "unavailable";
  reason: string | null;
  code: string | null;
  start_line: number | null;
  end_line: number | null;
  truncated: boolean;
  changed_after_analysis: boolean | null;
  freshness: "changed" | "unverified";
}
