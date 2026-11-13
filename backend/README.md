# CodeAtlas Backend

This directory contains the Flask backend and static Python source analysis pipeline with local, import, and cross-file call resolution; dependency builders; a NetworkX graph builder and query engine; and analysis and Inspector APIs. `DependencyEdgeIR` keeps caller-to-callee symbol edges distinct from file-level `ImportDependencyIR`. The graph is a directed `MultiDiGraph` and preserves parallel relationships. The current ProjectIR schema is version 3; older snapshots have no automatic migration. Backend testing and known coverage limits are documented in [the testing guide](../docs/testing/foundation.md).

## Function Inspector API

**Verified workspace environment (Phase 3.6A):** Python 3.14.2, NetworkX 3.7, Flask 3.1.3, pytest 9.2.0, and pytest-cov 7.1.0 are installed in `backend/.venv`. `pip check` reported no broken requirements. Install declared dependencies into the backend environment with the commands below.

`GET /api/analysis/{analysis_id}/symbols/lookup?symbol_id={id}` returns the exact stored function, method, or class `SymbolIR`, its parent symbol when available, and its `SourceFileIR` metadata. Lookup uses the session's retained schema-v3 `ProjectIR` and `SymbolIndex`; it does not rerun parsing or graph construction.

`GET /api/analysis/{analysis_id}/symbols/source?symbol_id={id}` returns the source snippet for the IR source range. The session retains the canonical project root privately. The service obtains the relative path only from the validated symbol, rejects traversal and link/junction path components, verifies resolved containment and regular-file status, and reads text without executing or importing analyzed code. Responses are bounded to 500 lines, 16,384 characters per line, and 256,000 characters. Truncation is indicated in the JSON. The source is read from the current file; a changed size is detected against scan metadata, but same-size edits cannot be detected, so snapshot freshness is not guaranteed. Missing or unreadable snippets have an explicit unavailable response while metadata lookup remains independent. Absolute project paths are not serialized.

## Requirements

- Python 3.14 or another Python version compatible with the pinned requirements.
- pip.

## Create an environment and install dependencies

Run these commands from `backend/`:

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
```

On macOS or Linux, activate the environment with `source .venv/bin/activate` after creating it with `python3 -m venv .venv`.

`requirements.txt` contains runtime dependencies. `requirements-dev.txt` includes those dependencies, pytest, and pytest-cov.

## Run the backend

From `backend/`, with the environment active:

```powershell
python run.py
```

The local development profile is selected by default. Set `CODEATLAS_ENV` to `development`, `testing`, or `production` to select another profile. For example, in PowerShell:

```powershell
$env:CODEATLAS_ENV = "production"
python run.py
```

`CODEATLAS_SECRET_KEY` may be supplied through the process environment; no secret is hardcoded. Configuration values are not returned by the API or written by the application logger.

## Health endpoint

`GET /api/health` returns HTTP 200 and a JSON response with `status`, `application`, and `version`. It reports application availability only; it does not verify a database, parser, or analysis subsystem.

## Analysis API

The local Flask API exposes these synchronous analysis-session routes:

- `POST /api/analysis` — accepts `{"project_path":"C:/Projects/sample-python"}` and returns HTTP 201 with `analysis_id`, `project_display_path` (directory name only), completion `status`, `completed_at`, IR schema version, summary counts, and diagnostic counts. The response does not include the graph.
- `GET /api/analysis/<analysis_id>` — returns the same analysis information or HTTP 404 when the session is unknown.
- `GET /api/analysis/<analysis_id>/graph` — returns deterministic frontend-neutral JSON with `metadata`, `nodes` (`id` and `data`), `edges` (`id`, `source`, `target`, `key`, and `data`), and graph `statistics`. Edge IDs are stable hashes of source ID, target ID, and multiedge key; the key itself is retained, so parallel edges remain distinct.
- `GET /api/analysis/<analysis_id>/nodes` — optional query filters: `node_type`, `name`, `qualified_name`, `symbol_kind`, `language`, `file_path`, and `file_identity`. Returns `analysis_id`, `count`, and `nodes`. Names and qualified names use case-insensitive substring matching; type, kind, and language use case-insensitive exact matching; paths use exact matching.
- `GET /api/analysis/<analysis_id>/nodes/lookup?node_id=…` — returns `analysis_id` and an exact `node` lookup.
- `GET /api/analysis/<analysis_id>/relationships?node_id=…&direction=outgoing&relationship_type=CALLS` — returns `analysis_id`, `node_id`, `direction`, `count`, and directed `relationships`; direction may be `incoming`, `outgoing` (default), or `both`; relationship type is optional and may be `CONTAINS`, `CALLS`, or `IMPORTS`. Each relationship preserves source, target, edge key, type, and metadata.
- `GET /api/analysis/<analysis_id>/neighbors?node_id=…&direction=outgoing&relationship_type=CALLS` — returns `analysis_id`, `node_id`, `direction`, unique adjacent `nodes` plus `node_count`, and every matching `relationships` plus `relationship_count`; direction and relationship type follow the relationships endpoint.
- `GET /api/analysis/<analysis_id>/statistics[?node_id=…]` — returns `analysis_id` and `statistics` with `total_nodes`, `total_edges`, file/symbol node counts, relationship type counts, and, when a node is supplied, its `in_degree`, `out_degree`, and `total_degree` (parallel edges count individually; self-loops count in both directions and twice in total degree).
- `POST /api/analysis/<analysis_id>/subgraph` — accepts `{"node_ids":["file:src/main.py","symbol:py:v1:…"]}` and returns the standard graph response shape for the induced subgraph. Well-formed unknown node IDs are omitted.
- `DELETE /api/analysis/<analysis_id>` — removes the session and returns `{"analysis_id":"…","deleted":true}`; unknown sessions return 404.

Example create request:

```http
POST /api/analysis
Content-Type: application/json

