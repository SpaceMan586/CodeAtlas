import type { GraphData, GraphEdge, GraphNode } from "../../types/graph";

export type TraceDirection = "OUTGOING" | "INCOMING" | "BOTH";

export interface CallTraceNode {
  id: string;
  depth: number;
  data: GraphNode["data"];
}

export interface CallTraceRelationship {
  id: string;
  key: string;
  source: string;
  target: string;
  /** Hop depth in the bounded trace; cycle-closing boundary edges use maxDepth. */
  depth: number;
  data: GraphEdge["data"];
}

export interface CallTraceResult {
  rootNodeId: string;
  direction: TraceDirection;
  maxDepth: number;
  nodesByDepth: CallTraceNode[][];
  relationships: CallTraceRelationship[];
  nodeCount: number;
  relationshipCount: number;
  maximumDepthReached: number;
  /** True if another eligible node was reachable beyond maxDepth. */
  depthBoundaryReached: boolean;
}

export interface CallTraceIndex {
  readonly nodesById: ReadonlyMap<string, GraphNode>;
  readonly outgoingCalls: ReadonlyMap<string, readonly GraphEdge[]>;
  readonly incomingCalls: ReadonlyMap<string, readonly GraphEdge[]>;
}

export class CallTraceError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "CallTraceError";
  }
}

