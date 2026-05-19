import json

from gas_server.agents import web_mapping_app_agent


def test_web_mapping_app_agent_reports_detailed_progress(tmp_path, monkeypatch):
    monkeypatch.setattr(web_mapping_app_agent, "DATA_DIR", tmp_path / "Data")
    dataset_path = tmp_path / "sample.geojson"
    dataset_path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"name": "A"},
                        "geometry": {"type": "Point", "coordinates": [-77.0, 40.0]},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    events = []
    agent = web_mapping_app_agent.WebMappingAppAgent(api_key=None)

    result = agent.run(
        "Create an interactive map with popups",
        [str(dataset_path)],
        progress_callback=events.append,
    )

    stages = {event["stage"] for event in events}
    assert "input_inspection" in stages
    assert "layer_preparation" in stages
    assert "fallback_start" in stages
    assert "fallback_complete" in stages
    assert "data_validation" in stages
    assert "complete" in stages
    assert result["outputs"]["dataset_path"].endswith(".html")


def test_web_mapping_app_fallback_includes_required_map_elements(tmp_path, monkeypatch):
    monkeypatch.setattr(web_mapping_app_agent, "DATA_DIR", tmp_path / "Data")
    dataset_path = tmp_path / "sample.geojson"
    dataset_path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"name": "A", "population": 10},
                        "geometry": {"type": "Point", "coordinates": [-77.0, 40.0]},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    agent = web_mapping_app_agent.WebMappingAppAgent(api_key=None)

    result = agent.run(
        "Create a choropleth web mapping app by population with popups",
        [str(dataset_path)],
    )

    html = open(result["outputs"]["dataset_path"], encoding="utf-8").read().lower()
    assert "l.control.layers" in html
    assert "map-title" in html
    assert "legend" in html
    valid, issues = agent._validate_html_output(
        "Create a choropleth web mapping app by population with popups",
        result["outputs"]["dataset_path"],
    )
    assert valid, issues


def test_web_mapping_app_validation_requires_real_layer_control(tmp_path):
    html_path = tmp_path / "app.html"
    html_path.write_text(
        """
        <html>
          <body>
            <h1>Test App</h1>
            <aside>Layer Control</aside>
            <div class="legend">Legend</div>
          </body>
        </html>
        """,
        encoding="utf-8",
    )
    agent = web_mapping_app_agent.WebMappingAppAgent(api_key=None)

    valid, issues = agent._validate_html_output("Create a web mapping app with layer control", str(html_path))

    assert not valid
    assert any("real Leaflet/Folium layer control" in issue for issue in issues)


def test_web_mapping_app_postprocess_positions_controls_and_legend(tmp_path):
    html_path = tmp_path / "app.html"
    html_path.write_text(
        """
        <html>
          <head><title>App</title></head>
          <body>
            <h1>Public Health App</h1>
            <div class="leaflet-control-layers" style="display:none">Layers</div>
            <div class="legend" style="position: fixed; left: 40%; top: 40%; width: 900px;">Legend</div>
            <script>L.control.layers({}, {}, {collapsed:false}).addTo(map);</script>
          </body>
        </html>
        """,
        encoding="utf-8",
    )
    agent = web_mapping_app_agent.WebMappingAppAgent(api_key=None)

    agent._postprocess_html_output(str(html_path))

    html = html_path.read_text(encoding="utf-8")
    assert "gas-web-map-app-safety-styles" in html
    assert "gas-web-map-app-safety-script" in html
    assert ".leaflet-control-layers" in html
    assert ".branca-colormap" in html
    assert "max-width: 340px" in html
    assert 'document.querySelectorAll("div")' not in html


def test_web_mapping_app_postprocess_does_not_target_app_containers(tmp_path):
    html_path = tmp_path / "app.html"
    html_path.write_text(
        """
        <html>
          <head><title>App</title></head>
          <body>
            <div class="app-header" style="position: fixed; width: 100%;">
              Pennsylvania obesity rate map
            </div>
            <div class="app-shell" style="position: fixed; width: 100%;">
              <div class="sidebar">Legend and data rate notes</div>
              <div class="map-wrap"><div class="folium-map" id="map"></div></div>
            </div>
            <div class="map-legend leaflet-control" style="position: fixed; width: 900px;">Legend</div>
            <script>L.control.layers({}, {}, {collapsed:false}).addTo(map);</script>
          </body>
        </html>
        """,
        encoding="utf-8",
    )
    agent = web_mapping_app_agent.WebMappingAppAgent(api_key=None)

    agent._postprocess_html_output(str(html_path))

    html = html_path.read_text(encoding="utf-8")
    assert "isLikelyAppContainer" in html
    assert "app-|app_|shell|sidebar|panel|header|map-wrap|map_container" in html
    assert 'document.querySelectorAll("div")' not in html


def test_web_mapping_app_postprocess_repairs_overlay_container_flow(tmp_path):
    html_path = tmp_path / "app.html"
    html_path.write_text(
        """
        <html>
          <head><title>App</title></head>
          <body>
            <div id="app-container" style="position: relative; height: 100%;">
              <div id="header">Population density map</div>
              <div id="sidebar">
                <div class="panel">
                  <div id="legend-container"></div>
                </div>
              </div>
            </div>
            <div class="folium-map" id="map"></div>
            <div class="legend leaflet-control">Legend</div>
            <script>L.control.layers({}, {}, {collapsed:false}).addTo(map);</script>
          </body>
        </html>
        """,
        encoding="utf-8",
    )
    agent = web_mapping_app_agent.WebMappingAppAgent(api_key=None)

    agent._postprocess_html_output(str(html_path))

    html = html_path.read_text(encoding="utf-8")
    assert "repairOverlayLayout" in html
    assert 'el.style.setProperty("position", "fixed", "important")' in html
    assert "#legend-container .legend" in html
    assert '!el.closest("#legend-container")' in html

