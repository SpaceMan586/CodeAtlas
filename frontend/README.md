# CodeAtlas Frontend

The React workspace submits Python project paths to Flask, loads analysis metadata and graph JSON, displays summary metrics, and can delete the active process-local analysis session. Typed models, the analysis API service, and a session hook keep transport, data, and UI state separate. Cytoscape.js renders the retained `GraphData` in an interactive 2D view. Nodes and edges are converted to separate visualization data without mutating the API arrays.

## Start the applications

Start Flask from `backend/` in one terminal:

```powershell
.\.venv\Scripts\python.exe run.py
```

Start Vite from `frontend/` in another terminal:

```powershell
npm run dev
```

By default Flask listens on `127.0.0.1:5000` and Vite on `localhost:5173`.

## Backend URL configuration

`VITE_API_BASE_URL` is the backend origin. Copy `.env.example` to `.env.local` to override the local default. Vite uses this value as the `/api` proxy target in development; the React service keeps using relative `/api` requests, so the browser does not need cross-origin access.

For a production build, the Vite development proxy is not included. Set `VITE_API_BASE_URL` to the deployed backend origin at build time, or leave it empty and configure the hosting layer to route same-origin `/api` requests to Flask. A direct cross-origin production deployment requires an intentional server-side CORS policy; this project does not add Flask-CORS.

The `.env.example` file documents the local Flask origin; copy it to `.env.local` when you need to override the default. Frontend environment values are public build configuration and must not contain secrets.

## Analysis workflow

Enter a directory path that Flask can access (for example, `C:\\Projects\\sample-python`) and choose **Analyze project**. Flask analyzes synchronously; the UI shows an honest busy state without percentage progress. The analysis creation response supplies analysis information, and the frontend then retrieves the graph once. A new session replaces the active session only after both are available; it then attempts to delete the old session while preventing duplicate analysis/deletion actions. Failure to clean up the old session leaves the new analysis active and displays a warning. Delete the active session to clear it; if the backend rejects deletion, the active data stays visible.

The backend response types mirror its current contracts. Analysis metadata includes `project_display_path`, summary counts, diagnostics, status, and schema version, but not the absolute input path. Graph data uses node `id`/`data` and edge `id`/`source`/`target`/`key`/`data`, with `metadata` and `statistics`. Edge arrays and keys are retained; the client does not collapse relationships by endpoint pair.

The request client reads nested backend errors from `error.message`, handles non-JSON HTTP failures and network errors, and validates analysis and graph response shapes before state updates. The submit request allows up to two minutes for synchronous backend analysis. Sessions remain in Flask memory and are lost when Flask restarts; the frontend does not persist them across page reloads.

On wide screens, Function Search, Function Trace, and Function Inspector share a bounded scrollable side panel beside the graph. On narrower screens the panels stack above the graph so graph controls remain available below them. Replacing or deleting the active session resets selection and panel state; a failed replacement retains the current analysis.

## 2D graph visualization

`GraphVisualization` is loaded as a separate frontend chunk and receives the graph from the active React analysis session; it does not fetch the API. File nodes use their relative path and a rounded rectangle. Symbol nodes use their actual symbol ID and name, with class, function, and method shapes/colors selected only when `symbol_kind` supplies those values. Containment edges are gray dashed, call edges are green solid, and import edges are blue dotted. Every relationship has a target arrow so the backend's source-to-target direction is visible.

Each backend edge keeps its unique serialized `id` for its Cytoscape identity. The source, target, multigraph key, relationship type, and original metadata are retained in the visualization element data. Duplicate IDs, unsupported node/relationship types, inconsistent edge types, statistics that disagree with the returned arrays, or missing edge endpoints produce a visible rendering error; edges are never collapsed by source/target pair. Cytoscape's bezier curves separate parallel edges.

The built-in breadth-first layout is the initial directed hierarchy; force-directed (CoSE) and circular layouts can be selected and reapplied. Zoom in/out, fit, center, pointer pan, wheel zoom, and node selection are available. Selecting a node preserves its actual graph ID through a typed callback and shows that ID below the canvas. The compact legend reflects the same shapes and edge styles. `GraphVisualizationBoundary` isolates initialization or chunk-load failures from the rest of the workspace. The Cytoscape instance and event listeners are removed on unmount, graph changes update the existing instance, and a `ResizeObserver` keeps its viewport sized with the container.

Large projects can still take time to lay out and render; the graph is not sampled or clustered. 3D rendering remains deferred. Cytoscape lifecycle behavior is covered with a controlled instance mock in Vitest, and real-browser canvas rendering, parallel bezier curves, and layouts are verified by Playwright (`npm run test:e2e`).

## Function Search

Function Search operates only on the active `GraphData` and makes no additional API request. It indexes symbol nodes whose actual `symbol_kind` is `function` or `method`; other symbols and file nodes are excluded. TypeScript, incomplete, or otherwise unsupported metadata is handled conservatively: missing names/qualified names cannot produce a result, and absent optional metadata is omitted from result details.

