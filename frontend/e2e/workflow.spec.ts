import { expect, test } from "@playwright/test";
import { access, readdir } from "node:fs/promises";
import { BACKEND, makeFeatureProject, makeReplacementProject, snapshotTree } from "./fixtures";
import {
  analyzeInUi,
  cyState,
  distanceFromCentre,
  edgeGeometry,
  fetchGraph,
  GRAPH_SELECTOR,
  inspector,
  nodeByQualifiedName,
  nodeClientPoint,
  paintedPixels,
  searchAndSelect,
  trackBrowserErrors,
  traceRegion,
} from "./helpers";

async function exists(path: string): Promise<boolean> {
  try {
    await access(path);
    return true;
  } catch {
    return false;
  }
}

test("complete real-browser workflow: analysis, Cytoscape graph, search, trace, Inspector, replacement, deletion", async ({ page, request }) => {
  const { errors, failedResources } = trackBrowserErrors(page);
  const apiCalls: string[] = [];
  page.on("request", (req) => {
    if (req.url().includes("/api/analysis")) apiCalls.push(`${req.method()} ${new URL(req.url()).pathname}`);
  });
  const project = await makeFeatureProject();
  const replacement = await makeReplacementProject();
  const before = await snapshotTree(project.dir);
  try {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");
    await expect(page.getByText("No project analyzed")).toBeVisible();
    await expect(page.getByRole("heading", { name: "CodeAtlas · v0.1.0" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Analyze project" })).toBeDisabled();

    // 1–4. Submit the path, complete analysis, retrieve the graph once.
    const analysisId = await analyzeInUi(page, project.dir);
    expect(apiCalls).toEqual(["POST /api/analysis", `GET /api/analysis/${analysisId}/graph`]);
    await expect(page.getByText("Completed with warnings")).toBeVisible();
    const graph = await fetchGraph(request, analysisId);
    expect(graph.statistics.nodes_by_category).toEqual({ file: 7, symbol: 17 });
    expect(graph.statistics.edges_by_relationship_type).toMatchObject({ CALLS: 10, IMPORTS: 5 });
    expect(graph.nodes.find((node) => node.id === "file:broken.py")?.data.parsing_status).not.toBe("parsed");

    // 5–6. The real Cytoscape instance renders every node and edge, unchanged.
    const state = await cyState(page);
    expect(state.exists).toBe(true);
    expect(state.nodeIds).toEqual(graph.nodes.map((node) => node.id).sort());
    expect(state.edgeIds).toEqual(graph.edges.map((edge) => edge.id).sort());
    expect(state.canvasCount).toBeGreaterThan(0);
    expect(state.width).toBe(state.containerWidth);
    expect(state.height).toBe(state.containerHeight);
    expect(await paintedPixels(page)).toBeGreaterThan(500);

    const process = nodeByQualifiedName(graph, "process");
    const calculate = nodeByQualifiedName(graph, "calculate_total");
    const countdown = nodeByQualifiedName(graph, "countdown");
    const main = nodeByQualifiedName(graph, "main");
    const sumPrices = nodeByQualifiedName(graph, "sum_prices");
    const inventoryHelper = nodeByQualifiedName(graph, "helper", "shop/inventory.py");
    const pricingHelper = nodeByQualifiedName(graph, "helper", "shop/pricing.py");

    // Parallel CALLS edges (two call sites in process) stay distinct and visibly separated.
    const parallel = await edgeGeometry(page, process.id, calculate.id);
    expect(parallel).toHaveLength(2);
    expect(new Set(parallel.map((edge) => edge.key)).size).toBe(2);
    expect(parallel.every((edge) => edge.visible && edge.width > 0)).toBe(true);
    const separation = Math.hypot(parallel[0].midpoint.x - parallel[1].midpoint.x, parallel[0].midpoint.y - parallel[1].midpoint.y);
    expect(separation).toBeGreaterThan(2);
    expect(await edgeGeometry(page, countdown.id, countdown.id)).toHaveLength(1);
    await page.locator(GRAPH_SELECTOR).screenshot({ path: test.info().outputPath("graph-rendered-1440.png") });

    // Real pointer selection of a rendered node.
    await page.getByRole("button", { name: "Fit graph to viewport" }).click();
    await page.locator(GRAPH_SELECTOR).scrollIntoViewIfNeeded();
    const point = await nodeClientPoint(page, main.id);
    await page.mouse.click(point.x, point.y);
    await expect(page.getByText(`Selected node ID: ${main.id}`)).toBeVisible();
    expect((await cyState(page)).selected).toEqual([main.id]);
    await expect(inspector(page).getByRole("heading", { level: 4, name: "main", exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Clear selected graph node" }).click();
    await expect(page.getByText("Select a graph node to see its ID here.")).toBeVisible();

    // Graph controls operate on the real instance without errors.
    const zoomBefore = await page.evaluate((selector) => (document.querySelector(selector) as any)._cyreg.cy.zoom(), GRAPH_SELECTOR);
    await page.getByRole("button", { name: "Zoom in" }).click();
    const zoomAfter = await page.evaluate((selector) => (document.querySelector(selector) as any)._cyreg.cy.zoom(), GRAPH_SELECTOR);
    expect(zoomAfter).toBeGreaterThan(zoomBefore);
    for (const layout of ["cose", "circle", "breadthfirst"]) {
      await page.getByLabel("Graph layout", { exact: true }).selectOption(layout);
      await page.getByRole("button", { name: "Reapply selected graph layout" }).click();
    }
    await page.getByRole("button", { name: "Fit graph to viewport" }).click();
    await page.getByRole("button", { name: "Re-center graph" }).click();

    // 7–8. Function Search: case-insensitive, duplicate names distinct, exact node focus.
    await page.getByRole("searchbox", { name: "Name or qualified name" }).fill("HELPER");
    const results = page.getByRole("list", { name: "Function search results" }).getByRole("button");
    await expect(results).toHaveCount(2);
    await expect(page.getByText("2 matching symbols")).toBeVisible();
    await searchAndSelect(page, "HELPER", /^helper function helper shop\/inventory\.py/);
    expect((await cyState(page)).selected).toEqual([inventoryHelper.id]);
    expect((await cyState(page)).selected).not.toContain(pricingHelper.id);
    expect(await distanceFromCentre(page, inventoryHelper.id)).toBeLessThan(5);
    await expect(inspector(page).getByText("shop/inventory.py", { exact: true })).toBeVisible();
    await expect(inspector(page).getByText("Inventory helper with a duplicated name.", { exact: true })).toBeVisible();

    // 9. Outgoing trace from process, with parallel relationships preserved.
    await searchAndSelect(page, "process", /^process function/);
    const trace = traceRegion(page);
    await trace.getByRole("button", { name: "Run trace" }).click();
    await expect(trace.getByText("Current result: outgoing · depth 3")).toBeVisible();
    await expect(trace.getByText("6 nodes · 6 individual CALLS relationships · reached depth 2")).toBeVisible();
    const depthOne = trace.getByRole("region", { name: "Trace nodes at depth 1" });
    await expect(depthOne.getByRole("listitem").filter({ hasText: "calculate_total" })).toBeVisible();
    await expect(depthOne.getByText("constructor target", { exact: true })).toBeVisible();
    await expect(depthOne.getByRole("listitem").filter({ hasText: "reserve" })).toBeVisible();
    const relationshipsOne = trace.getByRole("region", { name: "Trace call relationships at depth 1" });
    await expect(relationshipsOne.getByRole("listitem")).toHaveCount(4);
    for (const edge of parallel) await expect(relationshipsOne.getByText(`Edge key: ${edge.key}`)).toBeVisible();
    await expect(trace.getByRole("region", { name: "Trace nodes at depth 2" }).getByRole("listitem").filter({ hasText: "sum_prices" })).toBeVisible();

    // 10. Incoming and both-direction traces.
    await trace.getByLabel("Direction").selectOption("INCOMING");
    await trace.getByRole("button", { name: "Run trace" }).click();
    await expect(trace.getByText("Current result: incoming · depth 3")).toBeVisible();
    await expect(trace.getByRole("region", { name: "Trace nodes at depth 1" }).getByRole("listitem").filter({ hasText: "main" })).toBeVisible();
    await trace.getByLabel("Direction").selectOption("BOTH");
    await trace.getByLabel("Maximum depth").selectOption("5");
    await trace.getByRole("button", { name: "Run trace" }).click();
    await expect(trace.getByText("Current result: both · depth 5")).toBeVisible();
    // BOTH expands callers and callees at every hop, so it also reaches
    // Order.total (another caller of calculate_total) and main's helper callee.
    await expect(trace.getByText("9 nodes · 9 individual CALLS relationships · reached depth 2")).toBeVisible();
    await trace.getByLabel("Maximum depth").selectOption("1");
    await trace.getByRole("button", { name: "Run trace" }).click();
    await expect(trace.getByText("The depth limit blocked additional reachable nodes. Increase depth to continue.")).toBeVisible();

    // 11. Navigate through a trace result: it becomes the shared selection and next root.
    await trace.getByLabel("Direction").selectOption("OUTGOING");
    await trace.getByRole("button", { name: "Run trace" }).click();
    await trace.getByRole("region", { name: "Trace nodes at depth 1" }).getByRole("listitem").filter({ hasText: "calculate_total" }).getByRole("button", { name: "Focus & use as root" }).click();
    await expect(page.getByText(`Selected node ID: ${calculate.id}`)).toBeVisible();
    expect((await cyState(page)).selected).toEqual([calculate.id]);
    expect(await distanceFromCentre(page, calculate.id)).toBeLessThan(5);
    await expect(trace.getByText("Current result:")).toHaveCount(0);
    await trace.getByRole("button", { name: "Run trace" }).click();
    await expect(trace.getByText("3 nodes · 2 individual CALLS relationships · reached depth 1")).toBeVisible();

    // 12–13. Inspector details and bounded source for the navigated symbol.
    const panel = inspector(page);
    await expect(panel.getByRole("heading", { level: 4, name: "calculate_total", exact: true })).toBeVisible();
    await expect(panel.getByText("@log_call", { exact: true })).toBeVisible();
    await expect(panel.getByText("Return the taxed total of prices minus a discount.", { exact: true })).toBeVisible();
    await expect(panel.getByText("positional_or_keyword · list[float]", { exact: true })).toBeVisible();
    await expect(panel.getByText("Default: 0.0", { exact: true })).toBeVisible();
    await expect(panel.getByText("Return: float", { exact: true })).toBeVisible();
    await expect(panel.getByLabel("Source code")).toContainText("def calculate_total(prices: list[float], discount: float = 0.0) -> float:");
    await expect(panel.getByText("Incoming edges").locator("xpath=following-sibling::dd")).toHaveText("3");
    await expect(panel.getByText("Distinct callers").locator("xpath=following-sibling::dd")).toHaveText("2");

    // Recursive function: self-loop retained once, trace terminates.
    await searchAndSelect(page, "countdown", /^countdown function/);
    await trace.getByRole("button", { name: "Run trace" }).click();
    await expect(trace.getByText("1 nodes · 1 individual CALLS relationships · reached depth 0")).toBeVisible();
    await expect(trace.getByRole("region", { name: "Trace call relationships at depth 1" }).getByRole("listitem")).toHaveCount(1);

    // Method with parent class.
    await searchAndSelect(page, "Order.total", /^total method/);
    await expect(panel.getByRole("heading", { name: "Parent symbol" })).toBeVisible();
    await expect(panel.getByText("Total of the order.", { exact: true })).toBeVisible();
    await expect(panel.getByLabel("Source code")).toContainText("return calculate_total(self.items)");

    // Bounded source reads: 500-line range cap and per-line cap are reported.
    await searchAndSelect(page, "long_function", /^long_function function/);
    await expect(panel.getByText("The source snippet was truncated at the display limit.")).toBeVisible();
    await expect(panel.getByLabel("Source code").locator("code > *")).toHaveCount(500);
    await searchAndSelect(page, "wide_function", /^wide_function function/);
    await expect(panel.getByText("The source snippet was truncated at the display limit.")).toBeVisible();

    // Stale-response protection in a real browser: a slow source response for a
    // previous selection must not replace the current symbol's source.
    let delayed = 0;
    await page.route(`**/api/analysis/${analysisId}/symbols/source?symbol_id=${encodeURIComponent(calculate.data.symbol_id)}`, async (route) => {
      delayed++;
      await new Promise((resolve) => setTimeout(resolve, 1500));
      await route.continue().catch(() => undefined);
    });
    await searchAndSelect(page, "calculate_total", /^calculate_total function/);
    await searchAndSelect(page, "sum_prices", /^sum_prices function/);
    await page.waitForTimeout(2000);
    expect(delayed).toBe(1);
    await expect(panel.getByRole("heading", { level: 4, name: "sum_prices", exact: true })).toBeVisible();
    await expect(panel.getByLabel("Source code")).toContainText("def sum_prices");
    await expect(panel.getByLabel("Source code")).not.toContainText("def calculate_total");
    expect((await cyState(page)).selected).toEqual([sumPrices.id]);
    await page.unrouteAll({ behavior: "wait" });

    // Failed replacement keeps the current analysis and its graph.
    await page.getByLabel("Project path accessible to Flask").fill(`${project.dir}\\does-not-exist`);
    await page.getByRole("button", { name: "Analyze project" }).click();
    await expect(page.getByRole("alert").filter({ hasText: /./ }).first()).toBeVisible();
    await expect(page.locator("#analysis-summary-heading + p")).toHaveText(analysisId);
    expect((await cyState(page)).nodeIds).toHaveLength(graph.nodes.length);

    // 14–15. Replace the active analysis; old state must be gone.
    await searchAndSelect(page, "process", /^process function/);
    await trace.getByRole("button", { name: "Run trace" }).click();
    await expect(trace.getByText(/Current result:/)).toBeVisible();
    const replacementId = await analyzeInUi(page, replacement.dir);
    expect(replacementId).not.toBe(analysisId);
    expect((await request.get(`${BACKEND}/api/analysis/${analysisId}`)).status()).toBe(404);
    const replacementGraph = await fetchGraph(request, replacementId);
    const replaced = await cyState(page);
    expect(replaced.nodeIds).toEqual(replacementGraph.nodes.map((node) => node.id).sort());
    expect(replaced.edgeIds).toEqual(replacementGraph.edges.map((edge) => edge.id).sort());
    expect(replaced.nodeIds.filter((id) => graph.nodes.some((node) => node.id === id))).toEqual([]);
    expect(replaced.selected).toEqual([]);
    await expect(page.getByRole("searchbox", { name: "Name or qualified name" })).toHaveValue("");
    await expect(trace.getByText(/Current result:/)).toHaveCount(0);
    await expect(panel.getByText("Select a function, method, or class in the graph or from search/trace to inspect it.")).toBeVisible();
    await expect(page.getByText("Select a graph node to see its ID here.")).toBeVisible();
    await expect(page.getByText(/process|calculate_total/)).toHaveCount(0);
    await searchAndSelect(page, "gamma", /^gamma_root function/);
    await expect(panel.getByLabel("Source code")).toContainText("def gamma_root");

    // 16. Delete the active analysis.
    await page.getByRole("button", { name: "Delete analysis session" }).click();
    await expect(page.getByText("No project analyzed")).toBeVisible();
    await expect(page.locator(GRAPH_SELECTOR)).toHaveCount(0);
    expect((await request.get(`${BACKEND}/api/analysis/${replacementId}`)).status()).toBe(404);

    // Static-analysis guarantees for this workflow.
    for (const marker of [...project.markers, ...replacement.markers]) expect(await exists(marker), marker).toBe(false);
    expect(await snapshotTree(project.dir)).toEqual(before);
    expect(await readdir(project.dir)).not.toContain("__pycache__");

    // Only the deliberately invalid replacement produced a failed request.
    expect(errors).toEqual([]);
    expect(failedResources).toHaveLength(1);
    expect(failedResources[0]).toContain("400");
  } finally {
    await project.cleanup();
    await replacement.cleanup();
  }
});