{"project_path":"C:/Projects/sample-python"}
```

The create response (also returned by the information endpoint) has this shape:

```json
{
  "analysis_id": "0123456789abcdef0123456789abcdef",
  "project_display_path": "sample-python",
  "status": "completed",
  "completed_at": "2026-11-10T12:00:00+00:00",
  "project_ir_schema_version": 3,
  "summary": {
    "scanned_files": 4,
    "parsed_files": 4,
    "parse_total_files": 4,
    "symbols": 7,
    "call_sites": 3,
    "call_dependencies": 2,
    "import_dependencies": 1,
    "graph_nodes": 11,
    "graph_edges": 13
  },
  "diagnostics": {
    "total": 0,
    "project": 0,
    "call_resolution": 0,
    "imports": 0,
    "cross_file_calls": 0,
    "call_dependencies": 0
  }
}
```

The graph endpoint uses node and relationship attributes from `GraphBuilder`; this abbreviated example shows the outer contract:

```json
{
  "analysis_id": "0123456789abcdef0123456789abcdef",
  "metadata": {"graph_type":"CodeAtlasProjectGraph","ir_schema_version":3,"analysis_metadata":null},
  "nodes": [{"id":"file:src/main.py","data":{"node_type":"file","file_path":"src/main.py"}}],
  "edges": [{"id":"edge:…","source":"file:src/main.py","target":"symbol:py:v1:…","key":"contains:file:00000000","data":{"edge_type":"CONTAINS","relationship_type":"CONTAINS"}}],
  "statistics": {"node_count":1,"edge_count":1,"total_nodes":1,"total_edges":1,"nodes_by_category":{"file":1},"edges_by_relationship_type":{"CONTAINS":1}}
}
```

Errors use `{"error":{"status":400,"message":"…"}}`. Malformed JSON or query fields return 400 (non-JSON subgraph content type returns 415); inaccessible project paths return 403; unavailable NetworkX returns 503; analysis or graph construction failures return 422; unknown session or node IDs return 404; unexpected and serialization failures return a generic 500 message. Detailed failures are logged server-side. Absolute project paths are not returned in analysis information.

Sessions are held in a thread-safe in-memory store and disappear when the backend process restarts. Analysis is synchronous and stores one pipeline result and one defensive-copy graph result per session; query requests use a transient `GraphQueryEngine`. No project files are changed. NetworkX-dependent graph code loads only when analysis or graph querying is requested, so `/api/health` remains available without NetworkX. The existing Vite `/api` proxy targets `http://127.0.0.1:5000` by default. Start the backend from this directory with `python run.py`.

## Run tests

From `backend/`, with the environment active:

```powershell
python -m pytest
```

The test fixtures create an isolated testing-profile application and do not use production data.

## Project scanner

`app.scanner.ProjectScanner` performs read-only file discovery under a selected
project directory. By default it returns Python `.py` source files and `.pyi`
stubs, with relative forward-slash paths, filenames, normalized extensions,
byte sizes, language labels, and categories. It does not read source contents,
execute files, or parse Python.

```python
from app.scanner import ProjectScanner

result = ProjectScanner().scan("path/to/project")
for source_file in result.files:
    print(source_file.path)
```

The scanner skips these directory names at any depth, case-insensitively:
`.git`, `.venv`, `venv`, `env`, `node_modules`, `__pycache__`, `.pytest_cache`,
`.mypy_cache`, `.ruff_cache`, `.tox`, `.nox`, `dist`, `build`, `coverage`,
`htmlcov`, `.idea`, and `.vscode`. Constructor-supplied names add exclusions;
project-relative paths can exclude one specific directory. It does not support
wildcards or full Git ignore rules. File and directory symbolic links, along
with Windows junctions, are skipped without inspecting their targets. Link
warnings do not affect the skipped-directory count. Recoverable child
filesystem errors are recorded as relative-path warnings. The canonical
project root is available internally as `result.project_root` and omitted by
`result.to_dict()`.

## Python AST parser

`app.parsers.python.PythonParser` consumes one scanner-discovered file at a
time. It supports `.py` and `.pyi`, reads source with Python encoding-cookie
detection, and returns the genuine `ast.Module` for later analysis stages.
Syntax, filesystem, and encoding failures have distinct statuses and
serializable relative-path diagnostics. The parser validates project
containment and rejects traversal and links. It does not import or execute the
source; function, class, and import metadata are produced by separate
extractors.

```python
from app.parsers.python import PythonParser
from app.scanner import ProjectScanner

scan = ProjectScanner().scan("path/to/project")
parser = PythonParser()
result = parser.parse_file(scan.project_root, scan.files[0])
if result.module is not None:
    print(result.module)
```

## Python class and function extraction

`PythonFunctionExtractor` and `PythonClassExtractor` independently consume a
successful parse result. Function records include signatures and lexical
scope paths. Class records include lexical parent context, base expressions,
class keyword arguments, decorators, docstrings, and source positions. Base
expressions and class keywords are recorded as syntax; inheritance is not
resolved.

```python
from app.extractors.python import PythonClassExtractor, PythonFunctionExtractor

function_result = PythonFunctionExtractor().extract(result)
class_result = PythonClassExtractor().extract(result)
```

## Python import extraction

