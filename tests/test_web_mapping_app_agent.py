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


def test_web_mapping_app_prepares_projected_vectors_for_leaflet(tmp_path, monkeypatch):
    import geopandas as gpd
    from shapely.geometry import box

    monkeypatch.setattr(web_mapping_app_agent, "DATA_DIR", tmp_path / "Data")
    dataset_path = tmp_path / "projected_pa.geojson"
    gdf = gpd.GeoDataFrame(
        {"name": ["A"], "population_density": [10.0], "geometry": [box(1_200_000, 200_000, 1_210_000, 210_000)]},
        crs="ESRI:102004",
    )
    gdf.to_file(dataset_path, driver="GeoJSON")

    agent = web_mapping_app_agent.WebMappingAppAgent(api_key=None)
    prepared_paths = agent._prepare_leaflet_dataset_paths([str(dataset_path)])

    assert len(prepared_paths) == 1
    assert prepared_paths[0].endswith("_wgs84.geojson")
    prepared = gpd.read_file(prepared_paths[0])
    assert prepared.crs.to_epsg() == 4326
    minx, miny, maxx, maxy = prepared.total_bounds
    assert -180 <= minx <= 180
    assert -180 <= maxx <= 180
    assert -90 <= miny <= 90
    assert -90 <= maxy <= 90


def test_web_mapping_app_prepares_epoch_millisecond_time_fields_for_leaflet(tmp_path, monkeypatch):
    import geopandas as gpd
    from shapely.geometry import Point

    monkeypatch.setattr(web_mapping_app_agent, "DATA_DIR", tmp_path / "Data")
    dataset_path = tmp_path / "earthquakes.geojson"
    gdf = gpd.GeoDataFrame(
        {
            "event_time": [1780357269610],
            "magnitude": [2.66],
            "place": ["18 km WSW of Johannesburg, CA"],
            "geometry": [Point(-117.813333333333, 35.3115)],
        },
        crs="EPSG:4326",
    )
    gdf.to_file(dataset_path, driver="GeoJSON")

    agent = web_mapping_app_agent.WebMappingAppAgent(api_key=None)
    prepared_paths = agent._prepare_leaflet_dataset_paths([str(dataset_path)])
    prepared_payload = json.loads(open(prepared_paths[0], encoding="utf-8").read())

    event_time = prepared_payload["features"][0]["properties"]["event_time"]
    assert isinstance(event_time, str)
    assert event_time.startswith("2026-06-01")
    assert event_time.endswith("UTC")
    assert not event_time.startswith("1970")


def test_web_mapping_app_prompt_instructs_epoch_unit_handling(tmp_path):
    agent = web_mapping_app_agent.WebMappingAppAgent(api_key=None)

    messages = agent._build_prompt(
        "Map earthquake event_time values",
        ["earthquakes.geojson"],
        [{"type": "vector", "columns": ["event_time", "magnitude"]}],
        str(tmp_path / "map.html"),
    )
    prompt_text = "\n".join(message["content"] for message in messages)

    assert "13-digit values are milliseconds" in prompt_text
    assert 'pd.to_datetime(values, unit="ms", utc=True)' in prompt_text
    assert "new Date(value) expects milliseconds" in prompt_text


def test_web_mapping_app_prompt_keeps_map_legend_title_with_legend_box(tmp_path):
    agent = web_mapping_app_agent.WebMappingAppAgent(api_key=None)

    messages = agent._build_prompt(
        "Map earthquake events with a magnitude legend",
        ["earthquakes.geojson"],
        [{"type": "vector", "columns": ["magnitude"]}],
        str(tmp_path / "map.html"),
    )
    prompt_text = "\n".join(message["content"] for message in messages)

    assert "legend title inside the same legend box" in prompt_text
    assert 'Do not put a detached "Legend" title in the left panel' in prompt_text


def test_web_mapping_app_validation_rejects_projected_leaflet_bounds(tmp_path):
    html_path = tmp_path / "bad_bounds.html"
    html_path.write_text(
        """
        <html>
          <body>
            <h1>Projected Bounds App</h1>
            <div class="leaflet-control-layers">Layers</div>
            <div class="legend">Legend</div>
            <script>
              const map = L.map("map");
              map.fitBounds([[191829.35467364397, 1271502.8555646916], [523962.19127071055, 1786991.554818828]]);
            </script>
          </body>
        </html>
        """,
        encoding="utf-8",
    )
    agent = web_mapping_app_agent.WebMappingAppAgent(api_key=None)

    valid, issues = agent._validate_html_output("Create a choropleth web mapping app by density", str(html_path))

    assert not valid
    assert any("projected coordinates" in issue for issue in issues)


def test_web_mapping_app_validation_rejects_projected_latlng_bounds(tmp_path):
    html_path = tmp_path / "bad_latlng_bounds.html"
    html_path.write_text(
        """
        <html>
          <body>
            <h1>Projected Bounds App</h1>
            <div class="leaflet-control-layers">Layers</div>
            <div class="legend">Legend</div>
            <script>
              const map = L.map("map", {center: [357895.77, 1529247.20], zoom: 7});
              const bounds = L.latLngBounds(
                L.latLng(191829.35467364397, 1271502.8555646916),
                L.latLng(523962.19127071055, 1786991.554818828)
              );
              map.fitBounds(bounds);
            </script>
          </body>
        </html>
        """,
        encoding="utf-8",
    )
    agent = web_mapping_app_agent.WebMappingAppAgent(api_key=None)

    valid, issues = agent._validate_html_output("Create a choropleth web mapping app by density", str(html_path))

    assert not valid
    assert any("projected coordinates" in issue for issue in issues)


