# Foundation Testing

## Automated checks

Run the backend tests from `backend/`:

```powershell
.\.venv\Scripts\python.exe -m pytest --cov=app --cov-report=term-missing
```

Run the frontend checks from `frontend/`:

```powershell
npm run test
npm run test:coverage
npm run typecheck
npm run build
```

## Backend Phase 3.6A verification

From `backend/`, install only the declared development requirements into the
backend virtual environment and run:

```powershell
python -m pip install -r requirements-dev.txt
python -m pip check
python -m pytest --cov=app --cov-report=term-missing
python -m compileall -q app tests
```

The Phase 3.6A final run on Windows with Python 3.14.2, Flask 3.1.3, NetworkX
3.7, pytest 9.2.0, and pytest-cov 7.1.0 was **574 passed, 0 failed, 3
skipped**, with **87% statement coverage** (7,442 statements; 976 missed).
The baseline before this phase's test corrections and additions was 554 passed,
14 failed, 2 skipped, and 79% coverage. The original failures were outdated
schema-v2 and pre-dependency-integration assertions, plus the resolver test
comparing its pre-edge IR to the final pipeline IR.

The suite uses pytest temporary directories and synthetic Python source. It
never imports or executes those source files. The expanded tests exercise
scanner/parser and extraction behavior, schema-v3 IR, local and cross-file
resolution, repeated call and import dependencies, directed multigraph
construction, graph query semantics, API lifecycle/query routes, Inspector
metadata and source retrieval, source-size change reporting, and a package
initializer execution marker. Analysis/source retrieval checks confirm that
source remains untouched by the analyzer and that a symlink substituted after
analysis is rejected by Inspector.

Coverage is not complete: dependency-edge builder 80%, import dependency
builder 81%, GraphBuilder 60%, GraphQueryEngine 80%, API routes 82%, analysis
service 68%, graph query service 67%, and Inspector service 65%. Remaining
uncovered paths primarily include malformed internal inputs, injected OS and
service failures, special invalid graph metadata/provenance combinations,
and bounded-read failure conditions. The Scanner, Parser, IR, extraction,
identity, and resolver suites already contain broad targeted coverage, but
platform permission and junction behavior remain OS-dependent. Three tests
skip symlink operations on Windows when symlink privileges are unavailable
(two scanner/parser cases and the Inspector path-substitution case). Inspector can
detect size changes; same-size source edits are not detectable from stored
metadata. SQLite is not integrated into current backend session storage.

The backend suite covers Flask configuration and responses, the scanner,
Python parser and extractors, qualified names and stable IDs, the Python-to-IR
adapter, IR models and validation, the Symbol Index, the standalone local
resolver, and the integrated `PythonProjectAnalyzer`. Pipeline integration
tests use real temporary projects for multi-file analysis, duplicates, empty
projects, partial parse failures, deterministic logical results, and read-only
behavior; binding-aware resolution cases cover local matches, shadowing,
nested functions, methods, ambiguity, and same-file boundaries. Controlled
failure injection checks the fatal stage boundaries.

## Frontend Phase 3.6B verification

The frontend already had Vitest 5.1.0, React Testing Library, User Event,
jsdom, and `@vitest/coverage-v8`; no dependencies or package scripts needed to
be added. From `frontend/`, run `npm run test`, `npm run test:coverage`,
`npm run typecheck`, and `npm run build`.

The final suite passes **60 tests in 13 test files**. V8 coverage is 88.86%
statements, 79.48% branches, 90.76% functions, and 91.22% lines. Coverage by
key area: analysis session hook 95.55%, graph conversion 94.11%, trace engine
93.10%, Function Search 93.84%, Function Trace component 89.53%, Inspector
91.01%, analysis API 82.52%, Cytoscape component 77.67%, and dashboard/workspace
82.50%.

Tests use synthetic graph and analysis fixtures plus mocked fetch responses;
they make no network requests. Coverage includes API shapes and parallel edge
preservation, session replacement/deletion and stale state, graph conversion
validation, search filters and duplicate names, BFS trace directions/cycles/
depth/parallel edges, Inspector metadata/source handling and stale detail and
source responses, shared workspace selection, and Cytoscape lifecycle through
a controlled instance mock. That mock checks initialization, element
registration, selection, focus, layout, resize, and destruction, while real
browser rendering, responsive viewports, end-to-end security, and performance
are verified in Phase 3.6C below.