`PythonImportExtractor` independently consumes the same `PythonParseResult`
and emits one record per imported alias. It supports `import` and `from`
imports, aliases, wildcard imports, `__future__` imports, and relative import
levels. It also records statement locations and lexical class/function/method
scope paths, including imports inside conditionals, loops, and exception
blocks.

`bound_name` follows Python syntax: `import package.module` binds `package`,
`import package.module as mod` binds `mod`, and `from package import item`
binds `item`. Wildcard imports have no individual bound name. Relative imports
retain their AST `module_name` and `relative_level`; for `from . import item`,
the module name is `None` and the level is `1`. These records do not prove that
an imported module or name exists and do not resolve package targets.

The result separates statement counts from alias counts. A statement such as
`import os, sys` counts as one statement and two imported aliases. Category
counts for regular, from, relative, and future imports count statements;
`wildcard_import_count` counts wildcard alias records.

```python
from app.extractors.python import PythonImportExtractor

import_result = PythonImportExtractor().extract(result)
print(import_result.to_dict())
```

## Python module and import resolver

`PythonModuleResolver` consumes `ProjectIR` and the `SymbolIndex` built from
the same validated snapshot. It returns one `ImportResolutionRecord` per
Python `ImportIR` record without changing the IR or index:

```python
from app.analysis import PythonModuleResolutionConfig, PythonModuleResolver

result = PythonModuleResolver().resolve(
    project_ir=project_ir,
    symbol_index=symbol_index,
    config=PythonModuleResolutionConfig(search_roots=("src",)),
)
print(dict(result.summary))
```

With no config, the project root is the only search root. Additional roots are
explicit project-relative paths, normalized to `/`; absolute paths, drive or
UNC paths, `.`/`..` traversal, duplicate roots, and whitespace-only roots are
rejected. The resolver follows Python's case-sensitive import naming and
rejects a case-insensitive mode. It does not infer a `src` root, read
environment variables, reproduce runtime `sys.path`, scan the filesystem, or
read source contents.

The in-memory inventory uses Python `.py` and `.pyi` SourceFileIR paths,
including paths whose parse failed; those entries identify modules but have
no declaration evidence. Regular `.py` modules, `__init__.py` packages, stubs, and optional
namespace package portions are represented as distinct candidates. A
`.py`/`.pyi` pair, same-named `package.py` and `package/__init__.py`, or
competing search-root matches are preserved and reported as ambiguous.
Multiple namespace portions can represent one resolved logical namespace
package while preserving every location; a same-named submodule in multiple
portions remains ambiguous. Namespace candidates have a relative directory
path and no source file; no filesystem metadata outside ProjectIR is consulted.
Namespace support can be disabled with
`PythonModuleResolutionConfig(allow_namespace_packages=False)`.

Absolute imports match exact module identities. Relative imports derive the
importer's package context from its module identity under configured roots and
apply `ImportIR.relative_level`; missing context or attempts above the
available package root remain unresolved. For `from` imports, the base module
and imported name are resolved separately. A module-level function or class
can resolve through SymbolIndex; methods and nested declarations are not
treated as package exports. A matching package submodule is also considered,
and a same-named declaration and submodule produce ambiguity. Candidate
ordering follows configured root order and then ProjectIR source order.

Possible module-scope re-export imports are recorded as evidence, but arbitrary
re-export chains, assignment-based exports, dynamic behavior, and `__all__`
are not evaluated. Wildcard imports retain the base module result and mark
name expansion unknown; they do not fabricate imported symbols. Original
module/name, alias, binding name, statement/alias ordering, candidate paths,
resolution status, evidence, and diagnostics are retained in JSON-safe
immutable result records. External standard-library and third-party imports
remain unresolved when absent from the analyzed inventory; the result does not
classify their external origin.

This resolver is independent of the local call resolver, and the project
analyzer invokes it after ProjectIR validation and SymbolIndex construction.
It identifies modules and imported declarations; it does not mutate
SymbolIndex, read or execute package initialization code, or create
DependencyEdgeIR records. Configure additional roots with
`PythonPipelineConfig(module_search_roots=("src",))`; the project root is the
default and roots must be explicit, safe, project-relative paths.

## Python scope binding evidence

`PythonBindingExtractor` is a standalone AST analysis component. It requires a
successful `PythonParseResult` and the matching result from
`PythonImportExtractor`; the import result supplies existing alias and bound
name semantics rather than reinterpreting import syntax:

```python
from app.extractors.python import PythonBindingExtractor, PythonImportExtractor

import_result = PythonImportExtractor().extract(parse_result)
binding_result = PythonBindingExtractor().extract(
    parse_result=parse_result,
    import_result=import_result,
)
print(binding_result.to_dict())
```

Records cover parameters, assignments, annotated and augmented assignments,
named expressions, imports, function/class declarations, loop and with
targets, exception targets, match captures, comprehension targets, and
deletions. They preserve relative paths, AST source locations, deterministic
traversal order, execution context, branch context, and optional JSON-safe
metadata. Structured warnings identify uncertainty such as wildcard imports
and Python's exception-target cleanup. Wildcards are represented by an
uncertain `*` evidence record; they are not added to a scope's concrete
`bound_names`.

Scope summaries distinguish module, function, class, lambda, and comprehension
scopes. Named function/class paths use the existing name-component convention;
lambda and comprehension components are synthetic. A per-result scope ID
keeps repeated definitions with the same path separate. Source spans let the
resolver distinguish repeated scopes; comprehension summaries also identify
the outer iterable's span. Branches are flow metadata and do not create
scopes. All function-scope assignments and parameters are retained regardless
of source order. `global` and `nonlocal` directives include locations and
order; the resolver uses these records conservatively.

