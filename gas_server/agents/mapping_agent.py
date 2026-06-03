import code
import logging
import os
import sys
import time
import subprocess
import tempfile
import re
import json
import random
import shutil
import pandas as pd
import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy.stats import skew
from typing import Callable, List, Dict, Any, Optional, Tuple
from dotenv import load_dotenv
from openai import OpenAI
from gas_server.core.file_naming import build_output_filename
from gas_server.core.geo_agent import GeoAgent
from gas_server.core.llm_client import build_llm_client, format_service_name
from gas_server.core.config import OUTPUT_DIR as RUNTIME_OUTPUT_DIR, ensure_runtime_dirs

load_dotenv()
ensure_runtime_dirs()

OUTPUT_DIR = str(RUNTIME_OUTPUT_DIR)

# =============================================================================
# TEMPLATES
# =============================================================================

MAP_TEMPLATE = """'''
GeoPandas Visualization Template (Continuous + Classified)
==================================================================
Robust choropleth map template.
'''

import geopandas as gpd
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
from matplotlib.colorbar import ColorbarBase
import matplotlib
import numpy as np
import random
import mapclassify

# ── 1. CONFIG ──────────────────────────────────────────────────────────────────
VALUE_COLUMN = "value"

# classification options: None | "natural_breaks" | "quantiles" | "equal_interval" | "std_mean"
CLASSIFICATION = "natural_breaks"
K_CLASSES = 5

EDGE_COLOR   = "#C0C0C0"
EDGE_WIDTH   = 0.5
BG_COLOR     = "#FFFFFF"
FIG_COLOR    = "#FFFFFF"
TEXT_COLOR   = "#000000"
TITLE        = "Generated Title Here"
OUTPUT_FILE  = "__OUTPUT_DIR__/Generated_Map_123456.png"
DPI          = 180

# ── 2. COLOR SETS ──────────────────────────────────────────────────────────────
COLOR_SETS = {
    "Blues":   {"cmap": "Blues",   "bar_label": "#185FA5", "spine": "#C8D8E8", "tick": "#111111"},
    "Greens":  {"cmap": "Greens",  "bar_label": "#3B6D11", "spine": "#C8E8D0", "tick": "#111111"},
    "Oranges": {"cmap": "Oranges", "bar_label": "#854F0B", "spine": "#F0D8C0", "tick": "#111111"},
}
chosen = random.choice(list(COLOR_SETS.values()))
CMAP       = chosen["cmap"]
BAR_LABEL  = chosen["bar_label"]
SPINE_COL  = chosen["spine"]
TICK_COL   = chosen["tick"]

# ── 3. LOAD DATA (AGENT WILL REPLACE THIS) ─────────────────────────────────────
gdf = gpd.read_file(gpd.datasets.get_path("naturalearth_lowres"))
gdf = gdf[gdf["continent"] == "Europe"].copy()
gdf = gdf.to_crs(epsg=3857)
gdf[VALUE_COLUMN] = np.random.uniform(0, 100, len(gdf))

# Ensure numeric
gdf[VALUE_COLUMN] = pd.to_numeric(gdf[VALUE_COLUMN], errors="coerce")

# ── 4. AXIS LABELS ─────────────────────────────────────────────────────────────
crs = gdf.crs
if crs and crs.is_geographic:
    x_label = "Longitude (degrees)"
    y_label = "Latitude (degrees)"
else:
    try:
        axes = crs.axis_info
        x_label = f"{axes[0].name} ({axes[0].unit_name})"
        y_label = f"{axes[1].name} ({axes[1].unit_name})"
    except:
        x_label = "X"
        y_label = "Y"

crs_epsg = crs.to_epsg() if crs else None
crs_str = f"EPSG:{crs_epsg}" if crs_epsg else "Unknown CRS"
SUBTITLE = f"CRS: {crs_str}"

# ── 5. VALUE PREP ──────────────────────────────────────────────────────────────
values = gdf[VALUE_COLUMN]
valid_mask = values.notna()
clean_values = values[valid_mask]

if clean_values.empty:
    raise ValueError(f"No valid numeric values in '{VALUE_COLUMN}'")

vmin, vmax = clean_values.min(), clean_values.max()
cmap = matplotlib.colormaps.get_cmap(CMAP)

# Initialize safe default color
gdf["_color"] = "#D3D3D3"

classified = CLASSIFICATION is not None

# ── 6. COLOR ASSIGNMENT (FIXED LOGIC) ───────────────────────────────────────────
if not classified:
    norm = mcolors.Normalize(vmin=vmin, vmax=vmax)
    for idx, val in clean_values.items():
        gdf.at[idx, "_color"] = cmap(norm(val))

else:
    unique_count = clean_values.nunique()
    k = min(K_CLASSES, unique_count)

    if CLASSIFICATION == "natural_breaks":
        classifier = mapclassify.NaturalBreaks(clean_values, k=k)
    elif CLASSIFICATION == "quantiles":
        classifier = mapclassify.Quantiles(clean_values, k=k)
    elif CLASSIFICATION == "equal_interval":
        classifier = mapclassify.EqualInterval(clean_values, k=k)
    elif CLASSIFICATION == "std_mean":
        classifier = mapclassify.StdMean(clean_values)
    else:
        raise ValueError("Unsupported classification type")

    discrete_cmap = matplotlib.colormaps.get_cmap(CMAP).resampled(classifier.k)

    # ✅ SAFE INDEX‑ALIGNED COLOR ASSIGNMENT
    for idx, cls in zip(clean_values.index, classifier.yb):
        gdf.at[idx, "_color"] = discrete_cmap(int(cls))

# ── 7. FIGURE ──────────────────────────────────────────────────────────────────
fig = plt.figure(figsize=(12, 8), facecolor=FIG_COLOR)

ax = fig.add_axes([0.08, 0.18, 0.88, 0.74])
ax.set_facecolor(BG_COLOR)

for spine in ax.spines.values():
    spine.set_edgecolor(SPINE_COL)
    spine.set_linewidth(0.6)

ax.tick_params(colors=TICK_COL, labelsize=9)
ax.set_xlabel(x_label, fontsize=10, fontweight="bold", color=TEXT_COLOR)
ax.set_ylabel(y_label, fontsize=10, fontweight="bold", color=TEXT_COLOR)

gdf.plot(
    ax=ax,
    color=gdf["_color"],
    edgecolor=EDGE_COLOR,
    linewidth=EDGE_WIDTH
)

# ── 8. TITLES ──────────────────────────────────────────────────────────────────
ax.set_title(TITLE, fontsize=15, fontweight="bold", loc="left", pad=12)
fig.text(0.08, 0.02, SUBTITLE, fontsize=10, fontweight="bold")

# ── 9. COLORBAR ────────────────────────────────────────────────────────────────
cb_ax = fig.add_axes([0.08, 0.07, 0.88, 0.025])

if not classified:
    norm = mcolors.Normalize(vmin=vmin, vmax=vmax)
    cb = ColorbarBase(cb_ax, cmap=cmap, norm=norm, orientation="horizontal")
    cb_ax.set_title(VALUE_COLUMN, fontsize=8, pad=4)
else:
    bounds = classifier.bins
    boundaries = np.concatenate(([vmin], bounds))
    norm = mcolors.BoundaryNorm(boundaries, discrete_cmap.N)
    cb = ColorbarBase(
        cb_ax,
        cmap=discrete_cmap,
        norm=norm,
        boundaries=boundaries,
        orientation="horizontal"
    )
    cb_ax.set_title(f"{VALUE_COLUMN} ({CLASSIFICATION})", fontsize=8, pad=4)

cb.outline.set_edgecolor(SPINE_COL)

# ── 10. SAVE ───────────────────────────────────────────────────────────────────
plt.savefig(OUTPUT_FILE, dpi=DPI, bbox_inches="tight", facecolor=FIG_COLOR)
print(f"__OUTPUT_PATH__={OUTPUT_FILE}")
print(f"__FEATURE_COUNT__={len(gdf)}")"""

