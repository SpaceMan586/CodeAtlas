# CodeAtlas

CodeAtlas is a multi-language software architecture explorer. It analyzes source
code and presents relationships among functions, methods, classes, files, and
modules through an initial interactive 2D graph; 3D exploration is planned.

## Main objectives

- Analyze software projects written in multiple programming languages.
- Extract code structure into a language-independent representation.
- Identify relationships and dependencies between code entities.
- Explore project structure through interactive 2D graphs, Function Search, and multi-hop static call traces; 3D views remain planned.

## Core features

The implemented Python backend pipeline scans `.py` and `.pyi` files, parses
them statically, extracts declarations, imports, calls, and lexical binding
evidence, maps declarations and calls to Unified IR, validates the project
snapshot, builds an immutable in-memory Symbol Index, resolves local calls,
resolves project imports, and conservatively refines supported cross-file
calls. It exposes the raw stage results and an effective call-resolution
result, then constructs explicit call dependency edges and import dependencies
in a newly validated final ProjectIR. Call dependencies remain symbol-to-symbol
records; import dependencies remain file-to-file records.

- Python project scanning and AST analysis — implemented.
- Python function, method, class, and import extraction — implemented.
- Python call-expression extraction, CallSiteIR mapping, binding extraction, and same-file local call resolution in the project pipeline — implemented.
- Python qualified names and `py:v1:` stable symbol IDs — implemented.
- Version 3 Unified IR models, including CallSiteIR and file-level import dependencies — implemented.
- Project-level `PythonProjectAnalyzer` orchestration and IR validation — implemented.
- Immutable in-memory Symbol Index with duplicate-preserving lookups — implemented.
- Per-file binding evidence and same-file resolved/unresolved/ambiguous call results — implemented; resolution does not create dependency edges.
- Python module/import resolution against ProjectIR and SymbolIndex — integrated into the project pipeline with explicit unresolved/ambiguous outcomes.
- Supported Python cross-file call resolution and one effective result per CallSiteIR with local precedence and provenance — integrated into the project pipeline.
- Python call dependency-edge construction — integrated for supported resolved calls; tested in Phase 3.6A.
- Python import dependency construction — integrated for uniquely resolved in-project imports; tested in Phase 3.6A.
- Foundational unified graph construction — integrated using NetworkX `MultiDiGraph`; regression-tested in Phase 3.6A.
- Direct graph query engine — integrated for lookup, filtering, relationships, neighbors, statistics, and subgraphs; regression-tested in Phase 3.6A.
- Backend analysis API — analysis, graph query, Inspector, and session lifecycle routes are implemented and regression-tested in Phases 3.6A–3.6D.
- Function dependency analysis and advanced graph-query capabilities — planned; foundational graph construction and direct queries are implemented.
- Interactive 2D graph visualization with Cytoscape.js — implemented in Phase 3.1A; backend, frontend, and real-browser tests completed in Phases 3.6A–3.6D.
- Function and method search with graph-node navigation — implemented in Phase 3.2A; backend, frontend, and real-browser tests completed in Phases 3.6A–3.6D.
- Bounded incoming, outgoing, and bidirectional Function Trace over resolved CALLS relationships — implemented in Phase 3.3A; backend, frontend, and real-browser tests completed in Phases 3.6A–3.6D.
- Function, method, and class Inspector with schema-v3 symbol metadata and bounded current-source snippets — implemented in Phase 3.4A; backend, frontend, and real-browser tests completed in Phases 3.6A–3.6D.
- Integrated analysis workflow and consolidated workspace — implemented in Phase 3.5A; backend, frontend, real-browser end-to-end, and final release-readiness regression testing completed in Phases 3.6A–3.6D.
- Interactive 3D graph view — planned.
- Cross-language dependency analysis — planned.

## Planned language support

The project target is 12 programming languages. Python is the initial parser
target. JavaScript and TypeScript are planned for a later phase. The remaining
language set has not yet been selected, so this list does not claim current
language support.

## Planned technology stack

The backend foundation uses Flask and Pytest; the standalone Graph Engine uses
NetworkX. Tree-sitter and SQLite remain planned. The frontend uses Cytoscape.js
for its initial 2D graph visualization; 3D libraries remain planned.

- **Backend:** Python, Flask, NetworkX, Tree-sitter, SQLite, and Pytest.
- **Frontend:** React, TypeScript, Vite, Tailwind CSS, Cytoscape.js, Three.js,
  React Three Fiber, and Vitest.

## High-level architecture

The target architecture is:

Project Scanner → Language Detection → Parser Registry → Language Adapters →
Unified Intermediate Representation → Symbol Index → Dependency Resolver →
Graph Engine → 2D/3D Visualization

The current Python path starts at Project Scanner and runs through Python AST
parsing and extraction, binding evidence collection, qualified name and symbol
ID generation, the Python IR adapter, IR validation, Symbol Index construction,
local call resolution, import resolution, cross-file call resolution,
effective result aggregation, call dependency-edge construction, import dependency construction, final IR validation, standalone graph construction, and the Flask analysis API. Language Detection and Parser Registry are
planned; the Python analyzer currently invokes the Python components directly.
See [the architecture overview](docs/architecture/overview.md) for component
responsibilities and data flow.

## Development status

Completed work includes **Phase 0: Project Foundation**, **Phases 1.1–1.6D**,
**Phases 2.1, 2.2A–2.2B, 2.3A–2.3B, 2.4A–2.4C, 2.5A, 2.6A, 2.7A, 2.8A, 2.9A–2.9B, 3.0A–3.5A, and 3.6A–3.6D**: the scanner, Python AST parser and
extractors, qualified names, stable IDs, Unified IR schema, Python-to-IR
adapter, immutable Symbol Index, and full Python project pipeline integration
and validation, including binding-aware local and cross-file call resolution,
import resolution, effective result aggregation, call-edge construction, and
file-level import dependency construction, a standalone unified graph builder,
direct graph query engine, backend analysis/query APIs, frontend session integration, interactive 2D graph visualization, function/method search, multi-hop static call trace, and a function/method/class Inspector. The Phase 3.5A consolidation removes a redundant analysis-info request and keeps the workspace features tied to the active graph selection. Backend, frontend, real-browser end-to-end, and final release-readiness regression tests were completed in Phases 3.6A, 3.6B, 3.6C, and 3.6D.
The Flask application factory,
configuration profiles, health endpoint, and JSON error handling are also in
place. Analysis is static and read-only; individual syntax, encoding, and file
read failures are retained as diagnostics while other files continue. Call
sites preserve syntax and lexical context; local and cross-file targets are
returned as separate resolution outputs and aggregated before supported calls
produce directed caller-to-callee edges. Dependency edges remain separate
from syntactic calls and resolution records. Unresolved import diagnostics can yield
`completed_with_warnings`; unresolved calls by themselves remain valid and do
not automatically change project status.

Broader dependency analysis, database persistence, and 3D visualization remain planned. The frontend submits project paths, loads and summarizes session data, supports session replacement/deletion, renders an interactive 2D graph, searches functions and methods, traces static call dependencies, and inspects symbol metadata and bounded source ranges. Backend, frontend, real-browser, and final release-readiness regression tests were completed in Phases 3.6A–3.6D. See [the testing and release-readiness guide](docs/testing/foundation.md) for verification commands, benchmarks, known limitations, and the Python MVP release-readiness checklist, and see the [development roadmap](docs/development/roadmap.md) for the planned sequence.

## License

A license has not been selected. The root `LICENSE` file is a placeholder, not
license terms.
