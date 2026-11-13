/** Shared real-browser helpers for the Phase 3.6C Playwright suite. */
import { expect, type Page, type APIRequestContext } from "@playwright/test";
import { BACKEND } from "./fixtures";

export const GRAPH_SELECTOR = '[role="application"][aria-label^="Interactive software dependency graph"]';

export interface GraphNodeJson { id: string; data: Record<string, any> }
export interface GraphEdgeJson { id: string; source: string; target: string; key: string; data: Record<string, any> }
export interface GraphJson { analysis_id: string; nodes: GraphNodeJson[]; edges: GraphEdgeJson[]; statistics: Record<string, any> }

/**
 * Collect page errors and console errors. Browser-generated "Failed to load
 * resource" console errors are kept separately so a test can allow only the
 * HTTP failures it deliberately provokes.
 */
export function trackBrowserErrors(page: Page) {
  const errors: string[] = [];
  const failedResources: string[] = [];
  page.on("pageerror", (error) => errors.push(`pageerror: ${error.message}`));
  page.on("console", (message) => {
    if (message.type() !== "error") return;
    const text = message.text();
    if (text.startsWith("Failed to load resource")) failedResources.push(`${text} @ ${message.location().url}`);
    else errors.push(`console: ${text}`);
  });
  return { errors, failedResources };
}

/** Submit a project path through the UI and wait for the rendered graph. */
export async function analyzeInUi(page: Page, projectPath: string): Promise<string> {
  await page.getByLabel("Project path accessible to Flask").fill(projectPath);
  const created = page.waitForResponse((response) => response.request().method() === "POST" && response.url().endsWith("/api/analysis"));
  await page.getByRole("button", { name: "Analyze project" }).click();
  const response = await created;
  expect(response.status()).toBe(201);
  const analysisId = (await response.json()).analysis_id as string;
  await expect(page.locator("#analysis-summary-heading + p")).toHaveText(analysisId, { timeout: 120_000 });
  await expect(page.getByRole("heading", { name: "Graph loaded" })).toBeVisible();
  await waitForRenderedGraph(page);
  return analysisId;
}

export async function fetchGraph(request: APIRequestContext, analysisId: string): Promise<GraphJson> {
  const response = await request.get(`${BACKEND}/api/analysis/${analysisId}/graph`);
  expect(response.status()).toBe(200);
  return response.json();
}

/** Wait until the real Cytoscape instance exists and has elements. */
export async function waitForRenderedGraph(page: Page): Promise<void> {
  await page.waitForFunction((selector) => {
    const container = document.querySelector(selector) as any;
    const cy = container?._cyreg?.cy;
    return Boolean(cy && !cy.destroyed() && cy.nodes().length > 0);
  }, GRAPH_SELECTOR, { timeout: 60_000 });
}

export interface CyState {
  exists: boolean;
  nodeIds: string[];
  edgeIds: string[];
  selected: string[];
  width: number;
  height: number;
  containerWidth: number;
  containerHeight: number;
  canvasCount: number;
}

/** Read state from the real Cytoscape instance registered on its container. */
export async function cyState(page: Page): Promise<CyState> {
  return page.evaluate((selector) => {
    const container = document.querySelector(selector) as any;
    const cy = container?._cyreg?.cy;
    if (!cy || cy.destroyed()) {
      return { exists: false, nodeIds: [], edgeIds: [], selected: [], width: 0, height: 0, containerWidth: 0, containerHeight: 0, canvasCount: 0 };
    }
    return {
      exists: true,
      nodeIds: cy.nodes().map((node: any) => node.id()).sort(),
      edgeIds: cy.edges().map((edge: any) => edge.id()).sort(),
      selected: cy.$("node:selected").map((node: any) => node.id()),
      width: cy.width(),
      height: cy.height(),
      containerWidth: container.clientWidth,
      containerHeight: container.clientHeight,
      canvasCount: container.querySelectorAll("canvas").length,
    };
  }, GRAPH_SELECTOR);
}

/** Count painted (non-transparent) pixels across Cytoscape's canvas layers. */
export async function paintedPixels(page: Page): Promise<number> {
  return page.evaluate((selector) => {
    const container = document.querySelector(selector);
    let painted = 0;
    for (const canvas of Array.from(container?.querySelectorAll("canvas") ?? [])) {
      const context = (canvas as HTMLCanvasElement).getContext("2d");
      if (!context || canvas.width === 0 || canvas.height === 0) continue;
      const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
      for (let index = 3; index < pixels.length; index += 4 * 7) if (pixels[index] !== 0) painted++;
    }
    return painted;
  }, GRAPH_SELECTOR);
}

/** Rendered geometry of edges between two nodes (midpoints and bezier control points). */
export async function edgeGeometry(page: Page, source: string, target: string) {
  return page.evaluate(({ selector, source, target }) => {
    const cy = (document.querySelector(selector) as any)._cyreg.cy;
    return cy.edges().filter((edge: any) => edge.source().id() === source && edge.target().id() === target).map((edge: any) => ({
      id: edge.id(),
      key: edge.data("edgeKey"),
      visible: edge.visible(),
      width: edge.renderedWidth(),
      midpoint: edge.renderedMidpoint(),
    }));
  }, { selector: GRAPH_SELECTOR, source, target });
}

/** Rendered viewport coordinates of a node, for real pointer interaction. */
export async function nodeClientPoint(page: Page, nodeId: string): Promise<{ x: number; y: number }> {
  return page.evaluate(({ selector, nodeId }) => {
    const container = document.querySelector(selector) as any;
    const cy = container._cyreg.cy;
    const position = cy.getElementById(nodeId).renderedPosition();
    const box = container.getBoundingClientRect();
    return { x: box.left + position.x, y: box.top + position.y };
  }, { selector: GRAPH_SELECTOR, nodeId });
}

/** Distance of a node's rendered position from the graph viewport centre. */
export async function distanceFromCentre(page: Page, nodeId: string): Promise<number> {
  return page.evaluate(({ selector, nodeId }) => {
    const cy = (document.querySelector(selector) as any)._cyreg.cy;
    const position = cy.getElementById(nodeId).renderedPosition();
    return Math.hypot(position.x - cy.width() / 2, position.y - cy.height() / 2);
  }, { selector: GRAPH_SELECTOR, nodeId });
}

export function nodeByQualifiedName(graph: GraphJson, qualifiedName: string, filePath?: string): GraphNodeJson {
  const matches = graph.nodes.filter((node) => node.data.qualified_name === qualifiedName && (!filePath || node.data.file_path === filePath));
  expect(matches, `${qualifiedName} ${filePath ?? ""}`).toHaveLength(1);
  return matches[0];
}

export async function hasHorizontalOverflow(page: Page): Promise<boolean> {
  return page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1);
}

export async function searchAndSelect(page: Page, query: string, resultName: RegExp): Promise<void> {
  await page.getByRole("searchbox", { name: "Name or qualified name" }).fill(query);
  const result = page.getByRole("list", { name: "Function search results" }).getByRole("button", { name: resultName });
  await expect(result).toHaveCount(1);
  await result.click();
  await expect(result).toHaveAttribute("aria-pressed", "true");
}

export function inspector(page: Page) {
  return page.getByRole("region", { name: "Function Inspector" });
}

export function traceRegion(page: Page) {
  return page.getByRole("region", { name: "Function Trace" });
}
