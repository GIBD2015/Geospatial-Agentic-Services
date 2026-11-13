from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import box

from gas_server.agents.cloud_native_stac_agent import CloudNativeStacAgent
from gas_server.agents.geospatial_data_retrieval_agent import GeospatialDataRetrievalAgent
from gas_server.core.benchmark_suite import GasBenchmarkRunner
from gas_server.core.mcp_bridge import GasMcpBridge
from gas_server.core.workflow_dag_executor import (
    GasWorkflowDagExecutor,
    WorkflowDagError,
    inspect_geojson_crs_and_bbox,
)


def _write_wgs84_geojson(path: Path) -> Path:
    gdf = gpd.GeoDataFrame(
        {
            "id": [1, 2, 3],
            "name": ["Centre", "Dauphin", "Lancaster"],
            "value": [15.5, 28.2, 41.0],
            "geometry": [
                box(-77.9, 40.7, -77.7, 40.9),
                box(-77.0, 40.2, -76.7, 40.5),
                box(-76.5, 39.9, -76.1, 40.2),
            ],
        },
        crs="EPSG:4326",
    )
    gdf.to_file(path, driver="GeoJSON")
    return path


def test_workflow_dag_validation_and_cycle_detection():
    plan = {
        "steps": [
            {
                "step_id": "retrieve",
                "agent_id": "cloud_native_stac_agent",
                "instructions": "Search Sentinel-2 scenes over State College.",
            },
            {
                "step_id": "inspect",
                "agent_id": "geospatial_data_inspection_agent",
                "instructions": "Inspect dataset quality.",
                "depends_on": ["retrieve"],
            },
            {
                "step_id": "map_app",
                "agent_id": "web_mapping_app_agent",
                "instructions": "Create interactive web map.",
                "depends_on": ["retrieve"],
            },
            {
                "step_id": "stats",
                "agent_id": "spatial_statistics_agent",
                "instructions": "Compute summary statistics.",
                "depends_on": ["inspect", "map_app"],
            },
        ]
    }
    validation = GasWorkflowDagExecutor.build_and_validate_dag(plan)
    assert validation["valid"] is True
    assert validation["node_count"] == 4
    assert validation["edge_count"] == 4
    assert validation["execution_waves"] == [
        ["retrieve"],
        ["inspect", "map_app"],
        ["stats"],
    ]

    cyclic_plan = {
        "steps": [
            {"step_id": "a", "agent_id": "mapping_agent", "instructions": "Step A", "depends_on": ["b"]},
            {"step_id": "b", "agent_id": "mapping_agent", "instructions": "Step B", "depends_on": ["a"]},
        ]
    }
    with pytest.raises(WorkflowDagError, match="cycle"):
        GasWorkflowDagExecutor.build_and_validate_dag(cyclic_plan)


def test_workflow_dag_self_healing_crs_reprojection(tmp_path):
    wgs84_path = _write_wgs84_geojson(tmp_path / "pa_zones_wgs84.geojson")
    captured_inputs: dict[str, list[str]] = {}

    def fake_runner(step_req: dict) -> dict:
        sid = step_req["step_id"]
        captured_inputs[sid] = list(step_req.get("input_datasets") or [])
        if sid == "step_1":
            return {"outputs": {"dataset_paths": [str(wgs84_path)]}}
        return {"outputs": {"dataset_paths": captured_inputs[sid]}}

    executor = GasWorkflowDagExecutor(step_runner=fake_runner, auto_heal_crs=True, work_dir=tmp_path / "work")
    events = []
    result = executor.execute(
        {
            "steps": [
                {
                    "step_id": "step_1",
                    "agent_id": "geospatial_data_retrieval_agent",
                    "instructions": "Load Pennsylvania zones in EPSG:4326.",
                },
                {
                    "step_id": "step_2",
                    "agent_id": "spatial_analysis_agent",
                    "instructions": "Create a 500 meter buffer around each zone.",
                    "depends_on": ["step_1"],
                },
            ]
        },
        progress_callback=events.append,
    )

    assert result["status"] == "successful"
    assert result["diagnostics"]["self_healing_count"] == 1
    heal_action = result["diagnostics"]["self_healing_actions"][0]
    assert heal_action["step_id"] == "step_2"
    assert heal_action["action"] == "auto_crs_reprojection"
    assert heal_action["source_crs"] == "EPSG:4326"
    assert heal_action["target_crs"] == "EPSG:5070"

    healed_path = captured_inputs["step_2"][0]
    info = inspect_geojson_crs_and_bbox(healed_path)
    assert info["crs"] == "EPSG:5070"
    assert info["is_geographic"] is False
    assert any(ev.get("event") == "self_healing_applied" for ev in events)


