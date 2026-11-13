# Architecture Overview

**Status:** Project scanning, Python AST parsing and extraction (including call sites and scope-binding evidence), qualified names and stable IDs, version 3 Unified IR, the Python-to-IR adapter, validation, the in-memory Symbol Index, pipeline-integrated call/import resolution, call/import dependency construction, the foundational NetworkX Graph Engine, direct graph queries, initial interactive Cytoscape.js 2D visualization, Function Search, bounded static Function Trace, and the Function Inspector are implemented. 3D visualization remains planned.

CodeAtlas turns a source project into a normalized representation, analyzes relationships in that representation, and presents the resulting graph. This document describes current behavior where implemented and planned responsibilities and data flow; it does not define external API contracts.

## Components

### Python Cross-File Call Resolver

`PythonCrossFileCallResolver` is an explicit analysis operation over one
validated ProjectIR/SymbolIndex snapshot, local call-resolution results,
Python module/import-resolution results, and Python scope-binding evidence.
It rechecks exact import references against `ImportIR`, then considers only
locally unresolved calls whose binding evidence identifies a matching import
binding. Supported shapes are direct imported top-level functions/classes,
module-qualified top-level functions/classes, and explicitly imported
submodule attributes. Relative imports use the module resolver's candidates.
It preserves ambiguity and returns evidence, diagnostics, method counts, and
source/target file summaries in separate immutable records. It does not alter
local results or ProjectIR, create dependency edges, import/execute project
code, expand wildcard imports, or infer instance methods.

### Project Scanner

Finds project source files for analysis and provides them to the subsequent language-aware stages. The initial implementation discovers Python `.py` and `.pyi` files and returns relative paths, names, extensions, byte sizes, language labels, and source/stub categories. It does not read file contents or parse code.

The scanner resolves and validates the selected root, sorts results deterministically, and keeps absolute paths out of serialized scan results. It skips symbolic links and Windows junctions without inspecting their targets and does not traverse them. Link warnings are recorded separately from the skipped-directory count because the scanner deliberately does not follow a link to classify its target. Recoverable child-directory and file errors are returned as warnings with project-relative paths; errors opening the root are raised explicitly.