UNCLASSIFIED_MAP_TEMPLATE = '''"""
GeoPandas Visualization Template (Unclassified / Single Color)
==================================================================
A clean, publication-ready map template for unclassified data.
"""

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import random

# ── 1. CONFIG ──────────────────────────────────────────────────────────────────
EDGE_COLOR   = "#C0C0C0"
EDGE_WIDTH   = 0.5
BG_COLOR     = "#FFFFFF"
FIG_COLOR    = "#FFFFFF"
TEXT_COLOR   = "#000000"
TITLE        = "Generated Title Here" # AGENT: Generate a descriptive title
OUTPUT_FILE  = "__OUTPUT_DIR__/[Name+6-digit].png"
DPI          = 180

# ── 2. COLOR SETS ─────────────────────────────────────────────────────────────
COLOR_SETS = {
    "Blue":   {"fill": "#378ADD", "spine": "#C8D8E8", "tick": "#111111"},
    "Green":  {"fill": "#639922", "spine": "#C8E8D0", "tick": "#111111"},
    "Coral":  {"fill": "#D85A30", "spine": "#F0D8C0", "tick": "#111111"},
    "Purple": {"fill": "#8E44AD", "spine": "#E8D8F0", "tick": "#111111"},
    "Teal":   {"fill": "#1ABC9C", "spine": "#D1F2EB", "tick": "#111111"},
}
chosen = random.choice(list(COLOR_SETS.values()))
FILL_COLOR = chosen["fill"]
SPINE_COL  = chosen["spine"]
TICK_COL   = chosen["tick"]

# ── 3. LOAD DATA ───────────────────────────────────────────────────────────────
world = gpd.read_file(gpd.datasets.get_path("naturalearth_lowres"))
gdf = world[world["continent"] == "Europe"].copy()
gdf = gdf.to_crs(epsg=3857)

# ── 4. AXIS LABELS FROM CRS & DYNAMIC SUBTITLE ─────────────────────────────────
crs = gdf.crs
if crs.is_geographic:
    x_label = "Longitude (degrees)"
    y_label = "Latitude (degrees)"
else:
    try:
        axes = crs.axis_info
        x_label = f"{axes[0].name} ({axes[0].unit_name})"
        y_label = f"{axes[1].name} ({axes[1].unit_name})"
    except:
        x_label = "X"
        y_label = "Y"

crs_epsg = crs.to_epsg()
crs_str = f"EPSG:{crs_epsg}" if crs_epsg else getattr(crs, 'name', 'Unknown CRS')
SUBTITLE = f"CRS: {crs_str}"

# ── 5. BUILD FIGURE ────────────────────────────────────────────────────────────
fig = plt.figure(figsize=(12, 8), facecolor=FIG_COLOR)

ax = fig.add_axes([0.08, 0.08, 0.88, 0.84]) # expanded height since no colorbar
ax.set_facecolor(BG_COLOR)
for spine in ax.spines.values():
    spine.set_edgecolor(SPINE_COL)
    spine.set_linewidth(0.6)
ax.tick_params(colors=TICK_COL, labelsize=9)
ax.set_xlabel(x_label, fontsize=10, fontweight="bold", color=TEXT_COLOR, labelpad=6)
ax.set_ylabel(y_label, fontsize=10, fontweight="bold", color=TEXT_COLOR, labelpad=6)

# Plot
gdf.plot(ax=ax, color=FILL_COLOR, edgecolor=EDGE_COLOR, linewidth=EDGE_WIDTH)

# ── 6. TITLE + SUBTITLE ───────────────────────────────────────────────────────
ax.set_title(TITLE, fontsize=15, fontweight="bold", color=TEXT_COLOR, loc="left", pad=12)
fig.text(0.08, 0.02, SUBTITLE, fontsize=10, fontweight="bold", color=TEXT_COLOR)

# ── 7. SAVE / SHOW ─────────────────────────────────────────────────────────────
plt.savefig(OUTPUT_FILE, dpi=DPI, bbox_inches="tight", facecolor=FIG_COLOR)
print(f"Saved → {OUTPUT_FILE} | Style: Unclassified Single Color")
'''

