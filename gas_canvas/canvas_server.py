import os
import sys
import json
import tempfile
from pathlib import Path
import requests
from flask import Flask, Response, request, send_from_directory, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# Default GAS server url
DEFAULT_GAS_SERVER = "https://www.geospatial-agentic-services.online"

DIST_DIR = Path(__file__).resolve().parent / "dist"

@app.route("/api/health", methods=["GET"])
def health():
    from datetime import datetime
    return jsonify({
        "status": "ok",
        "time": datetime.utcnow().isoformat() + "Z"
    })

@app.route("/api/gas/capabilities", methods=["GET"])
def capabilities():
    server_url = request.args.get("serverUrl", DEFAULT_GAS_SERVER)
    server_url = server_url.rstrip("/")
    try:
        # Request capabilities
        cap_url = f"{server_url}/?SERVICE=GAS&VERSION=1.0.0&REQUEST=GetCapabilities"
        resp = requests.get(cap_url, timeout=30)
        resp.raise_for_status()
        capabilities_data = resp.json()
        
        # Extract agent list
        agents = capabilities_data.get("agents", [])
        agents_list = []
        for a in agents:
            if isinstance(a, dict):
                agent_id = a.get("agent_id") or a.get("name")
                if agent_id:
                    agents_list.append(agent_id)
            elif isinstance(a, str):
                agents_list.append(a)
                
        return jsonify({
            "capabilities": capabilities_data,
            "agentsList": agents_list
        })
    except Exception as e:
        return jsonify({"error": "Failed to fetch GAS server capabilities", "details": str(e)}), 500

@app.route("/api/gas/agent-details", methods=["GET"])
def agent_details():
    server_url = request.args.get("serverUrl", DEFAULT_GAS_SERVER)
    server_url = server_url.rstrip("/")
    agent_id = request.args.get("agentId")
    if not agent_id:
        return jsonify({"error": "agentId parameter is required"}), 400
        
    try:
        # Standard describe agent URL
        desc_url = f"{server_url}/?SERVICE=GAS&VERSION=1.0.0&REQUEST=DescribeAgent&agent_id={agent_id}"
        resp = requests.get(desc_url, timeout=30)
        resp.raise_for_status()
        return jsonify(resp.json())
    except Exception as e:
        return jsonify({"error": f"Failed to fetch description for agent {agent_id}", "details": str(e)}), 500

def build_execute_task_request(instructions, options):
    mode = options.get("mode", "sync")
    artifact_delivery = options.get("artifactDelivery", "URL")
    payload = {
        "task": {
            "instructions": instructions,
            "mode": mode
        },
        "outputs": {
            "artifact_delivery": "Encoded" if artifact_delivery.lower() == "encoded" else "URL"
        }
    }
    
    if "inputDatasets" in options and options["inputDatasets"] is not None:
        datasets = options["inputDatasets"]
        payload["inputs"] = {
            "input_datasets": datasets if isinstance(datasets, list) else [datasets]
        }
        
    params = {}
    if "model" in options:
        params["model"] = options["model"]
    if "parameters" in options:
        params.update(options["parameters"])
    if params:
        payload["parameters"] = params
        
    creds = {}
    if "credentials" in options:
        creds.update(options["credentials"])
    if creds:
        payload["credentials"] = creds
        
    if "metadata" in options:
        payload["metadata"] = options["metadata"]
        
    return payload

@app.route("/api/gas/run-task", methods=["POST"])
def run_task():
    body = request.get_json() or {}
    agent_id = body.get("agentId")
    instructions = body.get("instructions")
    options = body.get("options") or {}
    server_url = body.get("serverUrl", DEFAULT_GAS_SERVER)
    server_url = server_url.rstrip("/")
    
    if not agent_id or not instructions:
        return jsonify({"error": "agentId and instructions are required parameters."}), 400
        
    # Merge keys if OPENAI_API_KEY is in environment variables and not supplied
    if "credentials" not in options:
        options["credentials"] = {}
    if "OPENAI_API_KEY" not in options["credentials"] and os.environ.get("OPENAI_API_KEY"):
        options["credentials"]["OPENAI_API_KEY"] = os.environ.get("OPENAI_API_KEY")
        
    task_payload = build_execute_task_request(instructions, options)
    is_stream = options.get("mode") == "stream"
    
    try:
        url = f"{server_url}/agents/{agent_id}/tasks"
        
        if is_stream:
            # We must proxy stream response chunk-by-chunk
            def generate():
                try:
                    resp = requests.post(url, json=task_payload, stream=True, timeout=300)
                    resp.raise_for_status()
                    for line in resp.iter_lines():
                        if line:
                            decoded = line.decode("utf-8")
                            sanitized = decoded.replace(': NaN', ': null').replace('[NaN', '[null').replace(', NaN', ', null')
                            try:
                                event_obj = json.loads(sanitized)
                                yield f"data: {json.dumps(event_obj)}\n\n"
                            except Exception:
                                # if it's already in SSE format or has text, just forward it
                                yield f"data: {sanitized}\n\n"
                                
                    yield f"data: {json.dumps({'event': 'stream_done', 'payload': {'success': True}})}\n\n"
                except Exception as ex:
                    yield f"data: {json.dumps({'event': 'stream_error', 'payload': {'message': str(ex)}})}\n\n"
                    
            return Response(generate(), mimetype="text/event-stream", headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no"
            })
        else:
            resp = requests.post(url, json=task_payload, timeout=300)
            resp.raise_for_status()
            return jsonify(resp.json())
            
    except Exception as e:
        return jsonify({"error": f"Execution error on agent {agent_id}", "details": str(e)}), 500

