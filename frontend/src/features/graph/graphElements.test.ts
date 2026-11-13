import { describe, expect, it } from "vitest";

import { getSymbolKinds, toCytoscapeElements } from "./graphElements";
import { graphFixture } from "../../test/graphFixtures";

describe("toCytoscapeElements", () => {
  it("maps nodes and directed relationships without mutating or collapsing graph data", () => {
    const before = structuredClone(graphFixture);
    const elements = toCytoscapeElements(graphFixture);
    const nodes = elements.filter((element) => element.group === "nodes");
    const edges = elements.filter((element) => element.group === "edges");
    expect(nodes).toHaveLength(graphFixture.nodes.length);
    expect(edges).toHaveLength(graphFixture.edges.length);
    const parallel = edges.filter((element) => element.data.source === "symbol:one" && element.data.target === "symbol:two");
    expect(parallel.map((edge) => edge.data.id)).toEqual(["call-1", "call-2"]);
    expect(parallel.map((edge) => edge.data.edgeKey)).toEqual(["call:1", "call:2"]);
    expect(graphFixture).toEqual(before);
    expect(getSymbolKinds(graphFixture)).toEqual(new Set(["function", "method", "class"]));
  });

  it("uses safe fallback labels and rejects counts, IDs, endpoint, and relationship inconsistencies", () => {
    const fallback = structuredClone(graphFixture);
    const symbol = fallback.nodes.find((node) => node.id === "symbol:one")!;
    symbol.data.name = null;
    symbol.data.qualified_name = "qualified.fallback";
    expect(toCytoscapeElements(fallback).find((element) => element.data.id === "symbol:one")?.data.label).toBe("qualified.fallback");

    const badCount = { ...graphFixture, statistics: { ...graphFixture.statistics, total_edges: 4 } };
    expect(() => toCytoscapeElements(badCount)).toThrow(/counts/);
    const badEndpoint = structuredClone(graphFixture);
    badEndpoint.edges[0]!.target = "symbol:does-not-exist";
    expect(() => toCytoscapeElements(badEndpoint)).toThrow(/missing node/);
    const badRelationship = structuredClone(graphFixture);
    badRelationship.edges[0]!.data.relationship_type = "UNKNOWN";
    expect(() => toCytoscapeElements(badRelationship)).toThrow(/invalid relationship/);
  });
});