Remaining uncovered paths include request timeout cleanup edge cases, invalid
Inspector field combinations, unusual Cytoscape initialization/layout errors,
error-boundary fallback, trace evidence fallback formatting, and some rare
workspace error/loading branches.

## End-to-End Browser Verification (Phase 3.6C)

Run the real-browser Playwright suite from `frontend/`:

```powershell
npm run test:e2e
```

Playwright (`@playwright/test` 1.65.0) starts the Flask backend
(`backend/.venv/Scripts/python.exe run.py` on `127.0.0.1:5000`) and Vite dev
server (`127.0.0.1:5173`) automatically and drives the installed Microsoft Edge
browser (`channel: "msedge"`). All **4 end-to-end spec files (4 tests) pass**:

1. **`e2e/workflow.spec.ts` — Complete 16-step real-browser workflow:**
   - Generates a synthetic Python project (`makeFeatureProject`) with a package,
     multiple modules, classes, methods, nested functions, absolute/relative
     imports, cross-file calls, recursive calls, repeated parallel call sites,
     duplicate function names across files, parameters/annotations/defaults,
     decorators/docstrings, an unresolved call, a syntax-error file
     (`broken.py`), and functions exceeding the 500-line and 16,384-character
     source bounds.
   - Submits the project path in the UI, verifies a single `POST /api/analysis`
     and `GET /api/analysis/{id}/graph` lifecycle, and confirms real Cytoscape
     canvas initialization, non-transparent pixel rendering, node/edge parity,
     and distinct bezier midpoints for parallel `CALLS` edges.
   - Verifies pointer node selection, zoom/pan/fit/center and layout switching
     (`breadthfirst`, `cose`, `circle`), case-insensitive Function Search with
     distinct duplicate names and graph centering, outgoing/incoming/both
     Function Trace with parallel edge keys and depth boundary reporting, trace
     node navigation (`Focus & use as root`) with single-node selection
     exclusivity, Function Inspector metadata and bounded source display,
     delayed-response stale-selection protection, failed replacement retention,
     session replacement cleanup, and session deletion.
2. **`e2e/responsive.spec.ts` — Responsive viewports (`1440×900`, `1366×768`, `768×1024`, `390×844`):**
   - Verifies zero horizontal document overflow (`scrollWidth <= clientWidth`),
     automatic `ResizeObserver` synchronization between the graph container and
     Cytoscape canvas, and full operability of Function Search, Function Trace,
     Function Inspector, and graph toolbar controls across all four viewports.
   - Fixes discovered during Phase 3.6C:
     - `GraphVisualization.tsx`: Programmatic `focusNodeId` selection now
       unselects previously selected Cytoscape nodes before selecting the new
       target (`Cytoscape`'s `selectionType: "single"` only applies to user
       pointer events, not programmatic `.select()` calls).
     - `OverviewPage.tsx`: Added `min-w-0` to the left panel wrapper in the
       workspace CSS grid so wide `<pre>` blocks inside `FunctionInspector` scroll
       internally instead of causing horizontal page overflow on viewports narrower
       than the `xl` (`1280px`) breakpoint.
     - `index.html`: Added an inline SVG data-URI favicon to prevent a browser
       `404 /favicon.ico` console error.
3. **`e2e/security.spec.ts` — Static-analysis & path-security verification:**
   - Confirms package `__init__.py`, module top-level code, and `setup.py`
     execution markers are never created, no `__pycache__` directory is written,
     and file SHA-256 hashes, byte sizes, and modification times remain
     unchanged.
   - Confirms a Windows directory junction (`mklink /J`) pointing outside the
     project root is skipped during scanning and rejected if substituted for a
     source file after analysis. (Standard file symbolic links on Windows still
     require elevated privileges or Developer Mode; the 3 backend pytest symlink
     skips remain documented for unprivileged Windows environments.)
   - Confirms non-directory paths, missing paths, and traversal/absolute
     `symbol_id` payloads are rejected without leaking absolute filesystem paths.
   - Confirms bounded source reads enforce the 500-line and 16,384-character
     per-line caps and report `truncated: true`.
   - Verifies source freshness behavior honestly: size-changing edits after
     analysis set `changed_after_analysis: true` (`freshness: "changed"`) and
     display a warning in the UI, whereas same-size edits cannot be detected from
     stored byte-size metadata and remain `freshness: "unverified"`.
4. **`e2e/performance.spec.ts` — End-to-end browser & pipeline performance (`~3`, `~30`, `~100` Python files):**
   - Uses `makeScaleProject(3 | 30 | 100)` (each module defines 1 class with 2
     methods and 4 functions, imports the previous module, and makes local,
     cross-file, repeated, and unresolved calls, plus `scale/__init__.py`).
   - Runs 3 full browser iterations per fixture size and records the median
     durations:

| Scale target | Scanned files | Graph nodes | Graph edges (`CALLS` / `IMPORTS`) | Backend analysis median (ms) | Graph fetch median (ms) | Graph conversion median (ms) | Cytoscape init + layout median (ms) | Function Search UI median* (ms) | Trace engine median (ms) | Trace UI median (ms) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| ~3 files | 4 | 25 | 48 (23 / 4) | 57.17 | 23.61 | 0.16 | 70.73 | 223.65 | 0.14 | 70.01 |
| ~30 files | 31 | 241 | 507 (239 / 58) | 220.96 | 56.09 | 0.48 | 183.40 | 221.25 | 0.59 | 82.09 |
| ~100 files | 101 | 801 | 1,697 (799 / 198) | 696.41 | 175.27 | 0.97 | 479.88 | 230.03 | 1.14 | 76.28 |

*\*Function Search UI duration includes the 180 ms input debounce before results render.*

## Final Regression and Python MVP Release Readiness (Phase 3.6D)

Phase 3.6D re-ran all backend, frontend, static-check, production-build, and live real-browser Playwright suites together to confirm release readiness of the CodeAtlas Python MVP:

- **Backend (`pytest --cov=app --cov-report=term-missing`, `pip check`, `compileall -q app tests`):**
  - **574 passed, 0 failed, 3 skipped** in 6.68s on Python 3.14.2 with Flask 3.1.3 and NetworkX 3.7.
  - **87% statement coverage** (`7,442` statements, `976` missed) — identical to the Phase 3.6A baseline.
  - `pip check` and `compileall -q app tests` passed with zero errors.
- **Frontend (`npm run test:coverage`, `npm run typecheck`, `npm run build`, `npm ls --depth=0`):**
  - **60 passed, 0 failed, 0 skipped across 13 test files** in 3.68s.
  - V8 coverage: **88.86% statements, 79.48% branches, 90.76% functions, 91.22% lines** — identical to the Phase 3.6B/3.6C baseline.
  - `npm run typecheck` (`tsc -b`), `npm run build` (`tsc -b && vite build`), and `npm ls --depth=0` passed cleanly.
- **Browser E2E (`npm run test:e2e` in real Microsoft Edge against live Flask + Vite):**
  - **4 passed, 0 failed, 0 skipped across 4 E2E spec files** (`workflow.spec.ts`, `responsive.spec.ts`, `security.spec.ts`, `performance.spec.ts`) with zero browser console or uncaught page errors.
  - Re-verified all four Phase 3.6C fixes: Cytoscape programmatic single-node selection, `min-w-0` responsive horizontal overflow prevention across `1440×900` / `1366×768` / `768×1024` / `390×844`, inline SVG data-URI favicon (`0` 404 requests), and Vitest (`src/**/*.{test,spec}.{ts,tsx}`) vs. Playwright (`e2e/*.spec.ts`) separation.

### Phase 3.6C vs. Phase 3.6D End-to-End Performance Comparison

| Scale target (nodes / edges) | Metric | Phase 3.6C median (ms) | Phase 3.6D median (ms) |
| --- | --- | ---: | ---: |
| ~3 files (25 / 48) | Backend analysis | 57.17 | 49.42 |
| ~3 files (25 / 48) | Graph fetch | 23.61 | 21.66 |
| ~3 files (25 / 48) | Graph conversion | 0.16 | 0.10 |
| ~3 files (25 / 48) | Cytoscape init + layout | 70.73 | 52.56 |
| ~3 files (25 / 48) | Function Search UI (incl. 180 ms debounce) | 223.65 | 228.55 |
| ~3 files (25 / 48) | Trace engine / Trace UI | 0.14 / 70.01 | 0.27 / 58.82 |
| ~30 files (241 / 507) | Backend analysis | 220.96 | 231.87 |
| ~30 files (241 / 507) | Graph fetch | 56.09 | 53.27 |
| ~30 files (241 / 507) | Graph conversion | 0.48 | 0.56 |
| ~30 files (241 / 507) | Cytoscape init + layout | 183.40 | 171.12 |
| ~30 files (241 / 507) | Function Search UI (incl. 180 ms debounce) | 221.25 | 216.06 |
| ~30 files (241 / 507) | Trace engine / Trace UI | 0.59 / 82.09 | 0.84 / 83.84 |
| ~100 files (801 / 1,697) | Backend analysis | 696.41 | 640.38 |
| ~100 files (801 / 1,697) | Graph fetch | 175.27 | 160.47 |
| ~100 files (801 / 1,697) | Graph conversion | 0.97 | 0.86 |
| ~100 files (801 / 1,697) | Cytoscape init + layout | 479.88 | 424.55 |
| ~100 files (801 / 1,697) | Function Search UI (incl. 180 ms debounce) | 230.03 | 213.64 |
| ~100 files (801 / 1,697) | Trace engine / Trace UI | 1.14 / 76.28 | 0.71 / 71.52 |

