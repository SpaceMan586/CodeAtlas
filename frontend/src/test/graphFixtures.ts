import type { GraphData } from "../types/graph";

export const graphFixture: GraphData = {
  analysis_id: "analysis-1",
  metadata: { graph_type: "CodeAtlasProjectGraph", ir_schema_version: 3 },
  nodes: [
    { id: "file:src/a.py", data: { node_type: "file", file_path: "src/a.py" } },
    { id: "file:src/b.py", data: { node_type: "file", file_path: "src/b.py" } },
    { id: "symbol:one", data: { node_type: "symbol", symbol_id: "one", symbol_kind: "function", name: "run", qualified_name: "alpha.run", file_path: "src/a.py", language: "python", source_location: { start_line: 3 } } },
    { id: "symbol:two", data: { node_type: "symbol", symbol_id: "two", symbol_kind: "function", name: "run", qualified_name: "beta.run", file_path: "src/b.py", language: "python", source_location: { start_line: 8 } } },
    { id: "symbol:method", data: { node_type: "symbol", symbol_id: "method", symbol_kind: "method", name: "work", qualified_name: "Worker.work", file_path: "src/a.py", language: "python", source_location: { start_line: 12 } } },
    { id: "symbol:class", data: { node_type: "symbol", symbol_id: "class", symbol_kind: "class", name: "Worker", qualified_name: "Worker", file_path: "src/a.py", language: "python" } },
  ],
  edges: [
    { id: "call-1", source: "symbol:one", target: "symbol:two", key: "call:1", data: { edge_type: "CALLS", relationship_type: "CALLS", evidence: [{ source: "unit" }] } },
    { id: "call-2", source: "symbol:one", target: "symbol:two", key: "call:2", data: { edge_type: "CALLS", relationship_type: "CALLS", evidence: [{ source: "unit", index: 2 }] } },
    { id: "call-3", source: "symbol:two", target: "symbol:one", key: "call:3", data: { edge_type: "CALLS", relationship_type: "CALLS" } },
    { id: "contains-1", source: "file:src/a.py", target: "symbol:one", key: "contains:1", data: { edge_type: "CONTAINS", relationship_type: "CONTAINS" } },
    { id: "import-1", source: "file:src/a.py", target: "file:src/b.py", key: "import:1", data: { edge_type: "IMPORTS", relationship_type: "IMPORTS" } },
  ],
  statistics: {
    node_count: 6, edge_count: 5, total_nodes: 6, total_edges: 5,
    nodes_by_category: { file: 2, symbol: 4 },
    edges_by_relationship_type: { CALLS: 3, CONTAINS: 1, IMPORTS: 1 },
  },
};
