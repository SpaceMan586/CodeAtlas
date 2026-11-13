/**
 * Synthetic Python projects for Phase 3.6C browser tests.
 *
 * Every project is written to a fresh OS temporary directory and is only ever
 * analyzed statically by the backend. Files that would run on import or on
 * installation (package initializers, a module body, setup.py) write a marker
 * file next to the project root, so tests can prove nothing was executed.
 */
import { createHash } from "node:crypto";
import { mkdtemp, mkdir, readFile, readdir, rm, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, relative } from "node:path";

export const BACKEND = "http://127.0.0.1:5000";

export interface SyntheticProject {
  dir: string;
  /** Marker files that would only exist if analyzed code had been executed. */
  markers: string[];
  cleanup: () => Promise<void>;
}

function marker(dir: string, name: string): string {
  return join(dir, `EXECUTED_${name}.marker`);
}

function markerCode(path: string): string {
  return `open(${JSON.stringify(path)}, "w").write("executed")\n`;
}

async function writeTree(root: string, files: Record<string, string>): Promise<void> {
  for (const [path, content] of Object.entries(files)) {
    const full = join(root, ...path.split("/"));
    await mkdir(join(full, ".."), { recursive: true });
    await writeFile(full, content, "utf8");
  }
}

/**
 * Rich feature fixture: package with absolute and relative imports, classes,
 * methods, a nested function, decorators, docstrings, annotations, defaults,
 * recursion, repeated call sites, duplicate names in separate files, an
 * unresolved call, a syntax-error file, and a very long function for bounded
 * source reads.
 */
export async function makeFeatureProject(packageName = "shop"): Promise<SyntheticProject> {
  const dir = await mkdtemp(join(tmpdir(), "codeatlas-e2e-"));
  const markers = [marker(dir, "package_init"), marker(dir, "module_body"), marker(dir, "setup_py")];
  const longBody = Array.from({ length: 620 }, (_, index) => `    value_${index} = ${index}\n`).join("");
  const wideLine = `    wide = "${"x".repeat(17_000)}"\n`;
  await writeTree(dir, {
    "setup.py": `${markerCode(markers[2])}from setuptools import setup\nsetup(name="synthetic")\n`,
    [`${packageName}/__init__.py`]: `"""Synthetic package; must never be initialized."""\n${markerCode(markers[0])}`,
    [`${packageName}/pricing.py`]: [
      "from __future__ import annotations",
      "",
      "TAX_RATE = 0.1",
      "",
      "",
      "def log_call(func):",
      '    """Identity decorator used as decorator evidence."""',
      "    return func",
      "",
      "",
      "@log_call",
      "def calculate_total(prices: list[float], discount: float = 0.0) -> float:",
      '    """Return the taxed total of prices minus a discount."""',
      "    subtotal = sum_prices(prices)",
      "    return apply_tax(subtotal) - discount",
      "",
      "",
      "def sum_prices(prices: list[float]) -> float:",
      "    return sum(prices)",
      "",
      "",
      "def apply_tax(amount: float) -> float:",
      "    return amount * (1 + TAX_RATE)",
      "",
      "",
      "def countdown(n: int) -> int:",
      '    """Recursive countdown."""',
      "    if n <= 0:",
      "        return 0",
      "    return countdown(n - 1)",
      "",
      "",
      "def helper() -> str:",
      '    """Pricing helper with a duplicated name."""',
      '    return "pricing"',
      "",
    ].join("\n"),
    [`${packageName}/inventory.py`]: [
      `${markerCode(markers[1]).trimEnd()}`,
      "",
      "",
      "def reserve(count: int) -> bool:",
      "    return count >= 0",
      "",
      "",
      "def helper() -> str:",
      '    """Inventory helper with a duplicated name."""',
      '    return "inventory"',
      "",
    ].join("\n"),
    [`${packageName}/orders.py`]: [
      "from .pricing import calculate_total, countdown",
      `from ${packageName}.inventory import reserve`,
      "",
      "",
      "class Order:",
      '    """A customer order."""',
      "",
      "    def __init__(self, items: list[float]) -> None:",
      "        self.items = items",
      "",
      "    def total(self) -> float:",
      '        """Total of the order."""',
      "        return calculate_total(self.items)",
      "",
      "    def audit(self) -> int:",
      "        def inner(value: int) -> int:",
      "            return countdown(value)",
      "",
      "        return inner(3)",
      "",
      "",
      "def process(items: list[float]) -> float:",
      "    order = Order(items)",
      "    first = calculate_total(items)",
      "    second = calculate_total(items)",
      "    reserve(len(items))",
      "    undefined_function(items)",
      "    return first + second + order.total()",
      "",
    ].join("\n"),
    "main.py": [
      `from ${packageName}.orders import process`,
      `from ${packageName}.pricing import helper`,
      "",
      "",
      "def main() -> float:",
      "    helper()",
      "    return process([1.0, 2.0])",
      "",
      "",
      "def long_function() -> int:",
      '    """A function longer than the Inspector 500-line source bound."""',
      longBody.trimEnd(),
      "    return 0",
      "",
      "",
      "def wide_function() -> str:",
      '    """A function with one line longer than the per-line source bound."""',
      wideLine.trimEnd(),
      "    return wide",
      "",
    ].join("\n"),
    "broken.py": "def broken(:\n    return 1\n",
  });
  return { dir, markers, cleanup: () => rm(dir, { recursive: true, force: true }) };
}