def test_web_mapping_app_validation_rejects_projected_embedded_geojson(tmp_path):
    html_path = tmp_path / "bad_geojson.html"
    html_path.write_text(
        """
        <html>
          <body>
            <h1>Projected GeoJSON App</h1>
            <div class="leaflet-control-layers">Layers</div>
            <div class="legend">Legend</div>
            <script>
              const geojsonData = {
                "type": "FeatureCollection",
                "features": [{
                  "type": "Feature",
                  "properties": {"name": "Projected"},
                  "geometry": {
                    "type": "Point",
                    "coordinates": [1356944.9700070599, 248003.42842796544]
                  }
                }]
              };
              L.geoJSON(geojsonData).addTo(L.map("map"));
            </script>
          </body>
        </html>
        """,
        encoding="utf-8",
    )
    agent = web_mapping_app_agent.WebMappingAppAgent(api_key=None)

    valid, issues = agent._validate_html_output("Create a choropleth web mapping app by density", str(html_path))

    assert not valid
    assert any("projected GeoJSON coordinates" in issue for issue in issues)


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


def test_web_mapping_app_postprocess_repairs_python_rgba_tuple_legend_colors(tmp_path):
    html_path = tmp_path / "bad_legend_colors.html"
    html_path.write_text(
        """
        <html>
          <head><title>App</title></head>
          <body>
            <h1>Population Map</h1>
            <div class="leaflet-control-layers">Layers</div>
            <div class="map-legend">
              <div class="legend-row">
                <span class="swatch" style="background:(1.0, 0.903267973856209, 0.5725490196078431, 1.0);"></span>
                <span>Low population</span>
              </div>
            </div>
            <script>L.control.layers({}, {}, {collapsed:false}).addTo(map);</script>
          </body>
        </html>
        """,
        encoding="utf-8",
    )
    agent = web_mapping_app_agent.WebMappingAppAgent(api_key=None)

    valid_before, issues_before = agent._validate_html_output(
        "Create a choropleth web mapping app by population", str(html_path)
    )
    agent._postprocess_html_output(str(html_path))
    valid_after, issues_after = agent._validate_html_output(
        "Create a choropleth web mapping app by population", str(html_path)
    )

    html = html_path.read_text(encoding="utf-8")
    assert not valid_before
    assert any("color tuples" in issue for issue in issues_before)
    assert "background:(1.0" not in html
    assert "background:rgba(255, 230, 146, 1)" in html
    assert valid_after, issues_after


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
    assert "max-width: min(340px, calc(100vw - 48px))" in html
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


def test_web_mapping_app_postprocess_preserves_separate_sidebar_app_container(tmp_path):
    html_path = tmp_path / "separate_sidebar.html"
    html_path.write_text(
        """
        <html>
          <head><title>App</title></head>
          <body>
            <div class="folium-map" id="map"></div>
            <div class="app-container">
              <div class="sidebar">Earthquake summary and legend</div>
            </div>
            <div class="leaflet-control-layers">Layers</div>
            <script>L.control.layers({}, {}, {collapsed:false}).addTo(map);</script>
          </body>
        </html>
        """,
        encoding="utf-8",
    )
    agent = web_mapping_app_agent.WebMappingAppAgent(api_key=None)

    agent._postprocess_html_output(str(html_path))

    html = html_path.read_text(encoding="utf-8")
    assert 'el.classList.contains("app-container") && el.querySelector(".sidebar")' in html
    assert 'el.style.setProperty("max-height", "34vh", "important")' in html


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
    assert 'el.style.setProperty("max-height", "34vh", "important")' in html
    assert "#legend-container .legend" in html
    assert '!el.closest("#legend-container")' in html


def test_web_mapping_app_postprocess_replaces_old_safety_block(tmp_path):
    html_path = tmp_path / "app.html"
    html_path.write_text(
        """
        <html>
          <head>
            <style id="gas-web-map-app-safety-styles">.old-rule { color: red; }</style>
            <script id="gas-web-map-app-safety-script">console.log("old safety");</script>
          </head>
          <body>
            <h1>Population Map</h1>
            <div class="leaflet-control-layers">Layers</div>
            <div class="legend">Legend</div>
            <script>L.control.layers({}, {}, {collapsed:false}).addTo(map);</script>
          </body>
        </html>
        """,
        encoding="utf-8",
    )
    agent = web_mapping_app_agent.WebMappingAppAgent(api_key=None)

    agent._postprocess_html_output(str(html_path))

    html = html_path.read_text(encoding="utf-8")
    assert "old safety" not in html
    assert ".old-rule" not in html
    assert html.count("gas-web-map-app-safety-styles") == 1
    assert html.count("gas-web-map-app-safety-script") == 1
    assert 'el.style.setProperty("max-height", "34vh", "important")' in html

