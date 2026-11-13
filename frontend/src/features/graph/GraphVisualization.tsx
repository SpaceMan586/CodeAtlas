import cytoscape from "cytoscape";
import { useEffect, useRef, useState } from "react";
import type { GraphData } from "../../types/graph";
import { getSymbolKinds, toCytoscapeElements } from "./graphElements";

type GraphLayout = "breadthfirst" | "cose" | "circle";

interface GraphVisualizationProps {
  graph: GraphData;
  onNodeSelect?: (nodeId: string | null) => void;
  focusNodeId?: string | null;
  focusRequest?: number;
}

function layoutOptions(name: GraphLayout): cytoscape.LayoutOptions {
  if (name === "cose") {
    return {
      name: "cose",
      animate: false,
      randomize: true,
      nodeOverlap: 18,
      idealEdgeLength: 110,
      componentSpacing: 90,
      numIter: 500,
      fit: true,
    };
  }
  if (name === "circle") {
    return { name: "circle", animate: false, avoidOverlap: true, spacingFactor: 1.25, fit: true };
  }
  return {
    name: "breadthfirst",
    animate: false,
    directed: true,
    circle: false,
    avoidOverlap: true,
    spacingFactor: 1.2,
    fit: true,
  };
}

const stylesheet: cytoscape.StylesheetJson = [
  {
    selector: "node",
    style: {
      label: "data(label)",
      color: "#e5edf4",
      "font-size": 10,
      "font-family": "Inter, system-ui, sans-serif",
      "text-wrap": "wrap",
      "text-max-width": "150px",
      "text-valign": "center",
      "text-halign": "center",
      "text-outline-color": "#10171f",
      "text-outline-width": 1,
      width: 168,
      height: 52,
      "border-width": 1.5,
      "overlay-opacity": 0,
    },
  },
  {
    selector: ".file-node",
    style: { shape: "round-rectangle", "background-color": "#17352f", "border-color": "#85e2b9", width: 184, height: 58 },
  },
  {
    selector: ".symbol-class",
    style: { shape: "hexagon", "background-color": "#34301f", "border-color": "#e6c778" },
  },
  {
    selector: ".symbol-function",
    style: { shape: "ellipse", "background-color": "#18392f", "border-color": "#85e2b9" },
  },
  {
    selector: ".symbol-method",
    style: { shape: "round-rectangle", "background-color": "#1c3440", "border-color": "#79c6d8" },
  },
  {
    selector: ".symbol-unknown",
    style: { shape: "diamond", "background-color": "#2d3340", "border-color": "#aab9c8" },
  },
  {
    selector: "node:selected",
    style: { "border-width": 4, "border-color": "#ffffff", "overlay-color": "#85e2b9", "overlay-opacity": 0.16 },
  },
  {
    selector: "edge",
    style: {
      width: 1.5,
      "curve-style": "bezier",
      "control-point-step-size": 34,
      "target-arrow-shape": "triangle",
      "arrow-scale": 0.9,
      "line-cap": "round",
      "overlay-opacity": 0,
    },
  },
  {
    selector: ".contains-edge",
    style: { "line-color": "#71808f", "target-arrow-color": "#71808f", "line-style": "dashed", width: 1.2 },
  },
  {
    selector: ".calls-edge",
    style: { "line-color": "#85e2b9", "target-arrow-color": "#85e2b9", "line-style": "solid", width: 2.2 },
  },
  {
    selector: ".imports-edge",
    style: { "line-color": "#79c6d8", "target-arrow-color": "#79c6d8", "line-style": "dotted", width: 1.7 },
  },
];

