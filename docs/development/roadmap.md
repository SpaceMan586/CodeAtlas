# Development Roadmap

This roadmap describes the planned development sequence. Status labels distinguish completed work from planned functionality.

## Phase 0: Project Foundation — Complete

Establish the repository organization, project documentation, and development environment foundations.

## Phase 1: Python Parser — Complete

### Phase 1.1: Project Scanner & File Discovery — Complete

Discover Python source and stub files with deterministic, safe project-relative metadata.

### Phase 1.2: Python AST Parser — Complete

Read scanner-discovered `.py` and `.pyi` files, parse them with the built-in AST, and return typed parse results and diagnostics without executing project code.

### Phase 1.3: Python Function & Method Extraction — Complete

Extract ordered function, method, and nested-function metadata from successful Python parse results, including lexical scope paths, signatures, decorators, locations, and docstrings. The function extractor uses classes for context; class declaration extraction is covered by Phase 1.4A and stable identities by Phase 1.5B. These results are mapped into Unified IR in Phase 1.6B.

### Phase 1.4A: Python Class Extraction — Complete

Extract ordered class declaration metadata, including parent and lexical scopes, base expressions, class keywords, decorators, docstrings, and source positions. Inheritance resolution remains planned; Unified IR mapping is covered by Phase 1.6B.

### Phase 1.4B: Python Import Extraction — Complete

Extract ordered import-alias records with binding semantics, relative levels, wildcard and future-import metadata, source locations, and lexical scopes. Module and target resolution remain planned.

### Phase 1.5A: Python Qualified Name Generation — Complete

Combine function and class extraction records from one file, normalize full lexical scope paths into dot-separated names, preserve relative paths and duplicates, and order output by source position.

### Phase 1.5B: Python Stable Symbol ID Generation — Complete

Assign deterministic `py:v1:<sha256-hex>` IDs using compact canonical JSON of the schema version, language, normalized relative path, declaration type, qualified name, and per-group occurrence index. Preserve declaration order and metadata, validate project-relative paths, and detect duplicate generated IDs. Duplicate occurrence indexes can shift when identical declarations are inserted or reordered; these IDs are not persistent identity across arbitrary refactoring and have no cross-project namespace.

### Phase 1.6A: Unified Intermediate Representation Schema & Models — Complete

Define the version 1 language-independent data contract and typed models for project snapshots, source files, symbols, parameters, source locations, imports, dependency edges, and diagnostics. Add JSON-safe metadata, path/reference validation, and serialization. This phase does not map parser output into the IR or build a Symbol Index.

### Phase 1.6B: Python-to-IR Adapter — Complete

Map scanner, parser, extractor, qualified-name, and symbol-identity records into the completed IR models without changing IDs. Preserve parse failures and diagnostics, validate one-to-one file and declaration mappings, and keep dependency edges empty. Import resolution and dependency analysis remain separate tasks.

### Phase 1.6C: Unified Symbol Index — Complete

Build an immutable in-memory index from validated `ProjectIR` snapshots. Support exact and explicit case-insensitive lookups by ID, name, qualified name, file, kind, language, and stored parent ID; preserve duplicates and project ordering; include file records without symbols; and provide read-only index statistics. Import indexing, resolution, persistence, and dependency inference remain out of scope.

### Phase 1.6D: Full Python Pipeline Integration & Validation — Complete

Connect scanning, per-file Python parsing and extraction, qualified-name and stable-ID generation, Python-to-IR mapping, explicit ProjectIR validation, and SymbolIndex construction through the reusable `PythonProjectAnalyzer`. Preserve recoverable file failures as diagnostics, report fatal stage failures explicitly, verify deterministic logical results and read-only behavior, and document measured synthetic-project performance. Dependency resolution and graph construction remain out of scope.

## Phase 2: Call Evidence and Dependency Analysis — Complete

Add static call-site evidence, resolve syntactic references, and construct supported function-call and file-import dependency edges. Python call and import records flow through version 3 ProjectIR; same-file and cross-file resolvers, dependency builders, the unified graph engine, the direct query engine, the backend API, and the integrated frontend exploration workspace are implemented and regression-tested through Phase 3.6D.

### Phase 2.1: Python Call Expression Extraction — Complete

