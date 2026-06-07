# GAS Canvas

GAS Canvas is the workflow-building interface for Geospatial Agentic Services.
It provides a visual canvas for composing GAS agents, executing workflows, and
inspecting generated artifacts in persistent workspace tabs.

## Running GAS Canvas

From the `gas_canvas` folder:

```powershell
npm install
npm run dev
```

Open:

```text
http://localhost:3000
```

If `server.ts` changes, restart the Canvas server so the local API helpers are
reloaded.

## Workspace Tabs

GAS Canvas uses four workspace tabs:

- `Canvas`: build and execute multi-agent workflows.
- `Map`: inspect spatial artifacts as map layers.
- `HTML`: render generated HTML applications and reports.
- `Artifacts`: inspect non-spatial and non-HTML artifacts.

Artifact routing is automatic:

- GeoJSON and GeoPackage artifacts open in `Map`.
- HTML and HTM artifacts open in `HTML`.
- CSV, JSON, TXT, images, and other files open in `Artifacts`.

Use `View All Artifacts` in the node inspector to route all outputs from a
completed node into the appropriate workspace tabs. If spatial artifacts are
present, the Map tab is activated first.

## Canvas Navigation

The Canvas tab is the workflow graph editor.

- Drag empty canvas space to pan the workflow.
- Use the mouse wheel to zoom in and out around the cursor.
- Use toolbar buttons for explicit zoom controls.
- Drag nodes to reposition them.
- Drag from input and output ports to connect agents.

Visible scrollbars are hidden; panning and zooming still use the underlying
scroll container to keep large workflows navigable.

## Agent Inspector

Selecting an agent opens the inspector panel. The inspector supports:

- Editing agent task instructions.
- Reviewing active dataset inputs.
- Setting per-node credential overrides.
- Running or canceling an agent task.
- Watching the execution console stream.
- Opening individual artifacts.
- Downloading request and response JSON.

## Map View

The Map tab is optimized for GIS outputs.

Supported spatial artifacts:

- GeoJSON
- GeoPackage

Map View capabilities:

- Select common Leaflet basemaps.
- Add multiple spatial artifacts as layers.
- Show and hide layers.
- Drag layers to reorder map draw order.
- Right-click a layer to view attributes or download the layer.
- Resize and hide the bottom attribute table.
- Style layers by geometry type:
  - Points: color, size, outline.
  - Lines: color and width.
  - Polygons: outline, fill color, fill opacity.

Geometry icons in the layer list are neutral outlines so they are not confused
with layer display colors.

## HTML View

The HTML tab renders generated HTML artifacts in a full-workspace iframe. HTML
is fetched through the local Canvas server and rendered with `srcDoc` to avoid
common remote iframe restrictions.

## Artifacts View

The Artifacts tab is for outputs that are neither spatial layers nor HTML
documents.

Supported previews:

- Images: PNG, JPG, JPEG, GIF, SVG, WebP.
- Tables: CSV and JSON arrays.
- Text/code: JSON objects, TXT, LOG, Markdown, XML, YAML.
- Other files: download fallback with a short snippet when available.

## Local Preview API Helpers

The Canvas server includes local preview helpers:

- `/api/parse-gpkg`: downloads and parses a GeoPackage into GeoJSON for Map
  View.
- `/api/fetch-artifact`: fetches remote artifacts through the local app for
  HTML and generic artifact viewing.

These routes are intended for interactive preview and inspection.