Pattern capture extraction handles `MatchAs`, `MatchStar`, `MatchMapping`,
`MatchSequence`, `MatchClass`, and `MatchOr`; value patterns do not bind names.
Comprehension targets stay in separate comprehension scopes, and the
outermost iterable is associated with its containing scope. Exception target
records include `cleared_after_handler=True` and a warning because Python
clears the target when the handler exits.

This extractor does not execute source, evaluate conditions, resolve imported
targets, generate dependency edges, or perform runtime name resolution. The
project pipeline invokes it once for every successful parse and retains its
result separately from CallSiteIR and Unified IR. Type-parameter scopes and
runtime class namespace lookup are not modeled.

## Python call expression extraction

`PythonCallExtractor` independently consumes a successful `PythonParseResult`
and emits one `PythonCallSite` for every `ast.Call`. It recognizes direct
name, attribute, call-result, subscript, and other callee forms, including
nested calls, constructors, built-ins, `super()`, and reflection-related
expressions such as `getattr`, `__import__`, `eval`, and `exec`. It records
syntax only: it does not execute or interpret calls, import project modules,
assign caller IDs, resolve targets, or create dependency edges. The project
pipeline invokes it on the already parsed file.

```python
from app.extractors.python import PythonCallExtractor

call_result = PythonCallExtractor().extract(parse_result)
for call_site in call_result.call_sites:
    print(call_site.callee_expression, call_site.lexical_scope_path)
```

Each immutable call-site record preserves the relative file path, safe
`ast.unparse()` callee text, open-string `callee_kind`, optional Name/Attribute
`name_path`, attribute name and receiver when applicable, source location,
lexical scope, enclosing class and function/method, execution context,
argument shape, and zero-based occurrence order. Callee kinds initially
include `NAME`, `ATTRIBUTE`, `CALL_RESULT`, `SUBSCRIPT`, and `OTHER`. Name
paths are produced only for chains that can be decomposed from `ast.Name` and
`ast.Attribute` nodes; no target is inferred for dynamic or returned-object
calls.

Source locations retain AST one-based lines and UTF-8 byte columns; end
coordinates are exclusive and may be absent. Positional argument count is the
number of entries in `ast.Call.args`, counting a starred expression as one.
Keyword names stay in source order, with `None` for `**kwargs`; separate flags
record `*args` and `**kwargs` presence. The Phase 2.1 example's stated count of
3 for `process(user, *args, timeout=10, **options)` conflicts with Python's AST:
`ast.Call.args` contains only `user` and `*args`, so this implementation
reports 2 positional entries while preserving `("timeout", None)` as keyword
names.

Calls in named function and class bodies use their full enclosing name path.
Decorator, default, annotation, class-base, and class-keyword calls use the
enclosing scope where the definition occurs. Lambda-body calls use the
`LAMBDA_BODY` execution context without adding a fabricated name to the
lexical path. Execution context values are `MODULE_BODY`, `CLASS_BODY`,
`FUNCTION_BODY`, `DECORATOR`, `DEFAULT_ARGUMENT`, `ANNOTATION`, `CLASS_BASE`,
`CLASS_KEYWORD`, `LAMBDA_BODY`, and `OTHER`. Result serialization is JSON-safe
and contains no AST objects or absolute paths. Unsupported callee unparsing
yields a structured warning and retains the call record.

## Python qualified names

`PythonQualifiedNameGenerator` combines completed function and class
extraction results for the same file. It joins their full lexical scope
components with dots: a method under `Payment` is `Payment.process`, and a
nested function in that method is `Payment.process.validate`. The relative
file path remains separate; it is not treated as an importable Python module
name.

The generator merges records by source position and assigns `source_order` in
the combined result. Duplicate declarations keep the same qualified name and
remain separate records with their source locations. A separate stable ID
generator assigns identifiers to these records.

```python
from app.extractors.python import PythonQualifiedNameGenerator

qualified_result = PythonQualifiedNameGenerator().generate(
    function_result=function_result,
    class_result=class_result,
)
print(qualified_result.to_dict())
```

## Python stable symbol IDs

`PythonStableSymbolIdGenerator` consumes a `QualifiedNameGenerationResult` and
returns one typed identity record for each function, method, nested function,
and class. Imports are not declaration symbols and do not receive IDs.

Each ID has the form `py:v1:<64-character-sha256-hex>`. Its canonical identity
key is compact, sorted-key UTF-8 JSON containing the identity schema version,
language, normalized relative file path, uppercase declaration type,
qualified name, and duplicate occurrence index. Source code and source
locations are intentionally excluded, so line shifts, body edits, comments,
docstrings, parameter defaults, annotations, and formatting do not change IDs.

Windows and POSIX separators normalize to `/`. Absolute, drive-qualified, and
traversing paths are rejected; no project root or machine-specific path is
included. Duplicate declarations with the same relative path, declaration type,
and qualified name receive indexes `0`, `1`, and so on in source order. The
generator preserves result order and metadata and raises an explicit error if
two declarations produce the same ID.

These IDs are intended for use inside a CodeAtlas project, but they contain no
project namespace, so equivalent identity keys in different projects may
produce the same ID. Duplicate occurrence identity is order-based: inserting or
reordering identical declarations can shift indexes and IDs. Stable IDs do not
promise continuity across arbitrary refactoring.

```python
from app.extractors.python import PythonStableSymbolIdGenerator

identity_result = PythonStableSymbolIdGenerator().generate(
    qualified_name_result=qualified_result,
)
print(identity_result.to_dict())
```