Extract every Python `ast.Call` as an ordered, JSON-safe call-site record. Preserve callee syntax, open-string callee kind, name/attribute details when decomposable, source locations, enclosing named scopes, execution context, and argument shape. This phase does not assign caller IDs, resolve targets, change Unified IR, or create dependency edges.

### Phase 2.2A: CallSiteIR Schema & Models — Complete

Add immutable, language-independent `CallSiteIR` records to version 2 `ProjectIR`. Validate file and optional enclosing-symbol references, preserve call syntax, source locations, argument metadata, source order, and JSON metadata, and keep syntactic calls separate from resolved dependency edges.

### Phase 2.2B: Python CallSiteIR Mapping & Pipeline Integration — Complete

Map standalone Python call extraction records into `CallSiteIR` and include them in the reusable Python project analysis pipeline. Preserve extraction order and context, report actual call-site statistics and extractor warnings, and safely assign an enclosing symbol only when scope and source-span evidence identify one declaration. Calls remain unresolved and dependency edges remain empty.

### Phase 2.3: Python Call Target Resolution — Complete

Use call-site syntax, project symbols, and language-specific binding evidence to determine candidate targets, keeping unresolved and ambiguous references explicit. Same-file and supported cross-file Python resolution run in the project pipeline, with a separate effective result and no dependency edges. Dependency-edge creation remains future work.

#### Phase 2.3A: Python Local Call Resolution Engine — Complete

Resolve supported direct names, verified `self` and `cls` methods, local class-qualified methods, and simple same-file class constructors into a separate immutable resolution result. Enforce the same-file boundary, preserve call-site references and evidence, and report ambiguous and unresolved cases without modifying CallSiteIR or generating dependency edges.

The local resolver returns a separate immutable result and does not modify Unified IR or create dependency edges. At this subphase it was called directly with caller-supplied binding evidence; pipeline integration is covered by Phase 2.3B.

#### Phase 2.3A.1: Python Scope Binding Evidence Extraction — Complete

Add a standalone Python AST extractor for assignments, all parameter categories, imports reused from `PythonImportExtractor`, function/class declarations, global/nonlocal directives, conditional branches, loop/with/exception targets, pattern captures, and comprehension scopes. Preserve lexical scopes, source positions, ordering, wildcard uncertainty, exception-target cleanup, JSON-safe output, and structured warnings. The result is not part of the project pipeline.

#### Phase 2.3A.2: Binding-Aware Python Local Call Resolver — Complete

Accept validated binding results as explicit resolver input. Match call sites to source-bounded lexical scopes; account conservatively for parameters, local assignments, imports, declarations, global/nonlocal directives, conditional bindings, class namespace rules, and receiver reassignment. Keep uncertain or shadowed calls unresolved, preserve ambiguity for multiple concrete declarations, and leave IR and dependency edges unchanged. Cross-file and import target resolution remain out of scope.

#### Phase 2.3B: Python Binding & Local Resolution Pipeline Integration — Complete

Run binding extraction once for every successful file parse using the existing parse and import results. Preserve per-file evidence and warnings, validate/map the project as before, then invoke the binding-aware local resolver against the same validated ProjectIR and SymbolIndex. Return the immutable resolution result and binding/resolution summaries in `PythonProjectAnalysisResult`. Parse failures stay in ProjectIR without fabricated bindings; unresolved and ambiguous calls remain valid analysis outcomes. Resolution remains same-file only and creates no dependency edges. Updated synthetic benchmark results are recorded in `docs/testing/foundation.md`.

### Phase 2.4: Python Module, Import, and Cross-File Call Resolution — Complete

Resolve Python ImportIR records against parsed project modules, package structure, and top-level declarations. Preserve unresolved and ambiguous results, source candidates, aliases, and evidence. The module and cross-file call resolvers run in the project pipeline and return separate results; neither creates dependency edges.

#### Phase 2.4A: Python Module & Import Resolution Engine — Complete