### Known Limitations of the Python MVP

1. **Static analysis only:** Dynamic dispatch, runtime reflection, monkey-patching, inheritance/`super()` method resolution, arbitrary re-export chains, and runtime `sys.path` mutations are not inferred.
2. **Process-local in-memory sessions:** Analysis sessions live in Flask process memory and are not persisted across backend restarts or browser reloads; SQLite persistence is not integrated into session storage.
3. **Source snapshot freshness:** Inspector source reads compare current file byte size against scanned metadata (`freshness: "changed"` when size differs), but same-size source edits after analysis cannot be detected (`freshness: "unverified"`) because source content hashes/snapshots are not stored.
4. **Bounded source snippets:** Inspector source responses are capped at 500 lines, 16,384 characters per line, and 256,000 characters total (`truncated: true` when exceeded).
5. **Windows symlink privilege skips:** Three backend pytest symlink tests skip on unprivileged Windows accounts without Developer Mode (`os.symlink` privilege); Windows directory junctions (`mklink /J`) are tested without elevation in `e2e/security.spec.ts`.
6. **Stable symbol IDs (`py:v1:`):** Deterministic within a project snapshot, but not globally unique across separate projects, and occurrence indexes for duplicate same-scope declarations can shift if identical declarations are inserted or reordered.
7. **2D graph rendering scale:** Large graphs are rendered in full without clustering, sampling, or virtualization, and 3D visualization remains planned.
8. **Language support:** Only Python (`.py` and `.pyi`) is implemented in the MVP; additional languages, language detection, and parser registry routing remain planned.
9. **Local single-user deployment model:** The API has no authentication, rate limiting, background job queue, or built-in CORS policy for cross-origin production hosting.

### Python MVP Release-Readiness Checklist

- **Installation requirements:**
  - Windows with Python `3.14.x` (`backend/.venv`) and Node.js `24.x` / npm `11.x`.
  - Backend dependencies installed via `backend/.venv/Scripts/python.exe -m pip install -r requirements-dev.txt`.
  - Frontend dependencies installed via `npm install` in `frontend/`.
- **Backend startup:**
  - From `backend/`, run `.\.venv\Scripts\python.exe run.py` (listens on `http://127.0.0.1:5000`; verify `GET /api/health` returns `status: "ok"`).
- **Frontend startup:**
  - From `frontend/`, run `npm run dev` (listens on `http://localhost:5173` and proxies `/api` to `http://127.0.0.1:5000`).
- **Running automated unit/integration tests:**
  - Backend (`backend/`): `.\.venv\Scripts\python.exe -m pytest --cov=app --cov-report=term-missing`, `.\.venv\Scripts\python.exe -m pip check`, `.\.venv\Scripts\python.exe -m compileall -q app tests`.
  - Frontend (`frontend/`): `npm run test:coverage`, `npm run typecheck`, `npm run build`.
- **Running Playwright real-browser tests:**
  - From `frontend/`, run `npm run test:e2e` (automatically starts and stops Flask on `127.0.0.1:5000` and Vite on `127.0.0.1:5173`, running all 4 E2E specs in Microsoft Edge).
- **Starting a project analysis:**
  - Enter a local directory path accessible to Flask in **Project directory path** and click **Analyze project**; verify summary counts, status badge, and diagnostics load from a single `POST /api/analysis` + `GET /api/analysis/{id}/graph` sequence.
- **Graph exploration:**
  - Interact with the 2D Cytoscape canvas using pointer click selection, zoom in/out, fit, center, clear selection, and layout switching (`Breadth-first hierarchy`, `Force-directed (CoSE)`, `Circular`).
- **Function Search:**
  - Search by function/method name or qualified name with optional kind, language, and file-path filters; click a result to center and single-select that exact symbol node in the graph.
