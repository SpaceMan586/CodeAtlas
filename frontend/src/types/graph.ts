export type GraphNodeType = "file" | "symbol" | (string & {});
export type RelationshipType = "CONTAINS" | "CALLS" | "IMPORTS" | (string & {});

export interface GraphNodeData {
  node_type: GraphNodeType;
  [metadata: string]: unknown;
}

export interface GraphNode {
  id: string;
  data: GraphNodeData;
}

export interface GraphEdgeData {
  edge_type: RelationshipType;
  relationship_type: RelationshipType;
  [metadata: string]: unknown;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  /** MultiDiGraph key: this distinguishes parallel relationships. */
  key: string;
  data: GraphEdgeData;
}

export interface GraphStatistics {
  node_count: number;
  edge_count: number;
  total_nodes: number;
  total_edges: number;
  nodes_by_category: Record<string, number>;
  edges_by_relationship_type: Record<string, number>;
}

export interface GraphData {
  analysis_id: string;
  metadata: Record<string, unknown>;
  nodes: GraphNode[];
  /** Array representation intentionally preserves parallel edges and keys. */
  edges: GraphEdge[];
  statistics: GraphStatistics;
}