CHART_TEMPLATE = '''"""
Universal Matplotlib Chart Template
=====================================
A clean, publication-ready template for any chart type.
Supports: bar, horizontal bar, line, scatter, histogram, pie, box, area.
Just set CHART_TYPE and point DATA at your own source.
"""

import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import random

# ══════════════════════════════════════════════════════════════════════════════
# 1. CHART TYPE  —  pick one
# ══════════════════════════════════════════════════════════════════════════════
CHART_TYPE = "bar"
# Options: "bar" | "barh" | "line" | "scatter" | "histogram" | "pie" | "box" | "area"

# ══════════════════════════════════════════════════════════════════════════════
# 2. COLOR PALETTES  —  one chosen at random each run
# ══════════════════════════════════════════════════════════════════════════════
PALETTES = {
    "blue": {
        "colors":  ["#E6F1FB", "#85B7EB", "#378ADD", "#185FA5", "#042C53"],
        "primary": "#185FA5",
        "accent":  "#378ADD",
        "grid":    "#DCE8F5",
        "text":    "#000000",
        "muted":   "#222222",
    },
    "green": {
        "colors":  ["#EAF3DE", "#97C459", "#639922", "#3B6D11", "#173404"],
        "primary": "#3B6D11",
        "accent":  "#639922",
        "grid":    "#D8ECC4",
        "text":    "#000000",
        "muted":   "#222222",
    },
    "coral": {
        "colors":  ["#FAECE7", "#F0997B", "#D85A30", "#993C1D", "#4A1B0C"],
        "primary": "#993C1D",
        "accent":  "#D85A30",
        "grid":    "#F5D5C8",
        "text":    "#000000",
        "muted":   "#222222",
    },
}
P = random.choice(list(PALETTES.values()))

# ══════════════════════════════════════════════════════════════════════════════
# 3. LABELS & META
# ══════════════════════════════════════════════════════════════════════════════
TITLE    = "Generated Title Here" # AGENT: Generate a descriptive title
X_LABEL  = "X axis"
Y_LABEL  = "Y axis"
OUTPUT   = "__OUTPUT_DIR__/[Name+6-digit].png "
DPI      = 180

# ══════════════════════════════════════════════════════════════════════════════
# 4. SAMPLE DATA  —  replace with your own
# ══════════════════════════════════════════════════════════════════════════════
categories = ["Alpha", "Beta", "Gamma", "Delta", "Epsilon"]
values     = [42, 78, 55, 91, 63]
x_cont     = np.linspace(0, 10, 80)
y_cont     = np.sin(x_cont) * 30 + 50 + np.random.normal(0, 4, 80)
groups     = [np.random.normal(loc, 12, 60) for loc in [40, 55, 70, 85]]

# ══════════════════════════════════════════════════════════════════════════════
# 5. FIGURE SETUP
# ══════════════════════════════════════════════════════════════════════════════
fig, ax = plt.subplots(figsize=(11, 6), facecolor="white")
ax.set_facecolor("white")

for spine in ["top", "right"]:
    ax.spines[spine].set_visible(False)
for spine in ["left", "bottom"]:
    ax.spines[spine].set_color(P["grid"])
    ax.spines[spine].set_linewidth(0.8)

ax.tick_params(colors=P["text"], labelsize=9, length=3)
ax.xaxis.label.set_color(P["text"])
ax.xaxis.label.set_fontweight("bold")
ax.yaxis.label.set_color(P["text"])
ax.yaxis.label.set_fontweight("bold")
ax.grid(axis="y", color=P["grid"], linewidth=0.6, linestyle="--", zorder=0)

# ══════════════════════════════════════════════════════════════════════════════
# 6. CHART DRAWING
# ══════════════════════════════════════════════════════════════════════════════

if CHART_TYPE == "bar":
    bars = ax.bar(categories, values,
                  color=P["colors"][2], edgecolor="white",
                  linewidth=0.6, zorder=3, width=0.55)
    # Value labels on bars
    for bar in bars:
        h = bar.get_height()
        ax.text(bar.get_x() + bar.get_width() / 2, h + 1.5,
                f"{h:.0f}", ha="center", va="bottom",
                fontsize=8.5, color=P["primary"], fontweight="500")
    ax.set_xlabel(X_LABEL, fontsize=9, labelpad=8)
    ax.set_ylabel(Y_LABEL, fontsize=9, labelpad=8)
    ax.set_ylim(0, max(values) * 1.18)

elif CHART_TYPE == "barh":
    ax.grid(axis="x", color=P["grid"], linewidth=0.6, linestyle="--", zorder=0)
    ax.grid(axis="y", visible=False)
    bars = ax.barh(categories, values,
                   color=P["colors"][2], edgecolor="white",
                   linewidth=0.6, zorder=3, height=0.55)
    for bar in bars:
        w = bar.get_width()
        ax.text(w + 1.5, bar.get_y() + bar.get_height() / 2,
                f"{w:.0f}", va="center", fontsize=8.5,
                color=P["primary"], fontweight="500")
    ax.set_xlabel(Y_LABEL, fontsize=9, labelpad=8)
    ax.set_ylabel(X_LABEL, fontsize=9, labelpad=8)
    ax.set_xlim(0, max(values) * 1.18)
    ax.invert_yaxis()

elif CHART_TYPE == "line":
    ax.plot(x_cont, y_cont,
            color=P["accent"], linewidth=2, zorder=3)
    ax.fill_between(x_cont, y_cont, alpha=0.08, color=P["accent"])
    ax.scatter(x_cont[::10], y_cont[::10],
               color=P["primary"], s=30, zorder=4, edgecolors="white", linewidth=0.8)
    ax.set_xlabel(X_LABEL, fontsize=9, labelpad=8)
    ax.set_ylabel(Y_LABEL, fontsize=9, labelpad=8)

elif CHART_TYPE == "scatter":
    sizes  = np.random.uniform(30, 200, len(x_cont))
    alphas = np.clip(sizes / sizes.max(), 0.4, 1.0)
    ax.scatter(x_cont, y_cont,
               s=sizes, color=P["accent"], alpha=0.65,
               edgecolors=P["primary"], linewidth=0.5, zorder=3)
    ax.set_xlabel(X_LABEL, fontsize=9, labelpad=8)
    ax.set_ylabel(Y_LABEL, fontsize=9, labelpad=8)

elif CHART_TYPE == "histogram":
    ax.hist(y_cont, bins=18,
            color=P["accent"], edgecolor="white",
            linewidth=0.5, zorder=3, alpha=0.85)
    ax.set_xlabel(Y_LABEL, fontsize=9, labelpad=8)
    ax.set_ylabel("Frequency", fontsize=9, labelpad=8)

elif CHART_TYPE == "pie":
    ax.set_visible(False)
    pie_ax = fig.add_axes([0.15, 0.08, 0.45, 0.80])
    pie_ax.set_facecolor("white")
    wedge_colors = [P["colors"][i % len(P["colors"])] for i in range(len(categories))]
    wedges, texts, autotexts = pie_ax.pie(
        values,
        labels=None,
        colors=wedge_colors,
        autopct="%1.1f%%",
        startangle=90,
        pctdistance=0.75,
        wedgeprops=dict(edgecolor="white", linewidth=1.2),
    )
    for at in autotexts:
        at.set_fontsize(8.5)
        at.set_color(P["text"])
    # Legend
    leg_ax = fig.add_axes([0.60, 0.25, 0.35, 0.50])
    leg_ax.set_axis_off()
    for i, (cat, val) in enumerate(zip(categories, values)):
        y_pos = 0.85 - i * 0.18
        leg_ax.add_patch(plt.Rectangle((0, y_pos), 0.12, 0.10,
                          color=wedge_colors[i], transform=leg_ax.transAxes))
        leg_ax.text(0.18, y_pos + 0.05, f"{cat}  ({val})",
                    va="center", fontsize=9, color=P["text"],
                    transform=leg_ax.transAxes)

elif CHART_TYPE == "box":
    bp = ax.boxplot(groups,
                    patch_artist=True,
                    widths=0.45,
                    medianprops=dict(color="white", linewidth=1.8),
                    whiskerprops=dict(color=P["muted"], linewidth=0.8),
                    capprops=dict(color=P["muted"], linewidth=0.8),
                    flierprops=dict(marker="o", markerfacecolor=P["muted"],
                                   markersize=3, alpha=0.5, linestyle="none"))
    fill_colors = [P["colors"][1], P["colors"][2], P["colors"][3], P["colors"][4]]
    for patch, color in zip(bp["boxes"], fill_colors):
        patch.set_facecolor(color)
        patch.set_edgecolor("white")
        patch.set_linewidth(0.6)
    ax.set_xticks(range(1, len(groups) + 1))
    ax.set_xticklabels([f"Group {i+1}" for i in range(len(groups))], fontsize=9)
    ax.set_xlabel(X_LABEL, fontsize=9, labelpad=8)
    ax.set_ylabel(Y_LABEL, fontsize=9, labelpad=8)

elif CHART_TYPE == "area":
    n_series = 3
    series = [np.random.uniform(10, 40, len(x_cont)) for _ in range(n_series)]
    labels = ["Series A", "Series B", "Series C"]
    bottom = np.zeros(len(x_cont))
    for i, (s, lbl) in enumerate(zip(series, labels)):
        ax.fill_between(x_cont, bottom, bottom + s,
                        color=P["colors"][i + 1], alpha=0.85,
                        label=lbl, zorder=3)
        bottom += s
    ax.set_xlabel(X_LABEL, fontsize=9, labelpad=8)
    ax.set_ylabel(Y_LABEL, fontsize=9, labelpad=8)
    ax.legend(fontsize=8.5, frameon=False,
              labelcolor=P["text"], loc="upper left")

# ══════════════════════════════════════════════════════════════════════════════
# 7. TITLE + SUBTITLE
# ══════════════════════════════════════════════════════════════════════════════
if CHART_TYPE != "pie":
    fig.text(0.085, 0.97, TITLE,
             fontsize=14, fontweight="bold", color=P["text"], va="top")
    fig.text(0.085, 0.02, SUBTITLE,
             fontsize=10, fontweight="bold", color=P["text"], va="bottom")
else:
    fig.text(0.085, 0.97, TITLE,
             fontsize=14, fontweight="bold", color=P["text"], va="top")
    fig.text(0.085, 0.02, SUBTITLE,
             fontsize=10, fontweight="bold", color=P["text"], va="bottom")

# ══════════════════════════════════════════════════════════════════════════════
# 8. SAVE / SHOW
# ══════════════════════════════════════════════════════════════════════════════
plt.savefig(OUTPUT, dpi=DPI, bbox_inches="tight", facecolor="white")
print(f"Saved → {OUTPUT}  |  Chart: {CHART_TYPE}  |  Palette: {[k for k,v in PALETTES.items() if v is P][0]}")
'''

