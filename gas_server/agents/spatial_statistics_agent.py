from __future__ import annotations

import importlib.util
import html as html_lib
import json
import os
import platform
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import geopandas as gpd
import pandas as pd

from gas_server.core.file_naming import build_output_filename
from gas_server.core.geo_agent import GeoAgent
from gas_server.core.llm_client import build_llm_client, format_service_name
from gas_server.core.config import DATA_DIR, ensure_runtime_dirs


ensure_runtime_dirs()


PYSAL_USAGE_CATALOG = """
PySAL usage catalog for generated analysis code:

pysal.lib.cg: Computational Geometry
```python
from libpysal import cg
point = cg.Point((0, 0))
polygon = cg.Polygon([cg.Point((0, 0)), cg.Point((1, 0)), cg.Point((1, 1)), cg.Point((0, 1))])
```

pysal.lib.examples: Example datasets
```python
from libpysal import examples
path = examples.get_path("columbus.shp")
```

pysal.lib.graph: Graph spatial weights
```python
from libpysal.graph import Graph
graph = Graph.build_contiguity(gdf)
```

pysal.lib.io: Input-output
```python
from libpysal import io
reader = io.open("weights.gal")
w = reader.read()
reader.close()
```

pysal.lib.weights: Spatial weights
```python
from libpysal.weights import Queen, KNN
wq = Queen.from_dataframe(gdf)
wq.transform = "r"
wknn = KNN.from_dataframe(gdf, k=5)
```

pysal.explore.esda: Spatial autocorrelation
```python
from esda.moran import Moran, Moran_Local
from esda.getisord import G, G_Local
y = gdf["value"].to_numpy()
mi = Moran(y, wq)
lisa = Moran_Local(y, wq)
```

pysal.explore.giddy: Geospatial distribution dynamics
```python
from giddy.markov import Markov, Spatial_Markov
classes_by_period = values_by_period.astype(int)
markov = Markov(classes_by_period)
```

pysal.explore.inequality: Spatial inequality
```python
from inequality.gini import Gini
gini = Gini(gdf["income"].to_numpy())
```

pysal.explore.momepy: Urban morphology
```python
import momepy
gdf["area"] = momepy.Area(gdf).series
gdf["perimeter"] = momepy.Perimeter(gdf).series
```

pysal.explore.pointpats: Point pattern analysis
```python
from pointpats import PointPattern
points = [(geom.x, geom.y) for geom in point_gdf.geometry]
pattern = PointPattern(points)
```

pysal.explore.segregation: Segregation analysis
```python
from segregation.singlegroup import Dissim
dissim = Dissim(gdf, group_pop_var="group_pop", total_pop_var="total_pop")
```

pysal.explore.spaghetti: Spatial analysis on networks
```python
import spaghetti
network = spaghetti.Network(in_data=streets_gdf)
```

pysal.model.access: Spatial accessibility
```python
from access import Access
access_model = Access(demand_df=demand, demand_index="id", demand_value="population")
```

pysal.model.gwlearn: Geographically weighted modeling
```python
# Use gwlearn when installed for geographically weighted machine learning workflows.
# Inspect gwlearn API in the runtime before calling project-specific estimators.
```

pysal.model.mgwr: Multiscale geographically weighted regression
```python
from mgwr.gwr import GWR
from mgwr.sel_bw import Sel_BW
bw = Sel_BW(coords, y, X).search()
model = GWR(coords, y, X, bw).fit()
```

pysal.model.spglm: Sparse generalized linear models
```python
from spglm.glm import GLM
from spglm.family import Gaussian
glm = GLM(y, X, family=Gaussian()).fit()
```

pysal.model.spint: Spatial interaction modeling
```python
from spint.gravity import Gravity
gravity = Gravity(origin, destination, cost, flow)
```

pysal.model.spopt: Spatial optimization
```python
from spopt.locate import PMedian
model = PMedian.from_cost_matrix(cost_matrix, weights, p_facilities=5)
model.solve()
```

pysal.model.spreg: Spatial regression and econometrics
```python
from spreg import OLS, ML_Lag, ML_Error
ols = OLS(y, X, name_y="y", name_x=["x1", "x2"])
lag = ML_Lag(y, X, w=wq, name_y="y")
```

pysal.model.tobler: Areal interpolation and dasymetric mapping
```python
from tobler.area_weighted import area_interpolate
interpolated = area_interpolate(source_df, target_df, extensive_variables=["population"])
```

pysal.viz.mapclassify: Choropleth map classification
```python
import mapclassify
scheme = mapclassify.Quantiles(gdf["value"], k=5)
gdf["class"] = scheme.yb
```

pysal.viz.splot: Visualization for PySAL analytics
```python
from splot.esda import lisa_cluster
ax = lisa_cluster(lisa, gdf)
```
"""