function record(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function kindOf(node: GraphNode | undefined): string {
  return typeof node?.data.symbol_kind === "string" ? node.data.symbol_kind.toLowerCase() : "";
}

function isFunctionOrMethod(node: GraphNode | undefined): boolean {
  return node?.data.node_type === "symbol" && ["function", "method"].includes(kindOf(node));
}

function isConstructorTarget(edge: GraphEdge): boolean {
  const edgeMetadata = record(edge.data.metadata);
  const evidence = Array.isArray(edge.data.evidence) ? edge.data.evidence : [];
  const methods = [edgeMetadata?.resolution_method, ...evidence.map((item) => record(item)?.resolution_method)];
  return methods.some((method) => method === "CLASS_CONSTRUCTOR" || method === "IMPORTED_CLASS_CONSTRUCTOR");
}

function isValidTarget(edge: GraphEdge, node: GraphNode | undefined): boolean {
  if (isFunctionOrMethod(node)) return true;
  return node?.data.node_type === "symbol" && kindOf(node) === "class" && isConstructorTarget(edge);
}

function edgeOrder(a: GraphEdge, b: GraphEdge): number {
  return a.source.localeCompare(b.source) || a.target.localeCompare(b.target) || a.key.localeCompare(b.key) || a.id.localeCompare(b.id);
}

export function createCallTraceIndex(graph: GraphData): CallTraceIndex {
  const nodesById = new Map<string, GraphNode>();
  for (const node of graph.nodes) {
    if (!node.id || nodesById.has(node.id)) throw new CallTraceError("Graph contains a missing or duplicate node ID.");
    nodesById.set(node.id, node);
  }

  const outgoing = new Map<string, GraphEdge[]>();
  const incoming = new Map<string, GraphEdge[]>();
  const edgeIds = new Set<string>();
  for (const edge of graph.edges) {
    if (edge.data.relationship_type !== "CALLS" && edge.data.edge_type !== "CALLS") continue;
    if (edge.data.relationship_type !== "CALLS" || edge.data.edge_type !== "CALLS") {
      throw new CallTraceError("Graph contains a CALLS relationship with inconsistent type metadata.");
    }
    if (!edge.id || edgeIds.has(edge.id) || !edge.key || !nodesById.has(edge.source) || !nodesById.has(edge.target)) {
      throw new CallTraceError("Graph contains a duplicate CALLS edge ID or a missing endpoint.");
    }
    edgeIds.add(edge.id);
    const source = nodesById.get(edge.source);
    const target = nodesById.get(edge.target);
    if (!isFunctionOrMethod(source) || !isValidTarget(edge, target)) continue;
    const outgoingEdges = outgoing.get(edge.source) ?? [];
    outgoingEdges.push(edge);
    outgoing.set(edge.source, outgoingEdges);
    const incomingEdges = incoming.get(edge.target) ?? [];
    incomingEdges.push(edge);
    incoming.set(edge.target, incomingEdges);
  }
  for (const edges of outgoing.values()) edges.sort(edgeOrder);
  for (const edges of incoming.values()) edges.sort(edgeOrder);
  return { nodesById, outgoingCalls: outgoing, incomingCalls: incoming };
}

function edgesFrom(index: CallTraceIndex, nodeId: string, direction: TraceDirection): GraphEdge[] {
  if (direction === "OUTGOING") return [...(index.outgoingCalls.get(nodeId) ?? [])];
  if (direction === "INCOMING") return [...(index.incomingCalls.get(nodeId) ?? [])];
  const unique = new Map<string, GraphEdge>();
  for (const edge of [...(index.outgoingCalls.get(nodeId) ?? []), ...(index.incomingCalls.get(nodeId) ?? [])]) {
    unique.set(edge.id, edge);
  }
  return [...unique.values()].sort(edgeOrder);
}

function neighborFor(edge: GraphEdge, nodeId: string, direction: TraceDirection): string {
  if (direction === "OUTGOING") return edge.target;
  if (direction === "INCOMING") return edge.source;
  return edge.source === nodeId ? edge.target : edge.source;
}

function rememberRelationship(
  relationships: Map<string, { edge: GraphEdge; depth: number }>,
  edge: GraphEdge,
  depth: number,
): void {
  const previous = relationships.get(edge.id);
  if (!previous || depth < previous.depth) relationships.set(edge.id, { edge, depth });
}

export function runCallTrace(
  index: CallTraceIndex,
  rootNodeId: string,
  direction: TraceDirection,
  maxDepth: number,
): CallTraceResult {
  if (direction !== "OUTGOING" && direction !== "INCOMING" && direction !== "BOTH") {
    throw new CallTraceError("Choose outgoing, incoming, or both directions.");
  }
  if (!Number.isInteger(maxDepth) || maxDepth < 1 || maxDepth > 10) {
    throw new CallTraceError("Trace depth must be a whole number from 1 to 10.");
  }
  const root = index.nodesById.get(rootNodeId);
  if (!root) throw new CallTraceError("The selected graph node no longer exists.");
  if (!isFunctionOrMethod(root)) throw new CallTraceError("Choose a function or method as the trace root.");

  const depths = new Map<string, number>([[rootNodeId, 0]]);
  const queue = [rootNodeId];
  const relationshipDepths = new Map<string, { edge: GraphEdge; depth: number }>();
  let depthBoundaryReached = false;
  let maximumDepthReached = 0;

  for (let cursor = 0; cursor < queue.length; cursor += 1) {
    const nodeId = queue[cursor];
    const depth = depths.get(nodeId);
    if (depth === undefined) continue;
    const node = index.nodesById.get(nodeId);
    if (!isFunctionOrMethod(node)) continue;
    const edges = edgesFrom(index, nodeId, direction);

    if (depth >= maxDepth) {
      for (const edge of edges) {
        const neighborId = neighborFor(edge, nodeId, direction);
        if (depths.has(neighborId)) {
          // Keep cycle-closing edges within the reached subgraph without extending the traversal.
          rememberRelationship(relationshipDepths, edge, maxDepth);
        } else {
          depthBoundaryReached = true;
        }
      }
      continue;
    }

    for (const edge of edges) {
      const neighborId = neighborFor(edge, nodeId, direction);
      const neighbor = index.nodesById.get(neighborId);
      if (!isFunctionOrMethod(neighbor) && !(neighbor?.data.node_type === "symbol" && kindOf(neighbor) === "class" && isConstructorTarget(edge))) continue;

      const traversalDepth = depth + 1;
      rememberRelationship(relationshipDepths, edge, Math.min(maxDepth, traversalDepth));
      if (!depths.has(neighborId)) {
        depths.set(neighborId, traversalDepth);
        maximumDepthReached = Math.max(maximumDepthReached, traversalDepth);
        if (isFunctionOrMethod(neighbor)) queue.push(neighborId);
      }
    }
  }

  const nodesByDepth = Array.from({ length: maximumDepthReached + 1 }, () => [] as CallTraceNode[]);
  for (const [id, depth] of [...depths.entries()].sort(([a], [b]) => a.localeCompare(b))) {
    const node = index.nodesById.get(id);
    if (node) nodesByDepth[depth].push({ id, depth, data: node.data });
  }
  const relationships = [...relationshipDepths.values()]
    .sort((a, b) => a.depth - b.depth || edgeOrder(a.edge, b.edge))
    .map(({ edge, depth }) => ({
      id: edge.id,
      key: edge.key,
      source: edge.source,
      target: edge.target,
      depth,
      data: edge.data,
    }));

  return {
    rootNodeId,
    direction,
    maxDepth,
    nodesByDepth,
    relationships,
    nodeCount: depths.size,
    relationshipCount: relationships.length,
    maximumDepthReached,
    depthBoundaryReached,
  };
}
