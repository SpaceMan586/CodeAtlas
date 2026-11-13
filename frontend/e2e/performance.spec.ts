import { expect, test } from "@playwright/test";
import { BACKEND, makeScaleProject, median } from "./fixtures";
import { cyState, GRAPH_SELECTOR, trackBrowserErrors, traceRegion } from "./helpers";
import { toCytoscapeElements } from "../src/features/graph/graphElements";
import { createCallTraceIndex, runCallTrace } from "../src/features/trace/callTrace";
import type { GraphData } from "../src/types/graph";

interface ScaleRunMetrics {
  analysisMs: number;
  graphFetchMs: number;
  graphConversionMs: number;
  cytoscapeInitAndLayoutMs: number;
  searchUiMs: number;
  traceEngineMs: number;
  traceUiMs: number;
}

const SCALE_SIZES = [3, 30, 100] as const;
const RUNS_PER_SIZE = 3;

test("browser and pipeline performance across ~3, ~30, and ~100 Python files", async ({ page, request }) => {
  const { errors, failedResources } = trackBrowserErrors(page);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");

  const summaryTable: Array<{
    scaleFiles: number;
    scannedFiles: number;
    nodes: number;
    edges: number;
    callsEdges: number;
    importsEdges: number;
    medianAnalysisMs: number;
    medianGraphFetchMs: number;
    medianGraphConversionMs: number;
    medianCytoscapeInitLayoutMs: number;
    medianSearchUiMs: number;
    medianTraceEngineMs: number;
    medianTraceUiMs: number;
  }> = [];

  for (const scaleCount of SCALE_SIZES) {
    const project = await makeScaleProject(scaleCount);
    const runs: ScaleRunMetrics[] = [];
    let recordedScannedFiles = 0;
    let recordedNodes = 0;
    let recordedEdges = 0;
    let recordedCalls = 0;
    let recordedImports = 0;

    try {
      for (let runIndex = 0; runIndex < RUNS_PER_SIZE; runIndex += 1) {
        // 1. Measure backend analysis duration via UI submission
        await page.getByLabel("Project path accessible to Flask").fill(project.dir);
        const analysisResponsePromise = page.waitForResponse(
          (res) => res.request().method() === "POST" && res.url().endsWith("/api/analysis"),
        );
        const graphResponsePromise = page.waitForResponse(
          (res) => res.request().method() === "GET" && /\/api\/analysis\/[^/]+\/graph$/.test(res.url()),
        );

        const t0 = performance.now();
        await page.getByRole("button", { name: "Analyze project" }).click();
        const analysisResponse = await analysisResponsePromise;
        const tAnalysisDone = performance.now();
        expect(analysisResponse.status()).toBe(201);
        const analysisInfo = await analysisResponse.json();
        const analysisId = analysisInfo.analysis_id as string;
        recordedScannedFiles = analysisInfo.summary.scanned_files as number;

        // 2. Measure graph response duration and Cytoscape initialization/layout completion in browser
        const graphResponse = await graphResponsePromise;
        const graphJson = (await graphResponse.json()) as GraphData;
        const tGraphDone = performance.now();
        expect(graphResponse.status()).toBe(200);

        await page.waitForFunction(
          ({ selector, expectedNodes, expectedEdges }) => {
            const container = document.querySelector(selector) as any;
            const cy = container?._cyreg?.cy;
            return Boolean(
              cy &&
              !cy.destroyed() &&
              cy.nodes().length === expectedNodes &&
              cy.edges().length === expectedEdges,
            );
          },
          {
            selector: GRAPH_SELECTOR,
            expectedNodes: graphJson.nodes.length,
            expectedEdges: graphJson.edges.length,
          },
          { timeout: 60_000 },
        );
        const tCyRendered = performance.now();

        const state = await cyState(page);
        expect(state.nodeIds.length).toBe(graphJson.nodes.length);
        expect(state.edgeIds.length).toBe(graphJson.edges.length);

        recordedNodes = graphJson.statistics.total_nodes;
        recordedEdges = graphJson.statistics.total_edges;
        recordedCalls = graphJson.statistics.edges_by_relationship_type.CALLS ?? 0;
        recordedImports = graphJson.statistics.edges_by_relationship_type.IMPORTS ?? 0;

        // 3. Measure pure graph data conversion (toCytoscapeElements)
        const tConv0 = performance.now();
        const elements = toCytoscapeElements(graphJson);
        const tConv1 = performance.now();
        expect(elements.length).toBe(graphJson.nodes.length + graphJson.edges.length);

        // 4. Measure Function Search in real browser UI (including 180ms debounce)
        const targetModule = `m${String(scaleCount - 1).padStart(3, "0")}`;
        const searchBox = page.getByRole("searchbox", { name: "Name or qualified name" });
        const fileFilter = page.getByLabel("File path contains");
        await fileFilter.fill(targetModule);
        const tSearch0 = performance.now();
        await searchBox.fill("entry");
        const searchResultBtn = page
          .getByRole("list", { name: "Function search results" })
          .getByRole("button", { name: new RegExp(`^entry function.*${targetModule}`) });
        await expect(searchResultBtn).toHaveCount(1);
        const tSearch1 = performance.now();
        await searchResultBtn.click();

        // 5. Measure Function Trace (both pure engine and UI click-to-render at depth 5)
        const selectedState = await cyState(page);
        expect(selectedState.selected).toHaveLength(1);
        const rootNodeId = selectedState.selected[0];

        const tTraceEngine0 = performance.now();
        const traceIdx = createCallTraceIndex(graphJson);
        const engineTraceResult = runCallTrace(traceIdx, rootNodeId, "OUTGOING", 5);
        const tTraceEngine1 = performance.now();
        expect(engineTraceResult.nodeCount).toBeGreaterThan(1);

        const trace = traceRegion(page);
        await trace.getByLabel("Direction").selectOption("OUTGOING");
        await trace.getByLabel("Maximum depth").selectOption("5");
        const tTraceUi0 = performance.now();
        await trace.getByRole("button", { name: "Run trace" }).click();
        await expect(trace.getByText("Current result: outgoing · depth 5")).toBeVisible();
        const tTraceUi1 = performance.now();

        // Clean up active session before next run
        await page.getByRole("button", { name: "Delete analysis session" }).click();
        await expect(page.getByText("No project analyzed")).toBeVisible();

        runs.push({
          analysisMs: tAnalysisDone - t0,
          graphFetchMs: tGraphDone - tAnalysisDone,
          graphConversionMs: tConv1 - tConv0,
          cytoscapeInitAndLayoutMs: tCyRendered - tGraphDone,
          searchUiMs: tSearch1 - tSearch0,
          traceEngineMs: tTraceEngine1 - tTraceEngine0,
          traceUiMs: tTraceUi1 - tTraceUi0,
        });
      }

      summaryTable.push({
        scaleFiles: scaleCount,
        scannedFiles: recordedScannedFiles,
        nodes: recordedNodes,
        edges: recordedEdges,
        callsEdges: recordedCalls,
        importsEdges: recordedImports,
        medianAnalysisMs: Number(median(runs.map((r) => r.analysisMs)).toFixed(2)),
        medianGraphFetchMs: Number(median(runs.map((r) => r.graphFetchMs)).toFixed(2)),
        medianGraphConversionMs: Number(median(runs.map((r) => r.graphConversionMs)).toFixed(2)),
        medianCytoscapeInitLayoutMs: Number(median(runs.map((r) => r.cytoscapeInitAndLayoutMs)).toFixed(2)),
        medianSearchUiMs: Number(median(runs.map((r) => r.searchUiMs)).toFixed(2)),
        medianTraceEngineMs: Number(median(runs.map((r) => r.traceEngineMs)).toFixed(2)),
        medianTraceUiMs: Number(median(runs.map((r) => r.traceUiMs)).toFixed(2)),
      });
    } finally {
      await project.cleanup();
    }
  }

  console.log("PHASE_3_6C_PERFORMANCE_SUMMARY=" + JSON.stringify(summaryTable));
  expect(summaryTable).toHaveLength(3);
  expect(errors).toEqual([]);
  expect(failedResources).toEqual([]);
});
