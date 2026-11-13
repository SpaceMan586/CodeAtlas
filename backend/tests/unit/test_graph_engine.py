"""Graph construction and query regression coverage over synthetic projects."""

from __future__ import annotations

from pathlib import Path

import networkx as nx
import pytest

from app.analysis import PythonProjectAnalyzer
from app.analysis import DependencyEdgeBuilder, ImportDependencyBuilder
from app.graph import GraphBuilder, GraphQueryEngine, GraphNodeNotFoundError, InvalidGraphQueryError


def _graph(tmp_path: Path):
    (tmp_path / "main.py").write_text(
        "def recur(n):\n    return recur(n - 1) if n else 0\n\ndef run():\n    recur(2)\n    recur(1)\n",
        encoding="utf-8",
    )
    (tmp_path / "empty.py").write_text("# no symbols\n", encoding="utf-8")
    ir = PythonProjectAnalyzer().analyze(tmp_path).project_ir
    return ir, GraphBuilder().build(ir)


def test_builder_creates_directed_multigraph_nodes_and_preserves_parallel_calls(tmp_path):
    ir, result = _graph(tmp_path)
    graph = result.graph
    assert isinstance(graph, nx.MultiDiGraph)
    assert "file:empty.py" in graph
    assert graph.nodes["file:empty.py"]["node_type"] == "file"
    assert all(key.startswith("symbol:") for key, data in graph.nodes(data=True) if data["node_type"] == "symbol")
    recur_id = next(s.symbol_id for s in ir.symbols if s.original_name == "recur")
    recur_node = GraphBuilder.symbol_node_id(recur_id)
    self_edges = list(graph.get_edge_data(recur_node, recur_node).values())
    assert len(self_edges) == 1
    assert self_edges[0]["edge_type"] == "CALLS"
    run_id = next(s.symbol_id for s in ir.symbols if s.original_name == "run")
    parallel = graph.get_edge_data(GraphBuilder.symbol_node_id(run_id), recur_node)
    assert parallel and len(parallel) == 2


def test_graph_result_and_query_engine_are_defensive_and_preserve_edge_evidence(tmp_path):
    _, result = _graph(tmp_path)
    external = result.graph
    original_nodes = result.graph.number_of_nodes()
    external.clear()
    assert result.graph.number_of_nodes() == original_nodes
    engine = GraphQueryEngine(result)
    before = result.graph
    run = engine.filter_nodes(name="RUN")
    assert len(run) == 1
    assert len(engine.outgoing_relationships(run[0].node_id, relationship_types="calls")) == 2
    both = engine.direct_neighbors(run[0].node_id, direction="both", relationship_types="CALLS")
    assert len(both.neighbor_nodes) == 1
    assert len(both.relationships) == 2
    subgraph = engine.extract_subgraph([run[0].node_id, both.neighbor_nodes[0].node_id])
    assert subgraph.graph.number_of_edges() == 3
    assert list(result.graph.edges(data=True)) == list(before.edges(data=True))


def test_queries_validate_missing_nodes_directions_and_count_self_loop_degrees(tmp_path):
    _, result = _graph(tmp_path)
    engine = GraphQueryEngine(result)
    recur = engine.filter_nodes(name="recur")[0]
    degree = engine.statistics(recur.node_id)
    assert degree.in_degree >= 1
    assert degree.out_degree >= 1
    assert degree.total_degree == degree.in_degree + degree.out_degree
    self_edges = engine.outgoing_relationships(recur.node_id, relationship_types="CALLS")
    assert any(edge.target_node_id == recur.node_id for edge in self_edges)
    with pytest.raises(GraphNodeNotFoundError):
        engine.get_node("symbol:missing")
    with pytest.raises(InvalidGraphQueryError):
        engine.direct_neighbors(recur.node_id, direction="sideways")


def test_dependency_builders_are_deterministic_and_preserve_distinct_relationship_types(tmp_path):
    (tmp_path / "lib.py").write_text("def target(): return 1\n", encoding="utf-8")
    (tmp_path / "main.py").write_text(
        "from lib import target\n\ndef caller():\n    target()\n    target()\n", encoding="utf-8"
    )
    result = PythonProjectAnalyzer().analyze(tmp_path)
    call_edges = DependencyEdgeBuilder().build(
        project_ir=result.project_ir,
        symbol_index=result.symbol_index,
        effective_resolution=result.effective_call_resolution,
    )
    import_edges = ImportDependencyBuilder().build(
        project_ir=result.project_ir,
        import_resolution=result.module_resolution,
    )
    assert len(call_edges.edges) == 2
    assert call_edges.to_dict() == DependencyEdgeBuilder().build(
        project_ir=result.project_ir, symbol_index=result.symbol_index,
        effective_resolution=result.effective_call_resolution,
    ).to_dict()
    assert len(import_edges.dependencies) == 1
    assert import_edges.dependencies[0].source_file_path == "main.py"
    assert import_edges.dependencies[0].target_file_path == "lib.py"
    assert result.project_ir.to_dict()["dependency_edges"]
    assert result.project_ir.to_dict()["import_dependencies"]
