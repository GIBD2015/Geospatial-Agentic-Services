
# GAS Canvas

GAS Canvas is the workflow-building interface for Geospatial Agentic Services.
It lets users discover GAS agents, compose them into multi-agent workflows,
execute tasks, and inspect generated artifacts in persistent workspace tabs.

## Run Locally

From the `gas_canvas` folder:

```powershell
npm install
npm run dev
```

The development server runs at:

```text
http://localhost:3000
```

If you change `server.ts`, restart the Canvas server so new API routes are
loaded.

## Environment

Local secrets should go in `gas_canvas/.env`, which is ignored by git. Use
`.env.example` as the template.

```text
VITE_MAPBOX_TOKEN=your_mapbox_token_here
```

The current Map View uses Leaflet basemaps and does not require a Mapbox token.

## Workspace Tabs

GAS Canvas uses persistent workspace tabs instead of modal previews:

- `Canvas`: build, pan, zoom, and run the agent workflow.
- `Map`: view spatial artifacts as map layers.
- `HTML`: render generated HTML applications and reports.
- `Artifacts`: inspect non-spatial, non-HTML artifacts such as CSV, JSON, TXT,
  PNG, JPG, SVG, and other files.

Artifact routing is automatic:

- GeoJSON and GeoPackage artifacts open in `Map`.
- HTML and HTM artifacts open in `HTML`.
- CSV, JSON, text, image, and unsupported artifacts open in `Artifacts`.

## Canvas Workflow Controls

Use the GAS Servers panel to add available agents to the canvas. Agents can be
linked by connecting output artifacts from one node to the input socket of
another node.

Navigation:

- Drag empty canvas space to pan the workflow.
- Use the mouse wheel to zoom in and out around the cursor.
- Use the toolbar zoom buttons for explicit zoom controls.
- Drag a node to reposition it.
- Drag from input/output ports to create dataset connections.

The canvas keeps scroll behavior internally for panning and zoom math, but the
visible scrollbars are hidden.

## Running Agents

Select an agent node to open the inspector. The inspector lets users:

- Edit task instructions.
- Review active dataset inputs.
- Override credentials for a specific agent instance.
- Run or cancel node execution.
- Watch the execution console stream.
- Inspect output artifacts and response JSON.

Use `View All Artifacts` to route all artifacts from a completed node into the
appropriate workspace tabs. If spatial artifacts are present, the Map tab is
activated first.

## Map View

The Map tab is designed for GIS outputs and supports multiple loaded layers.

Capabilities:

- Leaflet basemap with selectable common basemaps.
- Add GeoJSON and GeoPackage artifacts as map layers.
- Show or hide layers.
- Drag layers in the layer control to reorder draw order.
- Right-click a layer name to view attributes or download the layer.
- Resize and hide the bottom attribute table.
- Style layers based on geometry type:
  - Point color, size, and outline.
  - Line color and width.
  - Polygon outline, fill color, and fill opacity.

The layer list uses neutral geometry icons for point, polyline, and polygon
types so icons are not confused with layer style colors.

## HTML View

The HTML tab renders generated HTML artifacts in a full-workspace iframe.
Remote HTML is fetched through the local Canvas server and rendered with
`srcDoc`, which avoids common iframe embedding restrictions from artifact
servers.

## Artifacts View

The Artifacts tab is for non-spatial and non-HTML outputs.

Supported previews:

- Images: PNG, JPG, JPEG, GIF, SVG, WebP.
- Tables: CSV and JSON arrays.
- Text/code: JSON objects, TXT, LOG, Markdown, XML, YAML.
- Other formats: download fallback with a short text snippet when possible.

The left artifact list keeps opened artifacts available while users switch
between workspace tabs.

## Local API Helpers

The Canvas server exposes helper endpoints used by the UI:

- `/api/parse-gpkg`: downloads and parses a GeoPackage into GeoJSON for map
  preview.
- `/api/fetch-artifact`: fetches remote artifacts through the local app for
  HTML and generic artifact viewing.

These helpers are preview-oriented and should not be treated as permanent data
storage.