Build a deterministic module inventory from Python `.py` and `.pyi` ProjectIR source paths under the project-root default and explicitly configured project-relative search roots. Parse-failed paths identify module candidates but provide no declaration evidence. Resolve absolute and relative module imports, regular and supported namespace packages, package submodules, and uniquely evidenced top-level function/class imports through the existing SymbolIndex. Preserve competing roots, `.py`/`.pyi` pairs, package/module name collisions, duplicate declarations, alias semantics, and wildcard uncertainty. Return an immutable JSON-safe result with statuses, candidates, evidence, diagnostics, and summary counts. The pipeline invokes this stage against the validated project/index snapshot. The resolver does not scan or read files, execute modules, follow arbitrary re-exports, or generate dependency edges.

#### Phase 2.4B: Python Cross-File Call Resolution Engine — Complete

Consume ProjectIR, SymbolIndex, same-file call results, verified module/import results, and binding evidence. Resolve supported direct imports, module aliases, explicit submodule calls, and relative imports to top-level functions or classes in another file. Preserve ambiguity and unresolved evidence in separate immutable results; leave locally resolved and ambiguous calls unchanged. This component does not construct dependency edges.

#### Phase 2.4C: Python Cross-File Resolution Pipeline Integration — Complete

Run module/import resolution and cross-file call resolution automatically after validated IR and SymbolIndex construction, reusing retained binding evidence and local results. Return all raw resolution outputs and an immutable effective result with exactly one record per CallSiteIR, local resolution precedence, and resolution provenance. Validate stage outputs, preserve recoverable parse failures, expose project-relative module search-root configuration, and record 3/30/100-file benchmark measurements. This stage does not create dependency edges.

### Phase 2.5: Python Call Dependency Edges — Complete

Convert supported resolved Python calls into directed caller-to-callee `DependencyEdgeIR` records. Preserve call-site evidence and repeated calls. Imports do not create edges; unresolved, ambiguous, unsupported, or caller-less calls are not inferred.

#### Phase 2.5A: Python Call Dependency Edge Construction — Complete

Implement an immutable deterministic builder that validates ProjectIR, SymbolIndex, effective resolution references, caller identity, and unique targets. Integrate it after effective call resolution, append generated call edges while preserving existing edges, and validate the final ProjectIR. Tested in Phases 3.6A–3.6D.

#### Phase 2.5B: Python Call Dependency Edge Testing — Complete (Covered in Phase 3.6A)

Unit and end-to-end tests for edge direction, caller/target validation, repeated and recursive calls, constructors, cross-file calls, skip statistics, final ProjectIR validation, determinism, immutability, and preservation of existing edges were completed in Phase 3.6A.

### Phase 2.6A: Python Import Dependency Construction — Complete

Build immutable file-to-file import dependencies from exact ProjectIR `ImportIR` records and existing module/import resolution evidence. Add the distinct `ImportDependencyIR` collection to ProjectIR schema version 3, validating both endpoints against source files while preserving call edges separately. Create an edge only for a uniquely resolved concrete in-project file; retain source order, alias, relative-import data, location, and provenance. Unresolved, ambiguous, wildcard, external, namespace-only, missing-evidence, and unsupported re-export cases create no edge. Automated tests and verification were completed in Phases 3.6A–3.6D.

### Phase 2.7A: Unified Graph Engine Construction — Complete

Build a directed NetworkX `MultiDiGraph` from ProjectIR file and symbol nodes, explicit containment, call dependency, and import dependency records. Use separate deterministic file and symbol ID namespaces and keyed multiedges to retain repeated evidence. Validate ProjectIR and parent/edge provenance, expose counts and a defensive-copy graph result, and keep graph construction separate from source analysis. Graph tests and verification were completed in Phases 3.6A–3.6D.

### Phase 2.8A: Graph Query Engine Construction — Complete

Implement direct read-only queries over `GraphBuildResult`: exact node lookup; combined filters; incoming/outgoing relationships and direct neighbors; graph and per-node MultiDiGraph statistics; and explicit induced subgraphs. Preserve multiedge keys and metadata, order results deterministically, freeze query result attributes, validate graph contract and arguments, and return subgraphs through the defensive-copy graph result. Multi-hop trace, path finding, advanced graph algorithms, and graph-query endpoints remain out of scope. Automated tests and verification were completed in Phases 3.6A–3.6D.

### Phase 2.9A: Backend Analysis API Foundation — Complete

