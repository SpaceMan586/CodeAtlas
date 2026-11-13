import { expect, test } from "@playwright/test";
import { makeFeatureProject } from "./fixtures";
import {
  analyzeInUi,
  cyState,
  distanceFromCentre,
  GRAPH_SELECTOR,
  hasHorizontalOverflow,
  inspector,
  nodeByQualifiedName,
  paintedPixels,
  searchAndSelect,
  trackBrowserErrors,
  traceRegion,
} from "./helpers";

const VIEWPORTS = [
  { width: 1440, height: 900, label: "desktop-1440x900" },
  { width: 1366, height: 768, label: "laptop-1366x768" },
  { width: 768, height: 1024, label: "tablet-768x1024" },
  { width: 390, height: 844, label: "mobile-390x844" },
] as const;

test("responsive viewports (1440x900, 1366x768, 768x1024, 390x844): no horizontal overflow, graph resizes, and all exploration tools remain usable", async ({ page }) => {
  const { errors, failedResources } = trackBrowserErrors(page);
  const project = await makeFeatureProject();
  try {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");
    expect(await hasHorizontalOverflow(page)).toBe(false);

    const analysisId = await analyzeInUi(page, project.dir);
    expect(analysisId).toBeTruthy();

    for (const vp of VIEWPORTS) {
      await page.setViewportSize({ width: vp.width, height: vp.height });
      // Allow ResizeObserver and layout reflow to settle.
      await page.waitForTimeout(200);

      expect(await hasHorizontalOverflow(page), `horizontal overflow at ${vp.label}`).toBe(false);

      // Verify graph container and Cytoscape viewport resize together.
      const state = await cyState(page);
      expect(state.exists, `Cytoscape exists at ${vp.label}`).toBe(true);
      expect(state.containerWidth, `container width at ${vp.label}`).toBeGreaterThan(200);
      expect(state.containerHeight, `container height at ${vp.label}`).toBeGreaterThanOrEqual(420);
      expect(state.width, `cy width matches container at ${vp.label}`).toBe(state.containerWidth);
      expect(state.height, `cy height matches container at ${vp.label}`).toBe(state.containerHeight);
      expect(await paintedPixels(page), `painted pixels at ${vp.label}`).toBeGreaterThan(200);

      // Verify Search is accessible and selects a node at this viewport.
      const searchBox = page.getByRole("searchbox", { name: "Name or qualified name" });
      await searchBox.scrollIntoViewIfNeeded();
      await searchAndSelect(page, "calculate_total", /^calculate_total function/);
      const afterSelect = await cyState(page);
      expect(afterSelect.selected).toHaveLength(1);
      const selectedId = afterSelect.selected[0];
      expect(await distanceFromCentre(page, selectedId), `centered at ${vp.label}`).toBeLessThan(5);

      // Verify Function Trace controls and results are usable at this viewport.
      const trace = traceRegion(page);
      await trace.scrollIntoViewIfNeeded();
      await trace.getByLabel("Direction").selectOption("OUTGOING");
      await trace.getByLabel("Maximum depth").selectOption("2");
      await trace.getByRole("button", { name: "Run trace" }).click();
      await expect(trace.getByText("Current result: outgoing · depth 2")).toBeVisible();
      await expect(trace.getByText("3 nodes · 2 individual CALLS relationships · reached depth 1")).toBeVisible();

      // Verify Function Inspector renders symbol details and scrollable source code without page overflow.
      const panel = inspector(page);
      await panel.scrollIntoViewIfNeeded();
      await expect(panel.getByRole("heading", { level: 4, name: "calculate_total", exact: true })).toBeVisible();
      await expect(panel.getByLabel("Source code")).toContainText("def calculate_total");
      expect(await hasHorizontalOverflow(page), `overflow after opening panels at ${vp.label}`).toBe(false);

      // Verify graph toolbar controls are reachable and operable at this viewport.
      const graphToolbar = page.getByRole("toolbar", { name: "Graph controls" });
      await graphToolbar.scrollIntoViewIfNeeded();
      await graphToolbar.getByRole("button", { name: "Fit graph to viewport" }).click();
      await graphToolbar.getByRole("button", { name: "Re-center graph" }).click();
      expect(await hasHorizontalOverflow(page), `overflow after graph controls at ${vp.label}`).toBe(false);

      await page.screenshot({ path: test.info().outputPath(`viewport-${vp.label}.png`), fullPage: false });
    }

    expect(errors).toEqual([]);
    expect(failedResources).toEqual([]);
  } finally {
    await project.cleanup();
  }
});