The qualified name describes a declaration's lexical name and can be duplicated
within or across files. The stable symbol ID combines that name with file,
declaration type, schema version, and duplicate occurrence information to
distinguish records within the analyzed project. Phases 1.6A, Unified IR Schema
and Models, and 1.6B, Python-to-IR Adapter, are complete.

## Unified IR schema and models

The language-independent core models are available from app.ir without
requiring Flask, SQLite, or language parsers. The current schema is version 3.
The package provides ProjectIR, SourceFileIR, SymbolIR, CallSiteIR,
ParameterIR, SourceLocationIR, ImportIR, DependencyEdgeIR, ImportDependencyIR, and DiagnosticIR,
plus validate_project_ir and shared diagnostic and dependency-resolution enums.

ProjectIR is an ordered snapshot. Its source files, symbols, imports, call
sites, explicit dependency edges, import dependencies, diagnostics, and optional analysis metadata serialize
recursively through to_dict(). Empty projects are valid. Optional fields
serialize as null; ordered tuples serialize as JSON arrays. Constructors use
frozen dataclasses and metadata is defensively copied and recursively frozen.

    from app.ir import ProjectIR, SourceFileIR, SymbolIR

    source_file = SourceFileIR(
        file_path="src/payment.py",
        file_name="payment.py",
        file_extension=".py",
        language="python",
        size_bytes=512,
        parsing_status="parsed",
        source_line_count=18,
        category="source",
    )
    symbol = SymbolIR(
        symbol_id="py:v1:existing-symbol-id",
        language="python",
        original_name="process",
        qualified_name="Payment.process",
        symbol_kind="method",
        file_path="src/payment.py",
    )
    project_ir = ProjectIR(
        source_files=(source_file,),
        symbols=(symbol,),
    )
    print(project_ir.to_dict())

symbol_kind, language, parse status, import statement type, diagnostic
category, and dependency type are open strings so future language adapters can
preserve native declaration concepts. SymbolIR retains its supplied ID; the IR
never recalculates it. Duplicate names and qualified names are allowed, while
IDs must be unique within one ProjectIR.

Use optional metadata for language-specific fields, preferably namespaced by
language, for example {"python": {"is_async": true, "method_kind":
"static_method"}}. Metadata accepts only JSON-compatible values: nested
objects with string keys, arrays, strings, numbers, booleans, and null. It
rejects arbitrary objects, cycles, non-string object keys, and non-finite
numbers. Do not put AST nodes, absolute project paths, or sensitive filesystem
data in metadata or diagnostic messages.

SourceLocationIR uses one-based line numbers and zero-based UTF-8 byte column
offsets; end coordinates are exclusive. Missing positions remain None.
Adapters with parser-native columns in another unit should normalize the values
or record the native unit in metadata. Parameter positions and import statement
and alias ordering are zero-based.

ImportIR represents syntax only. Its wildcard flag is preserved, and a
wildcard import cannot have a fabricated bound name. It contains no resolved
target. DependencyEdgeIR is a separate explicit relationship contract with
open dependency types, resolved, inferred, unresolved, and ambiguous statuses,
optional locations, JSON-safe evidence, and metadata. Unresolved and ambiguous
edges may omit a target. Imports do not automatically create dependency edges;
an empty project snapshot has no edges by default.

CallSiteIR records one syntactic invocation and is distinct from both a
declaration (`SymbolIR`) and a resolved relationship (`DependencyEdgeIR`). It
preserves language, normalized relative file path, optional source occurrence
order, callee expression and open callee kind, optional decomposed name path,
attribute and receiver, optional enclosing symbol ID and qualified name,
lexical scope, execution context, shared source location, argument counts and
keyword names, optional unpacking flags, and JSON metadata. Callee kinds and
language identifiers are open strings, so the schema can represent Python,
JavaScript, TypeScript, Java, C++, Go, Rust, and other languages without
parser-specific dependencies. A call may have no enclosing symbol, and a
supplied enclosing symbol ID is validated against ProjectIR symbols rather
than inferred from a name.

Call-site records have no target ID or resolution status. They remain
unresolved syntactic evidence until a future resolver establishes a target;
they never create dependency edges automatically. Calls are preserved as
separate ordered records even when their expressions are identical. The
optional occurrence order is source-order metadata, not a persistent call-site
identity. SourceLocationIR keeps one-based lines, zero-based UTF-8 byte
columns, and exclusive end coordinates. Per-language details can be preserved
in recursively frozen JSON metadata. ProjectIR serializes `call_sites` as an
ordered array, including an empty array when no call analysis is supplied.

Paths normalize backslashes to / without changing case. Absolute paths,
drive-qualified paths, and traversal are rejected. ProjectIR validates symbol,
import, and call-site file references, call-site enclosing symbol references,
project diagnostic paths, duplicate source paths, unique symbol IDs, and edge
symbol references. Duplicate symbol names remain valid. No model contains the
selected absolute project root.

Version 3 is independent of the Python runtime version: version 2 added the
serialized top-level `call_sites` collection, and version 3 added
`import_dependencies`. The current validator rejects version 1 and version 2
snapshots; no compatibility or database migration system is implemented.
Python `py:v1:` IDs remain project-scoped by usage rather than by an embedded
project namespace. Equivalent identity keys from different projects may therefore
produce identical IDs, and duplicate occurrence IDs can shift when identical
declarations are inserted or reordered.

## Python-to-IR adapter

`app.adapters.python.PythonIRAdapter` combines an existing `ScanResult` with
one `PythonFileAnalysis` for each discovered Python file. A successful parse
requires the already-produced function, class, import, call, qualified-name,
and symbol-identity results. The adapter maps those records without parsing
again or recalculating IDs. A successful call result may contain zero calls;
failed parses produce a `SourceFileIR` and parser diagnostics and must not
include AST extractor results.