- **Function Trace:**
  - With a function or method selected, run `Outgoing callees`, `Incoming callers`, or `Both directions` traces at depths `1–10`; inspect grouped depth levels, preserved parallel `CALLS` edge keys, and `Focus & use as root` navigation.
- **Function Inspector:**
  - Select any function, method, or class to view schema-v3 symbol metadata (parameters, annotations, decorators, docstring, parent, file, and direct `CALLS` counts) and bounded read-only source lines with honest freshness status.
- **Session replacement and deletion:**
  - Submit a new valid project path to replace the active session (automatically cleaning up the prior session), or click **Delete session** to remove the active session and reset workspace panels.

## Python pipeline benchmark

The Phase 1.6D benchmark was run on Windows (`win32`) with the project backend
virtual environment's Python 3.14.2. Each temporary synthetic project was
generated once, then analyzed five times in the same process using a new
`PythonProjectAnalyzer` per run. The table reports the median of the actual
`ProcessingStatistics` elapsed and per-stage measurements. File counts,
symbols, and import aliases are from the returned analysis summaries. The
fixtures contain regular Python syntax with module functions, one class and
method per file, and four import aliases per file.

| Fixture | Files | Symbols | Import aliases | Median total (s) |
| --- | ---: | ---: | ---: | ---: |
| Small: 3 files × 5 functions | 3 | 21 | 12 | 0.01343 |
| Medium: 30 files × 20 functions | 30 | 660 | 120 | 0.17394 |
| Large: 100 files × 30 functions | 100 | 3,200 | 400 | 1.27377 |

Median per-stage durations, in seconds:

| Stage | Small | Medium | Large |
| --- | ---: | ---: | ---: |
| Scan | 0.00156 | 0.00171 | 0.00597 |
| Parse | 0.00549 | 0.05216 | 0.34642 |
| Function extraction | 0.00087 | 0.01964 | 0.14098 |
| Class extraction | 0.00033 | 0.00922 | 0.05880 |
| Import extraction | 0.00042 | 0.00961 | 0.06123 |
| Qualified-name generation | 0.00050 | 0.01082 | 0.08813 |
| Stable-ID generation | 0.00077 | 0.01757 | 0.14871 |
| IR mapping | 0.00243 | 0.05008 | 0.40429 |
| IR validation | 0.00001 | 0.00010 | 0.00068 |
| Symbol Index | 0.00009 | 0.00151 | 0.01146 |

The totals include result and summary assembly around the timed stages, so
per-stage medians need not sum exactly to the total median. These measurements
are local synthetic-fixture baselines, not production-scale performance
claims; they do not measure dependency analysis, graph construction, or I/O
patterns from a representative external repository.

## Python binding and local resolution pipeline benchmark

Phase 2.3B was measured on Windows using the backend virtual environment's
Python 3.14.2. The earlier Phase 1.6D benchmark documented its synthetic
fixture shape and five-run methodology but did not retain an executable
benchmark script. This rerun used the same 3/30/100-file fixture sizes and
function/import pattern, with one local call per file; each temporary project
was analyzed five times in one process with a fresh analyzer per run. Values
are actual medians from `ProcessingStatistics`, in seconds.

| Fixture | Files | Symbols | Import aliases | Call sites | Bindings | Resolved | Unresolved | Ambiguous | Median total (s) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Small | 3 | 24 | 12 | 3 | 57 | 3 | 0 | 0 | 0.00992 |
| Medium | 30 | 690 | 120 | 30 | 1,470 | 30 | 0 | 0 | 0.17635 |
| Large | 100 | 3,300 | 400 | 100 | 6,900 | 100 | 0 | 0 | 1.62896 |

Median per-stage durations (seconds):

| Stage | Small | Medium | Large |
| --- | ---: | ---: | ---: |
| Scan | 0.00085 | 0.00112 | 0.00237 |
| Parse | 0.00265 | 0.03150 | 0.25181 |
| Function extraction | 0.00041 | 0.00958 | 0.08875 |
| Class extraction | 0.00020 | 0.00486 | 0.03832 |
| Import extraction | 0.00022 | 0.00511 | 0.03868 |
| Call extraction | 0.00044 | 0.00790 | 0.06439 |
| Binding extraction | 0.00188 | 0.04723 | 0.41177 |
| Qualified-name generation | 0.00030 | 0.00679 | 0.06606 |
| Stable-ID generation | 0.00052 | 0.01193 | 0.12099 |
| IR mapping | 0.00161 | 0.03383 | 0.36127 |
| IR validation | 0.00001 | 0.00008 | 0.00057 |
| Symbol Index | 0.00006 | 0.00072 | 0.00370 |
| Local call resolution | 0.00073 | 0.01424 | 0.08796 |

