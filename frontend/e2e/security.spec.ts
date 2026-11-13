import { expect, test } from "@playwright/test";
import { execFile } from "node:child_process";
import { access, mkdir, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { promisify } from "node:util";
import { BACKEND, makeFeatureProject, snapshotTree } from "./fixtures";
import {
  analyzeInUi,
  fetchGraph,
  inspector,
  nodeByQualifiedName,
  searchAndSelect,
  trackBrowserErrors,
} from "./helpers";

const execFileAsync = promisify(execFile);

async function exists(path: string): Promise<boolean> {
  try {
    await access(path);
    return true;
  } catch {
    return false;
  }
}

test("security and static-analysis guarantees: non-execution, read-only tree, path traversal & outside-root rejection, junction handling, bounded reads, and honest source freshness", async ({ page, request }) => {
  const { errors } = trackBrowserErrors(page);
  const project = await makeFeatureProject();
  const outsideFile = join(project.dir, "..", `codeatlas-outside-${Date.now()}.py`);
  await writeFile(outsideFile, "SECRET = 'outside_root'\ndef outside() -> str:\n    return SECRET\n", "utf8");

  // Attempt to create a Windows directory junction inside the project pointing outside,
  // to verify scanner and Inspector link/junction protections in a real OS environment.
  const externalDir = join(project.dir, "..", `codeatlas-ext-dir-${Date.now()}`);
  const externalMarker = join(externalDir, "EXT_EXECUTED.marker");
  await mkdir(externalDir, { recursive: true });
  await writeFile(
    join(externalDir, "sneaky.py"),
    `open(${JSON.stringify(externalMarker)}, "w").write("executed")\ndef sneaky() -> int:\n    return 42\n`,
    "utf8",
  );
  const junctionDir = join(project.dir, "linked_outside");
  let junctionCreated = false;
  try {
    await execFileAsync("cmd.exe", ["/c", "mklink", "/J", junctionDir, externalDir]);
    junctionCreated = true;
  } catch {
    junctionCreated = false;
  }

  const snapshotBefore = await snapshotTree(project.dir);

  try {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");

    // 1. Analyze project and verify no package __init__.py, module top-level, setup.py, or external junction file was executed.
    const analysisId = await analyzeInUi(page, project.dir);
    for (const marker of project.markers) {
      expect(await exists(marker), `Marker must not exist: ${marker}`).toBe(false);
    }
    expect(await exists(externalMarker), "External junction marker must not exist").toBe(false);

    // 2. Verify no source files were modified and no __pycache__ was created anywhere in the project.
    expect(await snapshotTree(project.dir)).toEqual(snapshotBefore);
    expect(await readdir(project.dir)).not.toContain("__pycache__");
    expect(await readdir(join(project.dir, "shop"))).not.toContain("__pycache__");

    // 3. If a directory junction was created, verify the scanner skipped it without traversing its target.
    const graph = await fetchGraph(request, analysisId);
    if (junctionCreated) {
      const leakedNodes = graph.nodes.filter((node) => node.id.includes("linked_outside") || node.id.includes("sneaky"));
      expect(leakedNodes).toEqual([]);
    }

    // 4. Verify analysis API rejects non-directory paths, missing paths, and file paths.
    const analyzeFileRes = await request.post(`${BACKEND}/api/analysis`, {
      data: { project_path: outsideFile },
    });
    expect(analyzeFileRes.status()).toBe(400);

    const analyzeTraversalMissing = await request.post(`${BACKEND}/api/analysis`, {
      data: { project_path: join(project.dir, "..", "nonexistent_traversal_target") },
    });
    expect(analyzeTraversalMissing.status()).toBe(400);

    // 5. Verify Inspector lookup & source endpoints reject path-traversal or fabricated symbol IDs.
    for (const payload of [
      "../outside.py",
      "..\\outside.py",
      "file:../outside.py",
      "py:v1:../../outside",
      "C:\\Windows\\System32\\drivers\\etc\\hosts",
    ]) {
      const lookupRes = await request.get(`${BACKEND}/api/analysis/${analysisId}/symbols/lookup?symbol_id=${encodeURIComponent(payload)}`);
      expect(lookupRes.status()).toBe(404);
      const sourceRes = await request.get(`${BACKEND}/api/analysis/${analysisId}/symbols/source?symbol_id=${encodeURIComponent(payload)}`);
      expect(sourceRes.status()).toBe(404);
    }

    // Verify absolute project path is never leaked in analysis info or symbol lookup/source responses.
    const infoRes = await request.get(`${BACKEND}/api/analysis/${analysisId}`);
    expect(infoRes.status()).toBe(200);
    const infoText = JSON.stringify(await infoRes.json());
    expect(infoText).not.toContain(project.dir);
    expect(infoText).not.toContain(project.dir.replaceAll("\\", "/"));

    // 6. Verify bounded source reads in Inspector (500-line cap and 16,384-char per-line cap).
    const longFn = nodeByQualifiedName(graph, "long_function");
    const longSourceRes = await request.get(
      `${BACKEND}/api/analysis/${analysisId}/symbols/source?symbol_id=${encodeURIComponent(longFn.data.symbol_id)}`,
    );
    expect(longSourceRes.status()).toBe(200);
    const longSource = await longSourceRes.json();
    expect(longSource.status).toBe("available");
    expect(longSource.truncated).toBe(true);
    expect(longSource.end_line - longSource.start_line + 1).toBeLessThanOrEqual(500);

    const wideFn = nodeByQualifiedName(graph, "wide_function");
    const wideSourceRes = await request.get(
      `${BACKEND}/api/analysis/${analysisId}/symbols/source?symbol_id=${encodeURIComponent(wideFn.data.symbol_id)}`,
    );
    expect(wideSourceRes.status()).toBe(200);
    const wideSource = await wideSourceRes.json();
    expect(wideSource.status).toBe("available");
    expect(wideSource.truncated).toBe(true);
    const wideLines = (wideSource.code as string).split(/\r\n|\n|\r/);
    expect(Math.max(...wideLines.map((line: string) => line.length))).toBeLessThanOrEqual(16_384);
    expect(wideSource.code.length).toBeLessThanOrEqual(256_000);

    // 7. Honest source freshness behavior:
    //    (a) Unmodified file reports changed_after_analysis: false and freshness: "unverified"
    const panel = inspector(page);
    await searchAndSelect(page, "reserve", /^reserve function/);
    await expect(panel.getByText("Current file contents are shown; exact snapshot freshness could not be verified.")).toBeVisible();

    //    (b) Same-size edit cannot be detected (honest documented limitation: changed_after_analysis remains false, freshness remains "unverified")
    const inventoryPath = join(project.dir, "shop", "inventory.py");
    const originalInventory = await readFile(inventoryPath, "utf8");
    const sameSizeEdited = originalInventory.replace('return "inventory"', 'return "INVENTORY"');
    expect(Buffer.byteLength(sameSizeEdited)).toBe(Buffer.byteLength(originalInventory));
    await writeFile(inventoryPath, sameSizeEdited, "utf8");

    await searchAndSelect(page, "helper", /^helper function helper shop\/inventory\.py/);
    await expect(panel.getByLabel("Source code")).toContainText('return "INVENTORY"');
    await expect(panel.getByText("Current file contents are shown; exact snapshot freshness could not be verified.")).toBeVisible();

    //    (c) Size-changing edit IS detected and warns the user in the UI
    await writeFile(inventoryPath, `${originalInventory}\n# appended comment after analysis\n`, "utf8");
    await searchAndSelect(page, "reserve", /^reserve function/);
    await expect(panel.getByText("This file’s size changed after analysis. The snippet shows its current contents.")).toBeVisible();

    //    (d) Post-analysis replacement of a source file with a junction/link is rejected safely by Inspector without crashing metadata
    const reserveNode = nodeByQualifiedName(graph, "reserve");
    await rm(inventoryPath, { force: true });
    let fileReplacedWithJunction = false;
    try {
      await execFileAsync("cmd.exe", ["/c", "mklink", "/J", inventoryPath, externalDir]);
      fileReplacedWithJunction = true;
    } catch {
      fileReplacedWithJunction = false;
    }
    const postRemoveSourceRes = await request.get(
      `${BACKEND}/api/analysis/${analysisId}/symbols/source?symbol_id=${encodeURIComponent(reserveNode.data.symbol_id)}`,
    );
    expect(postRemoveSourceRes.status()).toBe(200);
    const postRemoveSource = await postRemoveSourceRes.json();
    expect(postRemoveSource.status).toBe("unavailable");
    expect(postRemoveSource.code).toBeNull();
    if (fileReplacedWithJunction) {
      expect(postRemoveSource.reason).toContain("missing or no longer safe to read");
    }

    expect(errors).toEqual([]);
  } finally {
    if (junctionCreated) {
      await rm(junctionDir, { recursive: true, force: true }).catch(() => undefined);
    }
    await rm(outsideFile, { force: true }).catch(() => undefined);
    await rm(externalDir, { recursive: true, force: true }).catch(() => undefined);
    await project.cleanup();
  }
});