Expose synchronous project analysis, analysis metadata, graph JSON retrieval, and explicit session deletion through Flask. Keep orchestration Flask-independent, validate local project directories, build the graph before reporting success, and store complete sessions in a thread-safe process-local store. Serialize the NetworkX multigraph deterministically with parallel edge keys and supported metadata; return consistent safe JSON errors. Keep NetworkX-dependent imports lazy so health remains available without that dependency. API testing and verification were completed in Phases 3.6A–3.6D.

### Phase 2.9B: Graph Query API Implementation — Complete

Expose GraphQueryEngine node filtering and lookup, incoming/outgoing/both relationship queries, direct neighbors, graph and per-node statistics, and induced-subgraph extraction through analysis-scoped Flask routes. Keep graph semantics in the existing query engine, serialize results through the graph API's JSON contracts, preserve directed parallel edges, and map missing analyses/nodes, invalid queries, missing NetworkX, and serialization failures to safe HTTP errors. Query code remains lazily loaded so health stays independent of NetworkX. API tests and verification were completed in Phases 3.6A–3.6D.

### Phase 3.0A: Frontend Graph Foundation — Complete

Connect the React workspace to the existing Flask analysis lifecycle. Centralize the API URL and JSON client, define typed analysis and MultiDiGraph response models, submit a backend-accessible Python project path, load analysis information and graph JSON, summarize results, and support active-session deletion/replacement. Keep the active session in React state only; preserve it if a replacement fails and report session cleanup failures without invalidating a newly loaded analysis. This phase does not render graphs or add graph-query controls. API, unit, and real-browser verification were completed in Phases 3.6B–3.6D.

### Phase 3.1A: Interactive 2D Graph Visualization — Complete

Render the active backend graph with Cytoscape.js. Preserve each file/symbol node ID and every directed CONTAINS, CALLS, and IMPORTS edge, including backend edge IDs, keys, parallel relationships, and metadata. Provide breadth-first, CoSE, and circle layouts plus zoom, pan, fit, center, and node selection controls. Keep the graph renderer isolated from fetching/state logic, lazily load it, and dispose of the instance on unmount. Vitest and Playwright regression suites verify graph conversion, Cytoscape lifecycle, real-browser canvas rendering, parallel bezier curves, and layouts (Phases 3.6B–3.6D).

### Phase 3.2A: Function Search — Complete

Search only function and method symbols from the active graph data. Match names and qualified names with debounced, case-insensitive partial matching; filter by available kind, language, and relative path metadata. Preserve the actual symbol node ID and display qualified name, file, language, and source line when available. Selecting a result uses the existing Cytoscape instance to highlight and center the exact node. Keep search local to the active analysis, synchronize direct graph selection, and clear stale results/selection when the session changes. Vitest and Playwright regression suites cover this feature (Phases 3.6B–3.6D).

### Phase 3.3A: Function Trace — Complete

Trace outgoing, incoming, or both directions over loaded CALLS graph edges only, with bounded depth 1–10 and default 3. Build sorted adjacency indexes once per graph and use visited-node breadth-first traversal. Preserve the source/target direction, backend edge IDs and keys, parallel calls, call-site evidence, source locations, and provenance. Expand only function/method nodes; explicit constructor class targets are terminal. Group reachable nodes and individually preserved relationships by traversal depth, report whether further eligible nodes are beyond the depth boundary, and synchronize trace navigation with shared graph selection. This is static resolved-call analysis, not runtime tracing or full path enumeration. Vitest and Playwright regression suites cover this feature (Phases 3.6B–3.6D).

### Phase 3.4A: Function Inspector — Complete

Inspect function, method, and class symbols by exact selected graph/IR ID. Reuse stored ProjectIR and SymbolIndex data for symbol, parent, and source-file metadata; retrieve a bounded current source range separately with containment and symlink/junction protections. Share selection with the graph, search, and trace features. Source freshness is not guaranteed because the session stores no content snapshot or hash. Backend, frontend, and real-browser regression suites cover this feature (Phases 3.6A–3.6D).

### Phase 3.5A: Full Feature Integration and Application Consolidation — Complete