MAP_TEMPLATE = MAP_TEMPLATE.replace("__OUTPUT_DIR__", OUTPUT_DIR)
UNCLASSIFIED_MAP_TEMPLATE = UNCLASSIFIED_MAP_TEMPLATE.replace("__OUTPUT_DIR__", OUTPUT_DIR)
CHART_TEMPLATE = CHART_TEMPLATE.replace("__OUTPUT_DIR__", OUTPUT_DIR)

# =============================================================================
# AGENT CLASS
# =============================================================================

class MappingAgent(GeoAgent):
    agent_id = "mapping_agent"
    agent_name = "Mapping Agent"
    agent_version = "2.0.1"
    agent_description = "Generates maps and charts from prepared geospatial datasets."
    requires_input_datasets = True

    def __init__(self, api_key: Optional[str] = None, model: str | None = None):
        """Initialize the Mapping Agent with OpenAI credentials and configs."""
        super().__init__(api_key=api_key, model=model or "gpt-5.4", output_dir=OUTPUT_DIR)
        self.service_name = format_service_name("MappingAgent")
        self.client = build_llm_client(
            service_name=self.service_name,
            openai_api_key=self.api_key,
        )
        self.max_iterations = 4
        
        # State tracking per run
        self.primary_output_path = None
        self.output_dataset_paths: List[str] = []
        self.feature_count = None
        self.successful_code = None
        self.final_summary = ""
        self.last_error = ""

    def _resolve_python_runner(self) -> str:
        """
        Resolve a real Python executable for subprocess code execution.
        In hosted WSGI environments (e.g., PythonAnywhere), `sys.executable`
        can point to `uwsgi` instead of `python`.
        """
        executable = (sys.executable or "").strip()
        base = os.path.basename(executable).lower()
        if executable and "python" in base:
            return executable

        for candidate in ("python3", "python"):
            found = shutil.which(candidate)
            if found:
                return found

        # Last-resort fallback.
        return executable or "python3"

    def _error_reason_preview(self, error_text: str) -> str:
        text = (error_text or "").strip()
        if not text:
            return "the generated script did not complete successfully"
        ignored_lines = {
            "execution failed with the following error:",
            "please fix the python code.",
            "please fix the python code",
        }
        exception_patterns = (
            "error:",
            "exception:",
            "traceback",
            "keyerror:",
            "valueerror:",
            "nameerror:",
            "typeerror:",
            "attributeerror:",
            "filenotfounderror:",
            "importerror:",
            "moduleNotFoundError:".lower(),
            "syntaxerror:",
            "indexerror:",
        )
        useful_lines = []
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("File ") or line.lower() in ignored_lines:
                continue
            useful_lines.append(line)
        for line in reversed(useful_lines):
            lower_line = line.lower()
            if any(pattern in lower_line for pattern in exception_patterns):
                return line[:220]
        if useful_lines:
            return useful_lines[-1][:220]
        for line in reversed(text.splitlines()):
            line = line.strip()
            if line and not line.startswith("File "):
                return line[:220]
        return text[:220]

    def _detect_numeric_stored_as_string(self, series: pd.Series, threshold: float = 0.8) -> str:
        """Helper to identify columns that represent numeric data but are stored as strings."""
        try:
            non_null = series.dropna()
            if len(non_null) == 0:
                return ""

            converted = pd.to_numeric(non_null, errors="coerce")
            success_ratio = converted.notna().sum() / len(non_null)

            if success_ratio >= threshold:
                return "numeric values that are stored as string"

            return "values are not convertable to numeric"
        except:
            return ""

    def _suggest_classification_scheme(self, series: pd.Series) -> List[str]:
        """Helper to analyze distribution and suggest the best classification methods."""
        try:
            s = pd.to_numeric(series, errors="coerce").dropna()

            if len(s) == 0 or s.nunique() == 1:
                return ["equal_interval"]

            data_skew = skew(s)

            q1 = s.quantile(0.25)
            q3 = s.quantile(0.75)
            iqr = q3 - q1

            outlier_ratio = ((s < (q1 - 1.5 * iqr)) | (s > (q3 + 1.5 * iqr))).mean()

            if abs(data_skew) > 1:
                return ["natural_breaks", "headtail_breaks", "quantiles"]

            elif outlier_ratio > 0.05:
                return ["natural_breaks", "box_plot", "quantiles"]

            elif abs(data_skew) <= 0.5:
                return ["std_mean", "equal_interval", "natural_breaks"]

            else:
                return ["natural_breaks", "quantiles", "equal_interval"]

        except:
            return []

    def _dataset_to_markdown_table(self, file_path: str) -> str:
        """Reads a dataset and structures its metadata as a markdown table."""
        try:
            try:
                df = gpd.read_file(file_path)
            except:
                df = pd.read_csv(file_path)
        except:
            return "| column | type | description | suitable classification |\n|--------|------|-------------|--------------------------|\n"

        try:
            is_gdf = isinstance(df, gpd.GeoDataFrame)
            geom_col = df.geometry.name if is_gdf else None
        except:
            is_gdf = False
            geom_col = None

        # Safe geom types extraction
        geom_types = []
        if is_gdf and geom_col:
            try:
                geom_types = df.geom_type.dropna().unique().tolist()
            except:
                geom_types = []

        rows = []

        for col in df.columns:
            try:
                series = df[col]
            except:
                rows.append([col, "unknown", "", ""])
                continue

            col_type = ""
            desc = ""
            classification = ""

            try:
                col_type = str(series.dtype)
            except:
                pass

            # -----------------------------
            # Geometry column handling
            # -----------------------------
            try:
                if is_gdf and geom_col is not None and col == geom_col:
                    desc = f"geometry types: {geom_types}" if geom_types else "geometry"
                    classification = ""
                    rows.append([col, "geometry", desc, classification])
                    continue
            except:
                pass

            # -----------------------------
            # Normal columns
            # -----------------------------
            try:
                desc = self._detect_numeric_stored_as_string(series)
            except:
                desc = ""

            if desc == "":
                classification = ""
            else:
                try:
                    if desc == "values are not convertable to numeric":
                        classification = "categorical"
                    else:
                        cls = self._suggest_classification_scheme(series)
                        classification = ", ".join(cls)
                except:
                    classification = ""

            rows.append([col, col_type, desc, classification])

        # -----------------------------
        # Markdown output
        # -----------------------------
        try:
            md = "| column | type | description | suitable classification |\n"
            md += "|--------|------|-------------|--------------------------|\n"

            for r in rows:
                md += f"| {r[0]} | {r[1]} | {r[2]} | {r[3]} |\n"

            return md
        except:
            return "| column | type | description | suitable classification |\n|--------|------|-------------|--------------------------|\n"

    def _extract_python_code(self, response: str) -> str:
        """Extracts code from a python markdown block."""
        match = re.search(r"```python(.*?)```", response, re.DOTALL)
        if match:
            return match.group(1).strip()
        # Fallback if no markdown block is used
        return response.strip()

    def _sanitize_generated_code(self, code: str) -> str:
        """Patch common LLM-generated pandas assignment mistakes.

        A frequent generated-code failure is assigning an RGBA tuple returned by
        a colormap into multiple rows with ``df.loc[mask, "color"] = cmap(...)``.
        Pandas interprets the tuple as an iterable of values, producing:
        ``ValueError: Must have equal len keys and value when setting with an iterable``.
        Repeating the scalar-like color for the selected row count is the safe
        form for this pattern.
        """

        loc_cmap_pattern = re.compile(
            r"^(?P<indent>\s*)(?P<df>\w+)\.loc\[(?P<mask>[^,\n]+),\s*(?P<column>['\"][^'\"]+['\"])\]\s*=\s*(?P<expr>\w+\.?\w*\([^#\n]*\))\s*$",
            flags=re.MULTILINE,
        )

        def replace_loc_cmap(match: re.Match) -> str:
            expr = match.group("expr")
            if not re.search(r"(cmap|colormap|color_map|colors?)", expr, flags=re.IGNORECASE):
                return match.group(0)
            indent = match.group("indent")
            df = match.group("df")
            mask = match.group("mask").strip()
            column = match.group("column")
            return (
                f"{indent}__gas_mask = ({mask})\n"
                f"{indent}{df}.loc[__gas_mask, {column}] = [{expr}] * int(__gas_mask.sum())"
            )

        return loc_cmap_pattern.sub(replace_loc_cmap, code)

    def _execute_code(self, code: str) -> Tuple[bool, str, str]:
        """Executes the Python code in a temporary file and returns success, stdout, stderr."""
        code = self._sanitize_generated_code(code)
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding="utf-8") as f:
            f.write(code)
            temp_path = f.name
        
        try:
            # Run the code safely in a separate process
            runner = self._resolve_python_runner()
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            os.makedirs(self.output_dir, exist_ok=True)
            result = subprocess.run(
                [runner, temp_path],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                cwd=self.output_dir,
                timeout=60 # Prevent infinite loops
            )
            success = (result.returncode == 0)
            return success, result.stdout, result.stderr
        except subprocess.TimeoutExpired as e:
            return False, "", "Execution timed out after 60 seconds."
        except Exception as e:
            return False, "", str(e)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def _parse_agent_outputs(self, stdout: str):
        """Extract output paths and feature count from the script's standard output."""
        discovered_paths: List[str] = []
        for path_text in re.findall(r"__OUTPUT_PATH__=(.*)", stdout):
            normalized = path_text.strip()
            if normalized and not os.path.isabs(normalized):
                normalized = os.path.join(self.output_dir, normalized)
            if normalized and normalized not in discovered_paths:
                discovered_paths.append(normalized)
        if discovered_paths:
            self.output_dataset_paths = discovered_paths
            self.primary_output_path = discovered_paths[0]

        fc_match = re.search(r"__FEATURE_COUNT__=(\d+)", stdout)
        if fc_match:
            self.feature_count = int(fc_match.group(1).strip())

    def _is_identifier_column(self, column: str) -> bool:
        column_lower = (column or "").lower()
        identifier_terms = (
            "id",
            "geoid",
            "fips",
            "fp",
            "code",
            "year",
            "objectid",
            "index",
        )
        return any(term in column_lower for term in identifier_terms)

    def _coerce_numeric_values(self, series: pd.Series) -> pd.Series:
        if pd.api.types.is_numeric_dtype(series):
            return pd.to_numeric(series, errors="coerce")

        cleaned = (
            series.astype(str)
            .str.strip()
            .str.replace(",", "", regex=False)
            .str.replace("$", "", regex=False)
            .str.replace("%", "", regex=False)
            .str.replace(r"^\s*(nan|none|null|n/a)\s*$", "", regex=True, case=False)
        )
        return pd.to_numeric(cleaned, errors="coerce")

    def _select_fallback_value_column(self, gdf: gpd.GeoDataFrame, task: str) -> Tuple[str | None, pd.Series]:
        task_lower = (task or "").lower()
        columns = [column for column in gdf.columns if column != gdf.geometry.name]
        preferred_terms = []
        if "population" in task_lower or "people" in task_lower:
            preferred_terms.extend(("population", "pop", "b01001_001e"))
        if "income" in task_lower:
            preferred_terms.extend(("income", "b19013"))

        scored_candidates = []
        for column in columns:
            if self._is_identifier_column(str(column)):
                continue

            values = self._coerce_numeric_values(gdf[column])
            valid_count = int(values.notna().sum())
            unique_count = int(values.dropna().nunique())
            if valid_count == 0:
                continue

            column_lower = str(column).lower()
            score = valid_count
            if any(term in column_lower for term in preferred_terms):
                score += 100000
            if unique_count > 1:
                score += 1000

            scored_candidates.append((score, column, values))

        if not scored_candidates:
            return None, pd.Series(dtype="float64")

        scored_candidates.sort(key=lambda item: item[0], reverse=True)
        _, column, values = scored_candidates[0]
        return str(column), values

    def _is_raster_path(self, path: str) -> bool:
        return str(path).lower().split("?")[0].endswith((".tif", ".tiff", ".img", ".vrt", ".asc"))

    def _should_try_fast_renderer(self, task: str, dataset_paths: List[str]) -> bool:
        """Use deterministic rendering only when the caller explicitly asks for it.

        The default mapping path should stay LLM-backed because it usually
        produces more polished cartographic layouts. The deterministic renderer
        remains useful for quick previews, tests, and fallback behavior.
        """
        if self.request_parameters.get("force_llm") is True:
            return False
        if not dataset_paths or len(dataset_paths) != 1:
            return False
        if any(self._is_raster_path(path) for path in dataset_paths):
            return False

        task_lower = (task or "").lower()
        explicit_parameter_request = any(
            self.request_parameters.get(key) is True
            for key in ("quick_mapping", "quick_map", "deterministic", "force_deterministic")
        )
        renderer_request = str(
            self.request_parameters.get("renderer")
            or self.request_parameters.get("mapping_mode")
            or ""
        ).lower() in {"quick", "quick_mapping", "quick_map", "deterministic", "fast", "fast_renderer"}
        explicit_text_request = any(
            term in task_lower
            for term in (
                "quick map",
                "quick mapping",
                "quick visualization",
                "deterministic map",
                "deterministic mapping",
                "deterministic workflow",
                "built-in renderer",
                "fast renderer",
            )
        )
        if not (explicit_parameter_request or renderer_request or explicit_text_request):
            return False

        complex_terms = (
            "raster",
            "overlay",
            "multiple layer",
            "multi-layer",
            "side by side",
            "small multiple",
            "inset",
            "bivariate",
            "proportional symbol",
            "flow map",
            "network",
            "projection",
            "project ",
            "reproject",
            "lambert",
            "albers",
            "mercator",
            "epsg",
            "crs",
            "annotation",
            "annotate",
            "custom",
        )
        if any(term in task_lower for term in complex_terms):
            return False

        simple_terms = (
            "map",
            "choropleth",
            "plot",
            "chart",
            "bar",
            "visualize",
            "visualization",
        )
        return any(term in task_lower for term in simple_terms)

    def _requested_classification_scheme(self, task: str) -> str:
        task_lower = (task or "").lower()
        if "equal interval" in task_lower:
            return "equal_interval"
        if "natural break" in task_lower or "jenks" in task_lower:
            return "natural_breaks"
        if "quantile" in task_lower:
            return "quantiles"
        return "quantiles"

    def _fast_visualization(
        self,
        task: str,
        dataset_paths: List[str],
        target_output_path: str,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> bool:
        self._emit_progress(
            progress_callback,
            stage="method_selection",
            message=(
                "The request asked for quick or deterministic mapping, so I will use the "
                "built-in renderer instead of the default LLM cartography workflow."
            ),
            data={"renderer": "deterministic", "dataset_count": len(dataset_paths)},
        )
        success = self._fallback_visualization(
            task,
            dataset_paths,
            target_output_path,
            progress_callback,
            emit_failure=False,
            renderer_name="built-in",
        )
        if success:
            self.successful_code = self.successful_code or "# deterministic fast renderer"
            if self.final_summary:
                self.final_summary = self.final_summary.replace(
                    "after LLM retries failed",
                    "without requiring LLM-generated plotting code",
                )
            self._emit_progress(
                progress_callback,
                stage="artifact_generation",
                message=(
                    "The built-in renderer created the visualization successfully, so I can skip "
                    "the LLM retry loop for this request."
                ),
                data={"output_path": self.primary_output_path, "feature_count": self.feature_count},
            )
        return success

    def _fallback_visualization(
        self,
        task: str,
        dataset_paths: List[str],
        target_output_path: str,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        emit_failure: bool = True,
        renderer_name: str = "fallback",
    ) -> bool:
        """
        Deterministic fallback when LLM-generated code fails repeatedly.
        1) Try rendering the first readable GeoDataFrame.
        2) Otherwise try a simple bar chart from the first readable tabular dataset.
        """
        for path in dataset_paths:
            try:
                self._emit_progress(
                    progress_callback,
                    stage="fallback_start",
                    message=(
                        "I am checking whether the dataset has usable geometry so I can still create "
                        f"a simple map with the {renderer_name} renderer."
                    ),
                )
                gdf = gpd.read_file(path)
                if isinstance(gdf, gpd.GeoDataFrame) and not gdf.empty and gdf.geometry.notna().any():
                    value_column, values = self._select_fallback_value_column(gdf, task)
                    if value_column:
                        plot_gdf = gdf.copy()
                        plot_gdf["__fallback_value__"] = values
                        plot_gdf = plot_gdf[plot_gdf["__fallback_value__"].notna()].copy()
                        if not plot_gdf.empty:
                            unique_count = int(plot_gdf["__fallback_value__"].nunique())
                            fig, ax = plt.subplots(figsize=(11, 7))
                            plot_kwargs = {
                                "column": "__fallback_value__",
                                "ax": ax,
                                "legend": True,
                                "cmap": "viridis",
                                "edgecolor": "#ffffff",
                                "linewidth": 0.2,
                                "missing_kwds": {"color": "#eeeeee"},
                            }
                            if unique_count >= 2:
                                plot_kwargs["scheme"] = self._requested_classification_scheme(task)
                                plot_kwargs["k"] = min(5, unique_count)
                                plot_kwargs["legend_kwds"] = {"loc": "lower left", "title": value_column}

                            plot_gdf.plot(**plot_kwargs)
                            ax.set_title(task[:120] or "Generated Map")
                            ax.set_axis_off()
                            fig.savefig(target_output_path, dpi=180, bbox_inches="tight", facecolor="white")
                            plt.close(fig)

                            self.primary_output_path = target_output_path
                            self.output_dataset_paths = [target_output_path]
                            self.feature_count = int(len(plot_gdf))
                            self.successful_code = "# deterministic fallback choropleth renderer"
                            self.final_summary = (
                                f"Generated choropleth using deterministic fallback renderer with '{value_column}'."
                            )
                            self._emit_progress(
                                progress_callback,
                                stage="fallback_complete",
                                message=(
                                    f"The {renderer_name} choropleth renderer worked. I found a usable numeric field, "
                                    "coerced it to numeric values, and created a map artifact from the dataset."
                                ),
                                data={
                                    "output_path": target_output_path,
                                    "feature_count": int(len(plot_gdf)),
                                    "value_column": value_column,
                                },
                            )
                            return True

                    fig, ax = plt.subplots(figsize=(11, 7))
                    gdf.plot(ax=ax, color="#4c78a8", edgecolor="#ffffff", linewidth=0.3)
                    ax.set_title(task[:120] or "Generated Map")
                    ax.set_axis_off()
                    fig.savefig(target_output_path, dpi=180, bbox_inches="tight", facecolor="white")
                    plt.close(fig)

                    self.primary_output_path = target_output_path
                    self.output_dataset_paths = [target_output_path]
                    self.feature_count = int(len(gdf))
                    self.successful_code = "# deterministic fallback map renderer"
                    self.final_summary = (
                        "Generated visualization using deterministic fallback renderer after LLM retries failed."
                    )
                    self._emit_progress(
                        progress_callback,
                        stage="fallback_complete",
                        message=(
                            f"The {renderer_name} map renderer worked. I found valid geometries and created "
                            "a simple map artifact from the dataset."
                        ),
                        data={"output_path": target_output_path, "feature_count": int(len(gdf))},
                    )
                    return True
            except Exception:
                continue

        for path in dataset_paths:
            try:
                self._emit_progress(
                    progress_callback,
                    stage="fallback_start",
                    message=(
                        f"The {renderer_name} map renderer was not enough, so I am checking the tabular fields for "
                        "a numeric column that can support a simple chart."
                    ),
                )
                df = pd.read_csv(path)
                if df.empty:
                    continue
                numeric_cols = df.select_dtypes(include=["number"]).columns.tolist()
                if not numeric_cols:
                    continue
                col = numeric_cols[0]
                data = df[col].dropna().head(20)
                if data.empty:
                    continue

                fig, ax = plt.subplots(figsize=(11, 6))
                data.plot(kind="bar", ax=ax, color="#4c78a8")
                ax.set_title(task[:120] or "Generated Chart")
                ax.set_xlabel("Index")
                ax.set_ylabel(col)
                fig.savefig(target_output_path, dpi=180, bbox_inches="tight", facecolor="white")
                plt.close(fig)

                self.primary_output_path = target_output_path
                self.output_dataset_paths = [target_output_path]
                self.feature_count = int(len(df))
                self.successful_code = "# deterministic fallback chart renderer"
                self.final_summary = (
                    "Generated chart using deterministic fallback renderer after LLM retries failed."
                )
                self._emit_progress(
                    progress_callback,
                    stage="fallback_complete",
                    message=(
                        f"The {renderer_name} chart renderer worked. I found a usable numeric field and "
                        "created a chart artifact from the dataset."
                    ),
                    data={"output_path": target_output_path, "feature_count": int(len(df))},
                )
                return True
            except Exception:
                continue

        if emit_failure:
            self._emit_progress(
                progress_callback,
                stage="error",
                message=(
                    "I could not create a reliable fallback visualization from the provided datasets, "
                    "so I will return the failure details instead of inventing an output."
                ),
            )
        return False

    def run(
        self,
        query: str,
        input_dataset_paths: List[str] | str | None = None,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        """Runs the LLM visualization logic iteratively up to max_iterations."""
        task = query
        start_time = time.time()
        dataset_paths = self.normalize_dataset_paths(input_dataset_paths)
        target_output_path = os.path.join(
            OUTPUT_DIR,
            build_output_filename(
                task,
                extension=".png",
                fallback="map",
            ),
        )
        
        # Reset metrics
        self.llm_calls = 0
        self.tool_calls = 0
        self.primary_output_path = None
        self.output_dataset_paths = []
        self.feature_count = None
        self.successful_code = None
        self.last_error = ""
        self.input_tokens = 0
        self.output_tokens = 0
        requested_iterations = self.request_parameters.get("max_iterations")
        try:
            max_iterations = max(1, min(8, int(requested_iterations)))
        except (TypeError, ValueError):
            max_iterations = self.max_iterations

        self._emit_progress(
            progress_callback,
            stage="start",
            message=(
                f"I will inspect the requested visualization and the {len(dataset_paths)} dataset reference(s), "
                "then choose whether a map or chart is the best way to answer it."
            ),
            data={"dataset_count": len(dataset_paths), "max_iterations": max_iterations},
        )

        if self._should_try_fast_renderer(task, dataset_paths):
            self._fast_visualization(task, dataset_paths, target_output_path, progress_callback)
            if self.successful_code:
                max_iterations = 0
        
        # Build schemas info as Markdown
        schemas_info = ""
        for path in dataset_paths:
            schemas_info += f"\nDataset: {path}\n"
            schemas_info += self._dataset_to_markdown_table(path)
            schemas_info += "\n"

        # Build initial instructions
        system_instruction = (
            "You are an expert Python data visualization agent. "
            "You write robust, executable Python scripts using pandas, geopandas, and matplotlib. "
            "You must follow these rules strictly:\n"
            "1. Decide whether a map or chart is better suited. If a map is requested and 'geometry' is present, draw a map. If it's a chart, geometry is non-essential.\n"
            "2. If multiple datasets exist, decide the proper order of layers (z-order).\n"
            "3. If a classification/colorizing column is not explicitly requested and the task doesn't highlight a feature/parameter for visualization, DO NOT classify. Use the UNCLASSIFIED MAP TEMPLATE to just show objects with a uniform beautiful color. Otherwise, automatically choose the most logical continuous or categorical column based on the schema and use the MAP TEMPLATE.\n"
            f"4. Your output file MUST be saved to this exact path: '{target_output_path}'. Do not invent a different filename or path.\n"
            "5. If you need to produce multiple final figures, save the main deliverable to that exact path and any additional figures beside it with clear suffixes.\n"
            "6. If a map/chart type isn't covered in the templates, use the same layout/color concepts but write code for the custom visualization.\n"
            "7. You MUST print each generated output path exactly like this: `print(f\"__OUTPUT_PATH__={OUTPUT_FILE}\")`. If there is more than one artifact, print one line per file and print the main deliverable first.\n"
            "8. You MUST print the length of the main dataframe at the end: `print(f\"__FEATURE_COUNT__={len(gdf_or_df)}\")`\n"
            "9. Ensure the code does not include `plt.show()` causing it to block. Just save the figure.\n"
            "10. Only return valid Python code enclosed in a ```python block.\n"
            "11. NEVER colorize or classify based on identifiers like geoid, fstp code/id, id, etc. If you can't find a suitable/required column for classification/colorizing, return unclassified values.\n"
            "12. ALWAYS the legend Must be Outside of the axes.\n"
            "13. Always generate appropriate, descriptive text for TITLE variables in the templates.\n"
            "14. IMPORTANT: If the task request for a map but the dataset doesn not include geometry column NEVER download data from another external sources.\n"
            "15. NEVER annotae map / NEVER add lables to features on the map.\n"
            "16. Make sure ALWAYS there is just ONE title for the map or chart. If two layers are present, use a combined title and NOT one for each. For example if a vector and raster layer are presetn, never use title for raster.\n"
            "17. If you are plotting a raster dataset with another layer NEVER set title for that image.\n"
            "18. When using rasterio.plot.show(), NEVER pass the 'title' argument. Titles must ONLY be set using ax.set_title().\n"
            "19. NEVER assign an RGBA tuple, list, or other iterable directly to multiple rows with df.loc[mask, column] = value. If assigning one color to multiple rows, use df.loc[mask, column] = [value] * int(mask.sum()), or assign row-by-row with df.at[idx, column] = value. This avoids pandas ValueError: Must have equal len keys and value when setting with an iterable."
        )

        user_prompt = f"""
Task: {task}
Dataset Paths provided: {dataset_paths}

Schema Information: 
{schemas_info}

---
MAP TEMPLATE (Classified/Continuous):
```python
{MAP_TEMPLATE}
```

---
UNCLASSIFIED MAP TEMPLATE (Uniform Color):
```python
{UNCLASSIFIED_MAP_TEMPLATE}
```

---
CHART TEMPLATE:
```python
{CHART_TEMPLATE}
```
"""
        messages = [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": user_prompt}
        ]

        for iteration in range(max_iterations):
            if self.successful_code:
                break
            self._emit_progress(
                progress_callback,
                stage="map_design",
                message=(
                    f"I am drafting visualization code now. This is attempt {iteration + 1}; "
                    "I will run the code and check whether it creates the requested output correctly."
                ),
                data={"iteration": iteration + 1},
            )
            self.llm_calls += 1
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.2, # Keep low for coding consistency
            )
            usage = getattr(response, "usage", None)
            if usage:
                self.input_tokens += getattr(usage, "prompt_tokens", 0) or 0
                self.output_tokens += getattr(usage, "completion_tokens", 0) or 0
            
            ai_reply = response.choices[0].message.content
            code = self._extract_python_code(ai_reply)
            code = re.sub(r"show\(([^)]*?),\s*title\s*=\s*[^)]+\)", r"show(\1)", code)

            self.tool_calls += 1
            success, stdout, stderr = self._execute_code(code)

            if success:
                self._parse_agent_outputs(stdout)
                if not self.primary_output_path and os.path.isfile(target_output_path):
                    self.primary_output_path = target_output_path
                    self.output_dataset_paths = [target_output_path]

                if self.primary_output_path and os.path.isfile(self.primary_output_path):
                    self.successful_code = code
                    self.final_summary = f"Successfully generated visualization in {iteration + 1} iterations."
                    self._emit_progress(
                        progress_callback,
                        stage="artifact_generation",
                        message=(
                            f"The generated code ran successfully on attempt {iteration + 1}. "
                            "I found the output artifact and can now prepare the final map response."
                        ),
                        data={
                            "iteration": iteration + 1,
                            "output_path": self.primary_output_path,
                            "feature_count": self.feature_count,
                        },
                    )
                    break

                success = False
                stderr = (
                    stderr
                    + "\nThe generated script exited successfully, but it did not create or report the required output artifact."
                )
            if not success:
                # Add context of the failure to short-term memory
                messages.append({"role": "assistant", "content": ai_reply})
                error_msg = f"Execution failed with the following error:\n{stderr}\n{stdout}\nPlease fix the python code."
                if "Must have equal len keys and value when setting with an iterable" in error_msg:
                    error_msg += (
                        "\nSpecific repair instruction: this is usually caused by assigning an RGBA tuple, "
                        "list, or other iterable to multiple rows with .loc. Treat the color as a scalar by "
                        "repeating it for the selected row count, e.g. df.loc[mask, 'color'] = [color] * int(mask.sum()), "
                        "or assign one row at a time with df.at[idx, 'color'] = color."
                    )
                self.last_error = error_msg
                logging.warning(error_msg)
                error_preview = self._error_reason_preview(error_msg)
                self._emit_progress(
                    progress_callback,
                    stage="retry",
                    message=(
                        f"The generated code did not run correctly because {error_preview}. "
                        "I will use this error feedback to revise the code and try again."
                    ),
                    data={"iteration": iteration + 1, "error_preview": error_msg[:500]},
                )
                messages.append({"role": "user", "content": error_msg})

        if not self.successful_code:
            self._emit_progress(
                progress_callback,
                stage="fallback_start",
                message=(
                    "The generated-code attempts did not produce a valid visualization, "
                    "so I will switch to a deterministic fallback renderer instead of failing immediately."
                ),
            )
            if not self._fallback_visualization(task, dataset_paths, target_output_path, progress_callback):
                self.final_summary = f"Failed to generate visualization after {max_iterations} iterations."
                if self.last_error:
                    self.final_summary += f" Last error: {self.last_error[:600]}"

        duration = time.time() - start_time
        
        # Determine dataset size type (raster/vector) if possible
        data_type = "unknown"
        if self.primary_output_path:
            # Assuming pandas/geopandas implies vector for this implementation
            data_type = "vector"
        additional_output_paths = [
            path for path in self.output_dataset_paths
            if path and path != self.primary_output_path
        ]

        complementary_info = {
            "Execution": {
                "Inputs": {"task": task, "dataset_paths": dataset_paths},
                "Outputs": {"summary": self.final_summary, "output_path": self.primary_output_path, "output_paths": self.output_dataset_paths}
            },
            "Provenance": {
                "Lineage": {},
                "Tool Calls": {"count": self.tool_calls},
                "LLM Calls": {"count": self.llm_calls}
            },
            "Artifacts and Logs": {
                "Inline Artifacts": {"script": self.successful_code} if self.successful_code else {},
                "Persisted Artifacts": {"image_file": self.primary_output_path, "image_files": self.output_dataset_paths} if self.primary_output_path else {}
            }
        }

        # Construct exact final output
        result = {
            "agent_name": "Mapping Agent",
            "agent_version": "2.0.1",
            "model": self.model,
            "duration": round(duration, 2),
            "total_input_tokens": self.input_tokens,
            "total_output_tokens": self.output_tokens,
            "total_tokens": self.input_tokens + self.output_tokens,
            "inputs": {
                "text": task,
                "dataset_path": dataset_paths
            },
            "outputs": {
                "text": self.final_summary,
                "dataset_path": self.primary_output_path,
                "dataset_paths": additional_output_paths,
                "dataset_size": {
                    "type": data_type,
                    "dimensions": None,
                    "feature_count": self.feature_count
                }
            },
            "metrics": {
                "llm_calls": self.llm_calls,
                "tool_calls": self.tool_calls,
                "number_of_artifacts": len(self.output_dataset_paths)
            },
            "environment": {
                "python_version": sys.version.split(' ')[0],
                "domain-specific libraries": [
                    "pandas",
                    "geopandas",
                    "matplotlib",
                    "mapclassify",
                    "scipy"
                ]
            },
            "script": self.successful_code,
            "complementary": complementary_info
        }
        self._emit_progress(
            progress_callback,
            stage="complete",
            message=(
                "The mapping workflow is complete. I am packaging the generated visualization, "
                "summary, and supporting metadata for the final response."
            ),
            data={"summary": self.final_summary, "output_path": self.primary_output_path},
        )
        return result
