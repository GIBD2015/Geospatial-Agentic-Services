import React, { useEffect, useState, useRef } from "react";
import { X, ExternalLink, RefreshCw, Layers, ShieldCheck, Map as MapIcon, Database, Activity, Table } from "lucide-react";
import mapboxgl from "mapbox-gl";
import "mapbox-gl/dist/mapbox-gl.css";
import Papa from "papaparse";

const mapboxToken = import.meta.env.VITE_MAPBOX_TOKEN || "";
mapboxgl.accessToken = mapboxToken;

const previewMapStyle = mapboxToken
  ? "mapbox://styles/mapbox/light-v11"
  : {
      version: 8 as const,
      sources: {
        osm: {
          type: "raster" as const,
          tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
          tileSize: 256,
          attribution: "OpenStreetMap"
        }
      },
      layers: [
        {
          id: "osm",
          type: "raster" as const,
          source: "osm"
        }
      ]
    };

const getApiUrl = (path: string) => {
  const pathname = window.location.pathname;
  if (pathname.startsWith("/canvas")) {
    return `/canvas${path}`;
  }
  return path;
};

interface LivePreviewModalProps {
  isOpen: boolean;
  onClose: () => void;
  url: string;
  title: string;
}

export const LivePreviewModal: React.FC<LivePreviewModalProps> = ({
  isOpen,
  onClose,
  url,
  title,
}) => {
  const [iframeKey, setIframeKey] = React.useState(0);
  const [content, setContent] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [previewType, setPreviewType] = useState<"iframe" | "image" | "map" | "table" | "text">("iframe");

  const mapContainerRef = useRef<HTMLDivElement>(null);
  const mapInstanceRef = useRef<mapboxgl.Map | null>(null);

  useEffect(() => {
    if (!isOpen) {
      setContent(null);
      setError(null);
      return;
    }

    const ext = title.split('.').pop()?.toLowerCase() || url.split('.').pop()?.toLowerCase() || '';
    
    const isImage = ['png', 'jpg', 'jpeg', 'gif', 'svg', 'webp'].includes(ext);
    const isHtml = ['html', 'htm'].includes(ext);
    const isCsv = ['csv'].includes(ext);
    const isJson = ['json'].includes(ext);
    const isGeo = ['geojson', 'gpkg'].includes(ext);

    if (isHtml) {
      setPreviewType("iframe");
      return;
    }

    if (isImage) {
      setPreviewType("image");
      return;
    }

    setLoading(true);
    setError(null);

    const fetchUrl = (isGeo && ext === 'gpkg') ? getApiUrl(`/api/parse-gpkg?url=${encodeURIComponent(url)}`) : url;

    fetch(fetchUrl)
      .then(async (res) => {
        if (!res.ok) {
          const errText = await res.text().catch(() => "Unknown error");
          let parsedError = "";
          try {
            const parsed = JSON.parse(errText);
            parsedError = parsed.details || parsed.error || "";
          } catch {
            parsedError = "";
          }
          throw new Error(parsedError || `Failed to fetch file: ${errText}`);
        }
        return await res.text();
      })
      .then((data) => {
        if (isCsv && typeof data === 'string') {
          setPreviewType("table");
          Papa.parse(data, {
            header: true,
            skipEmptyLines: true,
            complete: (results) => {
              setContent({ headers: results.meta.fields, rows: results.data });
              setLoading(false);
            },
            error: (err: any) => {
              setError(err.message);
              setLoading(false);
            }
          });
        } else if (isGeo) {
          setPreviewType("map");
          if (typeof data === 'string') {
            try {
              const geojson = JSON.parse(data);
              setContent(geojson);
              setLoading(false);
            } catch (err: any) {
              setError("Failed to parse GeoJSON: " + err.message);
              setLoading(false);
            }
          }
        } else if (isJson && typeof data === 'string') {
           try {
             const parsed = JSON.parse(data);
             if (Array.isArray(parsed) && parsed.length > 0 && typeof parsed[0] === 'object') {
               setPreviewType("table");
               const hdrs = Array.from(new Set(parsed.flatMap(obj => Object.keys(obj))));
               setContent({ headers: hdrs, rows: parsed });
             } else {
               setPreviewType("text");
               setContent(JSON.stringify(parsed, null, 2));
             }
             setLoading(false);
           } catch(e: any) {
             setPreviewType("text");
             setContent(data);
             setLoading(false);
           }
        } else {
          setPreviewType("text");
          setContent(typeof data === 'string' ? data : "Binary data");
          setLoading(false);
        }
      })
      .catch((err) => {
        setError(err.message);
        setLoading(false);
      });

  }, [isOpen, url, title, iframeKey]);

  useEffect(() => {
    if (previewType === "map" && content && mapContainerRef.current) {
      if (mapInstanceRef.current) {
        mapInstanceRef.current.remove();
      }

      const map = new mapboxgl.Map({
        container: mapContainerRef.current,
        style: previewMapStyle,
        center: [0, 0],
        zoom: 1
      });

      mapInstanceRef.current = map;

      map.on('load', () => {
        map.addSource('preview-data', {
          type: 'geojson',
          data: content
        });

        map.addLayer({
          id: 'preview-polygon',
          type: 'fill',
          source: 'preview-data',
          paint: {
            'fill-color': '#088',
            'fill-opacity': 0.4
          },
          filter: ['==', '$type', 'Polygon']
        });

        map.addLayer({
          id: 'preview-line',
          type: 'line',
          source: 'preview-data',
          paint: {
            'line-color': '#088',
            'line-width': 2
          },
          filter: ['==', '$type', 'LineString']
        });

        map.addLayer({
          id: 'preview-point',
          type: 'circle',
          source: 'preview-data',
          paint: {
            'circle-radius': 6,
            'circle-color': '#f28cb1',
            'circle-stroke-width': 1,
            'circle-stroke-color': '#fff'
          },
          filter: ['==', '$type', 'Point']
        });

        try {
          const bounds = new mapboxgl.LngLatBounds();
          let hasFeatures = false;

          const addCoordToBound = (coord: any[]) => {
            if (coord.length >= 2 && typeof coord[0] === 'number') {
              bounds.extend(coord as [number, number]);
              hasFeatures = true;
            }
          };

          const processGeom = (geometry: any) => {
             if (!geometry) return;
             if (geometry.type === 'Point') {
               addCoordToBound(geometry.coordinates);
             } else if (geometry.type === 'LineString' || geometry.type === 'MultiPoint') {
               geometry.coordinates.forEach(addCoordToBound);
             } else if (geometry.type === 'Polygon' || geometry.type === 'MultiLineString') {
               geometry.coordinates.forEach((ring: any) => ring.forEach(addCoordToBound));
             } else if (geometry.type === 'MultiPolygon') {
               geometry.coordinates.forEach((poly: any) => poly.forEach((ring: any) => ring.forEach(addCoordToBound)));
             }
          };

          const data = content;
          if (data.type === 'FeatureCollection' && data.features) {
             data.features.forEach((f: any) => f.geometry && processGeom(f.geometry));
          } else if (data.type === 'Feature') {
             if (data.geometry) processGeom(data.geometry);
          } else {
             processGeom(data);
          }

          if (hasFeatures) {
             map.fitBounds(bounds, { padding: 40, maxZoom: 14 });
          }
        } catch(e) {
           console.warn("Could not calculate bounds", e);
        }
      });

      return () => {
        map.remove();
        mapInstanceRef.current = null;
      };
    }
  }, [previewType, content]);

  if (!isOpen) return null;

  const handleRefresh = () => {
    setIframeKey((prev) => prev + 1);
  };

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center p-4 bg-neutral-900/85 backdrop-blur-xs select-none">
      <div 
        className="w-full max-w-6xl h-[85vh] bg-white dark:bg-neutral-900 rounded-2xl shadow-2xl border border-neutral-200 dark:border-neutral-800 flex flex-col overflow-hidden animate-fade-in"
      >
        {/* MODAL HEADER */}
        <div className="p-4 border-b border-neutral-200 dark:border-neutral-800 bg-neutral-50 dark:bg-neutral-950 flex items-center justify-between">
          <div className="flex items-center space-x-3 overflow-hidden">
            <div className="p-2 rounded-lg bg-sky-100 dark:bg-sky-950 text-sky-600 dark:text-sky-400">
              <Layers className="w-5 h-5" />
            </div>
            <div className="overflow-hidden">
              <h3 className="text-sm font-bold text-neutral-800 dark:text-neutral-100 truncate">
                {title}
              </h3>
              <p className="text-[10px] text-neutral-500 font-mono truncate max-w-[500px]">
                Endpoint: {url}
              </p>
            </div>
          </div>

          <div className="flex items-center space-x-2 shrink-0">
            {/* Quick security warning disclaimer */}
            <div className="hidden md:flex items-center space-x-1 text-[10px] text-emerald-600 dark:text-emerald-400 bg-emerald-50 dark:bg-emerald-950/40 border border-emerald-100 dark:border-emerald-900 px-2 py-1 rounded-md mr-4">
              <ShieldCheck className="w-3.5 h-3.5" />
              <span>Secure Canvas Sandboxing Active</span>
            </div>

            <button
              onClick={handleRefresh}
              title="Reload preview draft"
              className="p-1.5 hover:bg-neutral-150 rounded dark:hover:bg-neutral-800 text-neutral-600 dark:text-neutral-400"
            >
              <RefreshCw className="w-4 h-4" />
            </button>

            <a
              href={url}
              target="_blank"
              rel="referrer"
              download
              className="p-1.5 hover:bg-neutral-150 rounded dark:hover:bg-neutral-800 text-neutral-605 dark:text-neutral-300 flex items-center space-x-1 text-xs font-semibold"
            >
              <ExternalLink className="w-4 h-4" />
              <span className="hidden sm:inline">Download</span>
            </a>

            <button
              onClick={onClose}
              className="p-1.5 hover:bg-rose-50 hover:text-rose-600 rounded bg-neutral-100 dark:bg-neutral-800 text-neutral-500 transition-colors"
            >
              <X className="w-4.5 h-4.5" />
            </button>
          </div>
        </div>

        {/* MODAL BODY */}
        <div className="flex-1 bg-neutral-50 dark:bg-neutral-950 relative overflow-hidden flex flex-col">
          {loading && (
            <div className="absolute inset-0 flex items-center justify-center bg-white/50 dark:bg-neutral-900/50 z-10">
              <span className="text-sm font-semibold text-neutral-500 animate-pulse">Loading preview...</span>
            </div>
          )}
          {error && (
            <div className="absolute inset-0 flex items-center justify-center flex-col p-6 text-center z-10">
              <Activity className="w-8 h-8 text-rose-500 mb-2" />
              <p className="text-sm font-semibold text-rose-600">Failed to load preview</p>
              <p className="text-xs text-rose-500 max-w-lg mt-1">{error}</p>
            </div>
          )}
          
          {!loading && !error && previewType === "iframe" && (
            <iframe
              key={iframeKey}
              src={url}
              title={title}
              referrerPolicy="no-referrer"
              sandbox="allow-scripts allow-same-origin allow-popups"
              className="w-full h-full border-none"
            />
          )}

          {!loading && !error && previewType === "image" && (
            <div className="w-full h-full flex items-center justify-center p-4 bg-neutral-100 dark:bg-neutral-900">
              <img src={url} alt={title} className="max-w-full max-h-full object-contain rounded drop-shadow-md" />
            </div>
          )}

          {!loading && !error && previewType === "text" && (
            <div className="w-full h-full p-6 overflow-auto">
               <pre className="text-xs font-mono text-neutral-800 dark:text-neutral-200 whitespace-pre-wrap">{content as string}</pre>
            </div>
          )}

          {!loading && !error && previewType === "map" && (
             <div ref={mapContainerRef} className="w-full h-full relative border-none" />
          )}

          {!loading && !error && previewType === "table" && content?.rows && (
             <div className="w-full h-full overflow-auto bg-white dark:bg-neutral-900">
                <table className="w-full text-left text-xs text-neutral-700 dark:text-neutral-300">
                   <thead className="bg-neutral-50 dark:bg-neutral-950 sticky top-0 shadow-sm">
                      <tr>
                         {(content.headers || []).map((h: string, i: number) => (
                           <th key={i} className="px-3 py-2 font-semibold border-b border-neutral-200 dark:border-neutral-800 whitespace-nowrap">{h}</th>
                         ))}
                      </tr>
                   </thead>
                   <tbody>
                      {content.rows.map((row: any, ri: number) => (
                         <tr key={ri} className="border-b border-neutral-100 dark:border-neutral-800/60 hover:bg-neutral-50 dark:hover:bg-neutral-800/40">
                            {(content.headers || []).map((h: string, ci: number) => {
                               const val = row[h];
                               const displayVal = typeof val === 'object' && val !== null ? JSON.stringify(val) : String(val ?? '');
                               return (
                                 <td key={ci} className="px-3 py-1.5 whitespace-nowrap max-w-xs truncate" title={displayVal}>{displayVal}</td>
                               );
                            })}
                         </tr>
                      ))}
                   </tbody>
                </table>
             </div>
          )}
        </div>
      </div>
    </div>
  );
};

