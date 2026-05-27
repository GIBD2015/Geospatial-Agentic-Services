
from __future__ import annotations

import html as html_lib
import importlib.util
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


VECTOR_EXTENSIONS = {".geojson", ".json", ".gpkg", ".shp", ".gml", ".kml", ".zip"}
TABLE_EXTENSIONS = {".csv", ".tsv", ".txt", ".xlsx", ".xls"}
REPORT_ASSET_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".svg", ".webp", ".gif",
    ".html", ".htm", ".pdf", ".csv", ".geojson",
}
NULL_WARNING_THRESHOLD = 0.25


class ExploratorySpatialDataAnalysisAgent(GeoAgent):
    agent_id = "exploratory_spatial_data_analysis_agent"
    agent_name = "Exploratory Spatial Data Analysis Agent"
    agent_version = "1.1.0"
    agent_description = (
        "Performs exploratory spatial data analysis (ESDA) on tabular and geospatial datasets. "
        "Surfaces distributions, summary statistics, missing-data patterns, correlations, and "
        "categorical breakdowns, plus a lightweight spatial layer — classified choropleths, "
        "point-density maps, geometry diagnostics, and a quick global spatial-autocorrelation "
        "(Moran's I) check — as a polished HTML report with charts."
    )
    requires_input_datasets = True

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        max_iterations: int = 3,
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
        self.generated_code: Optional[str] = None
        self.last_error = ""
        self.available_libraries = self._available_libraries()


    def _available_libraries(self) -> List[str]:
        candidates = (
            "geopandas", "pandas", "numpy", "matplotlib", "seaborn",
            "scipy", "shapely", "pyproj", "mapclassify", "contextily",
            "libpysal", "esda",
        )
        return [name for name in candidates if importlib.util.find_spec(name) is not None]

    def _resolve_python_runner(self) -> str:
        executable = (sys.executable or "").strip()
        if executable and "python" in os.path.basename(executable).lower():
            return executable
        return "python"

    def _environment_info(self) -> Dict[str, Any]:
        return {
            "python_version": platform.python_version(),
            "domain-specific libraries": self.available_libraries,
        }


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
            suffix = dataset.suffix.lower()
            if suffix in VECTOR_EXTENSIONS:
                gdf = gpd.read_file(dataset, rows=2000)
                info.update(self._profile_frame(gdf, is_spatial=True))
                info["type"] = "vector"
                info["crs"] = str(gdf.crs) if gdf.crs is not None else None
                try:
                    info["geometry_types"] = sorted(
                        str(v) for v in gdf.geometry.geom_type.dropna().unique()
                    )[:10]
                    info["bounds"] = [float(v) for v in gdf.total_bounds] if len(gdf) else None
                except Exception:
                    pass
                del gdf
            elif suffix in TABLE_EXTENSIONS:
                df = self._read_table(dataset)
                info.update(self._profile_frame(df, is_spatial=False))
                info["type"] = "table"
                del df
        except Exception as exc:
            info["error"] = str(exc)
        return info

    def _read_table(self, path: Path) -> pd.DataFrame:
        suffix = path.suffix.lower()
        if suffix in {".xlsx", ".xls"}:
            return pd.read_excel(path, nrows=5000)
        separator = "\t" if suffix == ".tsv" else ","
        return pd.read_csv(path, sep=separator, nrows=5000)

    def _profile_frame(self, frame: pd.DataFrame, is_spatial: bool) -> Dict[str, Any]:
        geometry_col = frame.geometry.name if isinstance(frame, gpd.GeoDataFrame) else None
        columns = [c for c in frame.columns if c != geometry_col]

        numeric_cols = [str(c) for c in frame[columns].select_dtypes(include="number").columns]
        datetime_cols = [str(c) for c in frame[columns].select_dtypes(include="datetime").columns]
        categorical_cols = [
            str(c) for c in columns
            if str(c) not in numeric_cols and str(c) not in datetime_cols
        ]

        null_fraction = {}
        if len(frame):
            for c in columns:
                frac = float(frame[c].isna().mean())
                if frac > 0:
                    null_fraction[str(c)] = round(frac, 3)
        high_null_cols = sorted(
            (c for c, f in null_fraction.items() if f >= NULL_WARNING_THRESHOLD),
            key=lambda c: null_fraction[c],
            reverse=True,
        )

        sample = {}
        for c in columns[:40]:
            try:
                val = frame[c].dropna().iloc[0]
                s = str(val)
                sample[str(c)] = s[:40] + "..." if len(s) > 40 else s
            except Exception:
                sample[str(c)] = None

        return {
            "row_count": int(len(frame)),
            "column_count": len(columns),
            "columns": [str(c) for c in columns][:60],
            "dtypes": {str(c): str(frame[c].dtype) for c in columns[:60]},
            "numeric_columns": numeric_cols[:40],
            "categorical_columns": categorical_cols[:40],
            "datetime_columns": datetime_cols[:40],
            "null_fraction": dict(sorted(null_fraction.items(), key=lambda kv: kv[1], reverse=True)[:20]),
            "high_null_columns": high_null_cols[:20],
            "sample_values": sample,
            "is_spatial": is_spatial,
        }

    def _suggested_focus(self, dataset_context: List[Dict[str, Any]]) -> List[str]:
        focus = self.request_parameters.get("focus_columns")
        if isinstance(focus, list) and focus:
            return [str(c) for c in focus]
        if isinstance(focus, str) and focus.strip():
            return [c.strip() for c in re.split(r"[,;]", focus) if c.strip()]
        for item in dataset_context:
            if item.get("numeric_columns"):
                return item["numeric_columns"][:8]
        return []

  
    def _build_prompt(
        self,
        task: str,
        dataset_paths: List[str],
        dataset_context: List[Dict[str, Any]],
        focus_columns: List[str],
        text_report_path: str,
        html_report_path: str,
    ) -> List[Dict[str, str]]:
        system = (
            "You are an expert in exploratory spatial data analysis (ESDA) for tabular and "
            "geospatial data. Generate a single robust Python script that performs the analysis "
            "requested in the Goal and writes BOTH a plain-text summary report and a polished HTML "
            "report. SCOPE THE WORK TO THE GOAL: when the Goal asks for a specific analysis (e.g. "
            "spatial autocorrelation), focus on that and omit unrelated sections; only when the Goal "
            "is generic or open-ended should you perform a comprehensive ESDA. Stay descriptive and "
            "exploratory — surface patterns and structure; do not run formal hypothesis tests or models. "
            "Use only the provided "
            "datasets; do not download external data. Use matplotlib with the 'Agg' backend "
            "(import matplotlib; matplotlib.use('Agg')) and never call plt.show(). Save every chart "
            "as a PNG in the directory given by the REPORT_ASSET_DIR environment variable, and "
            "embed/reference those charts in the HTML report with clear captions. Read input and "
            "output paths from environment variables. Return only Python code in one ```python block."
        )
        user = f"""
Goal:
{task or "Produce a comprehensive exploratory spatial data analysis of the provided dataset(s)."}

Dataset paths (also available at runtime via the env var ESDA_INPUTS as a JSON list):
{dataset_paths}

Dataset profile:
{json.dumps(dataset_context, indent=2, default=str)}

Columns to prioritize (if present):
{focus_columns}

Environment variables your script MUST read:
- TXT_REPORT       -> absolute path to write the plain-text report
- HTML_REPORT      -> absolute path to write the HTML report
- REPORT_ASSET_DIR -> directory to save all chart PNGs (already exists)
- ESDA_INPUTS      -> JSON list of the input dataset paths

## How to scope this analysis (READ FIRST)
The numbered items below are a MENU of available analyses, NOT a mandatory checklist.
- If the Goal names specific analyses (e.g. "spatial autocorrelation", "missing data",
  "correlation", "distribution of magnitude"), produce ONLY those, plus the minimal supporting
  context needed to interpret them (e.g. the focal variable's distribution and, for a spatial
  request, a quick map). OMIT every unrelated section, and say so briefly in the report.
- If the Goal is generic or open-ended (e.g. "explore", "EDA", "summarize the data", or empty),
  perform a COMPREHENSIVE ESDA covering every applicable section below.
- Match the report title and section headings to what you actually produced — do not add empty
  placeholder sections for analyses the Goal did not request.

## Non-spatial EDA (where applicable)
1. Dataset overview: shape, column types, memory, duplicate-row count.
2. Missing data: per-column null counts/percentages + a missingness bar chart.
3. Numeric variables: descriptive stats (mean, median, std, min, max, skew, kurtosis),
   histograms (KDE if seaborn available), and box plots for outlier detection.
4. Categorical variables: value counts and bar charts for the top 15 categories (truncate long labels).
5. Relationships: a correlation heatmap for numeric variables and scatter plots for the
   most strongly correlated pairs (a few only).
6. Datetime variables (if any): time range and a simple count-over-time line chart.

## Lightweight ESDA (only for vector datasets — keep it descriptive, not inferential)
7. Geometry diagnostics: geometry-type counts, valid/invalid/empty counts, and a histogram of
   feature area (for polygons, computed in an equal-area or projected CRS) or feature count by type.
8. Classified choropleths: for a key numeric variable, render the SAME variable under three
   classification schemes side by side — Quantiles, Equal Interval, and Natural Breaks
   (use mapclassify if available) — to show how the visual story depends on classification.
9. Point density: for point datasets, render a KDE / hexbin density map instead of plotting raw points.
10. Global spatial autocorrelation (quick check): if libpysal and esda are available and the data
    is polygons with a numeric variable, build Queen contiguity weights, compute global Moran's I,
    print I and its p-value, and draw a Moran scatterplot (standardized variable vs its spatial lag).
    If libpysal/esda are unavailable, instead draw a simple "value vs neighbor-average" lag scatter
    using a centroid k-nearest-neighbor average, and clearly note this is a descriptive approximation.

Robustness rules:
- Load vector files with geopandas (gpd.read_file) and tabular files with pandas.
- Guard every plotting block in try/except so one failure does not abort the whole report.
- Skip a section gracefully (note it in the report) when the relevant column/geometry types are absent.
- Reproject to an appropriate projected CRS before computing areas or distances.
- Close figures with plt.close() after saving.
- Keep the HTML report professional: title, section headings, readable typography, summary tables,
  and the embedded chart images referenced by filename.

Do not create a separate JSON results artifact — the service response is already JSON.
Return only the Python code in one ```python block.
"""
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    @staticmethod
    def _extract_python_code(text: Optional[str]) -> str:
        if not text:
            return ""
        match = re.search(r"```python\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
        if match:
            return match.group(1).strip()
        match = re.search(r"```\s*(.*?)```", text, re.DOTALL)
        return match.group(1).strip() if match else text.strip()

    
    def _execute_code(
        self,
        code: str,
        dataset_paths: List[str],
        text_report_path: str,
        html_report_path: str,
    ) -> tuple[bool, str, str]:
        script_path = Path(self.output_dir) / build_output_filename(
            "esda generated script", extension=".py", fallback="esda_script",
        )
        script_path.write_text(code, encoding="utf-8")
        env = os.environ.copy()
        env["TXT_REPORT"] = text_report_path
        env["REPORT_TXT"] = text_report_path
        env["HTML_REPORT"] = html_report_path
        env["REPORT_ASSET_DIR"] = str(Path(html_report_path).parent)
        env["ESDA_INPUTS"] = json.dumps(dataset_paths)
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

    def _discover_report_assets(
        self,
        text_report_path: str,
        html_report_path: str,
        created_after: float,
    ) -> List[str]:
        report_dir = Path(html_report_path).parent
        excluded = {Path(text_report_path).resolve(), Path(html_report_path).resolve()}
        assets: Dict[str, Path] = {}

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

        return [str(p) for p in sorted(assets.values(), key=lambda item: item.name.lower())]

   

    def _fallback_report(
        self,
        task: str,
        dataset_paths: List[str],
        dataset_context: List[Dict[str, Any]],
        focus_columns: List[str],
        text_report_path: str,
        html_report_path: str,
    ) -> List[str]:
        """Produce a deterministic pandas/geopandas/matplotlib ESDA report."""
        Path(text_report_path).parent.mkdir(parents=True, exist_ok=True)
        asset_dir = Path(html_report_path).parent
        chart_files: List[str] = []

        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np

        text_lines: List[str] = ["Exploratory Spatial Data Analysis (ESDA) Report", "=" * 47, "", f"Goal: {task}", ""]
        html_sections: List[str] = []

        for idx, path in enumerate(dataset_paths, start=1):
            p = Path(path)
            if not p.exists():
                continue
            try:
                if p.suffix.lower() in VECTOR_EXTENSIONS:
                    frame = gpd.read_file(p)
                    geom_col = frame.geometry.name if isinstance(frame, gpd.GeoDataFrame) else None
                else:
                    frame = self._read_table(p)
                    geom_col = None
            except Exception as exc:
                text_lines.append(f"[{p.name}] could not be read: {exc}")
                continue

            cols = [c for c in frame.columns if c != geom_col]
            numeric_cols = list(frame[cols].select_dtypes(include="number").columns)
            plot_cols = (focus_columns and [c for c in focus_columns if c in numeric_cols]) or numeric_cols

            text_lines += [
                f"Dataset {idx}: {p.name}",
                "-" * (9 + len(p.name)),
                f"Rows: {len(frame)}   Columns: {len(cols)}",
                f"Numeric columns: {', '.join(map(str, numeric_cols)) or 'none'}",
                "",
                "Missing values (%):",
            ]
            for c in cols:
                frac = float(frame[c].isna().mean()) * 100 if len(frame) else 0.0
                if frac > 0:
                    text_lines.append(f"  {c}: {frac:.1f}%")
            text_lines.append("")

            if numeric_cols:
                try:
                    text_lines += ["Numeric summary:", frame[numeric_cols].describe().to_string(), ""]
                except Exception:
                    pass

            # --- Non-spatial: histograms ---
            hist_cols = plot_cols[:6]
            if hist_cols:
                try:
                    n = len(hist_cols)
                    ncols = min(3, n)
                    nrows = (n + ncols - 1) // ncols
                    fig, axes = plt.subplots(nrows, ncols, figsize=(5 * ncols, 3.5 * nrows))
                    axes = (axes.flatten() if hasattr(axes, "flatten") else [axes])
                    for ax, col in zip(axes, hist_cols):
                        frame[col].dropna().plot(kind="hist", bins=30, ax=ax, color="#4C72B0", edgecolor="white")
                        ax.set_title(str(col))
                    for ax in axes[len(hist_cols):]:
                        ax.set_visible(False)
                    fig.suptitle(f"{p.name} — numeric distributions")
                    fig.tight_layout()
                    fp = asset_dir / f"esda_{idx}_histograms.png"
                    fig.savefig(fp, dpi=140, bbox_inches="tight"); plt.close(fig)
                    chart_files.append(str(fp))
                except Exception:
                    plt.close("all")

            # --- Non-spatial: correlation heatmap ---
            if len(numeric_cols) >= 2:
                try:
                    corr = frame[numeric_cols].corr(numeric_only=True)
                    fig, ax = plt.subplots(figsize=(1.2 * len(numeric_cols) + 2, 1.0 * len(numeric_cols) + 2))
                    im = ax.imshow(corr.values, cmap="RdBu_r", vmin=-1, vmax=1)
                    ax.set_xticks(range(len(numeric_cols))); ax.set_yticks(range(len(numeric_cols)))
                    ax.set_xticklabels(numeric_cols, rotation=45, ha="right", fontsize=8)
                    ax.set_yticklabels(numeric_cols, fontsize=8)
                    fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
                    ax.set_title(f"{p.name} — correlation")
                    fig.tight_layout()
                    fp = asset_dir / f"esda_{idx}_correlation.png"
                    fig.savefig(fp, dpi=140, bbox_inches="tight"); plt.close(fig)
                    chart_files.append(str(fp))
                except Exception:
                    plt.close("all")

            # --- Spatial (ESDA) blocks ---
            if geom_col is not None and len(frame):
                esda_text, esda_charts = self._esda_section(frame, p.name, idx, plot_cols, asset_dir)
                text_lines += esda_text
                chart_files += esda_charts

            section_imgs = "".join(
                f'<figure><img src="{Path(c).name}" alt="{Path(c).name}"><figcaption>{Path(c).name}</figcaption></figure>'
                for c in chart_files if f"esda_{idx}_" in Path(c).name
            )
            html_sections.append(
                f"<section><h2>{html_lib.escape(p.name)}</h2>"
                f"<p>Rows: {len(frame)} &nbsp;|&nbsp; Columns: {len(cols)} &nbsp;|&nbsp; "
                f"Numeric: {len(numeric_cols)}</p>"
                f"<div class='gallery'>{section_imgs}</div></section>"
            )
            del frame

        Path(text_report_path).write_text("\n".join(text_lines), encoding="utf-8")
        Path(html_report_path).write_text(self._html_shell(task, html_sections), encoding="utf-8")
        return chart_files

    def _esda_section(
        self,
        gdf: gpd.GeoDataFrame,
        name: str,
        idx: int,
        plot_cols: List[str],
        asset_dir: Path,
    ) -> tuple[List[str], List[str]]:
        """Lightweight, descriptive ESDA for a single vector dataset."""
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np

        text: List[str] = ["Spatial structure (ESDA):"]
        charts: List[str] = []

        # Projected copy for area/centroid/distance math
        try:
            work = gdf.to_crs(gdf.estimate_utm_crs()) if gdf.crs else gdf
        except Exception:
            work = gdf

        geom_types = sorted(str(v) for v in gdf.geometry.geom_type.dropna().unique())
        invalid = int((~gdf.geometry.is_valid).sum())
        empty = int(gdf.geometry.is_empty.sum())
        is_polygon = any("Polygon" in t for t in geom_types)
        is_point = any("Point" in t for t in geom_types)
        text += [
            f"  Geometry types: {', '.join(geom_types) or 'unknown'}",
            f"  Invalid geometries: {invalid}   Empty geometries: {empty}",
            f"  CRS: {gdf.crs}",
        ]

        # Geometry diagnostics: area histogram (polygons)
        if is_polygon:
            try:
                areas = work.geometry.area / 1e6  # km^2
                fig, ax = plt.subplots(figsize=(6, 4))
                areas.replace([np.inf, -np.inf], np.nan).dropna().plot(
                    kind="hist", bins=30, ax=ax, color="#6BB56A", edgecolor="white")
                ax.set_title(f"{name} — polygon area (km²)"); ax.set_xlabel("area (km²)")
                fig.tight_layout()
                fp = asset_dir / f"esda_{idx}_area_hist.png"
                fig.savefig(fp, dpi=140, bbox_inches="tight"); plt.close(fig)
                charts.append(str(fp))
            except Exception:
                plt.close("all")

        # Point density (hexbin)
        if is_point:
            try:
                pts = work.geometry.representative_point()
                fig, ax = plt.subplots(figsize=(7, 7))
                hb = ax.hexbin(pts.x.values, pts.y.values, gridsize=40, cmap="magma", mincnt=1)
                fig.colorbar(hb, ax=ax, label="point count")
                ax.set_title(f"{name} — point density"); ax.set_aspect("equal")
                fig.tight_layout()
                fp = asset_dir / f"esda_{idx}_point_density.png"
                fig.savefig(fp, dpi=140, bbox_inches="tight"); plt.close(fig)
                charts.append(str(fp))
            except Exception:
                plt.close("all")

        key_col = plot_cols[0] if plot_cols else None

        # Classification comparison choropleths (polygons + numeric)
        if is_polygon and key_col is not None:
            try:
                schemes = ["quantiles", "equal_interval", "fisher_jenks"]
                fig, axes = plt.subplots(1, 3, figsize=(18, 6))
                for ax, scheme in zip(axes, schemes):
                    try:
                        gdf.plot(column=key_col, scheme=scheme, k=5, cmap="viridis",
                                 legend=True, ax=ax, edgecolor="0.8", linewidth=0.2,
                                 legend_kwds={"fontsize": 7, "loc": "lower left"})
                    except Exception:
                        # mapclassify missing or scheme failed — plain continuous choropleth
                        gdf.plot(column=key_col, cmap="viridis", legend=True, ax=ax,
                                 edgecolor="0.8", linewidth=0.2)
                    ax.set_title(f"{scheme.replace('_', ' ').title()}"); ax.set_axis_off()
                fig.suptitle(f"{name} — '{key_col}' under three classification schemes", fontsize=14)
                fig.tight_layout()
                fp = asset_dir / f"esda_{idx}_classification_compare.png"
                fig.savefig(fp, dpi=140, bbox_inches="tight"); plt.close(fig)
                charts.append(str(fp))
            except Exception:
                plt.close("all")

        # Global spatial autocorrelation (Moran's I) + Moran scatter
        if is_polygon and key_col is not None:
            charts += self._moran_block(gdf, work, name, idx, key_col, asset_dir, text)

        text.append("")
        return text, charts

    def _moran_block(
        self,
        gdf: gpd.GeoDataFrame,
        work: gpd.GeoDataFrame,
        name: str,
        idx: int,
        key_col: str,
        asset_dir: Path,
        text: List[str],
    ) -> List[str]:
        """Quick global Moran's I when libpysal/esda exist; else a descriptive lag scatter."""
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np

        charts: List[str] = []
        try:
            values = pd.to_numeric(gdf[key_col], errors="coerce")
            valid = values.notna()
            if valid.sum() < 5:
                text.append(f"  Moran's I skipped — too few non-null '{key_col}' values.")
                return charts
            sub = gdf[valid].reset_index(drop=True)
            y = pd.to_numeric(sub[key_col], errors="coerce").to_numpy(dtype=float)

            have_pysal = (
                importlib.util.find_spec("libpysal") is not None
                and importlib.util.find_spec("esda") is not None
            )

            if have_pysal:
                from libpysal.weights import Queen
                from esda.moran import Moran
                w = Queen.from_dataframe(sub, use_index=False)
                w.transform = "r"
                mi = Moran(y, w)
                lag = w.sparse.dot((y - y.mean()) / y.std())
                zy = (y - y.mean()) / y.std()
                text.append(f"  Global Moran's I for '{key_col}': {mi.I:.3f} (p={mi.p_sim:.3f}) [Queen contiguity]")
                subtitle = f"Moran's I = {mi.I:.3f}, p = {mi.p_sim:.3f}"
            else:
                # Descriptive approximation: centroid kNN spatial lag.
                from libpysal.weights import KNN  # may still be missing
                raise ImportError  # force the numpy fallback below
        except ImportError:
            # numpy-only kNN lag approximation (no PySAL)
            try:
                cent = work.geometry.representative_point()
                coords = np.column_stack([cent.x.values, cent.y.values])
                sub_mask = pd.to_numeric(gdf[key_col], errors="coerce").notna().to_numpy()
                coords = coords[sub_mask]
                y = pd.to_numeric(gdf[key_col], errors="coerce").to_numpy(dtype=float)[sub_mask]
                if len(y) < 5:
                    return charts
                k = min(8, len(y) - 1)
                # pairwise distances (fine for a few thousand features)
                d = np.linalg.norm(coords[:, None, :] - coords[None, :, :], axis=2)
                np.fill_diagonal(d, np.inf)
                nn = np.argsort(d, axis=1)[:, :k]
                zy = (y - y.mean()) / (y.std() or 1.0)
                lag = zy[nn].mean(axis=1)
                # Pseudo Moran's I as slope of lag ~ zy
                slope = float(np.polyfit(zy, lag, 1)[0])
                text.append(
                    f"  Spatial lag scatter for '{key_col}' (k={k} nearest-neighbor average, "
                    f"descriptive approximation; install libpysal+esda for true Moran's I). "
                    f"lag~value slope = {slope:.3f}"
                )
                subtitle = f"kNN lag slope = {slope:.3f} (approx.)"
            except Exception:
                plt.close("all")
                return charts
        except Exception as exc:
            text.append(f"  Moran's I could not be computed: {exc}")
            plt.close("all")
            return charts

        # Moran scatterplot
        try:
            fig, ax = plt.subplots(figsize=(6, 6))
            ax.scatter(zy, lag, s=14, color="#4C72B0", alpha=0.7)
            try:
                b = np.polyfit(zy, lag, 1)
                xs = np.linspace(zy.min(), zy.max(), 50)
                ax.plot(xs, b[0] * xs + b[1], color="#C44E52", lw=1.5)
            except Exception:
                pass
            ax.axhline(0, color="0.6", lw=0.8); ax.axvline(0, color="0.6", lw=0.8)
            ax.set_xlabel(f"{key_col} (standardized)"); ax.set_ylabel("spatial lag")
            ax.set_title(f"{name} — Moran scatter\n{subtitle}")
            fig.tight_layout()
            fp = asset_dir / f"esda_{idx}_moran_scatter.png"
            fig.savefig(fp, dpi=140, bbox_inches="tight"); plt.close(fig)
            charts.append(str(fp))
        except Exception:
            plt.close("all")
        return charts

    @staticmethod
    def _html_shell(task: str, sections: List[str]) -> str:
        return f"""<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Exploratory Spatial Data Analysis Report</title>
<style>
  body {{ margin:0; font-family:Arial, sans-serif; color:#1f2937; background:#f3f4f6; }}
  main {{ max-width:1100px; margin:0 auto; padding:32px 24px 48px; background:#fff; min-height:100vh; }}
  h1 {{ margin:0 0 6px; color:#111827; }}
  h2 {{ margin-top:28px; padding-bottom:6px; border-bottom:1px solid #d1d5db; color:#1d4ed8; }}
  .subtitle {{ color:#4b5563; margin-bottom:18px; }}
  .gallery {{ display:flex; flex-wrap:wrap; gap:18px; }}
  figure {{ margin:0; max-width:100%; }}
  figure img {{ max-width:520px; width:100%; border:1px solid #e5e7eb; border-radius:6px; }}
  figcaption {{ font-size:11px; color:#6b7280; margin-top:4px; }}
</style></head>
<body><main>
  <h1>Exploratory Spatial Data Analysis Report</h1>
  <div class="subtitle">{html_lib.escape(task or "Deterministic ESDA of the provided dataset(s).")}</div>
  {''.join(sections)}
</main></body></html>"""

   

    def run(
        self,
        query: str,
        input_dataset_paths: Optional[list[str] | str] = None,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        start_time = time.time()
        dataset_paths = self.normalize_dataset_paths(input_dataset_paths)
        self.ensure_directory(self.output_dir)
        self.reset_metrics()
        self.generated_code = None
        self.last_error = ""

        text_report_path = str(Path(self.output_dir) / build_output_filename(query, extension=".txt", fallback="esda_report"))
        html_report_path = str(Path(self.output_dir) / build_output_filename(query, extension=".html", fallback="esda_report"))

        self._emit_progress(
            progress_callback,
            stage="start",
            message="I will profile the dataset(s), generate an ESDA script, execute it, and return an HTML report with charts.",
            data={"dataset_count": len(dataset_paths), "max_iterations": self.max_iterations},
        )

        if not dataset_paths:
            return self._error_response(query, dataset_paths, start_time, "No input datasets supplied for ESDA.", progress_callback)

        self._emit_progress(
            progress_callback,
            stage="input_inspection",
            message="I am profiling each dataset — shape, dtypes, missingness, and numeric/categorical/datetime/geometry classification.",
            data={"dataset_paths": dataset_paths},
        )
        dataset_context = [self._inspect_dataset(path) for path in dataset_paths]
        focus_columns = self._suggested_focus(dataset_context)
        self._emit_progress(
            progress_callback,
            stage="data_validation",
            message="Profiling complete. I identified column types, missing-data hotspots, geometry, and columns worth focusing on.",
            data={"focus_columns": focus_columns, "available_libraries": self.available_libraries},
        )

        messages = self._build_prompt(query, dataset_paths, dataset_context, focus_columns, text_report_path, html_report_path)

        if self.client is None:
            self.last_error = "No LLM client configured; producing a deterministic ESDA report."
            self._emit_progress(
                progress_callback,
                stage="warning",
                message="No LLM client is configured, so I will produce a deterministic ESDA report instead of generated-code output.",
                data={"reason": self.last_error},
            )
        else:
            for iteration in range(self.max_iterations):
                self._emit_progress(
                    progress_callback,
                    stage="llm_generation",
                    message=f"I am asking the LLM to generate the ESDA script (attempt {iteration + 1} of {self.max_iterations}).",
                    data={"iteration": iteration + 1},
                )
                self.increment_llm_calls()
                response = self.client.chat.completions.create(model=self.model, messages=messages, temperature=0.1)
                usage = getattr(response, "usage", None)
                if usage:
                    self.input_tokens += getattr(usage, "prompt_tokens", 0) or 0
                    self.output_tokens += getattr(usage, "completion_tokens", 0) or 0
                code = self._extract_python_code(response.choices[0].message.content)
                self._emit_progress(
                    progress_callback,
                    stage="code_execution",
                    message="The LLM returned ESDA code. I will execute it in the server runtime.",
                    data={"iteration": iteration + 1, "code_length": len(code)},
                )
                self.increment_tool_calls()
                try:
                    success, stdout, stderr = self._execute_code(code, dataset_paths, text_report_path, html_report_path)
                except Exception as exc:
                    success, stdout, stderr = False, "", str(exc)
                if success:
                    self.generated_code = code
                    self._emit_progress(
                        progress_callback,
                        stage="report_generation",
                        message="The generated ESDA code ran successfully and produced the text and HTML reports.",
                        data={"text_report_path": text_report_path, "html_report_path": html_report_path},
                    )
                    break
                self.last_error = f"Attempt {iteration + 1} failed.\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}"
                self.increment_retries()
                self._emit_progress(
                    progress_callback,
                    stage="retry",
                    message="The generated ESDA code failed. I will ask the LLM to repair it.",
                    data={"iteration": iteration + 1, "stderr_preview": stderr[:800]},
                )
                messages.append({
                    "role": "user",
                    "content": (
                        f"The code failed. Fix it and still write {text_report_path} and {html_report_path}, "
                        f"saving charts to REPORT_ASSET_DIR. Do not create a separate JSON artifact.\n{self.last_error}"
                    ),
                })

        fallback_used = False
        if not Path(text_report_path).is_file() or not Path(html_report_path).is_file():
            fallback_used = True
            self._emit_progress(
                progress_callback,
                stage="fallback_start",
                message="Generated ESDA code did not produce valid reports, so I am writing a deterministic ESDA report.",
                data={},
            )
            try:
                self._fallback_report(query, dataset_paths, dataset_context, focus_columns, text_report_path, html_report_path)
            except Exception as exc:
                return self._error_response(query, dataset_paths, start_time, f"ESDA failed and fallback errored: {exc}", progress_callback)
            self._emit_progress(
                progress_callback,
                stage="fallback_complete",
                message="The deterministic ESDA report and charts were created successfully.",
                data={"text_report_path": text_report_path, "html_report_path": html_report_path},
            )

        media_artifact_files = self._discover_report_assets(text_report_path, html_report_path, start_time)
        valid_outputs = Path(text_report_path).is_file() and Path(html_report_path).is_file()
        self.set_artifact_count(2 + len(media_artifact_files))

        summary = (
            f"Produced an exploratory spatial data analysis report for {len(dataset_paths)} dataset(s) "
            f"with {len(media_artifact_files)} chart artifact(s). "
            f"Used {'LLM-generated ESDA code' if self.generated_code else 'a deterministic ESDA report'}."
        )

        self._emit_progress(
            progress_callback,
            stage="complete",
            message=summary,
            data={"valid": valid_outputs, "media_artifact_count": len(media_artifact_files)},
        )

        return {
            "agent_name": self.agent_name,
            "agent_version": self.agent_version,
            "model": self.model,
            "duration": round(time.time() - start_time, 2),
            "total_input_tokens": self.input_tokens,
            "total_output_tokens": self.output_tokens,
            "total_tokens": self.input_tokens + self.output_tokens,
            "inputs": {
                "text": query,
                "dataset_paths": dataset_paths,
                "parameters": {
                    "focus_columns": focus_columns,
                    "max_iterations": self.max_iterations,
                    "timeout_seconds": self.timeout_seconds,
                },
            },
            "outputs": {
                "text": summary,
                "text_report_file": text_report_path,
                "html_report_file": html_report_path,
                "report_file": html_report_path,
                "media_artifact_files": media_artifact_files,
                "dataset_path": html_report_path,
                "dataset_paths": [text_report_path, html_report_path, *media_artifact_files],
                "dataset_size": {"type": "esda_report", "feature_count": None, "dimensions": None},
            },
            "metrics": {
                "llm_calls": self.llm_calls,
                "tool_calls": self.tool_calls,
                "number_of_artifacts": self.number_of_artifacts,
            },
            "environment": self._environment_info(),
            "script": self.generated_code,
            "complementary": {
                "Execution": {
                    "Inputs": {"task": query, "dataset_paths": dataset_paths, "dataset_context": dataset_context},
                    "Outputs": {
                        "summary": summary,
                        "status": "fallback_report" if fallback_used else "completed",
                        "text_report_path": text_report_path,
                        "html_report_path": html_report_path,
                        "media_artifact_files": media_artifact_files,
                    },
                },
                "Provenance": {
                    "Lineage": [
                        "Profiled input dataset(s).",
                        "Generated and executed ESDA code." if self.generated_code else "Produced deterministic ESDA report.",
                        "Saved plain text report, HTML report, and chart artifacts.",
                    ],
                    "Tool Calls": {"count": self.tool_calls},
                    "LLM Calls": {"count": self.llm_calls},
                },
                "Artifacts and Logs": {
                    "Inline Artifacts": {"script": self.generated_code} if self.generated_code else {},
                    "Persisted Artifacts": {
                        "text_report_file": text_report_path,
                        "html_report_file": html_report_path,
                        "media_artifact_files": media_artifact_files,
                    },
                },
                "Validation": {
                    "status": "passed" if valid_outputs else "failed",
                    "checks": ["Plain text report exists.", "HTML report exists."],
                },
            },
        }

    def _error_response(
        self,
        query: str,
        dataset_paths: List[str],
        start_time: float,
        message: str,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        if progress_callback is not None:
            self._emit_progress(progress_callback, stage="error", message=message)
        return {
            "agent_name": self.agent_name,
            "agent_version": self.agent_version,
            "model": self.model,
            "duration": round(time.time() - start_time, 2),
            "total_input_tokens": self.input_tokens,
            "total_output_tokens": self.output_tokens,
            "total_tokens": self.input_tokens + self.output_tokens,
            "error": message,
            "inputs": {"text": query, "dataset_paths": dataset_paths},
            "outputs": {"text": message, "dataset_path": None, "dataset_paths": []},
            "metrics": {"llm_calls": self.llm_calls, "tool_calls": self.tool_calls, "number_of_artifacts": 0},
            "environment": self._environment_info(),
            "complementary": {
                "Execution": {"Inputs": {"task": query, "dataset_paths": dataset_paths}, "Outputs": {}, "Error": {"message": message}},
                "Provenance": {"Lineage": ["Failed before producing a report."], "Tool Calls": {"count": self.tool_calls}, "LLM Calls": {"count": self.llm_calls}},
                "Artifacts and Logs": {"Inline Artifacts": {}, "Persisted Artifacts": {}},
            },
        }