The adapter validates normalized file paths and one-to-one declaration and
identity correspondence. It preserves scanner metadata, Python runtime and
encoding metadata, source locations, signatures, class syntax, imports, and
extractor warnings. Syntax-error columns remain in Python-specific metadata
because Python reports those offsets in a different unit from the IR's UTF-8
byte columns. Direct lexical parent IDs are assigned only when qualified scope
and source-span evidence identify one parent; unresolved parent links are
reported as diagnostics. Stable symbol IDs are copied unchanged.

The adapter produces no dependency edges. Imports and call sites remain syntax
records at this boundary. Separate same-file, module/import, and cross-file
Python call resolvers are available; none creates dependency edges.
Constructing `ProjectIR` invokes the existing IR validation for source-file
references, duplicate paths and IDs, and metadata. Invalid mapped IR data is
reported through an adapter validation exception.

```python
from app.adapters.python import PythonFileAnalysis, PythonIRAdapter

project_ir = PythonIRAdapter().adapt(
    scan_result,
    (
        PythonFileAnalysis(
            parse_result=parse_result,
            function_result=function_result,
            class_result=class_result,
            import_result=import_result,
            call_result=call_result,
            qualified_name_result=qualified_name_result,
            identity_result=identity_result,
        ),
    ),
)
```

### Python call-site mapping

The adapter maps every ordered `PythonCallSite` to `CallSiteIR`, preserving the
relative file path, occurrence order, callee expression and open kind, optional
name/attribute/receiver decomposition, source span, lexical scope, execution
context, argument count and keyword names, unpacking flags, and Python-specific
enclosing class/function names in JSON metadata. AST columns remain UTF-8 byte
offsets; missing coordinates remain missing. Repeated expressions remain
separate call-site records.

An optional enclosing symbol ID is assigned only when the call extractor's
lexical scope and execution context identify the expected declaration kind,
the file matches, and source-span containment leaves exactly one candidate.
Function and method body calls may refer to that function or method; class
body calls may refer to the class. Calls in decorators, defaults, annotations,
class bases, and class keywords use the enclosing definition scope reported by
the extractor. Module-level calls have no enclosing ID. Missing or ambiguous
non-module scope evidence leaves the ID empty and adds a warning in the
`python_call_mapping` category. This does not resolve the callee target.

Call extractor warnings become `DiagnosticIR` records in each source file's
`python_call_extractor` diagnostics, retaining code, message, relative path,
warning severity, and all available source coordinates. Duplicate diagnostics
are removed using the adapter's existing diagnostic deduplication. Successful
files always run call extraction, including files with no calls. Failed parses
skip all AST extractors, remain represented as source files, and do not receive
fabricated empty call results. Fatal call extractor and mapping errors retain
their pipeline stage and chained cause.

`PythonProjectAnalysisResult.call_summary` is derived from ProjectIR and file
diagnostics. It reports total call sites, counts by callee kind and execution
context, successfully analyzed files, and files with call-extractor
diagnostics. An unavailable execution context is grouped as `UNSPECIFIED`.
The Symbol Index continues to index declaration symbols only. Calls do not
populate `DependencyEdgeIR`; the edge collection stays empty until a future
resolver establishes target evidence.

## Unified Symbol Index

`app.symbols.SymbolIndex` builds immutable in-memory lookup indexes from a
validated `ProjectIR`. It retains the original `SymbolIR` and `SourceFileIR`
objects and their project order. Its indexes support symbol ID, original name,
qualified name, relative file path, open-string symbol kind, language, and
explicit parent symbol ID. File lookups include source files with no symbols
and files whose parsing failed.

```python
from app.symbols import SymbolIndex

symbol_index = SymbolIndex.from_project_ir(project_ir)
by_id = symbol_index.get_by_id("py:v1:...")
duplicates = symbol_index.find_by_name("process_payment")
matches = symbol_index.search(
    name="process_payment",
    language="python",
    kind="function",
    file_path="src/payment.py",
)
stats = symbol_index.stats
```

All lookups use exact matching by default. Name, qualified-name, path, kind,
and language lookups accept `case_sensitive=False`; symbol IDs and parent IDs
remain exact. Combined search applies supplied filters with AND semantics and
returns results in `ProjectIR.symbols` order. Calling `search()` with no
filters returns all indexed symbols in that order. Duplicate names and
qualified names remain separate results. A new index should be built for each
new project snapshot; the index exposes no update methods. Its statistics
include file and symbol totals, distinct names, languages, and counts by kind.

The index does not index imports or resolve references. It does not infer
parent links, calls, dependencies, or graph edges, and it has no persistence
layer.

## Python local call resolver

`app.analysis.PythonLocalCallResolver` is an explicit static-analysis
operation over a validated `ProjectIR`, the matching `SymbolIndex`, and
binding evidence produced independently for each relevant Python file:

```python
from app.analysis import PythonLocalCallResolver

result = PythonLocalCallResolver().resolve(
    project_ir=project_ir,
    symbol_index=symbol_index,
    binding_results=binding_results,
)
print(result.resolved_count, result.unresolved_count, result.ambiguous_count)
```

It returns immutable `CallResolutionResult`, `CallResolution`,
`CallSiteReference`, and `ResolutionDiagnostic` records. A reference uses the
relative file path and per-file occurrence order, plus source location when
available. Missing occurrence orders receive a deterministic snapshot-local
ordinal. These references are not persistent identities across source edits.
Results include resolved, unresolved, and ambiguous totals, counts by
resolution method, candidate IDs, evidence, diagnostics, and JSON-safe
`to_dict()` output. Resolution method strings include `LEXICAL_NAME`,
`SELF_METHOD`, `CLS_METHOD`, `CLASS_QUALIFIED_METHOD`, and
`CLASS_CONSTRUCTOR`.