The name input performs debounced, case-insensitive partial matching against `name` and `qualified_name`. Optional filters select function/method kind, an available language, and a partial relative file path. Results show the symbol name, qualified name, kind, path, language, and one-based source line when supplied by `source_location`. Result identity and React keys use the real graph node ID, so duplicate names remain distinct.

Selecting a result passes its node ID to the graph workspace. The existing Cytoscape instance selects and centers that exact node without rerunning layout or rebuilding the graph. Direct graph selection updates the shared selection state and marks the corresponding visible result when it matches the current search. Clearing a query or filters clears the result list but leaves graph selection alone. Selection and query state are reset when the active analysis changes. Results are navigable with the keyboard, and selection is indicated by both styling and text.

## Function Trace

Function Trace uses the selected function or method as its root and operates on the active in-memory `GraphData`; it does not call a new API or read project source. It supports outgoing caller-to-callee traversal, incoming reverse traversal, or both, with a selected maximum depth from 1 through 10 (default 3). The root is depth 0 and each hop increases depth by one.

The trace engine builds sorted incoming/outgoing CALLS adjacency maps once per graph and uses breadth-first traversal with visited-node tracking. CONTAINS and IMPORTS are excluded. Function and method symbols can be expanded; a class appears only as a constructor target when edge metadata identifies `CLASS_CONSTRUCTOR` or `IMPORTED_CLASS_CONSTRUCTOR`, and is terminal. Each relationship retains its backend edge ID, edge key, original source/target direction, metadata, call-site reference, source location, and provenance. Parallel relationships remain distinct. Reached nodes are grouped by their shortest discovered depth; cycle-closing edges encountered within the depth bound are kept once by edge ID. The trace represents the reachable call subgraph and does not enumerate every call path.

Results show nodes and individual caller/callee relationships by traversal depth. A depth-boundary message appears if an additional eligible node is reachable beyond the selected limit. Selecting a result node focuses the existing Cytoscape graph and makes it the next trace root; the user explicitly runs the next trace. Changing the active analysis or root clears the prior trace. This is static analysis of supported resolved calls, not runtime tracing; unresolved/dynamic calls are absent, and constructor targets are terminal. The trace algorithm, component, and real-browser interactions are covered by Vitest and Playwright regression suites.

## Function Inspector

The Inspector shares the active graph selection used by direct Cytoscape clicks, Function Search, and Function Trace. The graph node ID remains the shared navigation identity; because graph symbol IDs use the `symbol:` namespace prefix, the Inspector maps the selected node's exact `symbol_id` metadata to the unprefixed SymbolIR ID expected by the lookup endpoint. It accepts only selected symbol nodes whose actual symbol kind is function, method, or class; file nodes and other kinds show an explanatory state. Duplicate names do not collide.

The frontend calls `GET /api/analysis/{analysis_id}/symbols/lookup?symbol_id=...` for the stored schema-v3 `SymbolIR`, parent symbol, and `SourceFileIR` metadata. It displays available identity, relative path, location, ordered parameters and their kinds/defaults/annotations, return annotation, decorators, documentation, and language metadata. Missing optional fields remain absent or are labeled unavailable; the Inspector does not infer types or parse source.

Source is fetched separately from `GET /api/analysis/{analysis_id}/symbols/source?symbol_id=...`. Flask derives the path and inclusive source line range from validated IR, resolves it under the privately retained analysis root, rejects traversal and symbolic links/junctions, checks containment and regular-file status, then reads text without importing or executing it. The response is bounded to 500 lines, 16,384 characters per line, and 256,000 characters total; truncation is explicit and original line numbers are shown. The backend compares current file size with the scanned size, but same-size edits cannot be detected. Source therefore represents current contents and snapshot freshness is not guaranteed. Missing/unreadable source is shown separately and does not suppress symbol metadata. Code uses a safe monospaced text viewer without a syntax-highlighting dependency.

Direct CALLS edge and distinct caller/callee counts are calculated from the active graph, preserving parallel edges in relationship counts. Requests are aborted when selection changes and keyed by analysis and symbol IDs to prevent stale responses from rendering. Vitest and Playwright verify metadata/source mapping, bounded source display, and stale-response protection.

## Static checks

```powershell
npm run typecheck
npm run build
```

## Frontend tests

The project uses Vitest, React Testing Library, User Event, jsdom, and the V8
coverage provider. Development dependencies are declared in `package.json` and
locked in `package-lock.json`.

```powershell
npm run test
npm run test:coverage
npm run typecheck
npm run build
npm run test:e2e
```

Unit tests use synthetic graph/analysis fixtures and mocked fetch responses; they
make no network requests. Cytoscape lifecycle unit tests use a controlled instance
mock to verify data registration, selection callbacks, focus, layout, resize,
and cleanup. Real-browser Playwright verification (`npm run test:e2e`) starts
both Flask and Vite and verifies end-to-end workflows, Cytoscape canvas
rendering, responsive viewports, static-analysis security guarantees, and
3/30/100-file performance benchmarks in Microsoft Edge.