REPORT_ASSET_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".svg",
    ".webp",
    ".gif",
    ".html",
    ".htm",
    ".pdf",
    ".csv",
    ".geojson",
}


class SpatialStatisticsAgent(GeoAgent):
    agent_id = "spatial_statistics_agent"
    agent_name = "Spatial Statistics Agent"
    agent_version = "1.0.0"
    agent_description = "Runs PySAL-based spatial statistics, spatial econometrics, and geospatial analytics workflows."
    requires_input_datasets = True

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str | None = None,
        max_iterations: int = 5,
        timeout_seconds: int = 180,
    ):
        super().__init__(
            api_key=api_key,
            model=model or "gpt-5.2",
            output_dir=DATA_DIR / self.agent_id,
        )
        self.max_iterations = max_iterations
        self.timeout_seconds = timeout_seconds
        self.service_name = format_service_name(self.agent_name)
        self.client = build_llm_client(service_name=self.service_name, openai_api_key=self.api_key)
        self.generated_code: str | None = None
        self.last_error = ""
        self.available_libraries = self._available_libraries()

    def _available_libraries(self) -> list[str]:
        candidates = (
            "pysal",
            "libpysal",
            "esda",
            "giddy",
            "inequality",
            "momepy",
            "pointpats",
            "segregation",
            "spaghetti",
            "access",
            "gwlearn",
            "mgwr",
            "spglm",
            "spint",
            "spopt",
            "spreg",
            "tobler",
            "mapclassify",
            "splot",
            "geopandas",
            "pandas",
            "numpy",
        )
        return [name for name in candidates if importlib.util.find_spec(name) is not None]

    def _extract_python_code(self, text: str | None) -> str:
        if not text:
            return ""
        match = re.search(r"```python\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
        if match:
            return match.group(1).strip()
        match = re.search(r"```\s*(.*?)```", text, re.DOTALL)
        return match.group(1).strip() if match else text.strip()

    def _resolve_python_runner(self) -> str:
        executable = (sys.executable or "").strip()
        if executable and "python" in os.path.basename(executable).lower():
            return executable
        return "python"

    def _inspect_dataset(self, path: str) -> Dict[str, Any]:
        dataset = Path(path)
        info: Dict[str, Any] = {
            "path": str(dataset),
            "name": dataset.name,
            "format": dataset.suffix.lower().lstrip("."),
            "exists": dataset.exists(),
            "type": "unknown",
        }
        if not dataset.exists():
            info["error"] = "File does not exist."
            return info

        try:
            if dataset.suffix.lower() in {".geojson", ".json", ".gpkg", ".shp", ".gml", ".kml"}:
                gdf = gpd.read_file(dataset)
                numeric_columns = [str(col) for col in gdf.select_dtypes(include="number").columns[:30]]
                info.update(
                    {
                        "type": "vector",
                        "feature_count": int(len(gdf)),
                        "crs": str(gdf.crs) if gdf.crs else None,
                        "columns": [str(col) for col in gdf.columns[:40]],
                        "numeric_columns": numeric_columns,
                        "geometry_types": sorted(str(value) for value in gdf.geometry.geom_type.dropna().unique())[:10],
                        "bounds": [float(value) for value in gdf.total_bounds] if len(gdf) else None,
                    }
                )
                del gdf
            elif dataset.suffix.lower() == ".csv":
                df = pd.read_csv(dataset, nrows=500)
                info.update(
                    {
                        "type": "table",
                        "columns": [str(col) for col in df.columns[:40]],
                        "numeric_columns": [str(col) for col in df.select_dtypes(include="number").columns[:30]],
                        "sample_rows": min(len(df), 5),
                    }
                )
                del df
        except Exception as exc:
            info["error"] = str(exc)
        return info

    def _build_prompt(
        self,
        task: str,
        dataset_paths: list[str],
        dataset_context: list[dict[str, Any]],
        text_report_path: str,
        html_report_path: str,
    ) -> list[dict[str, str]]:
        system = (
            "You are an expert spatial statistics agent specializing in PySAL and the broader PySAL ecosystem. "
            "Choose appropriate spatial statistics, spatial weights, ESDA, point pattern, inequality, accessibility, "
            "spatial regression, optimization, interpolation, or visualization methods based on the user's request "
            "and dataset metadata. Generate robust Python code that executes the analysis and writes a plain text "
            "modeling report and a polished HTML modeling report. Use only the provided datasets. Do not download external data. "
            "If maps or charts are generated, save them beside the reports using REPORT_ASSET_DIR and include them in the HTML report with clear captions. "
            "If a requested PySAL subpackage is unavailable in the runtime, write a clear limitation and use an "
            "available compatible method when reasonable. Return only Python code in a python fenced block."
        )
        user = f"""
Task:
{task}

Dataset paths:
{dataset_paths}

Dataset context:
{dataset_context}

Available libraries:
{self.available_libraries}

Required outputs:
- Plain text report path: {text_report_path}
- Polished HTML report path: {html_report_path}

Both reports must include:
- objective
- input datasets used
- selected PySAL methods and why they fit the task
- model/statistical outputs
- interpretation
- assumptions and limitations
- reproducibility notes

The HTML report must be professionally formatted with a title, section headings, readable typography, tables where appropriate, and embedded/referenced maps or charts if any are produced.

When creating maps, charts, or supporting files, write them into this directory and reference their filenames in the HTML report:
{Path(html_report_path).parent}

The service response itself is JSON, so do not create a separate JSON results artifact. Put the important model outputs, selected methods, parameters, diagnostics, and warnings into the text and HTML reports.

{PYSAL_USAGE_CATALOG}
"""
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    def _candidate_methods(self, task: str, dataset_context: list[dict[str, Any]]) -> list[str]:
        text = task.lower()
        candidates: list[str] = []
        if any(term in text for term in ("moran", "autocorrelation", "hotspot", "hot spot", "cluster", "lisa", "getis")):
            candidates.extend(["libpysal.weights", "esda"])
        if any(term in text for term in ("regression", "econometric", "lag", "error model", "spatial model")):
            candidates.extend(["libpysal.weights", "spreg"])
        if any(term in text for term in ("gwr", "geographically weighted", "mgwr")):
            candidates.extend(["mgwr"])
        if any(term in text for term in ("inequality", "gini", "segregation")):
            candidates.extend(["inequality", "segregation"])
        if any(term in text for term in ("point pattern", "nearest neighbor", "ripley", "points")):
            candidates.extend(["pointpats"])
        if any(term in text for term in ("network", "street", "route")):
            candidates.extend(["spaghetti"])
        if any(term in text for term in ("access", "accessibility", "facility")):
            candidates.extend(["access", "spopt"])
        if any(term in text for term in ("interpolation", "dasymetric", "areal")):
            candidates.extend(["tobler"])
        if any(term in text for term in ("classify", "choropleth", "quantile", "natural breaks")):
            candidates.extend(["mapclassify", "splot"])

        has_vector = any(item.get("type") == "vector" for item in dataset_context)
        has_numeric = any(item.get("numeric_columns") for item in dataset_context)
        if has_vector and has_numeric and not candidates:
            candidates.extend(["libpysal.weights", "esda"])
        if has_vector and "libpysal.weights" not in candidates:
            candidates.insert(0, "libpysal.weights")
        return list(dict.fromkeys(candidates or ["pysal ecosystem method selection"]))

    def _execute_code(self, code: str, text_report_path: str, html_report_path: str) -> tuple[bool, str, str]:
        script_path = Path(self.output_dir) / build_output_filename(
            "spatial statistics generated script",
            extension=".py",
            fallback="spatial_statistics_script",
        )
        script_path.write_text(code, encoding="utf-8")
        env = os.environ.copy()
        env["REPORT_TXT"] = text_report_path
        env["TXT_REPORT"] = text_report_path
        env["HTML_REPORT"] = html_report_path
        env["REPORT_ASSET_DIR"] = str(Path(html_report_path).parent)
        Path(self.output_dir).mkdir(parents=True, exist_ok=True)
        process = subprocess.run(
            [self._resolve_python_runner(), str(script_path)],
            capture_output=True,
            text=True,
            timeout=self.timeout_seconds,
            env=env,
            cwd=self.output_dir,
        )
        stdout = process.stdout or ""
        stderr = process.stderr or ""
        success = (
            process.returncode == 0
            and Path(text_report_path).is_file()
            and Path(html_report_path).is_file()
        )
        return success, stdout, stderr

    def _fallback_report(
        self,
        task: str,
        dataset_paths: list[str],
        dataset_context: list[dict[str, Any]],
        text_report_path: str,
        html_report_path: str,
    ) -> None:
        Path(text_report_path).parent.mkdir(parents=True, exist_ok=True)
        available = ", ".join(self.available_libraries) or "none detected"
        report = f"""Spatial Statistics Modeling Report
==================================

Objective
---------
{task}

Input Datasets
--------------
{json.dumps(dataset_context, indent=2)}

Selected Methods
----------------
The agent could not run LLM-generated PySAL code in this request, so it produced a diagnostic modeling report. Available runtime libraries: {available}.

Recommended PySAL Workflow
--------------------------
- Build spatial weights with `libpysal.weights.Queen`, `Rook`, or `KNN`.
- Run spatial autocorrelation with `esda.Moran`, `esda.Moran_Local`, or Getis-Ord statistics when numeric variables are available.
- Use `spreg` for spatial econometric models when dependent and explanatory variables are specified.
- Use `mgwr` for geographically weighted regression when coordinates and enough observations are available.
- Use `pointpats` for point pattern workflows and `mapclassify`/`splot` for diagnostics and visualization.

Assumptions And Limitations
---------------------------
- This fallback report does not replace a full PySAL model run.
- The request may require installing PySAL ecosystem packages in the server environment.
- The final statistical model depends on selecting suitable dependent, explanatory, and spatial weighting variables.
"""
        Path(text_report_path).write_text(report, encoding="utf-8")
        escaped_task = html_lib.escape(task)
        escaped_context = html_lib.escape(json.dumps(dataset_context, indent=2))
        escaped_available = html_lib.escape(available)
        html_report = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Spatial Statistics Modeling Report</title>
  <style>
    body {{ margin: 0; font-family: Arial, sans-serif; color: #1f2937; background: #f3f4f6; }}
    main {{ max-width: 980px; margin: 0 auto; padding: 32px 24px 48px; background: #ffffff; min-height: 100vh; }}
    h1 {{ margin: 0 0 8px; font-size: 30px; color: #111827; }}
    h2 {{ margin-top: 30px; padding-bottom: 6px; border-bottom: 1px solid #d1d5db; color: #1d4ed8; }}
    .subtitle {{ color: #4b5563; font-size: 15px; margin-bottom: 24px; }}
    .notice {{ background: #eff6ff; border-left: 4px solid #2563eb; padding: 12px 14px; margin: 18px 0; }}
    pre {{ background: #111827; color: #f9fafb; padding: 14px; overflow: auto; border-radius: 6px; }}
    ul {{ line-height: 1.55; }}
    .meta {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; }}
    .card {{ border: 1px solid #d1d5db; border-radius: 6px; padding: 12px; background: #f9fafb; }}
  </style>
</head>
<body>
<main>
  <h1>Spatial Statistics Modeling Report</h1>
  <div class="subtitle">PySAL-oriented diagnostic report generated by the GAS Spatial Statistics Agent.</div>
  <section class="notice"><strong>Objective:</strong> {escaped_task}</section>
  <section>
    <h2>Runtime Context</h2>
    <div class="meta">
      <div class="card"><strong>Available libraries</strong><br>{escaped_available}</div>
      <div class="card"><strong>Artifacts</strong><br>Plain text report, HTML report</div>
    </div>
  </section>
  <section>
    <h2>Input Datasets</h2>
    <pre>{escaped_context}</pre>
  </section>
  <section>
    <h2>Recommended PySAL Workflow</h2>
    <ul>
      <li>Build spatial weights with <code>libpysal.weights.Queen</code>, <code>Rook</code>, or <code>KNN</code>.</li>
      <li>Run spatial autocorrelation with <code>esda.Moran</code>, <code>esda.Moran_Local</code>, or Getis-Ord statistics.</li>
      <li>Use <code>spreg</code> for spatial econometric models when variables are specified.</li>
      <li>Use <code>mgwr</code> for geographically weighted regression when observations and coordinates are suitable.</li>
      <li>Use <code>mapclassify</code> and <code>splot</code> for classification and diagnostic visualization.</li>
    </ul>
  </section>
  <section>
    <h2>Maps And Charts</h2>
    <p>No generated map or chart artifacts were available in the fallback path. When generated analysis code produces figures, they should be embedded or referenced in this section.</p>
  </section>
  <section>
    <h2>Assumptions And Limitations</h2>
    <ul>
      <li>This fallback report does not replace a full PySAL model run.</li>
      <li>Advanced workflows require the relevant PySAL ecosystem packages in the runtime.</li>
      <li>Model validity depends on variable selection, sample size, geometry quality, and spatial weights.</li>
    </ul>
  </section>
</main>
</body>
</html>
"""
        Path(html_report_path).write_text(html_report, encoding="utf-8")

    def _discover_report_assets(self, text_report_path: str, html_report_path: str, created_after: float) -> list[str]:
        report_dir = Path(html_report_path).parent
        excluded = {Path(text_report_path).resolve(), Path(html_report_path).resolve()}
        assets: dict[str, Path] = {}

        if report_dir.is_dir():
            for candidate in report_dir.iterdir():
                if not candidate.is_file():
                    continue
                try:
                    resolved = candidate.resolve()
                except OSError:
                    continue
                if resolved in excluded:
                    continue
                if candidate.suffix.lower() not in REPORT_ASSET_EXTENSIONS:
                    continue
                if candidate.stat().st_mtime < created_after - 1:
                    continue
                assets[str(resolved)] = resolved

        html_path = Path(html_report_path)
        if html_path.is_file():
            try:
                html = html_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                html = ""
            references = re.findall(r"\b(?:src|href)=(?:['\"])([^'\"]+)(?:['\"])", html)
            references.extend(re.findall(r"url\((?:['\"]?)([^)'\"\s]+)(?:['\"]?)\)", html))
            for reference in references:
                if not reference or reference.startswith(("#", "data:", "http://", "https://", "mailto:", "tel:")):
                    continue
                candidate = (report_dir / Path(reference).name).resolve()
                if candidate in excluded or not candidate.is_file():
                    continue
                assets[str(candidate)] = candidate

        return [str(path) for path in sorted(assets.values(), key=lambda item: item.name.lower())]

    def run(
        self,
        query: str,
        input_dataset_paths: list[str] | str | None = None,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        start_time = time.time()
        dataset_paths = self.normalize_dataset_paths(input_dataset_paths)
        self.ensure_directory(self.output_dir)
        self.llm_calls = 0
        self.tool_calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.generated_code = None
        self.last_error = ""

        text_report_path = str(Path(self.output_dir) / build_output_filename(query, extension=".txt", fallback="spatial_statistics_report"))
        html_report_path = str(Path(self.output_dir) / build_output_filename(query, extension=".html", fallback="spatial_statistics_report"))

        self._emit_progress(
            progress_callback,
            stage="start",
            message="I will inspect the datasets, choose suitable PySAL methods, generate analysis code, and return a modeling report.",
            data={"dataset_count": len(dataset_paths), "max_iterations": self.max_iterations},
        )
        self._emit_progress(
            progress_callback,
            stage="input_inspection",
            message="I am inspecting the input datasets to identify geometry types, CRS, numeric fields, candidate modeling variables, and usable PySAL workflows.",
            data={"dataset_paths": dataset_paths},
        )
        dataset_context = [self._inspect_dataset(path) for path in dataset_paths]
        self._emit_progress(
            progress_callback,
            stage="data_validation",
            message="Dataset inspection is complete. I identified candidate variables, geometry metadata, and available PySAL libraries.",
            data={"dataset_context": dataset_context, "available_libraries": self.available_libraries},
        )

        candidate_methods = self._candidate_methods(query, dataset_context)
        self._emit_progress(
            progress_callback,
            stage="model_selection",
            message="I selected candidate PySAL method families based on the request and dataset metadata.",
            data={"candidate_methods": candidate_methods},
        )

        self._emit_progress(
            progress_callback,
            stage="planning",
            message="I prepared the plain text and HTML report paths that the analysis code must write.",
            data={"text_report_path": text_report_path, "html_report_path": html_report_path},
        )

        messages = self._build_prompt(query, dataset_paths, dataset_context, text_report_path, html_report_path)
        self._emit_progress(
            progress_callback,
            stage="llm_generation",
            message="I prepared the PySAL expert prompt with dataset context, available libraries, output requirements, and API usage examples.",
            data={"usage_catalog_included": True},
        )
        if self.client is None:
            self.last_error = "No LLM client was configured; producing fallback PySAL modeling report."
            self._emit_progress(
                progress_callback,
                stage="warning",
                message="No LLM client is configured, so I will create a diagnostic PySAL modeling report instead of generated-code output.",
                data={"reason": self.last_error},
            )
        else:
            for iteration in range(self.max_iterations):
                self._emit_progress(progress_callback, stage="llm_generation", message=f"I am asking the LLM to generate PySAL analysis code (attempt {iteration + 1} of {self.max_iterations}).", data={"iteration": iteration + 1})
                self.llm_calls += 1
                response = self.client.chat.completions.create(model=self.model, messages=messages, temperature=0.1)
                usage = getattr(response, "usage", None)
                if usage:
                    self.input_tokens += getattr(usage, "prompt_tokens", 0) or 0
                    self.output_tokens += getattr(usage, "completion_tokens", 0) or 0
                code = self._extract_python_code(response.choices[0].message.content)
                self._emit_progress(
                    progress_callback,
                    stage="code_execution",
                    message="The LLM returned Python code. I extracted it and will execute the PySAL workflow in the server runtime.",
                    data={"iteration": iteration + 1, "code_length": len(code)},
                )
                self.tool_calls += 1
                self._emit_progress(progress_callback, stage="model_execution", message="I am executing the generated PySAL analysis code now.", data={"iteration": iteration + 1, "timeout_seconds": self.timeout_seconds})
                try:
                    success, stdout, stderr = self._execute_code(code, text_report_path, html_report_path)
                except Exception as exc:
                    success, stdout, stderr = False, "", str(exc)
                if success:
                    self.generated_code = code
                    self._emit_progress(progress_callback, stage="report_generation", message="The generated PySAL analysis code ran successfully and produced the text and HTML report artifacts.", data={"text_report_path": text_report_path, "html_report_path": html_report_path})
                    break
                self.last_error = f"Attempt {iteration + 1} failed.\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}"
                self._emit_progress(progress_callback, stage="retry", message="The generated code failed. I will ask the LLM to repair the PySAL workflow.", data={"iteration": iteration + 1, "stderr_preview": stderr[:800]})
                messages.append({"role": "user", "content": f"The generated code failed. Fix it and still write {text_report_path} and {html_report_path}. If maps/charts are generated, include them in the HTML report. Do not create a separate JSON results artifact.\n{self.last_error}"})

        fallback_used = False
        if not Path(text_report_path).is_file() or not Path(html_report_path).is_file():
            fallback_used = True
            self._emit_progress(progress_callback, stage="fallback_start", message="Generated PySAL code did not produce valid artifacts, so I am writing a diagnostic fallback modeling report.", data={})
            self._fallback_report(query, dataset_paths, dataset_context, text_report_path, html_report_path)
            self._emit_progress(
                progress_callback,
                stage="fallback_complete",
                message="The diagnostic fallback text report and HTML report artifacts were created successfully.",
                data={"text_report_path": text_report_path, "html_report_path": html_report_path},
            )

        summary = (
            f"Created a spatial statistics modeling report for {len(dataset_paths)} dataset(s). "
            f"Used {'LLM-generated PySAL code' if self.generated_code else 'a diagnostic fallback report'}."
        )
        if fallback_used and self.last_error:
            summary += " The fallback report includes the execution limitation and recommended PySAL workflow."

        media_artifact_files = self._discover_report_assets(text_report_path, html_report_path, start_time)
        self._emit_progress(
            progress_callback,
            stage="artifact_generation",
            message="I checked for generated maps, charts, and supporting files so they can be returned as individual artifacts and linked from the HTML report.",
            data={"media_artifact_count": len(media_artifact_files), "media_artifact_files": media_artifact_files},
        )

        valid_outputs = Path(text_report_path).is_file() and Path(html_report_path).is_file()
        self._emit_progress(
            progress_callback,
            stage="data_validation",
            message="I verified the text and HTML report artifacts and am preparing the final structured JSON service response.",
            data={"text_report_exists": Path(text_report_path).is_file(), "html_report_exists": Path(html_report_path).is_file(), "media_artifact_count": len(media_artifact_files), "valid": valid_outputs},
        )

        self._emit_progress(progress_callback, stage="complete", message="Spatial statistics workflow is complete. The final JSON response includes the text report, HTML report, execution details, and provenance.", data={"summary": summary})

        return {
            "agent_name": self.agent_name,
            "agent_version": self.agent_version,
            "model": self.model,
            "duration": round(time.time() - start_time, 2),
            "total_input_tokens": self.input_tokens,
            "total_output_tokens": self.output_tokens,
            "total_tokens": self.input_tokens + self.output_tokens,
            "inputs": {"text": query, "dataset_paths": dataset_paths, "parameters": {"max_iterations": self.max_iterations, "timeout_seconds": self.timeout_seconds}},
            "outputs": {
                "text": summary,
                "text_report_file": text_report_path,
                "html_report_file": html_report_path,
                "report_file": html_report_path,
                "media_artifact_files": media_artifact_files,
                "dataset_path": html_report_path,
                "dataset_paths": [text_report_path, html_report_path, *media_artifact_files],
                "dataset_size": {"type": "model_report", "dimensions": None, "feature_count": None},
                "model_results": {
                    "status": "fallback_report" if fallback_used else "completed",
                    "candidate_methods": candidate_methods,
                    "dataset_count": len(dataset_paths),
                    "valid_reports": valid_outputs,
                    "warnings": [self.last_error] if self.last_error else [],
                },
            },
            "metrics": {"llm_calls": self.llm_calls, "tool_calls": self.tool_calls, "number_of_artifacts": 2 + len(media_artifact_files)},
            "script": self.generated_code,
            "environment": {"python_version": platform.python_version(), "domain-specific libraries": self.available_libraries},
            "complementary": {
                "Execution": {"Inputs": {"task": query, "dataset_paths": dataset_paths, "dataset_context": dataset_context}, "Outputs": {"summary": summary, "text_report_path": text_report_path, "html_report_path": html_report_path, "media_artifact_files": media_artifact_files}},
                "Provenance": {"Lineage": ["Inspected input dataset metadata.", "Generated and executed PySAL analysis code." if self.generated_code else "Produced fallback diagnostic PySAL report.", "Saved plain text report, HTML report, and generated media artifacts."], "Tool Calls": {"count": self.tool_calls}, "LLM Calls": {"count": self.llm_calls}},
                "Artifacts and Logs": {"Inline Artifacts": {"script": self.generated_code} if self.generated_code else {}, "Persisted Artifacts": {"text_report_file": text_report_path, "html_report_file": html_report_path, "media_artifact_files": media_artifact_files}},
                "Validation": {"status": "passed" if valid_outputs else "failed", "checks": ["Plain text report exists.", "HTML report exists."]},
                "Assumptions and Limitations": {"assumptions": ["Provided datasets are accessible to the GAS server runtime."], "limitations": ["Advanced PySAL workflows require the relevant PySAL ecosystem subpackages to be installed.", "Model validity depends on suitable variables, sample size, geometry quality, and spatial weights choices."]},
            },
        }
