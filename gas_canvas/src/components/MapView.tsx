import React, { useEffect, useRef, useState } from "react";
import {
  Activity,
  Check,
  ChevronDown,
  Download,
  Eye,
  EyeOff,
  GripVertical,
  Layers,
  RefreshCw,
  SlidersHorizontal,
  Table,
  Trash2,
  X
} from "lucide-react";

declare global {
  interface Window {
    L?: any;
  }
}

const LEAFLET_CSS_URL = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css";
const LEAFLET_JS_URL = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js";
const LEAFLET_CANVAS_STYLE_ID = "gas-canvas-leaflet-overrides";
const ATTRIBUTE_TABLE_ROW_LIMIT = 500;

let leafletPromise: Promise<any> | null = null;

const BASEMAPS = [
  {
    id: "osm",
    name: "OpenStreetMap",
    url: "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
    attribution: "&copy; OpenStreetMap contributors",
    maxZoom: 19
  },
  {
    id: "carto-light",
    name: "Carto Light",
    url: "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png",
    attribution: "&copy; OpenStreetMap contributors &copy; CARTO",
    maxZoom: 20
  },
  {
    id: "carto-dark",
    name: "Carto Dark",
    url: "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
    attribution: "&copy; OpenStreetMap contributors &copy; CARTO",
    maxZoom: 20
  },
  {
    id: "esri-imagery",
    name: "Esri Imagery",
    url: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    attribution: "Tiles &copy; Esri",
    maxZoom: 19
  },
  {
    id: "esri-topo",
    name: "Esri Topo",
    url: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}",
    attribution: "Tiles &copy; Esri",
    maxZoom: 19
  }
];

type MapDataset = {
  id: string;
  title: string;
  url: string;
  kind: "vector" | "raster";
  visible: boolean;
  styleOpen: boolean;
  style: LayerStyle;
  geometry: GeometrySummary;
  paneName: string;
  attributes: AttributeTable;
  raster?: RasterSummary;
};

type LayerStyle = {
  strokeColor: string;
  strokeWidth: number;
  fillColor: string;
  fillOpacity: number;
  pointColor: string;
  pointRadius: number;
};

type GeometrySummary = {
  hasPoint: boolean;
  hasLine: boolean;
  hasPolygon: boolean;
  hasRaster: boolean;
};

type RasterSummary = {
  width?: number;
  height?: number;
  bandCount?: number;
  crs?: string | null;
  opacity?: number;
  stats?: {
    min?: number | null;
    max?: number | null;
  };
};

type AttributeTable = {
  columns: string[];
  rows: Record<string, any>[];
};

type LayerContextMenu = {
  datasetId: string;
  x: number;
  y: number;
} | null;

const DEFAULT_LAYER_STYLE: LayerStyle = {
  strokeColor: "#0f766e",
  strokeWidth: 1.5,
  fillColor: "#0f766e",
  fillOpacity: 0.38,
  pointColor: "#dc2626",
  pointRadius: 5
};

const EMPTY_GEOMETRY_SUMMARY: GeometrySummary = {
  hasPoint: false,
  hasLine: false,
  hasPolygon: false,
  hasRaster: false
};

const addGeometryType = (summary: GeometrySummary, geometryType = "") => {
  if (geometryType.includes("Point")) summary.hasPoint = true;
  if (geometryType.includes("LineString")) summary.hasLine = true;
  if (geometryType.includes("Polygon")) summary.hasPolygon = true;
};

const collectGeometryTypes = (geojson: any): GeometrySummary => {
  const summary = { ...EMPTY_GEOMETRY_SUMMARY };

  const visitGeometry = (geometry: any) => {
    if (!geometry) return;
    if (geometry.type === "GeometryCollection") {
      geometry.geometries?.forEach(visitGeometry);
      return;
    }
    addGeometryType(summary, geometry.type);
  };

  if (geojson?.type === "FeatureCollection") {
    geojson.features?.forEach((feature: any) => visitGeometry(feature.geometry));
  } else if (geojson?.type === "Feature") {
    visitGeometry(geojson.geometry);
  } else {
    visitGeometry(geojson);
  }

  return summary;
};

const getGeoJsonFeatures = (geojson: any) => {
  if (geojson?.type === "FeatureCollection" && Array.isArray(geojson.features)) return geojson.features;
  if (geojson?.type === "Feature") return [geojson];
  return [];
};

const collectAttributeTable = (geojson: any): AttributeTable => {
  const rows = getGeoJsonFeatures(geojson).map((feature: any) => feature?.properties || {});
  const columns = Array.from(new Set<string>(rows.flatMap((row: Record<string, any>) => Object.keys(row))));
  return { columns, rows };
};

const formatAttributeValue = (value: any) => {
  if (value === null || value === undefined || value === "") return "-";
  if (typeof value === "object") {
    try {
      return JSON.stringify(value);
    } catch {
      return String(value);
    }
  }
  return String(value);
};

const getPathStyle = (style: LayerStyle, geometryType = "") => {
  if (geometryType.includes("LineString")) {
    return {
      color: style.strokeColor,
      weight: style.strokeWidth,
      opacity: 0.95
    };
  }

  return {
    color: style.strokeColor,
    weight: style.strokeWidth,
    fillColor: style.fillColor,
    fillOpacity: style.fillOpacity
  };
};

const getPointStyle = (style: LayerStyle) => ({
  radius: style.pointRadius,
  fillColor: style.pointColor,
  color: style.strokeColor,
  weight: Math.max(1, style.strokeWidth),
  opacity: 1,
  fillOpacity: 0.9
});

