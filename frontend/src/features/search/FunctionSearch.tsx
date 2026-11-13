import { useEffect, useMemo, useState } from "react";
import type { GraphData, GraphNode } from "../../types/graph";

interface FunctionSearchProps {
  graph: GraphData;
  selectedNodeId: string | null;
  onSelectNode: (nodeId: string) => void;
}

interface FunctionSearchResult {
  id: string;
  name: string;
  qualifiedName: string;
  filePath: string;
  language: string;
  kind: "function" | "method";
  startLine: number | null;
}

function optionalString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function startLine(node: GraphNode): number | null {
  const location = node.data.source_location;
  if (typeof location !== "object" || location === null || Array.isArray(location)) return null;
  const line = (location as Record<string, unknown>).start_line;
  return typeof line === "number" && Number.isInteger(line) && line > 0 ? line : null;
}

function buildSearchIndex(graph: GraphData): FunctionSearchResult[] {
  const results: FunctionSearchResult[] = [];
  for (const node of graph.nodes) {
    if (node.data.node_type !== "symbol") continue;
    const kind = optionalString(node.data.symbol_kind).toLowerCase();
    if (kind !== "function" && kind !== "method") continue;
    const name = optionalString(node.data.name);
    const qualifiedName = optionalString(node.data.qualified_name);
    if (!name && !qualifiedName) continue;
    results.push({
      id: node.id,
      name: name || qualifiedName,
      qualifiedName,
      filePath: optionalString(node.data.file_path),
      language: optionalString(node.data.language),
      kind,
      startLine: startLine(node),
    });
  }
  return results;
}

