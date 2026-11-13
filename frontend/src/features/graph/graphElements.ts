import type cytoscape from "cytoscape";
import type { GraphData, GraphEdge, GraphNode } from "../../types/graph";

export interface VisualizationNodeData {
  id: string;
  label: string;
  nodeType: "file" | "symbol";
  filePath: string;
  language: string;
  symbolKind: string;
  qualifiedName: string;
  raw: GraphNode["data"];
}

export interface VisualizationEdgeData {
  id: string;
  source: string;
  target: string;
  edgeKey: string;
  relationshipType: "CONTAINS" | "CALLS" | "IMPORTS";
  raw: GraphEdge["data"];
}

export type VisualizationElementData = VisualizationNodeData | VisualizationEdgeData;

function isSupportedNodeType(value: string): value is "file" | "symbol" {
  return value === "file" || value === "symbol";
}

function isSupportedRelationship(value: string): value is "CONTAINS" | "CALLS" | "IMPORTS" {
  return value === "CONTAINS" || value === "CALLS" || value === "IMPORTS";
}

function optionalString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function symbolClass(kind: string): string {
  switch (kind.toLocaleLowerCase()) {
    case "class": return "symbol-class";
    case "function": return "symbol-function";
    case "method": return "symbol-method";
    default: return "symbol-unknown";
  }
}

function nodeLabel(node: GraphNode): string {
  if (node.data.node_type === "file") {
    return optionalString(node.data.file_path) || optionalString(node.data.file_name) || node.id;
  }
  return optionalString(node.data.name) || optionalString(node.data.qualified_name) || node.id;
}

export function toCytoscapeElements(graph: GraphData): cytoscape.ElementDefinition[] {
  if (graph.statistics.total_nodes !== graph.nodes.length || graph.statistics.total_edges !== graph.edges.length) {
    throw new Error("Graph counts do not match the node and edge collections.");
  }

  const nodeIds = new Set<string>();
  const elements: cytoscape.ElementDefinition[] = [];
  for (const node of graph.nodes) {
    if (!node.id || nodeIds.has(node.id) || !isSupportedNodeType(node.data.node_type)) {
      throw new Error("Graph contains a duplicate node ID or unsupported node type.");
    }
    nodeIds.add(node.id);
    const kind = optionalString(node.data.symbol_kind);
    const classes = node.data.node_type === "file" ? "file-node" : `symbol-node ${symbolClass(kind)}`;
    elements.push({
      group: "nodes",
      classes,
      data: {
        id: node.id,
        label: nodeLabel(node),
        nodeType: node.data.node_type,
        filePath: optionalString(node.data.file_path),
        language: optionalString(node.data.language),
        symbolKind: kind,
        qualifiedName: optionalString(node.data.qualified_name),
        raw: node.data,
      } satisfies VisualizationNodeData,
    });
  }

  const edgeIds = new Set<string>();
  for (const edge of graph.edges) {
    const relationshipType = edge.data.relationship_type;
    if (
      !edge.id || edgeIds.has(edge.id) || !edge.key || !edge.source || !edge.target ||
      !nodeIds.has(edge.source) || !nodeIds.has(edge.target) ||
      edge.data.edge_type !== relationshipType ||
      !isSupportedRelationship(relationshipType)
    ) {
      throw new Error("Graph contains a duplicate or invalid relationship, or an edge references a missing node.");
    }
    edgeIds.add(edge.id);
    elements.push({
      group: "edges",
      classes: `${relationshipType.toLocaleLowerCase()}-edge`,
      data: {
        id: edge.id,
        source: edge.source,
        target: edge.target,
        edgeKey: edge.key,
        relationshipType,
        raw: edge.data,
      } satisfies VisualizationEdgeData,
    });
  }
  return elements;
}

export function getSymbolKinds(graph: GraphData): Set<string> {
  return new Set(graph.nodes
    .filter((node) => node.data.node_type === "symbol")
    .map((node) => optionalString(node.data.symbol_kind).toLocaleLowerCase())
    .filter((kind) => kind.length > 0));
}
