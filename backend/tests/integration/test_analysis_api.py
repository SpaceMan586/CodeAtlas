"""Analysis lifecycle and Inspector route tests; project sources stay inert."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def test_analysis_lifecycle_graph_queries_and_inspector_are_json_safe(client, tmp_path: Path):
    marker = tmp_path / "executed.txt"
    (tmp_path / "__init__.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n", encoding="utf-8"
    )
    source = tmp_path / "module.py"
    source.write_text("def target():\n    return 1\n\ndef caller():\n    return target()\n", encoding="utf-8")
    original = source.read_bytes()

    created = client.post("/api/analysis", json={"project_path": str(tmp_path)})
    assert created.status_code == 201
    info = created.get_json()
    analysis_id = info["analysis_id"]
    assert info["project_display_path"] == tmp_path.name
    assert str(tmp_path) not in json.dumps(info)
    assert client.get(f"/api/analysis/{analysis_id}").status_code == 200
    graph = client.get(f"/api/analysis/{analysis_id}/graph")
    assert graph.status_code == 200
    assert graph.get_json()["statistics"]["total_nodes"] >= 3
    nodes = client.get(f"/api/analysis/{analysis_id}/nodes?node_type=symbol")
    assert nodes.status_code == 200 and nodes.get_json()["count"] == 2
    caller_node = next(n for n in nodes.get_json()["nodes"] if n["data"]["name"] == "caller")
    lookup = client.get(f"/api/analysis/{analysis_id}/nodes/lookup", query_string={"node_id": caller_node["id"]})
    assert lookup.status_code == 200
    rels = client.get(f"/api/analysis/{analysis_id}/relationships", query_string={"node_id": caller_node["id"]})
    assert rels.status_code == 200 and rels.get_json()["count"] == 1
    neighbors = client.get(f"/api/analysis/{analysis_id}/neighbors", query_string={"node_id": caller_node["id"], "relationship_type": "CALLS"})
    assert neighbors.status_code == 200 and neighbors.get_json()["relationship_count"] == 1
    invalid_direction = client.get(f"/api/analysis/{analysis_id}/neighbors", query_string={"node_id": caller_node["id"], "direction": "sideways"})
    assert invalid_direction.status_code == 400
    assert isinstance(invalid_direction.get_json()["error"], dict)
    stats = client.get(f"/api/analysis/{analysis_id}/statistics")
    assert stats.status_code == 200
    subgraph = client.post(f"/api/analysis/{analysis_id}/subgraph", json={"node_ids": [caller_node["id"]]})
    assert subgraph.status_code == 200

    symbols = client.get(f"/api/analysis/{analysis_id}/symbols/lookup", query_string={"symbol_id": caller_node["data"]["symbol_id"]})
    assert symbols.status_code == 200
    source_response = client.get(f"/api/analysis/{analysis_id}/symbols/source", query_string={"symbol_id": caller_node["data"]["symbol_id"]})
    assert source_response.status_code == 200
    assert "return target()" in source_response.get_json()["code"]
    source.write_text("def target():\n    return 1000\n\ndef caller():\n    return target()\n", encoding="utf-8")
    changed = client.get(f"/api/analysis/{analysis_id}/symbols/source", query_string={"symbol_id": caller_node["data"]["symbol_id"]})
    assert changed.get_json()["changed_after_analysis"] is True
    assert not marker.exists()
    assert source.read_bytes() != original

    assert client.delete(f"/api/analysis/{analysis_id}").status_code == 200
    assert client.get(f"/api/analysis/{analysis_id}").status_code == 404


def test_analysis_api_reports_validation_and_unknown_session_errors(client):
    missing_body = client.post("/api/analysis", json={})
    assert missing_body.status_code == 400
    assert isinstance(missing_body.get_json()["error"], dict)
    assert client.get("/api/analysis/unknown/statistics").status_code == 404
    assert client.get("/api/analysis/unknown/nodes/lookup").status_code == 400
    invalid_path = client.post("/api/analysis", json={"project_path": ""})
    assert invalid_path.status_code == 400


def test_inspector_refuses_symlinked_source_file(client, tmp_path: Path):
    target = tmp_path / "outside.py"
    target.write_text("def hidden(): pass\n", encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    (project / "module.py").write_text("def hidden(): pass\n", encoding="utf-8")
    response = client.post("/api/analysis", json={"project_path": str(project)})
    assert response.status_code == 201
    analysis_id = response.get_json()["analysis_id"]
    symbol_id = next(n["data"]["symbol_id"] for n in client.get(f"/api/analysis/{analysis_id}/nodes?node_type=symbol").get_json()["nodes"])
    (project / "module.py").unlink()
    try:
        (project / "module.py").symlink_to(target)
    except (OSError, NotImplementedError) as error:
        pytest.skip(f"This platform does not allow creating symlinks: {error}")
    source = client.get(f"/api/analysis/{analysis_id}/symbols/source", query_string={"symbol_id": symbol_id})
    assert source.status_code == 200
    assert source.get_json()["status"] == "unavailable"
    assert "hidden" not in (source.get_json()["code"] or "")