export function FunctionSearch({ graph, selectedNodeId, onSelectNode }: FunctionSearchProps) {
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [kindFilter, setKindFilter] = useState<"all" | "function" | "method">("all");
  const [languageFilter, setLanguageFilter] = useState("");
  const [fileFilter, setFileFilter] = useState("");

  const searchIndex = useMemo(() => buildSearchIndex(graph), [graph]);
  const languages = useMemo(
    () => [...new Set(searchIndex.map((result) => result.language).filter(Boolean))].sort((a, b) => a.localeCompare(b)),
    [searchIndex],
  );
  useEffect(() => {
    if (!query.trim()) {
      setDebouncedQuery("");
      return;
    }
    const timer = window.setTimeout(() => setDebouncedQuery(query.trim()), 180);
    return () => window.clearTimeout(timer);
  }, [query]);

  const normalizedQuery = debouncedQuery.toLowerCase();
  const results = useMemo(() => {
    if (!query.trim() || !normalizedQuery) return [];
    return searchIndex.filter((result) => {
      const matchesName = result.name.toLowerCase().includes(normalizedQuery);
      const matchesQualifiedName = result.qualifiedName.toLowerCase().includes(normalizedQuery);
      const matchesKind = kindFilter === "all" || result.kind === kindFilter;
      const matchesLanguage = !languageFilter || result.language === languageFilter;
      const matchesFile = !fileFilter || result.filePath.toLowerCase().includes(fileFilter.trim().toLowerCase());
      return (matchesName || matchesQualifiedName) && matchesKind && matchesLanguage && matchesFile;
    });
  }, [fileFilter, kindFilter, languageFilter, normalizedQuery, query, searchIndex]);

  const filtersApplied = kindFilter !== "all" || Boolean(languageFilter) || Boolean(fileFilter);
  const isDebouncing = Boolean(query.trim()) && query.trim() !== debouncedQuery;

  function clearSearch() {
    setQuery("");
    setDebouncedQuery("");
    setKindFilter("all");
    setLanguageFilter("");
    setFileFilter("");
  }

  return (
    <section aria-labelledby="function-search-heading" className="rounded-lg border border-atlas-line bg-atlas-surface p-4 sm:p-5">
      <div>
        <p className="text-[10px] font-semibold tracking-[0.18em] text-atlas-muted">FIND A SYMBOL</p>
        <h3 className="mt-1 text-base font-semibold text-white" id="function-search-heading">Function Search</h3>
        <p className="mt-1 text-xs leading-5 text-atlas-muted">Search functions and methods in this analysis.</p>
      </div>

      <label className="mt-4 block text-xs font-medium text-atlas-copy" htmlFor="function-search-query">Name or qualified name</label>
      <div className="mt-1.5 flex gap-2">
        <input
          autoComplete="off"
          className="min-h-10 min-w-0 flex-1 rounded-md border border-atlas-line bg-atlas-raised px-3 text-sm text-white placeholder:text-atlas-dim focus:border-atlas-accent"
          id="function-search-query"
          onChange={(event) => setQuery(event.target.value)}
          placeholder="e.g. calculate"
          type="search"
          value={query}
        />
        <button
          aria-label="Clear search and filters"
          className="graph-control px-3"
          disabled={!query && !filtersApplied}
          onClick={clearSearch}
          type="button"
        >
          Clear
        </button>
      </div>

      <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-3 xl:grid-cols-1">
        <div>
          <label className="mb-1 block text-xs text-atlas-muted" htmlFor="function-search-kind">Kind</label>
          <select className="min-h-9 w-full rounded-md border border-atlas-line bg-atlas-raised px-2 text-xs text-atlas-copy focus:border-atlas-accent" id="function-search-kind" onChange={(event) => setKindFilter(event.target.value as typeof kindFilter)} value={kindFilter}>
            <option value="all">Functions and methods</option>
            <option value="function">Functions</option>
            <option value="method">Methods</option>
          </select>
        </div>
        {languages.length > 0 && (
          <div>
            <label className="mb-1 block text-xs text-atlas-muted" htmlFor="function-search-language">Language</label>
            <select className="min-h-9 w-full rounded-md border border-atlas-line bg-atlas-raised px-2 text-xs text-atlas-copy focus:border-atlas-accent" id="function-search-language" onChange={(event) => setLanguageFilter(event.target.value)} value={languageFilter}>
              <option value="">All languages</option>
              {languages.map((language) => <option key={language} value={language}>{language}</option>)}
            </select>
          </div>
        )}
        <div>
          <label className="mb-1 block text-xs text-atlas-muted" htmlFor="function-search-file">File path contains</label>
          <input className="min-h-9 w-full rounded-md border border-atlas-line bg-atlas-raised px-2 text-xs text-white placeholder:text-atlas-dim focus:border-atlas-accent" id="function-search-file" onChange={(event) => setFileFilter(event.target.value)} placeholder="e.g. services/" value={fileFilter} />
        </div>
      </div>

      <div aria-live="polite" className="mt-4 border-t border-atlas-line pt-3" role="status">
        {!query.trim() && <p className="text-xs leading-5 text-atlas-muted">Enter a name to search. Results are limited to functions and methods in the active analysis.</p>}
        {isDebouncing && <p className="text-xs text-atlas-accent">Updating results…</p>}
        {!isDebouncing && query.trim() && results.length > 0 && <p className="text-xs text-atlas-muted">{results.length} matching {results.length === 1 ? "symbol" : "symbols"}</p>}
        {!isDebouncing && query.trim() && results.length === 0 && <p className="text-xs text-atlas-muted">No functions or methods match this search.</p>}
      </div>

      {results.length > 0 && !isDebouncing && (
        <ul aria-label="Function search results" className="mt-2 max-h-[440px] space-y-1.5 overflow-y-auto pr-1">
          {results.map((result) => {
            const selected = selectedNodeId === result.id;
            return (
              <li key={result.id}>
                <button
                  aria-pressed={selected}
                  className={`w-full rounded-md border p-3 text-left transition-colors focus-visible:outline-offset-2 ${selected ? "border-atlas-accent bg-atlas-accent/10" : "border-atlas-line bg-atlas-raised hover:border-atlas-accent/50"}`}
                  onClick={() => onSelectNode(result.id)}
                  type="button"
                >
                  <span className="flex items-center justify-between gap-2">
                    <span className="min-w-0 truncate text-sm font-semibold text-white">{result.name}</span>
                    <span className="shrink-0 rounded border border-atlas-line px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-atlas-muted">{result.kind}</span>
                  </span>
                  {result.qualifiedName && <span className="mt-1 block break-all text-xs text-atlas-accent">{result.qualifiedName}</span>}
                  {result.filePath && <span className="mt-1 block break-all text-xs text-atlas-muted">{result.filePath}{result.startLine ? ` · line ${result.startLine}` : ""}</span>}
                  {result.language && <span className="mt-1 block text-[10px] text-atlas-dim">{result.language}</span>}
                  {selected && <span className="mt-2 inline-flex rounded bg-atlas-accent px-1.5 py-0.5 text-[10px] font-semibold text-atlas-bg">Selected in graph</span>}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