Default excluded directory names are `.git`, `.venv`, `venv`, `env`, `node_modules`, `__pycache__`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache`, `.tox`, `.nox`, `dist`, `build`, `coverage`, `htmlcov`, `.idea`, and `.vscode`. Names are matched case-insensitively at any depth. Additional directory names or exact project-relative directory paths can be supplied to `ProjectScanner`; wildcard matching and Git ignore compatibility are not provided. Future adapters may provide additional file-extension registrations, but only Python file types are registered by default.

### Python AST Parser

The Python parser consumes one scanner `DiscoveredFile` and the selected project root at a time. It accepts `.py` and `.pyi` files, resolves the project-relative path, rejects traversal and symbolic links or junctions, and checks that the resolved source remains within the selected root. Source is read only when parsing is requested, using Python's `tokenize.open()` encoding detection, then passed to `ast.parse()` with type-comment parsing enabled. It returns a typed result with the genuine `ast.Module`, runtime version, detected encoding, and source line count. Results distinguish parsed, syntax-error, read-error, and encoding-error outcomes; diagnostics contain relative paths and syntax locations without source text or absolute paths. The JSON-safe result representation excludes the raw AST. Parsing is static analysis only: source modules are not imported or executed.

### Python Function and Method Extractor

Consumes one successful `PythonParseResult` and traverses its existing AST without rereading the source. It records module-level functions, methods directly declared in class scopes, and nested functions, including async declarations. Class definitions establish lexical context but are not emitted as separate records. Each declaration includes its lexical scope path, nearest enclosing class where applicable, signature parameters and safe textual annotation/default representations, return annotation, decorators, source locations, and AST-derived docstring. Recognized method decorators distinguish instance, static, class, property, property setter, and property deleter kinds; unknown decorators are retained as text without inferring their behavior. Results preserve AST traversal order, keep duplicate declarations, provide category counts and JSON-safe serialization, and do not expose AST objects. Invalid or unsuccessful parse results raise explicit extraction exceptions. The extractor neither executes nor evaluates source constructs. Stable IDs are assigned by the separate Python Stable Symbol ID Generator.

### Python Class Extractor

Consumes a successful `PythonParseResult` independently of the function extractor and walks its AST without rereading source. It records module-level and nested class declarations in AST order, preserving duplicate names and lexical scope paths. Each record includes its nearest parent scope and path, enclosing class and function/method names, ordered base expressions, ordered class keyword arguments (including unpacked keywords), decorators, AST-derived docstring, and source positions. AST expressions are represented with `ast.unparse()`; bases, keyword values, and decorators are not evaluated. Results distinguish top-level and nested classes and count classes declared within functions or methods; serialization excludes AST objects. The extractor reports declaration metadata only; it does not resolve inheritance or create inheritance edges.

### Python Import Extractor

Consumes a successful `PythonParseResult` independently of the other extractors and inspects only `ast.Import` and `ast.ImportFrom` nodes. It emits one record for each imported alias, preserving statement and alias order, statement locations, relative import levels, wildcard status, and lexical class/function/method scope. Records distinguish module names, imported names, aliases, and Python's syntactic bound name; for example, an unaliased `import package.module` binds `package`, while `from package import item` binds `item`. Wildcard imports have no individual bound name. `__future__` statements are flagged. Counts distinguish statements from aliases, and category counts are statement counts except the wildcard count, which counts wildcard alias records. Conditional, loop, try/except, and context-manager bodies are traversed without choosing runtime branches. Dynamic import calls are not treated as import statements. The extractor does not import or resolve modules; package discovery, target resolution, and dependency analysis remain planned.

### Python Scope Binding Evidence Extractor

`app.extractors.python.PythonBindingExtractor` is a standalone static-analysis
component. It consumes a successful `PythonParseResult` and the matching
`ImportExtractionResult`; import names and alias semantics come from the
existing import extractor. It returns immutable binding records, scope
summaries, directive records, counts, and structured warnings. Scope
summaries carry source spans for named, lambda, and comprehension scopes;
comprehension summaries also retain the outer iterable span so calls evaluated
there remain in their containing scope. The resolver accepts this result
explicitly. `PythonProjectAnalyzer` invokes the extractor once per successful
file parse and retains the result for local call resolution.

Binding kinds include parameters, ordinary/annotated/augmented assignments,
named expressions, imports, function/class declarations, loop and with
targets, exception targets, pattern captures, comprehension targets, and
deletions. Their values are open strings. Records preserve normalized relative
paths, AST source coordinates, deterministic traversal order, execution
context, branch labels, and JSON-safe metadata. Unaliased imports use Python's
syntactic bound name. A wildcard import is retained as an uncertain `*`
evidence record and is omitted from the summary's concrete `bound_names`.

Named function and class scope paths follow the existing name-component
convention. Lambdas and each comprehension receive synthetic path components;
every scope also has a deterministic per-result `lexical_scope_id`, so
repeated same-name definitions remain separate summaries even when their
human-readable paths match. Conditional branches remain flow metadata rather
than lexical scopes. Assignment and parameter records are collected across a
whole function scope, regardless of source order, which provides shadowing
evidence but does not itself resolve calls. `global` and `nonlocal` directives
retain names, locations, and declaration order without resolving their target
environment.

Exception targets record `cleared_after_handler` because Python clears the
target when its handler exits. Structural pattern captures are supported for
the active runtime's `MatchAs`, `MatchStar`, `MatchMapping`, `MatchSequence`,
`MatchClass`, and `MatchOr` nodes; value patterns do not create bindings.
Comprehension targets belong to distinct comprehension scopes, and the
outermost iterable is visited in its containing scope. The component does not
execute or evaluate source.

This is evidence extraction, not complete Python name resolution. It does not
model type-parameter scopes, runtime branch outcomes, or the lifetime of
assigned values. Synthetic scope IDs are local to one extraction result. The
project pipeline retains each successful file's binding result and reuses it
in local and cross-file call resolution.

### Python Call Extractor

`app.extractors.python.PythonCallExtractor` independently consumes a successful
`PythonParseResult` and walks its existing AST. It emits one ordered call-site
record for every `ast.Call`, including direct names, attribute calls, call
results, subscript callables, constructors, built-ins, dynamic/reflection
calls, and nested calls in callees or arguments. It does not reread or reparse
source, import project modules, execute expressions, assign caller IDs, or
resolve call targets.

Each record preserves the relative file path, safe `ast.unparse()` callee text,
an open-string callee kind (`NAME`, `ATTRIBUTE`, `CALL_RESULT`, `SUBSCRIPT`, or
`OTHER`), a decomposed name path only for Name/Attribute chains, the attribute
name and receiver text where applicable, AST source coordinates, lexical
context, execution context, argument shape, and deterministic occurrence
order. AST line values remain one-based; `col_offset` and `end_col_offset` are
kept as Python's UTF-8 byte offsets, and end positions remain exclusive. An
unsupported callee representation produces a structured warning and keeps
the call record with a null callee expression.

The lexical path contains only named enclosing classes and functions or
methods. Calls in function bodies use the function's name in that path, while
function decorators, defaults, and annotations use the scope where the
function is defined. Class bases and class keywords likewise retain the
enclosing scope; class bodies add the class name. Calls in lambda bodies use
`LAMBDA_BODY` without fabricating a named scope component. Execution context
values are `MODULE_BODY`, `CLASS_BODY`, `FUNCTION_BODY`, `DECORATOR`,
`DEFAULT_ARGUMENT`, `ANNOTATION`, `CLASS_BASE`, `CLASS_KEYWORD`,
`LAMBDA_BODY`, and `OTHER`.

Argument metadata counts the syntactic entries in `ast.Call.args` (a starred
expression counts as one), preserves keyword names in order with `None` for
`**kwargs`, and separately flags starred positional and keyword unpacking.
The result exposes total, direct-name, attribute, and other call counts plus
counts by observed execution context; repeated identical call expressions are
retained as separate records in deterministic AST traversal order. Calls in
comprehensions keep the enclosing named scope.
This output is syntactic evidence only: a callee expression such as
`payment.process` does not establish which declaration it refers to. The
pipeline passes mapped calls and retained binding evidence to local and
cross-file resolution after ProjectIR validation and SymbolIndex construction.
Resolution results remain separate from CallSiteIR. A later pipeline stage
builds explicit DependencyEdgeIR records from supported effective resolutions.

### Python Qualified Name Generator

Consumes completed function and class extraction results for the same relative file. Both extractors provide full lexical scope paths that include the declaration itself; the generator normalizes them into dot-separated names such as `Payment.process.validate`. It merges the two result sets by source position and assigns a deterministic `source_order` within the output. The relative file path is kept separately and is not converted into an importable module name. Duplicate declarations retain their qualified names and source positions as separate records; the generator does not add suffixes or generate stable IDs. Different input file paths, incomplete extraction results, or inconsistent scope metadata raise explicit errors. The separate Python Stable Symbol ID Generator consumes this result because qualified names alone are not globally unique.

### Python Stable Symbol ID Generator

Consumes a completed `QualifiedNameGenerationResult`; it does not parse source or rediscover declarations. Each supported function, method, nested function, and class receives a `py:v1:<sha256-hex>` identifier. The SHA-256 input is compact UTF-8 JSON with sorted keys containing `schema_version`, `language` (`python`), normalized project-relative `file_path`, uppercase `declaration_type`, `qualified_name`, and `occurrence_index`. Source code, line numbers, columns, comments, formatting, and declaration bodies are excluded.

Path separators are normalized to `/`; absolute paths, drive-qualified paths, empty components, and `.`/`..` traversal components are rejected. The selected project root and machine-specific absolute paths are not part of identity. IDs are intended for use within a CodeAtlas project but contain no explicit project namespace; equivalent identity keys in different projects therefore produce the same ID and the IDs are not guaranteed globally unique.

Occurrence indexes start at zero independently for each normalized file path, declaration type, and qualified name, in the incoming source order. The result preserves that order, includes source locations and extractor provenance, and is JSON-safe. Generated IDs are checked for duplicates and an explicit collision error is raised. An unchanged declaration keeps its ID when only source location or non-identity source contents change. Because duplicate identity uses occurrence order, inserting or reordering identical declarations can change their indexes and IDs; this is not persistent identity across arbitrary refactoring. A future persistent project namespace or symbol database is outside this phase.

### Language Detection

Determines which supported language applies to a source file so it can be sent to an appropriate parser. The detection rules and behavior for ambiguous files remain to be designed.

### Parser Registry

Connects detected languages to their corresponding language adapters and selects an adapter for a file. The registry interface and adapter registration mechanism are not specified yet.

### Language Adapters

Parse source code for an individual language and translate recognized code structure into the shared representation. Python AST parsing, function, method, class, import, and call-site extraction, qualified name generation, stable symbol IDs, and mapping Python declarations, imports, and calls into the IR are implemented. Other language adapters are planned for later phases.

### Python-to-IR Adapter

`backend/app/adapters/python/` consumes one scanner result and one `PythonFileAnalysis` for each discovered Python file. Successful parse records must be accompanied by the existing function, class, import, qualified-name, and identity results; the adapter does not reread or reparse source. It validates normalized file paths and matches declarations using declaration kind, extractor provenance, lexical scope, and source positions. It then verifies identity records by explicit source order and their full metadata, preserving each supplied symbol ID unchanged.

The adapter maps scanner file metadata, parser status, runtime and encoding, declarations, signatures, decorators, class syntax, import aliases, call sites, and stage diagnostics. A successful `PythonFileAnalysis` must include a `PythonCallExtractionResult`, including when it contains zero calls; failed parses must not include call extraction output. Call warnings become file diagnostics in the `python_call_extractor` category. Failed parses remain as source-file records with parser diagnostics. AST and absolute project paths are excluded. Python AST columns already use UTF-8 byte offsets; syntax diagnostics retain their parser-native one-based character columns in Python-specific metadata and leave the IR column unset. Direct lexical parent IDs require matching scope and an unambiguous containing source span; otherwise a diagnostic records the missing or ambiguous evidence.

For a call site's optional enclosing symbol ID, the adapter uses the call extractor's lexical scope and execution context, the matching declaration kind, the normalized file path, and source-span containment. Body calls can link to the containing function, method, or class. Calls in decorators, defaults, annotations, class bases, and class keywords use the enclosing scope reported by the extractor rather than the declaration being defined. A unique candidate is required; missing or ambiguous evidence leaves the ID unset and records a `python_call_mapping` warning. This does not resolve the callee target.

This adapter does not resolve imported modules or callee targets, infer function dependencies, build an index, or create dependency edges. The graph and resolution stages remain separate.

### Unified Intermediate Representation

The version 3 core schema and typed models are implemented in `backend/app/ir/`. The IR package uses no Flask, SQLite, or language-parser dependencies. `ProjectIR` is an ordered project snapshot containing `SourceFileIR`, `SymbolIR`, `ImportIR`, syntactic `CallSiteIR` records, symbol-to-symbol `DependencyEdgeIR` call relationships, file-to-file `ImportDependencyIR` relationships, `DiagnosticIR` records, and JSON-compatible analysis metadata. Import dependency direction is the importing source file to the resolved imported target file. Both endpoints must be source files in the same ProjectIR. An empty project is valid; collections preserve their input order.

`SourceFileIR` preserves normalized relative paths and the scanner's file name, extension, language, byte size, and source/stub category, together with generic parse status, optional source line count, diagnostics, and metadata. `SymbolIR` stores the existing symbol ID as supplied, an open-string language and symbol kind, declaration names, source file and location, lexical scope, optional parent ID, ordered parameters, return type, decorators, documentation, and language metadata. Symbol kinds are not a closed Python enum; declarations such as functions, methods, interfaces, structs, constructors, and language-specific kinds use the same model. `ParameterIR` and `SourceLocationIR` provide shared parameter and span structures.

`ImportIR` represents syntactic imports only. It preserves an optional bound name, alias, wildcard flag, relative level, lexical scope, statement/alias ordering, location, and metadata; it does not imply a resolved module and never creates an edge. `CallSiteIR` represents one syntactic call occurrence, not a declaration or a resolved relationship. It preserves an open-string language and callee kind, relative file path, optional occurrence order, callee expression and decomposed name/attribute/receiver data, optional enclosing symbol ID and qualified name, lexical scope, execution context, `SourceLocationIR`, optional positional argument count, ordered keyword argument names (including `None` for an unnamed unpacking item), unpacking flags, and JSON metadata. Optional fields remain absent when an adapter cannot safely supply them. Duplicate expressions remain separate records, and occurrence order is source ordering rather than persistent identity.

`CallSiteIR` has no target symbol ID or resolution status. When `enclosing_symbol_id` is supplied, project validation requires that it reference a symbol in the same snapshot; it is assigned only when lexical, declaration-kind, file, execution-context, and source-span evidence identifies one declaration. Its file path must reference an included source file. It reuses the shared source-location convention: one-based lines, zero-based UTF-8 byte columns, and exclusive end coordinates. Call sites remain syntactic evidence; same-file and cross-file resolution are returned separately by the project pipeline. `DependencyEdgeIR` is a separate relationship contract with open-string dependency types, resolved/inferred/unresolved/ambiguous status, optional target and locations, evidence, and metadata. The pipeline's separate edge builder creates resolved call relationships without modifying CallSiteIR. `DiagnosticIR` provides common codes, categories, severity, message, optional relative file/location, and metadata. The Python adapter itself does not create dependency edges.

Language-specific details belong in JSON-compatible metadata, preferably under a language key such as `python`. Python adapters can preserve async state, method kinds, decorator expressions and locations, class bases and keyword arguments, and parameter conventions there without embedding AST objects. Unknown metadata keys are retained. Metadata is validated and recursively frozen by the models; unsupported objects, non-string object keys, non-finite numbers, and cycles are rejected. Optional values serialize as JSON `null`, and ordered tuples serialize as arrays.

Paths normalize Windows separators to `/` while preserving case. Absolute, drive-qualified, and traversing paths are rejected. Project snapshots do not have an absolute root field. Validation requires symbol, import, and call-site paths to reference included source files, checks call-site enclosing symbol IDs and project diagnostic paths, and rejects repeated source paths or symbol IDs; duplicate symbol names and qualified names are allowed. Dependency source IDs must exist in the snapshot; any supplied target ID must exist, and a resolved edge requires a target. Unresolved and ambiguous edges can omit a target. No edges are inferred from imports, declarations, or calls.

The current IR schema version is `3`, independent of the Python runtime version. Version 2 added `call_sites`; version 3 adds `import_dependencies`. Earlier serialized snapshots are not accepted by the current model validator, and no compatibility migration or database migration system is implemented. Existing `py:v1:` Symbol IDs are preserved unchanged. They are not globally unique across projects, and occurrence-based duplicate IDs may shift when identical declarations are inserted or reordered. The IR does not add a project namespace.

`ImportDependencyBuilder` consumes the exact Python `ImportIR` sequence and its corresponding `PythonModuleResolutionResult`; it does not rerun resolution or infer targets by name. It emits a dependency only when module and any imported-name resolution are resolved and a unique concrete target file exists in ProjectIR. Module imports, direct function/class imports, and resolved submodule imports use the resolver's candidates; aliases, relative level, source location, order, and resolver evidence are retained. `.py` and `.pyi` candidates are supported when the resolver identifies one. Namespace-only candidates have no source file and therefore do not form file-level dependencies. Unresolved, ambiguous, wildcard, unlocated, inconsistent, and unsupported re-export cases produce no dependency. Separate import aliases/statements remain separate records even when they point to the same file. Call edges remain `DependencyEdgeIR` records and imports remain `ImportDependencyIR` records for later Graph Engine consumption.

Language adapters map each supported language's parsed and extracted records into these shared models. Python-to-IR mapping is implemented; adapters for other languages remain planned.

### Symbol Index

`backend/app/symbols/` builds an immutable, in-memory index from a validated `ProjectIR`. It retains original IR record objects and canonical project order. Exact lookup indexes cover symbol ID, original name, qualified name, normalized relative file path, open-string symbol kind, language, and stored parent ID. The source-file lookup includes files with no symbols and files with failed parsing.

Duplicate names and qualified names are preserved in ordered result tuples. Combined search applies supplied filters with AND semantics, defaults to case-sensitive exact matching, and returns results in `ProjectIR.symbols` order. Case-insensitive matching is available for names, qualified names, paths, kinds, and languages; symbol and parent IDs remain exact. A search without filters returns all symbols in project order. Read-only statistics report file and symbol totals, unique names, language count, and counts by kind.

The index represents one project snapshot and is rebuilt for new project data. It does not index imports, resolve references, infer parent relationships, or create dependency edges. It has no persistence layer.

### Python Project Analyzer

`app.analysis.PythonProjectAnalyzer` composes the implemented Python
components into one project-level workflow without depending on Flask request
state. Its order is scanner, one Python parse per discovered file, function
extraction, class extraction, import extraction, call extraction, binding
extraction using the same parse and import results, qualified-name generation,
stable symbol ID generation, Python-to-IR mapping, explicit
`validate_project_ir` validation, `SymbolIndex` construction, local call
resolution using successful binding results, module/import resolution, and
cross-file call resolution followed by effective result aggregation. It uses the
public interfaces of those components and does not duplicate their parsing,
extraction, mapping, or index logic. The current analyzer directly selects
the Python components; language detection and a parser registry remain planned.

The immutable result contains the validated ProjectIR, the index built from
that same snapshot, ordered binding results, and a separate
`CallResolutionResult`, plus scan/parsing/extraction/call-site/binding/
resolution/IR summaries, index statistics, diagnostics, an overall status, and
measured processing statistics. Binding results are produced once for each
successfully parsed file, including files without calls or bindings. The
resolver produces exactly one ordered record per CallSiteIR. Repeated
analysis of unchanged input and configuration produces equivalent logical IR
and index results. Timestamps and durations are per-run metadata and are not
inputs to symbol identity.

The parser's syntax, encoding, and file-read result statuses are recoverable:
each failed file remains a `SourceFileIR` with its parser diagnostic, other
files continue, and AST extractors including the call extractor run only for
successful parses. Scanner warnings
are retained in the scan summary and mapped diagnostics. Binding extractor
warnings are retained in binding results and the binding summary. Import and
cross-file diagnostics also contribute to warning status; unresolved calls
alone do not. A result is `completed_with_warnings` when IR/scanner, binding,
module/import, or cross-file diagnostics exist, or parsing had recoverable
file failures. A fatal scanner, parser-contract, extractor, mapping,
validation, index, module-resolution, cross-file-resolution, or aggregation failure raises a stage-aware
`PythonProjectAnalysisError` with the cause chained and status `failed`; the
pipeline does not turn an internal failure into an empty result.

The pipeline is read-only static analysis. It does not import discovered
modules or execute source constructs, and the scanner and parser skip links and
reject paths outside the project root. It records calls as syntactic evidence,
runs binding-aware same-file resolution, resolves imports against the project
module inventory, then uses verified import and binding evidence for
conservative cross-file call resolution. The effective aggregate gives local
resolved and ambiguous results precedence and refines only unresolved local
calls. Raw local, import, and cross-file results remain separately available.
Each CallSiteIR receives one effective result; none of these stages mutates
ProjectIR or SymbolIndex or creates dependency edges. Resolver and aggregation
failures raise stage-aware pipeline errors; parse result failures remain
recoverable file records.

### Python Local Call Resolver

`app.analysis.PythonLocalCallResolver` accepts a validated `ProjectIR`, the
`SymbolIndex` built from the same symbol and source-file projection, and an
ordered collection of `PythonBindingAnalysisResult` values:

```python
from app.analysis import PythonLocalCallResolver