The old and new timings are comparable in fixture sizes and methodology, but
the regenerated fixtures include one additional function and a call per file,
so this is not a perfectly controlled before/after comparison. The 100-file
median is 1.62896 s versus the prior 1.27377 s baseline. Binding extraction
and resolution add measured work; these synthetic fixtures are not
representative production-scale performance claims. Per-stage medians need not
sum to the total median because summary/result assembly is included only in
the total.

## Manual browser verification

Automated browser-level verification is implemented in `frontend/e2e/` (`npm run test:e2e`). For manual visual inspection in a browser, start the backend and Vite in separate
terminals:

1. In `backend/`, run
   `.\.venv\Scripts\python.exe -m flask --app app:create_app run --host 127.0.0.1 --port 5000 --no-debugger --no-reload`.
2. In `frontend/`, run `npm run dev -- --host 127.0.0.1 --port 5173`.
3. Open `http://127.0.0.1:5173` and confirm the Overview shell and **Backend
   Connected** status with the CodeAtlas name and version.
4. Stop the Flask process, select **Retry connection**, and confirm the page
   shows **Backend Disconnected** with a safe message.
5. Restart Flask, select **Retry connection**, and confirm the connected state
   returns.
6. Repeat at a desktop viewport (1280 × 800) and a narrow mobile viewport
   (390 × 844). Confirm there is no horizontal overflow and check the browser
   console for errors.

The frontend unit suite and API-through-Vite integration check cover service
and proxy behavior, but they do not replace this visual browser check.

## Cross-file resolution pipeline benchmark

Phase 2.4C was measured on Windows with the backend virtual environment's
Python 3.14.2. Each generated temporary project was analyzed five times in the
same process with a fresh `PythonProjectAnalyzer` per run. The fixtures contain
3, 30, or 100 modules. Each module imports four aliased functions from the
next module in a deterministic cycle, imports one external package, defines
four target functions plus local/run functions and filler declarations, and
has one local call, four cross-file calls, and one unresolved call. The table
reports actual record counts from one result and medians from the five runs.

| Fixture | Files | Symbols | Import records | Call sites | Bindings | Resolved module imports | Unresolved imports | Local resolved calls | Cross-file resolved calls | Effective resolved | Effective unresolved | Effective ambiguous | Median total (s) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Small | 3 | 18 | 15 | 18 | 33 | 12 | 3 | 3 | 12 | 15 | 3 | 0 | 0.013981 |
| Medium | 30 | 600 | 150 | 180 | 750 | 120 | 30 | 30 | 120 | 150 | 30 | 0 | 0.227603 |
| Large | 100 | 3,000 | 500 | 600 | 3,500 | 400 | 100 | 100 | 400 | 500 | 100 | 0 | 1.669463 |

Median per-stage durations, in seconds:

| Stage | Small | Medium | Large |
| --- | ---: | ---: | ---: |
| Scan | 0.001004 | 0.001448 | 0.004711 |
| Parse | 0.003085 | 0.044229 | 0.264288 |
| Function extraction | 0.000322 | 0.008439 | 0.063954 |
| Class extraction | 0.000154 | 0.003342 | 0.024340 |
| Import extraction | 0.000235 | 0.004182 | 0.028526 |
| Call extraction | 0.000735 | 0.012119 | 0.079978 |
| Binding extraction | 0.001518 | 0.037951 | 0.303402 |
| Qualified-name generation | 0.000251 | 0.007434 | 0.063923 |
| Stable-ID generation | 0.000472 | 0.013145 | 0.120581 |
| IR mapping | 0.001898 | 0.037263 | 0.358009 |
| IR validation | 0.000008 | 0.000095 | 0.000944 |
| Symbol Index | 0.000068 | 0.001244 | 0.009375 |
| Local call resolution | 0.001682 | 0.023785 | 0.190601 |
| Module resolution | 0.000815 | 0.007494 | 0.045231 |
| Cross-file call resolution | 0.000788 | 0.007673 | 0.049968 |
| Final result aggregation | 0.000536 | 0.005673 | 0.031704 |

This benchmark expands the call workload to six calls per module and includes
cross-file import resolution, so the previous Phase 2.3B measurements are not
a controlled before/after comparison. Per-stage medians need not sum exactly
to total medians because total time includes orchestration and result assembly.
These local synthetic measurements are not production-scale performance
claims.