export function GraphVisualization({ graph, onNodeSelect, focusNodeId = null, focusRequest = 0 }: GraphVisualizationProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const instanceRef = useRef<cytoscape.Core | null>(null);
  const selectedCallbackRef = useRef(onNodeSelect);
  const layoutNameRef = useRef<GraphLayout>("breadthfirst");
  const [layoutName, setLayoutName] = useState<GraphLayout>("breadthfirst");
  const [renderError, setRenderError] = useState<string | null>(null);
  const hasNodes = graph.nodes.length > 0;

  useEffect(() => {
    selectedCallbackRef.current = onNodeSelect;
  }, [onNodeSelect]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    let instance: cytoscape.Core;
    try {
      instance = cytoscape({
        container,
        elements: [],
        style: stylesheet,
        layout: { name: "preset", fit: true },
        minZoom: 0.12,
        maxZoom: 3.5,
        wheelSensitivity: 0.2,
        selectionType: "single",
        boxSelectionEnabled: false,
      });
    } catch {
      setRenderError("The graph renderer could not be initialized in this browser.");
      return;
    }
    instanceRef.current = instance;

    const selectHandler = (event: cytoscape.EventObject) => {
      if (event.target.isNode()) selectedCallbackRef.current?.(event.target.id());
    };
    const unselectHandler = () => {
      const selected = instance.$("node:selected");
      selectedCallbackRef.current?.(selected.length ? selected[0].id() : null);
    };
    const backgroundHandler = (event: cytoscape.EventObject) => {
      if (event.target === instance) instance.$("node:selected").unselect();
    };
    instance.on("select", "node", selectHandler);
    instance.on("unselect", "node", unselectHandler);
    instance.on("tap", backgroundHandler);

    const resizeObserver = typeof ResizeObserver === "undefined"
      ? null
      : new ResizeObserver(() => instance.resize());
    resizeObserver?.observe(container);
    const handleWindowResize = () => instance.resize();
    window.addEventListener("resize", handleWindowResize);

    return () => {
      resizeObserver?.disconnect();
      window.removeEventListener("resize", handleWindowResize);
      instance.destroy();
      instanceRef.current = null;
      selectedCallbackRef.current?.(null);
    };
  }, []);

  useEffect(() => {
    const instance = instanceRef.current;
    if (!instance) return;
    try {
      const elements = toCytoscapeElements(graph);
      instance.elements().remove();
      instance.add(elements);
      if (graph.nodes.length > 0) instance.layout(layoutOptions(layoutNameRef.current)).run();
      else instance.fit(undefined, 36);
      setRenderError(null);
    } catch (error) {
      instance.elements().remove();
      setRenderError(error instanceof Error ? error.message : "The graph data could not be rendered.");
      selectedCallbackRef.current?.(null);
    }
  }, [graph]);

  useEffect(() => {
    const instance = instanceRef.current;
    if (!instance) return;
    if (!focusNodeId) {
      instance.$("node:selected").unselect();
      return;
    }
    const target = instance.getElementById(focusNodeId);
    if (target.empty() || !target.isNode()) return;
    instance.$("node:selected").not(target).unselect();
    if (!target.selected()) target.select();
    instance.center(target);
  }, [focusNodeId, focusRequest, graph]);

  useEffect(() => {
    if (layoutNameRef.current === layoutName) return;
    layoutNameRef.current = layoutName;
    const instance = instanceRef.current;
    if (instance && hasNodes && !renderError) {
      try {
        instance.layout(layoutOptions(layoutName)).run();
      } catch {
        setRenderError("The selected graph layout could not be applied.");
      }
    }
  }, [hasNodes, layoutName, renderError]);

  function zoom(factor: number) {
    const instance = instanceRef.current;
    if (!instance) return;
    const center = { x: instance.width() / 2, y: instance.height() / 2 };
    instance.zoom({ level: Math.min(3.5, Math.max(0.12, instance.zoom() * factor)), renderedPosition: center });
  }

  function fitGraph() {
    instanceRef.current?.fit(undefined, 36);
  }

  function centerGraph() {
    instanceRef.current?.center();
  }

  function clearSelection() {
    instanceRef.current?.$("node:selected").unselect();
  }

  const symbolKinds = getSymbolKinds(graph);
  const nodeTypes = new Set(graph.nodes.map((node) => node.data.node_type));
  const relationshipTypes = new Set(graph.edges.map((edge) => edge.data.relationship_type));
  const hasOtherSymbolKind = [...symbolKinds].some((kind) => !["class", "function", "method"].includes(kind));
  return (
    <section aria-labelledby="graph-visualization-heading" className="overflow-hidden rounded-lg border border-atlas-line bg-atlas-surface">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-atlas-line px-4 py-4 sm:px-5">
        <div>
          <p className="text-[10px] font-semibold tracking-[0.18em] text-atlas-muted">INTERACTIVE GRAPH</p>
          <h3 className="mt-1 text-base font-semibold text-white" id="graph-visualization-heading">Project structure and dependencies</h3>
        </div>
        <div className="flex flex-wrap items-center gap-2" aria-label="Graph controls" role="toolbar">
          <button aria-label="Zoom in" className="graph-control" disabled={!hasNodes || Boolean(renderError)} onClick={() => zoom(1.2)} type="button">+</button>
          <button aria-label="Zoom out" className="graph-control" disabled={!hasNodes || Boolean(renderError)} onClick={() => zoom(1 / 1.2)} type="button">−</button>
          <button aria-label="Fit graph to viewport" className="graph-control px-3" disabled={!hasNodes || Boolean(renderError)} onClick={fitGraph} type="button">Fit</button>
          <button aria-label="Re-center graph" className="graph-control px-3" disabled={!hasNodes || Boolean(renderError)} onClick={centerGraph} type="button">Center</button>
          <label className="sr-only" htmlFor="graph-layout">Graph layout</label>
          <select
            className="min-h-9 rounded-md border border-atlas-line bg-atlas-raised px-2 text-xs text-atlas-copy focus:border-atlas-accent"
            disabled={!hasNodes || Boolean(renderError)}
            id="graph-layout"
            onChange={(event) => setLayoutName(event.target.value as GraphLayout)}
            value={layoutName}
          >
            <option value="breadthfirst">Hierarchy</option>
            <option value="cose">Force</option>
            <option value="circle">Circle</option>
          </select>
          <button aria-label="Reapply selected graph layout" className="graph-control px-3" disabled={!hasNodes || Boolean(renderError)} onClick={() => {
            const instance = instanceRef.current;
            if (!instance) return;
            try {
              instance.layout(layoutOptions(layoutName)).run();
              setRenderError(null);
            } catch {
              setRenderError("The selected graph layout could not be applied.");
            }
          }} type="button">Apply layout</button>
          <button aria-label="Clear selected graph node" className="graph-control px-3" disabled={!hasNodes || Boolean(renderError)} onClick={clearSelection} type="button">Clear selection</button>
        </div>
      </div>

      <div className="relative">
        <div
          aria-label="Interactive software dependency graph. Use pointer drag to pan, wheel to zoom, and select a node for its ID."
          className="h-[620px] min-h-[420px] w-full bg-atlas-bg/70"
          ref={containerRef}
          role="application"
        />
        {renderError && (
          <div className="absolute inset-0 flex items-center justify-center bg-atlas-bg/95 p-6 text-center" role="alert">
            <div>
              <p className="font-medium text-rose-300">Graph rendering failed</p>
              <p className="mt-2 max-w-xl text-sm leading-6 text-atlas-muted">{renderError}</p>
            </div>
          </div>
        )}
        {!renderError && !hasNodes && (
          <div className="absolute inset-0 flex items-center justify-center p-6 text-center" role="status">
            <p className="text-sm text-atlas-muted">This analysis contains no graph nodes.</p>
          </div>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-x-5 gap-y-3 border-t border-atlas-line px-4 py-3 text-xs text-atlas-muted sm:px-5">
        <span className="font-medium text-atlas-copy">Legend</span>
        {nodeTypes.has("file") && <span className="inline-flex items-center gap-2"><i aria-hidden="true" className="size-3 rounded-sm border border-atlas-accent bg-[#17352f]" />File</span>}
        {symbolKinds.has("class") && <span className="inline-flex items-center gap-2"><i aria-hidden="true" className="size-3 rotate-45 border border-[#e6c778] bg-[#34301f]" />Class</span>}
        {symbolKinds.has("function") && <span className="inline-flex items-center gap-2"><i aria-hidden="true" className="size-3 rounded-full border border-atlas-accent bg-[#18392f]" />Function</span>}
        {symbolKinds.has("method") && <span className="inline-flex items-center gap-2"><i aria-hidden="true" className="size-3 rounded-sm border border-[#79c6d8] bg-[#1c3440]" />Method</span>}
        {hasOtherSymbolKind && <span className="inline-flex items-center gap-2"><i aria-hidden="true" className="size-3 rotate-45 border border-[#aab9c8] bg-[#2d3340]" />Other symbol</span>}
        {relationshipTypes.has("CONTAINS") && <span className="inline-flex items-center gap-2"><i aria-hidden="true" className="w-6 border-t border-dashed border-[#71808f]" />CONTAINS</span>}
        {relationshipTypes.has("CALLS") && <span className="inline-flex items-center gap-2"><i aria-hidden="true" className="w-6 border-t-2 border-atlas-accent" />CALLS</span>}
        {relationshipTypes.has("IMPORTS") && <span className="inline-flex items-center gap-2"><i aria-hidden="true" className="w-6 border-t border-dotted border-[#79c6d8]" />IMPORTS</span>}
      </div>
    </section>
  );
}