Integrate analysis creation, graph retrieval, Cytoscape visualization, search, trace, Inspector, and session replacement/deletion in the existing React workspace. Keep one selected graph node ID scoped to the active analysis and clear stale selection on replacement. Preserve edge IDs, keys, metadata, and parallel relationships. Use the AnalysisInfo returned by the create request and fetch the graph once, preserving the prior valid session if replacement fails. The Inspector remains keyed by analysis and symbol IDs, with source retrieval separate from metadata. Documented behavior and known source-freshness limits are reflected in the application READMEs and architecture overview. Backend, frontend, and real-browser regression suites cover this feature (Phases 3.6A–3.6D).

### Phase 3.6A–3.6B: Backend and Frontend Testing — Complete

Backend and frontend regression suites now cover the static analysis pipeline, API, graph services, session lifecycle, graph selection, parallel edges, bounded trace, Inspector source handling, stale request protection, and dependency availability. Frontend typecheck and production build also pass. See the testing guide for final test and coverage results.

### Phase 3.6C: Browser-Based Verification — Complete

Real-browser Playwright verification (`npm run test:e2e` using Microsoft Edge) covers the complete 16-step analysis, Cytoscape rendering, Function Search, Function Trace, Function Inspector, session replacement, and session deletion workflow; responsive viewports (`1440×900`, `1366×768`, `768×1024`, `390×844`); static-analysis non-execution, read-only, junction/traversal, bounded source, and freshness checks; and 3/30/100-file end-to-end performance benchmarks. See `docs/testing/foundation.md` for details.

### Phase 3.6D: Final Regression and Python MVP Release Readiness — Complete

Confirmed zero regressions across the complete backend pytest suite (`574 passed, 0 failed, 3 skipped`, `87%` statement coverage), frontend Vitest suite (`60 passed, 0 failed, 0 skipped` across 13 files, `88.86%` statements / `79.48%` branches / `90.76%` functions / `91.22%` lines), TypeScript typecheck, Vite production build, and live real-browser Playwright suite (`4 passed` in Microsoft Edge). Verified all Phase 3.6C fixes, reconciled documentation, and established the Python MVP release-readiness checklist in `docs/testing/foundation.md`.

## Phase 3: Function Dependency Analyzer — Planned

Use resolved call relationships to analyze dependencies between functions and methods.

## Phase 4: Advanced Graph Analysis — Planned

Add advanced graph traversal and analysis capabilities beyond the foundational direct-query engine.

## Phase 5: 2D Graph Visualization — Foundation Implemented in Phase 3.1A

The interactive Cytoscape.js graph foundation is in place. Additional graph exploration work remains scoped to its later dedicated phases.

## Phase 6: Function Search and Trace — Complete (Implemented in Phases 3.2A–3.3A; Tested in Phases 3.6B–3.6D)

Function/method search and bounded incoming/outgoing/bidirectional static call tracing are implemented and verified by unit and real-browser regression suites.

## Phase 7: Function Inspector — Complete (Implemented in Phase 3.4A; Tested in Phases 3.6A–3.6D)

Inspect selected function, method, and class symbols by exact graph/IR ID. Retrieve stored schema-v3 symbol data, parent and source-file metadata through the analysis session's existing ProjectIR and SymbolIndex. Display supported parameters, annotations, decorators, documentation, language metadata, source locations, and bounded current source snippets. Protect source reads with relative-path validation, project-root containment, and link/junction checks; never execute analyzed code. Integrate the Inspector with shared graph, search, and trace selection, display dependency counts from current CALLS graph evidence, and invalidate stale requests on selection/session changes. Source snapshot freshness is not guaranteed: scan metadata records byte size but not content hashes or snapshots, and same-size modifications cannot be detected. Automated unit, API, and real-browser regression suites were completed in Phases 3.6A–3.6D.

Provide a focused view of information about a selected function.

## Phase 8: 3D Graph Visualization — Planned

Add an interactive three-dimensional graph view.

## Phase 9: JavaScript and TypeScript Support — Planned

Add language adapters for JavaScript and TypeScript.

## Phase 10: Additional Language Support — Planned

Expand language support toward the 12-language project target. The remaining language list is to be decided.

## Phase 11: Cross-Language Dependency Analysis — Planned

Analyze dependencies that span supported programming languages.

## Phase 12: Testing, Optimization, and Final Integration — Planned

Broaden verification, improve performance, and integrate the planned project capabilities.

