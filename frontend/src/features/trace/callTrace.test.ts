import { describe, expect, it } from "vitest";

import { CallTraceError, createCallTraceIndex, runCallTrace } from "./callTrace";
import { graphFixture } from "../../test/graphFixtures";

describe("call trace engine", () => {
  it("walks outgoing, incoming, and both directions with BFS and keeps parallel edges", () => {
    const index = createCallTraceIndex(graphFixture);
    const outgoing = runCallTrace(index, "symbol:one", "OUTGOING", 1);
    expect(outgoing.nodesByDepth.map((level) => level.map((node) => node.id))).toEqual([["symbol:one"], ["symbol:two"]]);
    expect(outgoing.relationships.map((edge) => edge.id)).toEqual(["call-1", "call-2", "call-3"]);
    expect(outgoing.depthBoundaryReached).toBe(false);
    const incoming = runCallTrace(index, "symbol:two", "INCOMING", 2);
    expect(incoming.relationships.map((edge) => edge.id)).toEqual(["call-1", "call-2", "call-3"]);
    expect(incoming.relationships[0]).toMatchObject({ source: "symbol:one", target: "symbol:two", key: "call:1" });
    const both = runCallTrace(index, "symbol:one", "BOTH", 3);
    expect(both.relationships.map((edge) => edge.id)).toEqual(["call-1", "call-2", "call-3"]);
  });

  it("terminates on recursion and mutual cycles, and reports invalid roots/directions/depths", () => {
    const result = runCallTrace(createCallTraceIndex(graphFixture), "symbol:one", "OUTGOING", 4);
    expect(result.nodeCount).toBe(2);
    expect(result.relationshipCount).toBe(3);
    expect(result.maximumDepthReached).toBe(1);
    const index = createCallTraceIndex(graphFixture);
    expect(() => runCallTrace(index, "file:src/a.py", "OUTGOING", 1)).toThrow(CallTraceError);
    expect(() => runCallTrace(index, "missing", "OUTGOING", 1)).toThrow(/no longer exists/);
    expect(() => runCallTrace(index, "symbol:one", "sideways" as never, 1)).toThrow(/Choose outgoing/);
    expect(() => runCallTrace(index, "symbol:one", "BOTH", 0)).toThrow(/whole number/);
  });

  it("rejects duplicate graph identities and keeps a supported constructor target terminal", () => {
    const duplicate = structuredClone(graphFixture);
    duplicate.nodes.push(duplicate.nodes[0]!);
    expect(() => createCallTraceIndex(duplicate)).toThrow(/duplicate node/);
    const constructorGraph = structuredClone(graphFixture);
    constructorGraph.edges.push({
      id: "ctor", key: "ctor-key", source: "symbol:one", target: "symbol:class",
      data: { edge_type: "CALLS", relationship_type: "CALLS", metadata: { resolution_method: "CLASS_CONSTRUCTOR" } },
    });
    const trace = runCallTrace(createCallTraceIndex(constructorGraph), "symbol:one", "OUTGOING", 3);
    expect(trace.nodesByDepth[1]?.some((node) => node.id === "symbol:class")).toBe(true);
    expect(trace.nodesByDepth[2]).toBeUndefined();
  });
});