The resolver requires exactly one complete binding result for each file with
Python call sites. Duplicate, unexpected, unparsed, or structurally
inconsistent results raise `InvalidBindingEvidenceError`. Additional results
are accepted only for successfully parsed Python files in the same ProjectIR.
The resolver verifies the index snapshot, scope and parent identities, source
spans, declaration records, and import evidence. It does not mutate ProjectIR,
SymbolIndex, or binding results; does not reread source files; and does not
reparse ASTs. The project pipeline supplies these successful per-file binding
results automatically after validating ProjectIR and constructing SymbolIndex.

Call-site scope matching combines relative file path, lexical path, scope
kind, parent relationships, enclosing Symbol ID, execution context, and
source containment. Source spans distinguish repeated same-name scopes.
Lambda and comprehension scopes are matched by synthetic paths and spans;
calls in a comprehension's outer iterable use the containing scope. An
ambiguous or unverified scope remains unresolved.

Direct name lookup is same-file only. Parameters and all function-local
assignment forms block outer declarations regardless of whether an assignment
appears before or after the call. Imports, deletes, loop and with targets,
exception targets, pattern captures, and comprehension targets also prevent
unsafe name-only resolution. Nested declarations are matched by scope,
declaration kind, original name, and source location. A declaration after a
call in its execution scope is unresolved; conditional declarations remain
unresolved with their candidate IDs. `global` routes lookup to module scope;
`nonlocal` searches enclosing function scopes and never falls through to a
module global. Wildcard imports remain uncertain and are not expanded.

Class namespaces are not searched from method bodies for bare names.
`self.method()` and `cls.method()` require a verified receiver and class;
receiver reassignment, shadowing class members, conditional definitions, and
duplicate methods are handled conservatively. Class-qualified methods and
constructors require an unshadowed local class binding. Inheritance and
imported aliases are not resolved.

Only multiple concrete declaration candidates produce `AMBIGUOUS`. Unknown
local values, imports, conditional declarations, unsupported binding forms,
and insufficient scope evidence produce `UNRESOLVED` with reason categories
such as `LOCAL_PARAMETER_SHADOWING`, `LOCAL_ASSIGNMENT_SHADOWING`,
`IMPORT_BINDING_SHADOWING`, `GLOBAL_BINDING`, `NONLOCAL_BINDING`,
`CONDITIONAL_BINDING`, `UNINITIALIZED_LOCAL`, and
`AMBIGUOUS_SCOPE_MATCH`. Evidence remains JSON-safe and records the scope,
binding kinds, directives, declaration IDs, and reason without source text or
absolute paths. This is conservative static analysis; it does not reproduce
complete Python compiler or runtime name-resolution behavior. Resolution is
returned separately from `CallSiteIR` and creates no dependency edges.

## Full Python project analysis pipeline

`app.analysis.PythonProjectAnalyzer` connects the existing components into a
reusable, Flask-independent project workflow. It scans Python `.py` and `.pyi`
files, parses each file once, runs function, class, import, call, and binding
extractors for successful parses (binding extraction reuses the parse and
import results), generates qualified names and stable IDs, creates one adapter
bundle per discovered file, maps and explicitly validates `ProjectIR`, builds
`SymbolIndex` from that snapshot, then runs the local call resolver, module
resolver, cross-file resolver, and effective result aggregator against the
same validated snapshot. No stage reparses or executes project code.

```python
from app.analysis import PythonProjectAnalyzer

result = PythonProjectAnalyzer().analyze("path/to/project")
project_ir = result.project_ir
symbol_index = result.symbol_index
resolutions = result.call_resolution
imports = result.import_resolution
cross_file = result.cross_file_call_resolution
effective = result.effective_call_resolution
print(result.status, result.parsing_summary, result.index_summary)
print(effective.summary)
```

`PythonProjectAnalysisResult` is immutable and provides scan, parsing,
declaration/import/call/binding extraction, local/import/cross-file/effective
resolution, IR, index, and processing summaries, along with per-file binding
evidence, the separate raw resolution results, diagnostics, and validated
ProjectIR and SymbolIndex. Import counts derive from
`PythonModuleResolutionResult`; local counts remain in `call_resolution`;
cross-file counts remain in `cross_file_resolution`; final counts and records
are in `effective_call_resolution`.
The stage order is scan, per-file parse, function extraction, class extraction,
import extraction, call extraction, binding extraction, qualified-name and
stable-ID generation, IR mapping, explicit IR validation, index construction,
local call resolution, module/import resolution, cross-file call resolution,
final result aggregation, dependency-edge construction, dependency-edge
integration, and final ProjectIR validation. Timings are
measured at runtime using a monotonic clock; per-file stage values are
aggregated.

Syntax, encoding, and file-read failures returned by the parser remain in
`ProjectIR.source_files` with diagnostics; processing continues for other
files, and AST extractors are not called for those failed files. Scanner warnings
are retained in the scan summary and IR diagnostics. A valid result is marked
`completed_with_warnings` if it contains IR/scanner diagnostics, binding
warnings, module/import or cross-file resolver diagnostics, or a failed file
parse; unresolved calls without diagnostics do not automatically change
status. Otherwise it is `completed`. A fatal stage failure raises
`PythonProjectAnalysisError` with the failing stage, optional relative file
path, `failed` status, and the original exception chained as its cause. A
failed analysis does not return a partial result.

