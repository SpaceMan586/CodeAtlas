"""Convert validated Unified IR into a directed NetworkX multigraph."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from typing import Any

import networkx as nx

from app.graph.graph_exceptions import GraphBuildError
from app.graph.graph_models import GraphBuildResult, GraphBuildStatistics
from app.ir import DependencyResolutionStatus, ProjectIR, validate_project_ir
from app.ir.exceptions import IRValidationError


class GraphBuilder:
    """Build a deterministic project graph without changing its ProjectIR."""

    def build(self, project_ir: ProjectIR) -> GraphBuildResult:
        if not isinstance(project_ir, ProjectIR):
            raise GraphBuildError("project_ir must be a ProjectIR instance.")
        try:
            validate_project_ir(project_ir)
        except IRValidationError as error:
            raise GraphBuildError("ProjectIR validation failed before graph construction.") from error

        symbols = {symbol.symbol_id: symbol for symbol in project_ir.symbols}
        self._validate_parent_references(symbols)

        graph = nx.MultiDiGraph(
            graph_type="CodeAtlasProjectGraph",
            ir_schema_version=project_ir.schema_version,
            analysis_metadata=project_ir.to_dict()["analysis_metadata"],
        )
        for source in project_ir.source_files:
            node_id = self.file_node_id(source.file_path)
            graph.add_node(
                node_id,
                node_type="file",
                file_path=source.file_path,
                file_identity=source.file_path,
                language=source.language,
                file_name=source.file_name,
                file_extension=source.file_extension,
                parsing_status=source.parsing_status,
                source_line_count=source.source_line_count,
                category=source.category,
                metadata=source.to_dict()["metadata"],
                diagnostic_count=len(source.diagnostics),
            )

        for symbol in project_ir.symbols:
            node_id = self.symbol_node_id(symbol.symbol_id)
            graph.add_node(
                node_id,
                node_type="symbol",
                symbol_id=symbol.symbol_id,
                name=symbol.original_name,
                qualified_name=symbol.qualified_name,
                symbol_kind=symbol.symbol_kind,
                language=symbol.language,
                file_path=symbol.file_path,
                source_location=symbol.source_location.to_dict() if symbol.source_location else None,
                parent_symbol_id=symbol.parent_symbol_id,
                metadata=symbol.to_dict()["metadata"],
            )

        for index, symbol in enumerate(project_ir.symbols):
            child_id = self.symbol_node_id(symbol.symbol_id)
            if symbol.parent_symbol_id is None:
                source_id = self.file_node_id(symbol.file_path)
                graph.add_edge(source_id, child_id, key=f"contains:file:{index:08d}", edge_type="CONTAINS", containment="file_symbol")
            else:
                parent_id = self.symbol_node_id(symbol.parent_symbol_id)
                graph.add_edge(parent_id, child_id, key=f"contains:symbol:{index:08d}", edge_type="CONTAINS", containment="symbol_symbol")

        unresolved_skipped = ambiguous_skipped = 0
        call_index = 0
        for edge in project_ir.dependency_edges:
            if edge.resolution_status is DependencyResolutionStatus.UNRESOLVED:
                unresolved_skipped += 1
                continue
            if edge.resolution_status is DependencyResolutionStatus.AMBIGUOUS:
                ambiguous_skipped += 1
                continue
            if edge.target_symbol_id is None:
                raise GraphBuildError("A resolved or inferred dependency edge requires a target symbol.")
            self._validate_call_edge_evidence(edge.to_dict(), edge.source_symbol_id, edge.target_symbol_id)
            source_id = self.symbol_node_id(edge.source_symbol_id)
            target_id = self.symbol_node_id(edge.target_symbol_id)
            self._require_endpoints(graph, source_id, target_id, "call dependency")
            relationship_type = "CALLS" if edge.dependency_type == "function_call" else edge.dependency_type.upper()
            graph.add_edge(
                source_id,
                target_id,
                key=f"dependency:{call_index:08d}",
                edge_type=relationship_type,
                dependency_type=edge.dependency_type,
                resolution_status=edge.resolution_status.value,
                source_location=edge.source_location.to_dict() if edge.source_location else None,
                target_location=edge.target_location.to_dict() if edge.target_location else None,
                evidence=[item for item in edge.to_dict()["evidence"]],
                metadata=edge.to_dict()["metadata"],
            )
            call_index += 1

        for index, dependency in enumerate(project_ir.import_dependencies):
            self._validate_import_evidence(dependency.to_dict())
            source_id = self.file_node_id(dependency.source_file_path)
            target_id = self.file_node_id(dependency.target_file_path)
            self._require_endpoints(graph, source_id, target_id, "import dependency")
            graph.add_edge(
                source_id,
                target_id,
                key=f"import:{dependency.statement_order:08d}:{dependency.alias_order:08d}:{index:08d}",
                edge_type="IMPORTS",
                dependency_type=dependency.dependency_type,
                source_file_path=dependency.source_file_path,
                target_file_path=dependency.target_file_path,
                module_name=dependency.module_name,
                imported_symbol=dependency.imported_symbol,
                alias=dependency.alias,
                relative_level=dependency.relative_level,
                statement_order=dependency.statement_order,
                alias_order=dependency.alias_order,
                source_location=dependency.source_location.to_dict() if dependency.source_location else None,
                evidence=[item for item in dependency.to_dict()["evidence"]],
                metadata=dependency.to_dict()["metadata"],
            )

        nodes_by_category = Counter(data["node_type"] for _, data in graph.nodes(data=True))
        edges_by_type = Counter(data["edge_type"] for _, _, data in graph.edges(data=True))
        statistics = GraphBuildStatistics(
            nodes_by_category=dict(nodes_by_category),
            edges_by_relationship_type=dict(edges_by_type),
            total_nodes=graph.number_of_nodes(),
            total_edges=graph.number_of_edges(),
            unresolved_call_edges_skipped=unresolved_skipped,
            ambiguous_call_edges_skipped=ambiguous_skipped,
        )
        return GraphBuildResult(graph, statistics)

    @staticmethod
    def file_node_id(file_path: str) -> str:
        return f"file:{file_path}"

    @staticmethod
    def symbol_node_id(symbol_id: str) -> str:
        return f"symbol:{symbol_id}"

    @staticmethod
    def _validate_parent_references(symbols: dict[str, Any]) -> None:
        parents: dict[str, str] = {}
        for symbol in symbols.values():
            if symbol.parent_symbol_id is None:
                continue
            parent = symbols.get(symbol.parent_symbol_id)
            if parent is None:
                raise GraphBuildError(f"Symbol {symbol.symbol_id!r} references missing parent {symbol.parent_symbol_id!r}.")
            if parent.file_path != symbol.file_path:
                raise GraphBuildError(f"Symbol {symbol.symbol_id!r} references a parent in a different file.")
            if parent.symbol_id == symbol.symbol_id:
                raise GraphBuildError(f"Symbol {symbol.symbol_id!r} cannot contain itself.")
            parents[symbol.symbol_id] = parent.symbol_id
        for symbol_id in parents:
            visited: set[str] = set()
            current = symbol_id
            while current in parents:
                if current in visited:
                    raise GraphBuildError(f"Symbol containment contains a cycle involving {current!r}.")
                visited.add(current)
                current = parents[current]

    @staticmethod
    def _validate_call_edge_evidence(
        serialized: dict[str, object], source_symbol_id: str, target_symbol_id: str,
    ) -> None:
        for evidence in serialized["evidence"]:
            if not isinstance(evidence, Mapping):
                continue
            for key, expected in (("caller_symbol_id", source_symbol_id), ("source_symbol_id", source_symbol_id), ("target_symbol_id", target_symbol_id)):
                if key in evidence and evidence[key] != expected:
                    raise GraphBuildError(f"Call dependency evidence {key} does not match its edge endpoint.")

    @staticmethod
    def _validate_import_evidence(serialized: dict[str, object]) -> None:
        source_path = serialized["source_file_path"]
        statement_order = serialized["statement_order"]
        alias_order = serialized["alias_order"]
        for evidence in serialized["evidence"]:
            if not isinstance(evidence, Mapping):
                continue
            reference = evidence.get("import_reference")
            if not isinstance(reference, Mapping):
                continue
            if reference.get("file_path") != source_path:
                raise GraphBuildError("Import dependency provenance does not match its source file.")
            if reference.get("statement_order") != statement_order or reference.get("alias_order") != alias_order:
                raise GraphBuildError("Import dependency provenance does not match its source ordering.")

    @staticmethod
    def _require_endpoints(graph: nx.MultiDiGraph, source: str, target: str, relationship: str) -> None:
        if source not in graph or target not in graph:
            raise GraphBuildError(f"The {relationship} relationship references a missing graph endpoint.")