resolution = PythonLocalCallResolver().resolve(
    project_ir=result.project_ir,
    symbol_index=result.symbol_index,
    binding_results=binding_results,
)
```

Supply exactly one completed binding result for every file containing Python
call sites. A result for another file, a duplicate result, an unsuccessful or
unparsed file, or inconsistent scope/import evidence is rejected explicitly.
Additional results are accepted only for successfully parsed Python files
present in the same ProjectIR. The resolver does not reread files or reparse
ASTs. The explicit resolver API remains independent of Flask; the project
pipeline calls it after building the matching SymbolIndex.

The resolver returns a separate `CallResolutionResult`; it does not add target
IDs or statuses to `CallSiteIR`, mutate the IR or index, or create
`DependencyEdgeIR`. The result contains one record per call site, derived
resolved/unresolved/ambiguous totals, method counts, diagnostics, evidence, and
JSON-safe serialization. Each `CallResolution` carries a `CallSiteReference`
identified by relative file path and per-file occurrence order, with the source
location included when available. If a call lacks occurrence order, the
resolver assigns a deterministic snapshot-local order from the ProjectIR
sequence. Neither form is a persistent identity after source edits.
Resolution method strings are `LEXICAL_NAME`, `SELF_METHOD`, `CLS_METHOD`,
`CLASS_QUALIFIED_METHOD`, and `CLASS_CONSTRUCTOR` when the corresponding
evidence is satisfied.

Resolution is restricted to declarations in the exact same normalized file.
The resolver verifies the binding result, scope IDs and parent IDs, source
locations, declaration records, and import evidence against ProjectIR. It
maps named binding scopes to symbols by file, scope kind, lexical path,
parent relationship, and source containment. Call-site location and
execution context distinguish repeated same-name scopes. Lambda and
comprehension scopes are matched by their synthetic paths and source spans;
the comprehension's outer iterable is treated as part of its containing
scope. Missing or multiply matched scope evidence remains unresolved.

For direct name calls, the resolver walks verified function and module scopes
and considers function, nested-function, and class declaration bindings. A
parameter, assignment, deletion, import, loop target, with target, exception
target, pattern capture, or comprehension target prevents fallback to an
outer declaration. Function-local bindings are considered across the whole
scope, including assignments textually after a call. A later nested
declaration remains unresolved as `UNINITIALIZED_LOCAL`; module and class
body calls use source order for declarations and imports evaluated in those
bodies. Duplicate concrete declarations remain `AMBIGUOUS`; unknown local
values and conditional declarations remain `UNRESOLVED` with their candidate
IDs and blocking evidence.

`global` directives route lookup to the module scope; local writes declared
global remain unresolved. `nonlocal` directives search enclosing function
scopes and do not fall through to module globals. Conditional declarations,
wildcard imports, and missing or conflicting directive evidence remain
unresolved. Imports only block unsafe local matching: the resolver does not
follow modules, resolve imported targets, or infer cross-file calls.

Class namespaces are not searched from method bodies for ordinary bare-name
calls. `self.method()` and `cls.method()` are considered only inside a
verified current method whose method kind and first parameter match the
receiver convention. Reassignment of the receiver name, imports or
assignments that shadow the class member, and conditional member declarations
prevent resolution. The target must be a unique method directly declared in
that method's verified class; inheritance is not searched.

A class-qualified call such as `Payment.calculate()` requires a local,
unambiguous class binding and an unshadowed direct method declaration.
Dotted nested class paths are followed only through verified class-parent
links and binding records. A bare local class call, including a supported
nested class call, resolves to the class declaration itself when its binding
is safe. The resolver does not resolve imported class aliases or constructor
implementations.

Ambiguity is reserved for multiple concrete declaration candidates.
Unresolved diagnostics use reason categories including
`LOCAL_PARAMETER_SHADOWING`, `LOCAL_ASSIGNMENT_SHADOWING`,
`IMPORT_BINDING_SHADOWING`, `GLOBAL_BINDING`, `NONLOCAL_BINDING`,
`CONDITIONAL_BINDING`, `LOOP_BINDING`, `EXCEPTION_BINDING`,
`PATTERN_BINDING`, `COMPREHENSION_SCOPE`, `AMBIGUOUS_SCOPE_MATCH`,
`UNINITIALIZED_LOCAL`, `WILDCARD_IMPORT_UNCERTAINTY`,
`INSUFFICIENT_BINDING_EVIDENCE`, `DYNAMIC_CALL`, `UNKNOWN_RECEIVER_TYPE`,
`NO_LOCAL_DECLARATION`, and `BUILTIN_OR_EXTERNAL`. Evidence identifies the
matched scope, binding kinds, declaration, directive, receiver context, and
candidate IDs without including source text or absolute paths. Repeated
resolution is deterministic and leaves ProjectIR, SymbolIndex, and binding
results unchanged.

This resolver does not follow import targets or module attributes, inspect
other files, infer runtime types, resolve inheritance or `super()`, or handle
dynamic dispatch and calls through returned values or subscripts. It does not
reproduce the complete Python compiler symbol table or runtime evaluation of
branches, exception-target cleanup, module execution order, or class namespace
semantics. Type-parameter scopes are not represented. It is conservative
static analysis, not a complete model of Python runtime name resolution.

### Python Module and Import Resolver

`app.analysis.PythonModuleResolver` consumes one validated `ProjectIR`, its
matching `SymbolIndex`, and optional `PythonModuleResolutionConfig`. It builds
an in-memory inventory from Python `.py` and `.pyi` `SourceFileIR` paths,
including parse-failed files (which identify module paths but provide no
declaration evidence). It does not scan directories or read files. The default module search
root is the project root; additional project-relative roots must be explicitly
configured, for example `search_roots=("src",)`. Roots are normalized to `/`,
preserve their configured order, and reject absolute paths and traversal. The
resolver follows case-sensitive Python import names; a case-insensitive policy
is rejected. It does not read environment variables or reproduce runtime
`sys.path` changes.

Regular modules map from `.py` files, and `__init__.py` maps to the package
identity while keeping its own source path. `.pyi` modules and package markers
are retained as stub candidates. When a `.py` and `.pyi`, `package.py` and
`package/__init__.py`, or multiple search roots produce competing module
identities, all candidates are preserved and the result is `AMBIGUOUS`; neither
source file is discarded. Namespace package portions are recognized from
source-file directory evidence when enabled. A logical namespace package can
resolve with multiple portion candidates, each retaining its relative
directory and search root. A same-named child module found in multiple
portions remains ambiguous. Namespace portions outside the analyzed inventory
are not represented.

Absolute imports match exact module identities. Relative imports derive
package context from the importing file's identity under each configured
search root and apply the `ImportIR.relative_level`; imports beyond or without
a verified package context remain unresolved. `from` imports resolve their
base module separately from the imported name. The resolver matches
module-level function and class symbols through `SymbolIndex`, or a package
submodule. A same-named declaration and submodule remain ambiguous. It notices
possible module-scope re-export imports but does not follow arbitrary re-export
chains or infer assignments, `__all__`, or runtime package behavior.

Wildcard imports preserve base-module resolution while marking individual
name expansion as unknown; no names are fabricated. Every output record keeps
its import reference, original module/name, alias and bound name. Results are
immutable and JSON-safe and include candidate paths, source identities,
statuses, evidence, diagnostics, and aggregate counts. Unresolved external,
standard-library, third-party, missing, or unsupported imports are represented
as `UNRESOLVED`; the resolver cannot identify which of those categories applies
without broader environment data. The resolver does not execute package code,
import modules, read `__init__.py`, or create dependency edges.

This stage returns import-to-module/declaration results independently from the
IR. `PythonProjectAnalyzer` invokes it automatically using the same validated
IR and SymbolIndex as local and cross-file call resolution. Its optional
`module_search_roots` configuration is passed through; the default remains
the project root.

### Dependency Resolver

The Python Local Call Resolver establishes supported same-file call results.
The Python Module Resolver provides verified import targets, and the Python
Cross-File Call Resolver refines only locally unresolved calls with matching
import-binding evidence. An effective result aggregates these stages while
preserving local precedence and provenance. Dependency-edge creation and
relationship policies are handled by the separate Python Dependency Edge
Builder described below; broader dependency inference remains planned.

### Python Call Dependency Edge Builder

`app.analysis.DependencyEdgeBuilder` consumes the validated ProjectIR, its
matching SymbolIndex, and the effective call-resolution result. It creates a
resolved `function_call` DependencyEdgeIR only for an exactly matched
RESOLVED CallSiteIR with one target that exists in both the project and index,
and a same-file enclosing function or method symbol. The edge direction is
caller (`source_symbol_id`) to callee (`target_symbol_id`). Constructor edges
are allowed only when the effective resolver identifies a class target with a
constructor resolution method. Each call site produces its own edge, even
when multiple calls share the same caller and target; occurrence reference,
resolution method, provenance, and source/target locations are retained.

Unresolved and ambiguous calls are counted and skipped. Calls with no supported
caller, invalid target evidence, non-Python language, unsupported callee
shape, or an unverified class target do not produce edges. The builder does
not infer callers from names or lexical paths, create synthetic symbols,
resolve calls again, or modify ProjectIR or SymbolIndex. The analyzer appends
its immutable edge result to a new final ProjectIR and validates that snapshot
again. Pre-existing edges are preserved. Dynamic dispatch and inheritance
remain unsupported. Call-edge and import-dependency construction have focused
integration coverage as of Phase 3.6A; malformed evidence and uncommon
failure branches remain partially uncovered.

### Graph Engine

`GraphBuilder` accepts a validated ProjectIR and creates a directed NetworkX `MultiDiGraph`. File node IDs use `file:<project-relative-path>` and symbol node IDs use `symbol:<stable-symbol-id>`, separating identity namespaces. Every source file and symbol is represented, including files without symbols or with parse diagnostics. File-to-top-level-symbol and parent-symbol-to-child-symbol relationships use `CONTAINS`, based on explicit `parent_symbol_id` evidence. Resolved/inferred symbol dependencies use `CALLS` for function-call edges; file dependencies use `IMPORTS`. Repeated calls, imports, and containment edges remain individually keyed in the multigraph. The builder validates ProjectIR and additional parent/provenance consistency without modifying it; unresolved and ambiguous dependency records are omitted as non-errors. `GraphBuildResult.graph` returns a defensive deep copy so callers can use NetworkX algorithms without changing the stored result. Graph construction itself does not add persistence or visualization. Phase 3.6A added core graph construction tests; validation error branches remain partially uncovered.

### Graph Query Engine

`GraphQueryEngine` accepts a `GraphBuildResult` and keeps a private graph snapshot. It provides exact ID lookup, combined node filters, incoming/outgoing relationship queries, direct neighbors in either direction or both, graph-wide and per-node statistics, and explicit induced-subgraph extraction. Node results are sorted by node ID; relationship results are sorted by source, target, and edge key. Multiple filters are combined with AND. Name and qualified-name filters use case-insensitive substring matching; node type, language, and symbol kind use case-insensitive exact matching; file path and file identity use exact matching. Unknown node IDs raise `GraphNodeNotFoundError`; unknown IDs in a subgraph selection are omitted, while malformed IDs and invalid direction/type filters raise `InvalidGraphQueryError`. Neighbor results list unique adjacent nodes separately from relationship records, so parallel relationships are preserved. MultiDiGraph in-degree and out-degree count every parallel edge; total degree is their sum and counts a self-loop twice. Query node/relationship attributes are recursively frozen JSON mappings. Extracted subgraphs preserve selected node/edge attributes and keys and are returned through the existing defensive-copy `GraphBuildResult` contract. The engine supports direct queries only; it does not implement multi-hop trace, path finding, or graph ranking. Phase 3.6A added direct query regression tests; remaining validation branches are listed in the testing guide.

### Backend Analysis API

The Flask API registers health, analysis lifecycle, graph retrieval, node filter/lookup, relationship and neighbor query, statistics, and subgraph endpoints. The POST accepts a JSON object with a non-empty local `project_path`. A Flask-independent `AnalysisService` validates and resolves the directory, invokes `PythonProjectAnalyzer`, validates its final ProjectIR, builds the graph, and only then stores a completed session. An `AnalysisStore` uses a lock-protected in-memory mapping and UUID analysis IDs; it stores the pipeline result and one graph result without duplicating graph snapshots. Sessions are process-local and disappear at restart. API responses return the directory name rather than an absolute project path.

The graph endpoint serializes the existing `MultiDiGraph` to deterministic JSON nodes and edges. Node data retains graph attributes; each edge includes a stable ID derived from its source, target, and multiedge key, while retaining the original key, relationship type, and metadata. Query endpoints delegate filtering and graph semantics to a transient `GraphQueryEngine` created from the stored session graph. Relationships and neighbors default to outgoing direction; `both` preserves each directed multiedge once. Subgraph requests return the same graph response shape. Graph attributes are recursively serialized for mappings, sequences, sets, enums, paths, and JSON primitives. Unsupported values raise a serialization error instead of being stringified or dropped. API errors use `{"error":{"status":400,"message":"..."}}`; unknown analyses/nodes return 404, invalid query arguments return 400, missing NetworkX returns 503, and production unexpected errors return a generic message with details logged. NetworkX-dependent modules load only during analysis or graph query requests, leaving health available if NetworkX is absent. The API is synchronous and process-local, with no authentication, persistence, or background processing. API lifecycle and query route tests were added in Phase 3.6A; error and platform-specific branches remain partially uncovered.

### Frontend Analysis Integration

The React application keeps HTTP requests in a shared JSON client and typed analysis API service. Analysis and graph response models reflect the Flask serializers: analysis information contains only the project display name, status, summary, diagnostics, and timestamps; graph nodes retain IDs and raw data, while graph edges retain IDs, source/target, individual multigraph key, type, and metadata. The UI keeps one active session in React state, submits a backend-accessible local path, uses the AnalysisInfo returned by creation, and fetches graph data once. It replaces an old active analysis only after the new session and graph are loaded, then attempts to delete the prior process-local session. A failed replacement leaves the existing session intact; failed cleanup leaves the new session active and reports the failure. Search, graph selection, trace, and Inspector share the graph node ID scoped to the active analysis; keyed panels clear their prior state when the analysis changes. Session state is not persisted across browser reloads. Vite development requests use the `/api` proxy, whose target can be configured with `VITE_API_BASE_URL`; production builds require a same-origin host route or a configured backend origin. The workspace displays summary counts and integrates search, trace, and inspection beside the existing 2D visualization. Backend, frontend, and real-browser verification are documented in the testing guide (`docs/testing/foundation.md`).

### 2D Visualization

The React `GraphVisualization` component uses Cytoscape.js and receives the active session's raw graph response. It converts each backend node and edge to visualization data without changing API state. File nodes display project-relative paths; symbol nodes display names and use distinct class, function, and method styles when `symbol_kind` provides that distinction. Directed CONTAINS, CALLS, and IMPORTS edges use separate colors and line patterns with target arrows. The backend edge ID is used as the Cytoscape ID; duplicate IDs, invalid endpoints, unsupported types, or count mismatches are surfaced as rendering errors. Edge keys and raw metadata remain attached to each visualization element, and bezier curves preserve the visibility of parallel relationships.

The initial breadth-first directed layout is deterministic for the same graph; built-in CoSE force-directed and circle layouts can be selected and reapplied. The workspace also provides zoom, pan, fit, center, and clear-selection controls. Selecting a node returns its actual graph ID to the parent React workspace. The instance is updated when graph data changes, observes container resizing, and is destroyed at unmount. The visualization does not fetch data or implement graph queries, search, trace, or inspection; Function Search and Function Trace are separate workspace features that query the loaded graph data. Large graphs are rendered in full and may take time to lay out.

### Function Search

`FunctionSearch` builds a memoized index from the active GraphData's symbol nodes and includes only nodes with `symbol_kind` equal to `function` or `method`. Its debounced name query uses case-insensitive substring matching against the available name and qualified name. Kind and language filters use node metadata; the file path field filters the available relative path by case-insensitive substring. Results include the actual graph node ID, name, qualified name, kind, path, language, and `source_location.start_line` when present. Missing or unsupported metadata is not fabricated, and file/class symbols are excluded. No additional API request or source read is made.

Search selection and direct Cytoscape selection share the active analysis's selected node ID. A search result asks the existing graph component to select and center that exact node; it does not rebuild Cytoscape or rerun layout. Direct graph selection marks a matching visible search result without changing the query. Query clearing leaves selection intact; changing analysis clears stale search and selection state. Duplicate names remain separate because identity is the graph node ID. Multi-hop trace and function inspection are not part of this search feature.

### Function Trace

`createCallTraceIndex` builds sorted incoming/outgoing adjacency lists from only valid CALLS edges once per loaded graph; React memoizes the index for the graph snapshot. `runCallTrace` validates that the root is an existing function or method, then performs breadth-first traversal in outgoing, incoming, or both directions to a maximum depth of 10. Root depth is 0; direct relationships are depth 1. Class targets are accepted only when call-edge metadata/evidence identifies an explicit supported constructor resolution, and they are terminal. File nodes, CONTAINS edges, and IMPORTS edges never participate.

Visited-node tracking bounds cycles and recursion; the engine returns the reachable call subgraph within the selected traversal depth rather than enumerating simple paths. Every discovered relationship uses backend edge ID identity, so parallel edges and their keys/evidence remain separate; BOTH direction encounters are deduplicated by that edge ID. Original source/target orientation is preserved even for reverse traversal. Results group nodes by shortest BFS depth; relationship depth follows its first traversal, with cycle-closing edges between reached nodes retained at the selected boundary. Results include counts and a depth-boundary flag and never mutate GraphData. When an additional eligible node is reachable beyond the selected maximum, the UI indicates the boundary. Search, graph click selection, and trace navigation share the selected node ID. Choosing a discovered node focuses it and makes it the next root; the user runs a new trace explicitly. This is static tracing of supported resolved calls only, not runtime behavior. Automated unit and real-browser regression tests cover this feature (Phases 3.6B–3.6D).

### Function Inspector

The Inspector receives the same active selected graph node ID used by graph navigation, search, and trace. GraphBuilder namespaces IDs as `symbol:<SymbolIR ID>`, so the frontend uses the selected graph node's exact `symbol_id` attribute when calling the lookup endpoint while retaining the graph node ID for shared selection and graph dependency counts. It accepts only function, method, and class symbol kinds. Its Flask-independent `SymbolInspectorService` reads the exact immutable `SymbolIR`, parent, and source-file metadata from the analysis session's existing schema-v3 ProjectIR and SymbolIndex; it does not rerun analysis. The `symbols/lookup` route serializes supported `SymbolIR.to_dict()` fields, including parameters, annotations, decorators, docs, location, and JSON-compatible language metadata.

The `symbols/source` route separately resolves the symbol's validated relative file path and source location beneath the canonical project root retained privately in the process-local analysis session. It rejects traversal, symlink/junction components, containment escapes, and non-regular files. Only scanner-supported Python `.py`/`.pyi` source is read, using Python's encoding detection, with no imports or execution. It returns the symbol's inclusive IR line range, bounded to 500 lines, 16,384 characters per line, and 256,000 characters overall, with an explicit truncation flag and original line positions. Missing or unreadable source returns a structured unavailable result without affecting metadata. Current file size is compared to scanner metadata; size changes are reported, but same-size changes cannot be detected, so the service does not claim snapshot consistency. Absolute paths are not returned.

The frontend safely renders returned code as monospaced text and computes direct outgoing/incoming CALLS edge and distinct neighbor counts from the active graph. It aborts prior selection requests and associates loaded states with analysis and symbol IDs. File and unsupported nodes receive clear states. Automated backend, frontend, and real-browser regression tests cover this feature (Phases 3.6A–3.6D).

### 3D Visualization

Presents the graph in an interactive three-dimensional view. Its layout and interaction details remain to be designed.

## Planned data flow

1. The Project Scanner identifies source files in a project.
2. Language Detection assigns a language to each file that can be analyzed.
3. The Parser Registry selects the matching Language Adapter.
4. The current `PythonProjectAnalyzer` directly invokes the Python AST Parser,
   which reads and parses a discovered file into an AST result. Language
   Detection and Parser Registry remain planned workflow components.
5. The Python Function and Method Extractor walks that AST and returns ordered
   declaration metadata with lexical scopes, signatures, decorators,
   locations, and docstrings.
6. The Python Class Extractor independently walks the same AST and returns
   class declarations with lexical parents, bases, keywords, decorators,
   locations, and docstrings.
7. The Python Import Extractor independently walks the AST and returns ordered
   alias records with binding syntax, relative levels, locations, and scopes.
8. The Python Call Extractor independently walks the same AST for every
   successfully parsed file, including files with no calls, and returns
   ordered call-site syntax, diagnostics, and lexical/execution context.
9. The Python Scope Binding Evidence Extractor consumes the same AST and the
    existing import result, returning bindings, source-bounded lexical scope
    summaries, and directives. The project pipeline retains these results and
    passes them to the local resolver.
10. The Python Qualified Name Generator combines function and class extraction
   results from the same file and preserves source order and duplicates.
11. The Python Stable Symbol ID Generator assigns versioned SHA-256 IDs from
    normalized relative paths, declaration kinds, qualified names, and
    duplicate occurrence indexes.
12. The version 3 Unified IR models define the language-independent project,
    file, symbol, parameter, location, import, call-site, call-dependency,
    import-dependency, and diagnostic contracts. CallSiteIR itself performs no target resolution.
13. The Python-to-IR adapter maps Python declaration, import, and call records
    while preserving IDs, call ordering, source locations, argument metadata,
    and diagnostics. It assigns an enclosing symbol ID only when one symbol
    matches the available lexical and source-span evidence. It does not resolve
    callee targets.
14. The Python Project Analyzer validates ProjectIR and builds one immutable
    Symbol Index snapshot. It invokes the local call and module/import
    resolvers with that shared snapshot; local resolution also receives
    successful binding evidence.
15. The Python Cross-File Call Resolver receives the same ProjectIR/index,
    local results, module/import results, and binding evidence. It refines only
    locally unresolved calls. The effective aggregate retains raw stage
    results and selects one status per CallSiteIR, giving local resolved and
    ambiguous results precedence.
16. The Python Dependency Edge Builder consumes effective call results and
    creates one directed caller-to-callee DependencyEdgeIR per supported
    resolved call site. It preserves repeated call sites and appends edges to
    a revalidated final ProjectIR without changing the SymbolIndex.
17. The Python Import Dependency Builder consumes exact ImportIR records and
    existing module/import results. It adds one directed source-file-to-target-file
    ImportDependencyIR per uniquely resolved in-project import, retaining
    separate records and evidence for repeated imports. Call and import records
    are separate collections in the final ProjectIR.
18. The Graph Engine represents analyzed entities and relationships as a
    graph; the 2D and 3D Visualization components will present it for
    exploration.

The version 3 IR schema, Python declaration/import/call mapping, explicit
project validation, in-memory Symbol Index, Python project orchestration with
binding-aware same-file and cross-file call resolution, module/import
resolution, effective result aggregation, and call and import dependency
construction, unified graph construction, direct graph queries, the synchronous analysis API with graph-query and symbol-inspection routes, frontend analysis integration, interactive 2D graph visualization, Function Search, bounded static Function Trace, and Function Inspector are implemented. Backend, frontend, and real-browser end-to-end paths have automated regression coverage from Phases 3.6A–3.6D. Persistent storage, runtime tracing, and 3D visualization remain for later design and implementation tasks.