def test_canvas_server_dag_validation_and_execution_endpoints(tmp_path):
    from gas_canvas.canvas_server import app as canvas_app

    sample_path = _write_wgs84_geojson(tmp_path / "canvas_input.geojson")
    client = canvas_app.test_client()

    val_resp = client.post(
        "/api/gas/validate-workflow-dag",
        json={
            "nodes": [
                {"id": "n1", "agentId": "geospatial_data_inspection_agent", "instructions": "Inspect dataset."},
                {"id": "n2", "agentId": "web_mapping_app_agent", "instructions": "Create web map."},
            ],
            "edges": [{"source": "n1", "target": "n2"}],
        },
    )
    assert val_resp.status_code == 200
    val_json = val_resp.get_json()
    assert val_json["valid"] is True
    assert val_json["execution_waves"] == [["n1"], ["n2"]]

    exec_resp = client.post(
        "/api/gas/execute-workflow-dag",
        json={
            "plan": {
                "steps": [
                    {
                        "step_id": "s1",
                        "agent_id": "geospatial_data_inspection_agent",
                        "instructions": "Inspect this dataset.",
                        "input_datasets": [str(sample_path)],
                    }
                ]
            }
        },
    )
    assert exec_resp.status_code == 200
    exec_json = exec_resp.get_json()
    assert exec_json["status"] == "successful"
    assert "s1" in exec_json["step_results"]


def test_mcp_bridge_jsonrpc_and_http_routes(tmp_path):
    from gas_server.entrypoints.gas_server import app as gas_app

    sample_path = _write_wgs84_geojson(tmp_path / "mcp_input.geojson")
    bridge = GasMcpBridge()

    init_resp = bridge.handle_jsonrpc_message({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert init_resp["result"]["serverInfo"]["name"] == "gas-mcp-bridge"

    tools_resp = bridge.handle_jsonrpc_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    tool_names = {t["name"] for t in tools_resp["result"]["tools"]}
    assert "gas_cloud_native_stac_agent" in tool_names
    assert "gas_geospatial_data_inspection_agent" in tool_names
    assert "gas_execute_workflow_dag" in tool_names

    res_list = bridge.handle_jsonrpc_message({"jsonrpc": "2.0", "id": 3, "method": "resources/list"})
    uris = {r["uri"] for r in res_list["result"]["resources"]}
    assert "gas://capabilities" in uris
    assert "gas://agents/cloud_native_stac_agent/describe" in uris

    res_read = bridge.handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "resources/read",
            "params": {"uri": "gas://agents/cloud_native_stac_agent/describe"},
        }
    )
    described = json.loads(res_read["result"]["contents"][0]["text"])
    assert described["profile"]["agent_id"] == "cloud_native_stac_agent"

    # Test HTTP endpoints on gas_server
    http_client = gas_app.test_client()
    http_tools = http_client.get("/mcp/tools")
    assert http_tools.status_code == 200
    assert any(t["name"] == "gas_cloud_native_stac_agent" for t in http_tools.get_json()["tools"])

    http_call = http_client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {
                "name": "gas_cloud_native_stac_agent",
                "arguments": {
                    "instructions": "Filter and summarize this dataset over State College.",
                    "input_datasets": [str(sample_path)],
                },
            },
        },
    )
    assert http_call.status_code == 200
    call_result = http_call.get_json()["result"]
    assert call_result["isError"] is False
    task_payload = json.loads(call_result["content"][0]["text"])
    assert task_payload["task"]["status"] == "successful"