const applyLayerStyle = (layer: any, style: LayerStyle) => {
  layer?.eachLayer?.((item: any) => {
    if (typeof item.setRadius === "function") {
      item.setRadius(style.pointRadius);
      item.setStyle?.(getPointStyle(style));
      return;
    }

    const geometryType = item.feature?.geometry?.type || "";
    item.setStyle?.(getPathStyle(style, geometryType));
  });
};

const GeometryIcons: React.FC<{ geometry: GeometrySummary }> = ({ geometry }) => (
  <span className="flex shrink-0 items-center gap-1" aria-label="Layer geometry type">
    {geometry.hasRaster && (
      <span title="Raster layer" className="grid h-3.5 w-3.5 grid-cols-2 gap-px overflow-hidden rounded-[2px] border border-neutral-500">
        <span className="bg-emerald-300" />
        <span className="bg-sky-300" />
        <span className="bg-amber-300" />
        <span className="bg-neutral-300" />
      </span>
    )}
    {geometry.hasPoint && (
      <span title="Point layer" className="h-2.5 w-2.5 rounded-full border border-neutral-500 bg-transparent" />
    )}
    {geometry.hasLine && (
      <svg
        viewBox="0 0 16 14"
        title="Line layer"
        aria-hidden="true"
        className="h-3.5 w-4 text-neutral-500"
      >
        <path
          d="M1.5 9.5 5.2 4.2l4.1 4.1 5.2-6"
          fill="none"
          stroke="currentColor"
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth="1.5"
        />
      </svg>
    )}
    {geometry.hasPolygon && (
      <svg
        viewBox="0 0 14 14"
        title="Polygon layer"
        aria-hidden="true"
        className="h-3.5 w-3.5 text-neutral-500"
      >
        <path
          d="M2.2 4.6 6.1 1.8l5.4 2.1-.9 6.2-4.8 1.9-3.9-3.2Z"
          fill="none"
          stroke="currentColor"
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth="1.4"
        />
      </svg>
    )}
  </span>
);

const reorderDatasets = (items: MapDataset[], fromIndex: number, toIndex: number) => {
  const next = [...items];
  const [moved] = next.splice(fromIndex, 1);
  next.splice(toIndex, 0, moved);
  return next;
};

const getLayerPaneName = (datasetId: string) => {
  let hash = 0;
  for (let index = 0; index < datasetId.length; index += 1) {
    hash = (hash * 31 + datasetId.charCodeAt(index)) | 0;
  }
  return `gas-layer-${Math.abs(hash)}`;
};

const getApiUrl = (path: string) => {
  const pathname = window.location.pathname;
  if (pathname.startsWith("/canvas")) {
    return `/canvas${path}`;
  }
  return path;
};

const getArtifactExtension = (url: string, title: string) => {
  const normalizedTitle = title.toLowerCase();
  if (normalizedTitle.includes("geotiff") || normalizedTitle.includes("geo tiff")) return "geotiff";

  const fromTitle = title.split(".").pop()?.toLowerCase();
  if (fromTitle && fromTitle !== title.toLowerCase()) return fromTitle;

  try {
    const pathname = new URL(url, window.location.href).pathname;
    const normalizedPathname = pathname.toLowerCase();
    if (normalizedPathname.includes("geotiff") || normalizedPathname.includes("geo_tiff")) return "geotiff";
    return pathname.split(".").pop()?.toLowerCase() || "";
  } catch {
    const normalizedUrl = url.toLowerCase();
    if (normalizedUrl.includes("geotiff") || normalizedUrl.includes("geo_tiff")) return "geotiff";
    return url.split("?")[0].split(".").pop()?.toLowerCase() || "";
  }
};

const isGeoTiffExtension = (extension: string) =>
  ["tif", "tiff", "geotiff", "geotif"].includes(extension.toLowerCase());

const loadLeaflet = () => {
  if (window.L) return Promise.resolve(window.L);
  if (leafletPromise) return leafletPromise;

  leafletPromise = new Promise((resolve, reject) => {
    if (!document.querySelector(`link[href="${LEAFLET_CSS_URL}"]`)) {
      const link = document.createElement("link");
      link.rel = "stylesheet";
      link.href = LEAFLET_CSS_URL;
      document.head.appendChild(link);
    }

    let style = document.getElementById(LEAFLET_CANVAS_STYLE_ID);
    if (!style) {
      style = document.createElement("style");
      style.id = LEAFLET_CANVAS_STYLE_ID;
      document.head.appendChild(style);
    }
    style.textContent = `
      .gas-map-view .leaflet-container img.leaflet-tile {
        border: 0 !important;
        max-width: none !important;
        outline: none !important;
        box-shadow: none !important;
        transform-origin: top left;
        backface-visibility: hidden;
      }
      .gas-map-view .leaflet-tile-container {
        line-height: 0;
      }
      .gas-map-view .leaflet-container,
      .gas-map-view .leaflet-pane,
      .gas-map-view .leaflet-map-pane {
        background: #d8dde3;
      }
    `;

    const existingScript = document.querySelector<HTMLScriptElement>(`script[src="${LEAFLET_JS_URL}"]`);
    if (existingScript) {
      existingScript.addEventListener("load", () => resolve(window.L));
      existingScript.addEventListener("error", () => reject(new Error("Failed to load Leaflet.")));
      return;
    }

    const script = document.createElement("script");
    script.src = LEAFLET_JS_URL;
    script.async = true;
    script.onload = () => resolve(window.L);
    script.onerror = () => reject(new Error("Failed to load Leaflet."));
    document.body.appendChild(script);
  });

  return leafletPromise;
};