Fatal resolver and invariant failures report `local_call_resolution`,
`module_resolution`, `cross_file_call_resolution`,
`final_result_aggregation`, `dependency_edge_construction`,
`dependency_edge_integration`, or `final_ir_validation` as the stage. Unresolved/ambiguous imports and
calls remain valid result records. Existing module and cross-file resolver
diagnostics contribute to `completed_with_warnings`; an unresolved call alone
does not. `result.diagnostics` retains ProjectIR diagnostics, while
`result.analysis_diagnostics` also includes module, cross-file, and edge-builder
diagnostics. Missing callers are informational skips; invalid target evidence
is reported as an error diagnostic and does not produce an edge.

The analyzer does not import project modules, execute source, evaluate
decorators, annotations or defaults, write into the scanned project, infer
unsupported dependency edges, or create graph edges. Local and cross-file call
resolution and edge construction do not modify source files or SymbolIndex.
The builder appends supported call edges to a new final ProjectIR. Local resolved
and ambiguous results take precedence; cross-file results refine only eligible
local unresolved calls. Unsupported dynamic calls, wildcard expansion,
instance-method dispatch, inheritance, and arbitrary re-exports remain
unresolved.
Scanner and parser path
checks reject symbolic links and Windows junctions. IDs and logical IR output
are repeatable for unchanged inputs and configuration; timestamps and measured
durations are result metadata and do not affect IDs.

The backend integration suite verifies multi-file call mapping and summaries,
empty-call and failed-parse files, duplicate declarations, warning propagation,
partial parser failures, static read-only behavior, deterministic logical
results, and injected failures at pipeline boundaries.
Measured synthetic-project benchmark results and their methodology are in
[`docs/testing/foundation.md`](../docs/testing/foundation.md). These local
measurements are not production-scale performance claims.

The project analyzer returns a final validated ProjectIR, its in-memory Symbol
Index, per-file binding evidence, raw module/import, local, and cross-file call
resolution results, one effective immutable call record per CallSiteIR, and
the immutable `DependencyEdgeBuildResult`. Local results remain unchanged;
provenance identifies local or cross-file resolution. Only supported resolved
Python calls generate `function_call` edges from enclosing function/method
symbols to the unique target; repeated call sites remain separate edges with
their own location and occurrence evidence. Existing ProjectIR edges are
preserved. ProjectIR is schema version 3 and stores resolved file-level import
relationships separately from symbol-level call dependencies. The standalone
GraphBuilder and GraphQueryEngine support graph construction and direct
read-only queries. The Flask analysis API submits a local project, returns
analysis metadata, serializes the graph, exposes direct graph-query endpoints,
and deletes process-local sessions. Dynamic dispatch, inheritance, and persistent
storage remain out of scope; multi-hop call tracing is implemented in the
frontend workspace over the loaded graph. Automated backend regression coverage
for Phases 2.5A–2.9B and 3.4A is documented in [`docs/testing/foundation.md`](../docs/testing/foundation.md).

## Python cross-file call resolution

`app.analysis.PythonCrossFileCallResolver` takes the same project snapshot,
local call results, a `PythonModuleResolutionResult`, and the pipeline's
binding results. It validates import records against exact ImportIR ordering
and syntax fields, then uses local resolver binding evidence and scope records
to associate an unresolved call with its import declaration. It resolves
uniquely evidenced direct imported functions/classes and top-level functions
or classes called through module aliases or explicitly imported submodules.
Relative imports use the module resolver's already validated module
candidates. Ambiguous and unsupported cases remain explicit; wildcard imports,
instance methods, dynamic calls, and inferred re-exports are not expanded.

```python
from app.analysis import PythonCrossFileCallResolver

cross_file = PythonCrossFileCallResolver().resolve(
    project_ir=analysis.project_ir,
    symbol_index=analysis.symbol_index,
    local_resolution=analysis.call_resolution,
    module_resolution=module_resolution,
    binding_results=analysis.binding_results,
)
print(cross_file.summary, cross_file.counts_by_resolution_method)
```

The immutable result has per-call references, status, target or candidates,
source/target paths, import evidence, diagnostics, and JSON-safe `to_dict()`
serialization. Locally resolved or ambiguous call results are not reconsidered.
The project analyzer invokes this operation automatically; it does not change
the inputs, add a Flask endpoint, or construct DependencyEdgeIR.

## Python call dependency-edge construction

`DependencyEdgeBuilder` runs after effective call resolution. Its immutable
`DependencyEdgeBuildResult` exposes ordered edges, diagnostics, and statistics
for considered calls, resolved calls, generated edges, and skip categories.
Each edge uses `source_symbol_id` for the enclosing function/method caller and
`target_symbol_id` for the uniquely resolved callee, with dependency type
`function_call` and resolved status. The exact CallSiteReference, resolution
method, and local/cross-file provenance are retained in JSON-safe edge evidence
and metadata. Repeated calls between a caller and target remain separate
edges.

Only Python NAME/ATTRIBUTE calls with an exact ProjectIR/index caller and one
verified target are eligible. Class targets require explicit constructor
resolution. Unresolved and ambiguous calls are counted and skipped; calls
without an enclosing function/method, unsupported call shapes, and invalid
target evidence do not create edges. The builder does not infer relationships
from names/scopes, rerun resolution, execute code, or mutate its ProjectIR or
SymbolIndex input. The analyzer appends new call edges to a new ProjectIR while
preserving existing edges, then validates the final snapshot. Automated tests
for this builder are included in the Phase 3.6A backend test suite.