def test_cloud_native_stac_agent_and_new_handbooks(tmp_path, monkeypatch):
    sample_path = _write_wgs84_geojson(tmp_path / "stac_filter_input.geojson")
    agent = CloudNativeStacAgent(api_key=None)
    agent.output_dir = tmp_path / "stac_out"

    # 1. Test local GeoParquet/GeoJSON dataset filtering and report generation
    res_local = agent.run(
        "Filter this dataset over State College and export GeoParquet with map and HTML report.",
        [str(sample_path)],
    )
    assert res_local["agent_name"] == "Cloud-Native STAC and GeoParquet Agent"
    out_files = res_local["outputs"]["output_files"]
    assert any(f.endswith(".parquet") for f in out_files)
    assert any(f.endswith(".png") for f in out_files)
    assert any(f.endswith(".html") for f in out_files)

    # 2. Test STAC API search with mocked STAC response
    class FakeStacResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "type": "FeatureCollection",
                "features": [
                    {
                        "id": "S2B_18TWL_20260701_0_L2A",
                        "collection": "sentinel-2-l2a",
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [
                                [
                                    [-77.95, 40.72],
                                    [-77.75, 40.72],
                                    [-77.75, 40.86],
                                    [-77.95, 40.86],
                                    [-77.95, 40.72],
                                ]
                            ],
                        },
                        "properties": {
                            "datetime": "2026-07-01T15:58:21Z",
                            "eo:cloud_cover": 4.2,
                            "platform": "sentinel-2b",
                            "proj:epsg": 32618,
                        },
                        "assets": {
                            "visual": {"href": "https://earth-search.aws.element84.com/S2B_visual.tif"}
                        },
                    }
                ],
            }

    monkeypatch.setattr(agent.session, "post", lambda *a, **kw: FakeStacResponse())
    res_stac = agent.run("Search Sentinel-2 scenes over State College with less than 15% cloud cover.")
    assert res_stac["outputs"]["stac_query"]["scene_count"] == 1
    assert res_stac["outputs"]["stac_query"]["source_mode"] == "live_stac_api"

    # 3. Verify new global handbooks are discovered by GeospatialDataRetrievalAgent
    retriever = GeospatialDataRetrievalAgent(api_key=None)
    handbooks = retriever.collect_handbook_files(source_dir=retriever.handbook_dir)
    _, source_dict = retriever.assemble_handbook_description(handbooks)
    source_ids = {v.get("ID") for v in source_dict.values()}
    assert {"Planetary_Computer_STAC", "Overture_Maps", "NASA_FIRMS_Wildfire"} <= source_ids


def test_gas_benchmark_suite_runs_and_exports_paper_artifacts(tmp_path):
    runner = GasBenchmarkRunner(work_dir=tmp_path / "bench")
    report = runner.run_benchmark_suite()

    assert report["summary"]["total_tasks"] == 5
    assert report["summary"]["task_success_rate"] == 1.0
    assert report["summary"]["schema_compliance_rate"] == 1.0
    assert report["summary"]["registered_agents_count"] >= 15
    assert report["contract_audit"]["all_capabilities_present"] is True

    exported = runner.export_reports(report, output_dir=tmp_path / "bench_out")
    assert Path(exported["json"]).is_file()
    assert Path(exported["markdown"]).is_file()
    assert Path(exported["latex"]).is_file()

    md_text = Path(exported["markdown"]).read_text(encoding="utf-8")
    tex_text = Path(exported["latex"]).read_text(encoding="utf-8")
    assert "GAS 1.0.0 Quantitative Benchmark Leaderboard" in md_text
    assert "\\begin{tabular}" in tex_text
    assert "cloud\\_native\\_stac\\_agent" in tex_text