interface MapViewProps {
  artifact?: {
    url: string;
    title: string;
  } | null;
  artifacts?: Array<{
    url: string;
    title: string;
  }>;
  isVisible?: boolean;
  resizeKey?: string;
  onRemoveArtifact?: (url: string) => void;
}

export const MapView: React.FC<MapViewProps> = ({ artifact = null, artifacts = [], isVisible = true, resizeKey = "", onRemoveArtifact }) => {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [mapError, setMapError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [mapReady, setMapReady] = useState(false);
  const [basemapId, setBasemapId] = useState("osm");
  const [isBasemapMenuOpen, setIsBasemapMenuOpen] = useState(false);
  const [datasets, setDatasets] = useState<MapDataset[]>([]);
  const [layerContextMenu, setLayerContextMenu] = useState<LayerContextMenu>(null);
  const [attributeDatasetId, setAttributeDatasetId] = useState<string | null>(null);
  const [attributeLoadingDatasetId, setAttributeLoadingDatasetId] = useState<string | null>(null);
  const [attributePanelHeight, setAttributePanelHeight] = useState(224);
  const mapContainerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<any>(null);
  const basemapLayerRef = useRef<any>(null);
  const layerRefs = useRef<Record<string, any>>({});
  const draggedDatasetIdRef = useRef<string | null>(null);
  const artifactsLoadKey = artifacts.map((item) => `${item.url}|${item.title}`).join("||");

  const syncLayerOrder = (orderedDatasets: MapDataset[]) => {
    const map = mapRef.current;
    if (!map) return;

    const topZIndex = 700;
    orderedDatasets.forEach((dataset, index) => {
      const pane = map.getPane?.(dataset.paneName);
      if (pane) {
        pane.style.zIndex = String(topZIndex + orderedDatasets.length - index);
      }
    });
  };

  useEffect(() => {
    let cancelled = false;

    loadLeaflet()
      .then((L) => {
        if (cancelled || !mapContainerRef.current || mapRef.current) return;

        const map = L.map(mapContainerRef.current, {
          center: [20, 0],
          zoom: 2,
          minZoom: 1,
          worldCopyJump: true,
          zoomControl: true
        });

        const basemap = BASEMAPS.find((item) => item.id === basemapId) || BASEMAPS[0];
        const tileLayer = L.tileLayer(basemap.url, {
          maxZoom: basemap.maxZoom,
          attribution: basemap.attribution
        });

        tileLayer.addTo(map);

        mapRef.current = map;
        basemapLayerRef.current = tileLayer;
        setMapReady(true);
        requestAnimationFrame(() => map.invalidateSize());
      })
      .catch((err) => {
        if (!cancelled) setMapError(err.message);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!mapReady || !map) return;

    loadLeaflet().then((L) => {
      const basemap = BASEMAPS.find((item) => item.id === basemapId) || BASEMAPS[0];
      if (basemapLayerRef.current) {
        map.removeLayer(basemapLayerRef.current);
      }

      const tileLayer = L.tileLayer(basemap.url, {
        maxZoom: basemap.maxZoom,
        attribution: basemap.attribution
      });
      tileLayer.addTo(map);
      basemapLayerRef.current = tileLayer;
      requestAnimationFrame(() => map.invalidateSize());
    });
  }, [basemapId, mapReady]);

  useEffect(() => {
    if (!isVisible || !mapRef.current) return;

    requestAnimationFrame(() => mapRef.current?.invalidateSize());
    window.setTimeout(() => mapRef.current?.invalidateSize(), 150);
    window.setTimeout(() => mapRef.current?.invalidateSize(), 350);
  }, [isVisible, resizeKey]);

  useEffect(() => {
    if (!layerContextMenu) return;

    const closeMenu = () => setLayerContextMenu(null);
    window.addEventListener("pointerdown", closeMenu);
    window.addEventListener("blur", closeMenu);

    return () => {
      window.removeEventListener("pointerdown", closeMenu);
      window.removeEventListener("blur", closeMenu);
    };
  }, [layerContextMenu]);

  useEffect(() => {
    let cancelled = false;

    const loadArtifact = async () => {
      const L = await loadLeaflet();
      const map = mapRef.current;
      if (!map) return;

      const artifactsToLoad = artifacts.length > 0 ? artifacts : artifact ? [artifact] : [];

      if (artifactsToLoad.length === 0) {
        setLoading(false);
        setError(null);
        return;
      }

      const zoomTargetId = artifact?.url || artifactsToLoad[0]?.url || "";

      for (const currentArtifact of artifactsToLoad) {
        const datasetId = currentArtifact.url;
        const existingLayer = layerRefs.current[datasetId];
        if (existingLayer) {
          if (!map.hasLayer(existingLayer)) {
            existingLayer.addTo(map);
          }
          setDatasets((prev) =>
            prev.map((dataset) => (dataset.id === datasetId ? { ...dataset, visible: true } : dataset))
          );
          if (datasetId === zoomTargetId) {
            const bounds = existingLayer.getBounds?.();
            if (bounds?.isValid?.()) {
              map.fitBounds(bounds, { padding: [56, 56], maxZoom: 14 });
            }
          }
          setLoading(false);
          setError(null);
          continue;
        }

        const ext = getArtifactExtension(currentArtifact.url, currentArtifact.title);
        const fetchUrl =
          ext === "gpkg"
            ? getApiUrl(`/api/parse-gpkg?url=${encodeURIComponent(currentArtifact.url)}`)
            : isGeoTiffExtension(ext)
              ? getApiUrl(`/api/parse-geotiff?url=${encodeURIComponent(currentArtifact.url)}`)
            : currentArtifact.url;

        setLoading(true);
        setError(null);

        try {
          const response = await fetch(fetchUrl);
          if (!response.ok) {
            const errText = await response.text().catch(() => "Unknown error");
            try {
              const parsed = JSON.parse(errText);
              throw new Error(parsed.details || parsed.error || errText);
            } catch (err: any) {
              throw new Error(err.message || errText);
            }
          }

          if (isGeoTiffExtension(ext)) {
            const rasterPreview = await response.json();
            if (cancelled) return;

            const bounds = rasterPreview?.bounds || {};
            const hasBounds = [bounds.south, bounds.west, bounds.north, bounds.east].every(
              (value) => typeof value === "number" && Number.isFinite(value)
            );
            if (!hasBounds || !rasterPreview?.image_data_url) {
              throw new Error("GeoTIFF preview did not include valid bounds or image data.");
            }

            const paneName = getLayerPaneName(datasetId);
            const pane = map.getPane(paneName) || map.createPane(paneName);
            pane.style.zIndex = String(700 + datasets.length + 1);
            const leafletBounds = L.latLngBounds(
              [bounds.south, bounds.west],
              [bounds.north, bounds.east]
            );
            const layer = L.imageOverlay(rasterPreview.image_data_url, leafletBounds, {
              pane: paneName,
              opacity: 0.86
            }).addTo(map);

            layerRefs.current[datasetId] = layer;
            setDatasets((prev) => {
              const next = [
                {
                  id: datasetId,
                  title: currentArtifact.title,
                  url: currentArtifact.url,
                  kind: "raster" as const,
                  visible: true,
                  styleOpen: false,
                  style: { ...DEFAULT_LAYER_STYLE },
                  geometry: { ...EMPTY_GEOMETRY_SUMMARY, hasRaster: true },
                  paneName,
                  attributes: { columns: [], rows: [] },
                  raster: {
                    width: rasterPreview.width,
                    height: rasterPreview.height,
                    bandCount: rasterPreview.band_count,
                    crs: rasterPreview.crs,
                    opacity: 0.86,
                    stats: rasterPreview.stats
                  }
                },
                ...prev.filter((dataset) => dataset.id !== datasetId)
              ];
              syncLayerOrder(next);
              return next;
            });

            if (datasetId === zoomTargetId && leafletBounds?.isValid?.()) {
              map.fitBounds(leafletBounds, { padding: [56, 56], maxZoom: 14 });
            }
          } else {
            const geojson = await response.json();
            if (cancelled) return;

            const layerStyle = { ...DEFAULT_LAYER_STYLE };
            const geometry = collectGeometryTypes(geojson);
            const attributes = collectAttributeTable(geojson);
            const paneName = getLayerPaneName(datasetId);
            const pane = map.getPane(paneName) || map.createPane(paneName);
            pane.style.zIndex = String(700 + datasets.length + 1);
            const layer = L.geoJSON(geojson, {
              pane: paneName,
              pointToLayer: (_feature: any, latlng: any) =>
                L.circleMarker(latlng, { ...getPointStyle(layerStyle), pane: paneName }),
              style: (feature: any) => {
                const geometryType = feature?.geometry?.type || "";
                return getPathStyle(layerStyle, geometryType);
              }
            }).addTo(map);

            layerRefs.current[datasetId] = layer;
            setDatasets((prev) => {
              const next = [
                {
                  id: datasetId,
                  title: currentArtifact.title,
                  url: currentArtifact.url,
                  kind: "vector" as const,
                  visible: true,
                  styleOpen: false,
                  style: layerStyle,
                  geometry,
                  paneName,
                  attributes
                },
                ...prev.filter((dataset) => dataset.id !== datasetId)
              ];
              syncLayerOrder(next);
              return next;
            });
            const bounds = layer.getBounds?.();
            if (datasetId === zoomTargetId && bounds?.isValid?.()) {
              map.fitBounds(bounds, { padding: [56, 56], maxZoom: 14 });
            }
          }

          requestAnimationFrame(() => map.invalidateSize());
          setLoading(false);
        } catch (err: any) {
          if (!cancelled) {
            setError(err.message);
            setLoading(false);
          }
        }
      }
    };

    loadArtifact().catch((err) => {
      if (!cancelled) {
        setError(err.message);
        setLoading(false);
      }
    });

    return () => {
      cancelled = true;
    };
  }, [artifact?.url, artifact?.title, artifactsLoadKey, reloadKey, mapReady]);

  const toggleDatasetVisibility = (datasetId: string) => {
    const map = mapRef.current;
    const layer = layerRefs.current[datasetId];
    if (!map || !layer) return;

    const nextVisible = !map.hasLayer(layer);
    if (nextVisible) {
      layer.addTo(map);
    } else {
      map.removeLayer(layer);
    }

    setDatasets((prev) =>
      prev.map((dataset) => (dataset.id === datasetId ? { ...dataset, visible: nextVisible } : dataset))
    );
  };

  const zoomToDataset = (datasetId: string) => {
    const map = mapRef.current;
    const layer = layerRefs.current[datasetId];
    if (!map || !layer) return;

    const bounds = layer.getBounds?.();
    if (bounds?.isValid?.()) {
      map.fitBounds(bounds, { padding: [56, 56], maxZoom: 14 });
    }
  };

  const toggleDatasetStylePanel = (datasetId: string) => {
    setDatasets((prev) =>
      prev.map((dataset) =>
        dataset.id === datasetId ? { ...dataset, styleOpen: !dataset.styleOpen } : dataset
      )
    );
  };

  const updateDatasetStyle = (datasetId: string, patch: Partial<LayerStyle>) => {
    setDatasets((prev) =>
      prev.map((dataset) => {
        if (dataset.id !== datasetId) return dataset;

        const nextStyle = { ...dataset.style, ...patch };
        applyLayerStyle(layerRefs.current[datasetId], nextStyle);
        return { ...dataset, style: nextStyle };
      })
    );
  };

  const updateRasterOpacity = (datasetId: string, opacity: number) => {
    const normalizedOpacity = Math.max(0, Math.min(1, opacity));
    layerRefs.current[datasetId]?.setOpacity?.(normalizedOpacity);
    setDatasets((prev) =>
      prev.map((dataset) =>
        dataset.id === datasetId
          ? {
              ...dataset,
              raster: {
                ...(dataset.raster || {}),
                opacity: normalizedOpacity
              }
            }
          : dataset
      )
    );
  };

  const removeDatasetFromMap = (datasetId: string) => {
    const map = mapRef.current;
    const layer = layerRefs.current[datasetId];
    if (map && layer && map.hasLayer(layer)) {
      map.removeLayer(layer);
    }

    delete layerRefs.current[datasetId];
    setDatasets((prev) => {
      const next = prev.filter((dataset) => dataset.id !== datasetId);
      syncLayerOrder(next);
      return next;
    });
    setAttributeDatasetId((currentId) => (currentId === datasetId ? null : currentId));
    setAttributeLoadingDatasetId((currentId) => (currentId === datasetId ? null : currentId));
    setLayerContextMenu(null);
    onRemoveArtifact?.(datasetId);
  };

  const handleDatasetDrop = (targetDatasetId: string) => {
    const draggedDatasetId = draggedDatasetIdRef.current;
    draggedDatasetIdRef.current = null;
    if (!draggedDatasetId || draggedDatasetId === targetDatasetId) return;

    setDatasets((prev) => {
      const fromIndex = prev.findIndex((dataset) => dataset.id === draggedDatasetId);
      const toIndex = prev.findIndex((dataset) => dataset.id === targetDatasetId);
      if (fromIndex < 0 || toIndex < 0) return prev;

      const next = reorderDatasets(prev, fromIndex, toIndex);
      syncLayerOrder(next);
      return next;
    });
  };

  const selectedBasemap = BASEMAPS.find((item) => item.id === basemapId) || BASEMAPS[0];
  const selectedAttributeDataset = datasets.find((dataset) => dataset.id === attributeDatasetId) || null;
  const loadingAttributeDataset = datasets.find((dataset) => dataset.id === attributeLoadingDatasetId) || null;
  const selectedAttributeRows = selectedAttributeDataset?.attributes.rows || [];
  const displayedAttributeRows = selectedAttributeRows.slice(0, ATTRIBUTE_TABLE_ROW_LIMIT);
  const selectedAttributeColumns = selectedAttributeDataset?.attributes.columns || [];

  const openAttributePanel = (datasetId: string) => {
    setLayerContextMenu(null);
    setAttributeLoadingDatasetId(datasetId);
    setAttributeDatasetId(null);
    window.setTimeout(() => {
      setAttributeDatasetId(datasetId);
      window.requestAnimationFrame(() => setAttributeLoadingDatasetId(null));
    }, 40);
  };

  const openLayerContextMenu = (datasetId: string, event: React.MouseEvent) => {
    event.preventDefault();
    event.stopPropagation();
    const menuWidth = 224;
    const menuHeight = 124;
    const bounds = mapContainerRef.current?.getBoundingClientRect();
    const minX = bounds?.left ?? 0;
    const minY = bounds?.top ?? 0;
    const maxX = (bounds?.right ?? window.innerWidth) - menuWidth - 8;
    const maxY = (bounds?.bottom ?? window.innerHeight) - menuHeight - 8;

    setLayerContextMenu({
      datasetId,
      x: Math.max(minX + 8, Math.min(event.clientX, maxX)),
      y: Math.max(minY + 8, Math.min(event.clientY, maxY))
    });
  };

  const handleAttributePanelResize = (event: React.PointerEvent) => {
    event.preventDefault();
    const startY = event.clientY;
    const startHeight = attributePanelHeight;
    const mapBounds = mapContainerRef.current?.getBoundingClientRect();
    const maxHeight = Math.max(240, (mapBounds?.height || window.innerHeight) - 120);

    const handlePointerMove = (moveEvent: PointerEvent) => {
      const nextHeight = startHeight + startY - moveEvent.clientY;
      setAttributePanelHeight(Math.max(140, Math.min(maxHeight, nextHeight)));
    };

    const handlePointerUp = () => {
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", handlePointerUp);
      window.removeEventListener("pointercancel", handlePointerUp);
    };

    window.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerup", handlePointerUp);
    window.addEventListener("pointercancel", handlePointerUp);
  };

  return (
    <div className="gas-map-view relative h-full min-h-[360px] overflow-hidden bg-[#d8dde3]">
      <div ref={mapContainerRef} className="absolute inset-0 z-0" />

      <div className="absolute right-4 top-4 z-[500] flex items-start gap-2">
        <div className="relative">
          <button
            type="button"
            onClick={() => setIsBasemapMenuOpen((open) => !open)}
            className="flex h-9 items-center gap-2 rounded-lg border border-neutral-200 bg-white/95 px-3 text-xs font-bold text-neutral-800 shadow-sm backdrop-blur hover:bg-white"
          >
            <Layers className="h-4 w-4 text-sky-600" />
            <span>{selectedBasemap.name}</span>
            <ChevronDown className="h-3.5 w-3.5 text-neutral-500" />
          </button>

          {isBasemapMenuOpen && (
            <div className="absolute right-0 top-11 w-48 overflow-hidden rounded-lg border border-neutral-200 bg-white shadow-lg">
              {BASEMAPS.map((basemap) => (
                <button
                  key={basemap.id}
                  type="button"
                  onClick={() => {
                    setBasemapId(basemap.id);
                    setIsBasemapMenuOpen(false);
                  }}
                  className="flex w-full items-center justify-between px-3 py-2 text-left text-xs font-semibold text-neutral-700 hover:bg-neutral-50"
                >
                  <span>{basemap.name}</span>
                  {basemap.id === basemapId && <Check className="h-3.5 w-3.5 text-sky-600" />}
                </button>
              ))}
            </div>
          )}
        </div>

        <button
          type="button"
          title="Reload current layer"
          onClick={() => setReloadKey((key) => key + 1)}
          className="flex h-9 w-9 items-center justify-center rounded-lg border border-neutral-200 bg-white/95 text-neutral-600 shadow-sm backdrop-blur hover:bg-white hover:text-sky-700"
        >
          <RefreshCw className="h-4 w-4" />
        </button>
      </div>

      <div
        className={`absolute right-4 z-[500] w-72 overflow-hidden rounded-lg border border-neutral-200 bg-white/95 shadow-lg backdrop-blur ${
          selectedAttributeDataset ? "bottom-64" : "bottom-4"
        }`}
      >
        <div className="flex items-center justify-between border-b border-neutral-100 px-3 py-2">
          <div className="flex items-center gap-2">
            <Layers className="h-4 w-4 text-emerald-600" />
            <h3 className="text-xs font-bold uppercase text-neutral-700">Layers</h3>
          </div>
          <span className="rounded bg-neutral-100 px-1.5 py-0.5 text-[10px] font-bold text-neutral-500">
            {datasets.length}
          </span>
        </div>
        {datasets.length === 0 ? (
          <div className="px-3 py-3 text-xs leading-relaxed text-neutral-500">
            Preview GeoJSON, GeoPackage, or GeoTIFF artifacts to add map layers.
          </div>
        ) : (
          <div className="max-h-80 overflow-y-auto p-2">
            {datasets.map((dataset) => (
              <div
                key={dataset.id}
                onDragOver={(event) => event.preventDefault()}
                onDrop={() => handleDatasetDrop(dataset.id)}
                className="rounded-md px-2 py-1.5 hover:bg-neutral-50"
              >
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    draggable
                    title="Drag to reorder layer"
                    onDragStart={() => {
                      draggedDatasetIdRef.current = dataset.id;
                    }}
                    onDragEnd={() => {
                      draggedDatasetIdRef.current = null;
                    }}
                    className="flex h-7 w-5 shrink-0 cursor-grab items-center justify-center rounded text-neutral-400 hover:bg-white hover:text-neutral-700 active:cursor-grabbing"
                  >
                    <GripVertical className="h-4 w-4" />
                  </button>
                  <button
                    type="button"
                    title={dataset.visible ? "Hide layer" : "Show layer"}
                    onClick={() => toggleDatasetVisibility(dataset.id)}
                    className="flex h-7 w-7 shrink-0 items-center justify-center rounded border border-neutral-200 bg-white text-neutral-600 hover:text-sky-700"
                  >
                    {dataset.visible ? <Eye className="h-3.5 w-3.5" /> : <EyeOff className="h-3.5 w-3.5" />}
                  </button>
                  <button
                    type="button"
                    onClick={() => zoomToDataset(dataset.id)}
                    onContextMenu={(event) => openLayerContextMenu(dataset.id, event)}
                    className="flex min-w-0 flex-1 items-center gap-2 text-left"
                    title={`${dataset.title}. Right-click for layer actions.`}
                  >
                    <GeometryIcons geometry={dataset.geometry} />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-xs font-bold text-neutral-800">{dataset.title}</span>
                      <span className="block truncate font-mono text-[10px] text-neutral-400">
                        {dataset.kind === "raster" && dataset.raster?.width && dataset.raster?.height
                          ? `${dataset.raster.width} x ${dataset.raster.height}${dataset.raster.bandCount ? `, ${dataset.raster.bandCount} band${dataset.raster.bandCount === 1 ? "" : "s"}` : ""}`
                          : dataset.url}
                      </span>
                    </span>
                  </button>
                  {dataset.kind === "vector" && (
                    <button
                      type="button"
                      title="Style layer"
                      onClick={() => toggleDatasetStylePanel(dataset.id)}
                      className={`flex h-7 w-7 shrink-0 items-center justify-center rounded border border-neutral-200 bg-white hover:text-sky-700 ${
                        dataset.styleOpen ? "text-sky-700" : "text-neutral-600"
                      }`}
                    >
                      <SlidersHorizontal className="h-3.5 w-3.5" />
                    </button>
                  )}
                </div>

                {dataset.kind === "vector" && dataset.styleOpen && (
                  <div className="mt-2 grid grid-cols-2 gap-2 border-t border-neutral-100 pt-2">
                    {(dataset.geometry.hasLine || dataset.geometry.hasPolygon || dataset.geometry.hasPoint) && (
                      <label className="text-[10px] font-bold uppercase text-neutral-500">
                        {dataset.geometry.hasPoint && !dataset.geometry.hasLine && !dataset.geometry.hasPolygon
                          ? "Outline"
                          : "Line"}
                        <input
                          type="color"
                          value={dataset.style.strokeColor}
                          onChange={(event) =>
                            updateDatasetStyle(dataset.id, { strokeColor: event.currentTarget.value })
                          }
                          className="mt-1 h-7 w-full cursor-pointer rounded border border-neutral-200 bg-white p-0.5"
                        />
                      </label>
                    )}
                    {(dataset.geometry.hasLine || dataset.geometry.hasPolygon || dataset.geometry.hasPoint) && (
                      <label className="text-[10px] font-bold uppercase text-neutral-500">
                        Width {dataset.style.strokeWidth.toFixed(1)}
                        <input
                          type="range"
                          min="0.3"
                          max="8"
                          step="0.1"
                          value={dataset.style.strokeWidth}
                          onChange={(event) =>
                            updateDatasetStyle(dataset.id, { strokeWidth: Number(event.currentTarget.value) })
                          }
                          className="mt-1 h-6 w-full accent-sky-600"
                        />
                      </label>
                    )}
                    {dataset.geometry.hasPolygon && (
                      <label className="text-[10px] font-bold uppercase text-neutral-500">
                        Fill
                        <input
                          type="color"
                          value={dataset.style.fillColor}
                          onChange={(event) =>
                            updateDatasetStyle(dataset.id, { fillColor: event.currentTarget.value })
                          }
                          className="mt-1 h-7 w-full cursor-pointer rounded border border-neutral-200 bg-white p-0.5"
                        />
                      </label>
                    )}
                    {dataset.geometry.hasPolygon && (
                      <label className="text-[10px] font-bold uppercase text-neutral-500">
                        Opacity {Math.round(dataset.style.fillOpacity * 100)}%
                        <input
                          type="range"
                          min="0"
                          max="1"
                          step="0.05"
                          value={dataset.style.fillOpacity}
                          onChange={(event) =>
                            updateDatasetStyle(dataset.id, { fillOpacity: Number(event.currentTarget.value) })
                          }
                          className="mt-1 h-6 w-full accent-sky-600"
                        />
                      </label>
                    )}
                    {dataset.geometry.hasPoint && (
                      <label className="text-[10px] font-bold uppercase text-neutral-500">
                        Point
                        <input
                          type="color"
                          value={dataset.style.pointColor}
                          onChange={(event) =>
                            updateDatasetStyle(dataset.id, { pointColor: event.currentTarget.value })
                          }
                          className="mt-1 h-7 w-full cursor-pointer rounded border border-neutral-200 bg-white p-0.5"
                        />
                      </label>
                    )}
                    {dataset.geometry.hasPoint && (
                      <label className="text-[10px] font-bold uppercase text-neutral-500">
                        Size {dataset.style.pointRadius}
                        <input
                          type="range"
                          min="2"
                          max="18"
                          step="1"
                          value={dataset.style.pointRadius}
                          onChange={(event) =>
                            updateDatasetStyle(dataset.id, { pointRadius: Number(event.currentTarget.value) })
                          }
                          className="mt-1 h-6 w-full accent-sky-600"
                        />
                      </label>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {layerContextMenu && (
        <div
          onPointerDown={(event) => event.stopPropagation()}
          className="fixed z-[900] w-56 overflow-hidden rounded-lg border border-neutral-200 bg-white text-xs shadow-xl"
          style={{ left: layerContextMenu.x, top: layerContextMenu.y }}
        >
          {(() => {
            const dataset = datasets.find((item) => item.id === layerContextMenu.datasetId);
            if (!dataset) return null;

            return (
              <>
              {dataset.kind === "vector" && (
                <button
                  type="button"
                  onClick={() => openAttributePanel(layerContextMenu.datasetId)}
                  className="flex w-full items-center gap-2 px-3 py-2 text-left font-semibold text-neutral-700 hover:bg-neutral-50"
                >
                  <Table className="h-3.5 w-3.5 text-sky-600" />
                  <span>View Attributes</span>
                </button>
              )}
              {dataset.kind === "raster" && (
                <div
                  className="border-b border-neutral-100 px-3 py-2"
                  onPointerDown={(event) => event.stopPropagation()}
                >
                  <label className="block text-[10px] font-bold uppercase text-neutral-500">
                    Opacity {Math.round((dataset.raster?.opacity ?? 0.86) * 100)}%
                    <input
                      type="range"
                      min="0"
                      max="1"
                      step="0.05"
                      value={dataset.raster?.opacity ?? 0.86}
                      onChange={(event) => updateRasterOpacity(dataset.id, Number(event.currentTarget.value))}
                      className="mt-1 h-6 w-full accent-sky-600"
                    />
                  </label>
                </div>
              )}
              <a
                href={dataset.url}
                download
                rel="referrer"
                className="flex w-full items-center gap-2 px-3 py-2 text-left font-semibold text-neutral-700 hover:bg-neutral-50"
              >
                <Download className="h-3.5 w-3.5 text-emerald-600" />
                <span>Download Layer</span>
              </a>
              <button
                type="button"
                onClick={() => removeDatasetFromMap(dataset.id)}
                className="flex w-full items-center gap-2 px-3 py-2 text-left font-semibold text-rose-600 hover:bg-rose-50"
              >
                <Trash2 className="h-3.5 w-3.5" />
                <span>Remove from Map</span>
              </button>
              </>
            );
          })()}
        </div>
      )}

      {loadingAttributeDataset && !selectedAttributeDataset && (
        <div className="absolute bottom-0 left-0 right-0 z-[520] flex h-28 items-center justify-center border-t border-neutral-200 bg-white/95 shadow-[0_-8px_24px_rgba(15,23,42,0.12)] backdrop-blur">
          <div className="flex items-center gap-3 rounded-lg border border-sky-100 bg-sky-50 px-4 py-3 text-sky-800 shadow-sm">
            <Activity className="h-4 w-4 animate-pulse" />
            <div>
              <p className="text-xs font-bold">Preparing attributes...</p>
              <p className="mt-0.5 max-w-md truncate text-[10px] text-sky-700">
                {loadingAttributeDataset.title} has {loadingAttributeDataset.attributes.rows.length.toLocaleString()} rows.
              </p>
            </div>
          </div>
        </div>
      )}

      {selectedAttributeDataset && (
        <div
          className="absolute bottom-0 left-0 right-0 z-[520] border-t border-neutral-200 bg-white/95 shadow-[0_-8px_24px_rgba(15,23,42,0.12)] backdrop-blur"
          style={{ height: `${attributePanelHeight}px` }}
        >
          <div
            title="Drag to resize attributes panel"
            onPointerDown={handleAttributePanelResize}
            className="absolute -top-1 left-0 right-0 flex h-2 cursor-row-resize items-center justify-center"
          >
            <div className="h-1 w-16 rounded-full bg-neutral-300 shadow-sm" />
          </div>
          <div className="flex h-11 items-center justify-between border-b border-neutral-100 px-4">
            <div className="flex min-w-0 items-center gap-2">
              <Table className="h-4 w-4 shrink-0 text-sky-600" />
              <div className="min-w-0">
                <h3 className="truncate text-xs font-bold text-neutral-800">{selectedAttributeDataset.title}</h3>
                <p className="text-[10px] text-neutral-500">
                  {selectedAttributeRows.length.toLocaleString()} total features,{" "}
                  showing {displayedAttributeRows.length.toLocaleString()} rows,{" "}
                  {selectedAttributeColumns.length.toLocaleString()} attributes
                </p>
              </div>
            </div>
            <button
              type="button"
              title="Hide attributes"
              onClick={() => setAttributeDatasetId(null)}
              className="flex h-8 w-8 shrink-0 items-center justify-center rounded border border-neutral-200 bg-white text-neutral-600 hover:bg-neutral-50 hover:text-neutral-900"
            >
              <X className="h-4 w-4" />
            </button>
          </div>

          {selectedAttributeColumns.length === 0 ? (
            <div className="flex h-[calc(100%-44px)] items-center justify-center text-xs text-neutral-500">
              This layer has no attribute fields.
            </div>
          ) : (
            <div className="h-[calc(100%-44px)] overflow-auto">
              <table className="min-w-full border-separate border-spacing-0 text-left text-xs">
                <thead className="sticky top-0 z-10 bg-neutral-100 text-[10px] uppercase text-neutral-500">
                  <tr>
                    <th className="sticky left-0 z-20 border-b border-r border-neutral-200 bg-neutral-100 px-2 py-1.5 font-bold">
                      #
                    </th>
                    {selectedAttributeColumns.map((column) => (
                      <th
                        key={column}
                        className="max-w-[220px] border-b border-r border-neutral-200 px-2 py-1.5 font-bold"
                        title={column}
                      >
                        <span className="block truncate">{column}</span>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="bg-white text-neutral-700">
                  {displayedAttributeRows.map((row, rowIndex) => (
                    <tr key={rowIndex} className="hover:bg-sky-50/50">
                      <td className="sticky left-0 border-b border-r border-neutral-100 bg-white px-2 py-1 font-mono text-[10px] text-neutral-400">
                        {rowIndex + 1}
                      </td>
                      {selectedAttributeColumns.map((column) => (
                        <td
                          key={column}
                          className="max-w-[220px] border-b border-r border-neutral-100 px-2 py-1 align-top"
                          title={formatAttributeValue(row[column])}
                        >
                          <span className="block max-w-[220px] truncate">{formatAttributeValue(row[column])}</span>
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {loading && (
        <div className="absolute inset-0 z-[600] flex items-center justify-center bg-white/50">
          <span className="text-sm font-semibold text-neutral-500">Loading map...</span>
        </div>
      )}

      {(error || mapError) && (
        <div className="absolute inset-0 z-[600] flex flex-col items-center justify-center bg-white/70 p-6 text-center">
          <Activity className="mb-2 h-8 w-8 text-rose-500" />
          <p className="text-sm font-semibold text-rose-600">
            {mapError ? "Failed to load Leaflet map" : "Failed to load spatial preview"}
          </p>
          <p className="mt-1 max-w-lg text-xs text-rose-500">{mapError || error}</p>
        </div>
      )}
    </div>
  );
};