@app.route("/api/parse-gpkg", methods=["GET"])
def parse_gpkg():
    gpkg_url = request.args.get("url")
    if not gpkg_url:
        return jsonify({"error": "url is required"}), 400
        
    try:
        resp = requests.get(gpkg_url, timeout=60)
        resp.raise_for_status()
    except Exception as e:
        return jsonify({"error": "Failed to fetch GPKG file", "details": str(e)}), 500
        
    temp_file = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".gpkg", delete=False) as tmp:
            tmp.write(resp.content)
            temp_file_path = tmp.name
            temp_file = temp_file_path
            
        features = []
        parsed = False
        
        # 1. Try fiona
        try:
            import fiona
            with fiona.open(temp_file_path) as src:
                for feature in src:
                    if hasattr(feature, "properties"):
                        features.append({
                            "type": "Feature",
                            "geometry": dict(feature.geometry) if feature.geometry else None,
                            "properties": dict(feature.properties)
                        })
                    else:
                        features.append(dict(feature))
            parsed = True
        except Exception as fiona_err:
            print(f"Fiona parsing failed or not installed: {fiona_err}. Falling back to sqlite3 parser.")
            
        # 2. Fallback to sqlite3 (with optional shapely for geometry decoding)
        if not parsed:
            import sqlite3
            try:
                from shapely.wkb import loads as wkb_loads
                from shapely.geometry import mapping
                has_shapely = True
            except ImportError:
                has_shapely = False
                print("shapely not installed. GPKG features will be parsed with empty geometries.")
            
            try:
                conn = sqlite3.connect(temp_file_path)
                cursor = conn.cursor()
                
                # Find feature tables
                cursor.execute("SELECT table_name FROM gpkg_contents WHERE data_type = 'features'")
                tables = [row[0] for row in cursor.fetchall()]
                
                for table in tables:
                    cursor.execute("SELECT column_name FROM gpkg_geometry_columns WHERE table_name = ?", (table,))
                    geom_row = cursor.fetchone()
                    if not geom_row:
                        continue
                    geom_col = geom_row[0]
                    
                    cursor.execute(f"PRAGMA table_info({table})")
                    cols = [col[1] for col in cursor.fetchall()]
                    
                    cursor.execute(f"SELECT * FROM {table}")
                    rows = cursor.fetchall()
                    
                    for row in rows:
                        props = {}
                        geom_val = None
                        for col_name, val in zip(cols, row):
                            if col_name == geom_col:
                                geom_val = val
                            else:
                                props[col_name] = val
                                
                        geometry = None
                        if geom_val and has_shapely:
                            try:
                                # Header is at least 8 bytes
                                if len(geom_val) > 8 and geom_val[0:2] == b'GP':
                                    flags = geom_val[3]
                                    envelope_indicator = (flags & 0x0E) >> 1
                                    envelope_sizes = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}
                                    envelope_size = envelope_sizes.get(envelope_indicator, 0)
                                    header_size = 8 + envelope_size
                                    wkb = geom_val[header_size:]
                                    geom_obj = wkb_loads(wkb)
                                    geometry = mapping(geom_obj)
                            except Exception as ex:
                                print(f"Error parsing geometry in sqlite3 fallback: {ex}")
                                
                        features.append({
                            "type": "Feature",
                            "geometry": geometry,
                            "properties": props
                        })
                conn.close()
                parsed = True
            except Exception as sql_err:
                print(f"Sqlite3 GPKG parsing failed: {sql_err}")
            
        if not parsed:
            raise Exception("Failed to parse GPKG file using both Fiona and Sqlite3 fallbacks.")
            
        return jsonify({
            "type": "FeatureCollection",
            "features": features
        })
    except Exception as e:
        return jsonify({"error": "Failed to parse GPKG", "details": str(e)}), 500
    finally:
        if temp_file and os.path.exists(temp_file):
            try:
                os.remove(temp_file)
            except Exception:
                pass

# Serving the Vite frontend
# If deployed on PythonAnywhere, Flask might run with or without subpaths.
# We configure it to serve index.html for "/" and all undefined static paths.
@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve_frontend(path):
    if path != "" and os.path.exists(DIST_DIR / path):
        return send_from_directory(DIST_DIR, path)
    else:
        return send_from_directory(DIST_DIR, "index.html")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    app.run(host="0.0.0.0", port=port, debug=True)