/**
 * Small replacement fixture whose symbol names do not overlap the feature
 * fixture, so stale data from the previous session is detectable.
 */
export async function makeReplacementProject(): Promise<SyntheticProject> {
  const dir = await mkdtemp(join(tmpdir(), "codeatlas-e2e-replacement-"));
  const markers = [marker(dir, "replacement_init")];
  await writeTree(dir, {
    "beta/__init__.py": markerCode(markers[0]),
    "beta/alpha.py": "def gamma_root() -> int:\n    return delta_leaf()\n\n\ndef delta_leaf() -> int:\n    return 1\n",
  });
  return { dir, markers, cleanup: () => rm(dir, { recursive: true, force: true }) };
}

/**
 * Deterministic scale fixture with `files` modules. Each module defines a
 * class with two methods and four functions, imports the previous module, and
 * makes local, cross-file, repeated, and unresolved calls.
 */
export async function makeScaleProject(files: number): Promise<SyntheticProject> {
  const dir = await mkdtemp(join(tmpdir(), `codeatlas-e2e-scale${files}-`));
  const tree: Record<string, string> = { "scale/__init__.py": '"""Scale fixture."""\n' };
  for (let i = 0; i < files; i++) {
    const name = `m${String(i).padStart(3, "0")}`;
    const previous = i > 0 ? `m${String(i - 1).padStart(3, "0")}` : null;
    tree[`scale/${name}.py`] = [
      previous ? `from .${previous} import entry as previous_entry, leaf as previous_leaf` : "",
      "",
      "",
      `class Service${i}:`,
      `    """Service ${i}."""`,
      "",
      "    def run(self, value: int) -> int:",
      "        return self.step(value)",
      "",
      "    def step(self, value: int) -> int:",
      "        return leaf(value)",
      "",
      "",
      "def leaf(value: int) -> int:",
      "    return value + 1",
      "",
      "",
      "def middle(value: int) -> int:",
      "    return leaf(leaf(value))",
      "",
      "",
      "def entry(value: int) -> int:",
      previous ? "    previous_entry(value)\n    previous_leaf(value)" : "    middle(value)",
      "    unknown_call(value)",
      "    return middle(value)",
      "",
      "",
      "def recurse(n: int) -> int:",
      "    return 0 if n <= 0 else recurse(n - 1)",
      "",
    ].join("\n");
  }
  await writeTree(dir, tree);
  return { dir, markers: [], cleanup: () => rm(dir, { recursive: true, force: true }) };
}

/** SHA-256, size, and mtime of every file below `root`, keyed by relative path. */
export async function snapshotTree(root: string): Promise<Record<string, string>> {
  const result: Record<string, string> = {};
  async function walk(current: string): Promise<void> {
    for (const entry of await readdir(current, { withFileTypes: true })) {
      const full = join(current, entry.name);
      if (entry.isDirectory()) await walk(full);
      else if (entry.isFile()) {
        const [content, info] = await Promise.all([readFile(full), stat(full)]);
        result[relative(root, full).split("\\").join("/")] =
          `${createHash("sha256").update(content).digest("hex")}:${info.size}:${info.mtimeMs}`;
      }
    }
  }
  await walk(root);
  return result;
}

export function median(values: number[]): number {
  const sorted = [...values].sort((a, b) => a - b);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
}
