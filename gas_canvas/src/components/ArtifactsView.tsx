import React, { useEffect, useState } from "react";
import { Activity, Download, FileText, GripVertical, Image as ImageIcon, RefreshCw, Trash2 } from "lucide-react";
import Papa from "papaparse";
import { TaskArtifact } from "../types";
import { getArtifactFilename, getArtifactHoverText, getArtifactPreviewTitle, getArtifactSemanticName } from "../lib/artifacts";

const getApiUrl = (path: string) => {
  const pathname = window.location.pathname;
  if (pathname.startsWith("/canvas")) {
    return `/canvas${path}`;
  }
  return path;
};

const getArtifactExtension = (artifact: TaskArtifact | null) => {
  if (!artifact) return "";
  const format = artifact.format?.toLowerCase();
  if (format) return format;
  return getArtifactFilename(artifact, "").split(".").pop()?.toLowerCase() || artifact.url.split("?")[0].split(".").pop()?.toLowerCase() || "";
};

const formatCellValue = (value: any) => {
  if (value === null || value === undefined) return "";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
};

interface ArtifactsViewProps {
  artifacts: TaskArtifact[];
  selectedUrl: string;
  onSelectArtifact: (url: string) => void;
  onDeleteArtifact?: (url: string) => void;
}

export const ArtifactsView: React.FC<ArtifactsViewProps> = ({ artifacts, selectedUrl, onSelectArtifact, onDeleteArtifact }) => {
  const selectedArtifact = artifacts.find((artifact) => artifact.url === selectedUrl) || artifacts[0] || null;
  const [sidebarWidth, setSidebarWidth] = useState(224);
  const [content, setContent] = useState<any>(null);
  const [viewerType, setViewerType] = useState<"empty" | "image" | "table" | "text" | "download">("empty");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;

    const loadArtifact = async () => {
      if (!selectedArtifact) {
        setContent(null);
        setViewerType("empty");
        setError(null);
        setLoading(false);
        return;
      }

      const ext = getArtifactExtension(selectedArtifact);
      if (["png", "jpg", "jpeg", "gif", "svg", "webp"].includes(ext)) {
        setViewerType("image");
        setContent(null);
        setError(null);
        setLoading(false);
        return;
      }

      setLoading(true);
      setError(null);

      try {
        const response = await fetch(getApiUrl(`/api/fetch-artifact?url=${encodeURIComponent(selectedArtifact.url)}`));
        if (!response.ok) {
          const detail = await response.text().catch(() => "");
          throw new Error(detail || `Failed to fetch artifact: HTTP ${response.status}`);
        }

        const text = await response.text();
        if (cancelled) return;

        if (ext === "csv") {
          Papa.parse(text, {
            header: true,
            skipEmptyLines: true,
            complete: (results) => {
              if (cancelled) return;
              setViewerType("table");
              setContent({ headers: results.meta.fields || [], rows: results.data || [] });
              setLoading(false);
            },
            error: (err: any) => {
              if (cancelled) return;
              setError(err.message);
              setLoading(false);
            }
          });
          return;
        }

        if (ext === "json") {
          try {
            const parsed = JSON.parse(text);
            if (Array.isArray(parsed) && parsed.every((row) => row && typeof row === "object")) {
              const headers = Array.from(new Set<string>(parsed.flatMap((row) => Object.keys(row))));
              setViewerType("table");
              setContent({ headers, rows: parsed });
            } else {
              setViewerType("text");
              setContent(JSON.stringify(parsed, null, 2));
            }
          } catch {
            setViewerType("text");
            setContent(text);
          }
          setLoading(false);
          return;
        }

        if (["txt", "text", "log", "md", "xml", "yaml", "yml"].includes(ext)) {
          setViewerType("text");
          setContent(text);
          setLoading(false);
          return;
        }

        setViewerType("download");
        setContent(text.slice(0, 4000));
        setLoading(false);
      } catch (err: any) {
        if (!cancelled) {
          setError(err.message || "Failed to load artifact.");
          setLoading(false);
        }
      }
    };

    loadArtifact();

    return () => {
      cancelled = true;
    };
  }, [selectedArtifact?.url, reloadKey]);

  const handleSidebarResize = (event: React.PointerEvent) => {
    event.preventDefault();
    const startX = event.clientX;
    const startWidth = sidebarWidth;

    const handlePointerMove = (moveEvent: PointerEvent) => {
      const nextWidth = startWidth + moveEvent.clientX - startX;
      setSidebarWidth(Math.max(180, Math.min(360, nextWidth)));
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

  if (!selectedArtifact) {
    return (
      <div className="flex h-full items-center justify-center bg-neutral-50 p-8 text-center">
        <div className="max-w-sm space-y-2">
          <FileText className="mx-auto h-10 w-10 text-neutral-300" />
          <h3 className="text-sm font-bold text-neutral-700">No Artifact Selected</h3>
          <p className="text-xs leading-relaxed text-neutral-500">
            View a CSV, JSON, TXT, image, or other artifact from the inspector to display it here.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-[360px] bg-white">
      <aside className="relative shrink-0 border-r border-neutral-200 bg-neutral-50" style={{ width: `${sidebarWidth}px` }}>
        <div className="border-b border-neutral-200 px-3 py-2">
          <h3 className="text-xs font-bold uppercase text-neutral-700">Artifacts</h3>
          <p className="mt-0.5 text-[10px] text-neutral-500">{artifacts.length} opened</p>
        </div>
        <div className="h-[calc(100%-45px)] overflow-y-auto p-2">
          {artifacts.map((artifact) => (
            <div
              key={artifact.url}
              title={getArtifactHoverText(artifact)}
              className={`mb-1.5 flex w-full items-start gap-1 rounded-md border p-1 ${
                artifact.url === selectedArtifact.url
                  ? "border-sky-200 bg-sky-50"
                  : "border-neutral-200 bg-white hover:bg-neutral-50"
              }`}
            >
              <button
                type="button"
                onClick={() => onSelectArtifact(artifact.url)}
                className="min-w-0 flex-1 px-1 py-1 text-left"
              >
                  <span className="block truncate text-xs font-bold text-neutral-800">
                    {getArtifactSemanticName(artifact, getArtifactFilename(artifact, "Artifact"))}
                  </span>
                  <span className="block truncate font-mono text-[10px] text-neutral-400">{artifact.url}</span>
              </button>
              {onDeleteArtifact && (
                <button
                  type="button"
                  title="Remove artifact from this list"
                  onClick={() => onDeleteArtifact(artifact.url)}
                  className="flex h-6 w-6 shrink-0 items-center justify-center rounded text-neutral-400 hover:bg-white hover:text-rose-600"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              )}
            </div>
          ))}
        </div>
        <div
          title="Drag to resize artifacts list"
          onPointerDown={handleSidebarResize}
          className="absolute bottom-0 right-[-4px] top-0 z-20 flex w-2 cursor-col-resize items-center justify-center hover:bg-sky-100/60"
        >
          <GripVertical className="h-4 w-4 text-neutral-300" />
        </div>
      </aside>

      <main className="relative min-w-0 flex-1 bg-white">
        <div className="flex h-12 items-center justify-between border-b border-neutral-200 bg-white px-4">
          <div className="min-w-0">
            <h3 className="truncate text-sm font-bold text-neutral-800" title={getArtifactHoverText(selectedArtifact)}>{getArtifactPreviewTitle(selectedArtifact, "Artifact")}</h3>
            <p className="truncate font-mono text-[10px] text-neutral-400">{selectedArtifact.url}</p>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <button
              type="button"
              title="Reload artifact"
              onClick={() => setReloadKey((key) => key + 1)}
              className="flex h-8 w-8 items-center justify-center rounded border border-neutral-200 text-neutral-600 hover:bg-neutral-50 hover:text-sky-700"
            >
              <RefreshCw className="h-4 w-4" />
            </button>
            <a
              href={selectedArtifact.url}
              download
              rel="referrer"
              className="flex h-8 items-center gap-1.5 rounded border border-neutral-200 px-2 text-xs font-semibold text-neutral-700 hover:bg-neutral-50"
            >
              <Download className="h-3.5 w-3.5" />
              <span>Download</span>
            </a>
          </div>
        </div>

        <div className="h-[calc(100%-48px)] overflow-hidden bg-neutral-50">
          {viewerType === "image" && (
            <div className="flex h-full items-center justify-center p-6">
              <img src={selectedArtifact.url} alt={getArtifactPreviewTitle(selectedArtifact, "Artifact")} className="max-h-full max-w-full object-contain shadow-sm" />
            </div>
          )}

          {viewerType === "table" && content?.rows && (
            <div className="h-full overflow-auto bg-white">
              <table className="min-w-full border-separate border-spacing-0 text-left text-xs">
                <thead className="sticky top-0 z-10 bg-neutral-100 text-[10px] uppercase text-neutral-500">
                  <tr>
                    {(content.headers || []).map((header: string) => (
                      <th key={header} className="max-w-[240px] border-b border-r border-neutral-200 px-3 py-2 font-bold">
                        <span className="block truncate">{header}</span>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {content.rows.map((row: any, rowIndex: number) => (
                    <tr key={rowIndex} className="hover:bg-sky-50/50">
                      {(content.headers || []).map((header: string) => {
                        const value = formatCellValue(row[header]);
                        return (
                          <td key={header} title={value} className="max-w-[240px] border-b border-r border-neutral-100 px-3 py-1.5">
                            <span className="block truncate">{value}</span>
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {viewerType === "text" && (
            <pre className="h-full overflow-auto p-5 font-mono text-xs leading-relaxed text-neutral-800 whitespace-pre-wrap">
              {content}
            </pre>
          )}

          {viewerType === "download" && (
            <div className="flex h-full items-center justify-center p-8 text-center">
              <div className="max-w-md space-y-3">
                <ImageIcon className="mx-auto h-10 w-10 text-neutral-300" />
                <h3 className="text-sm font-bold text-neutral-700">Preview Not Available</h3>
                <p className="text-xs leading-relaxed text-neutral-500">
                  This artifact type is available for download. A text snippet is shown when possible.
                </p>
                {content && (
                  <pre className="max-h-40 overflow-auto rounded border border-neutral-200 bg-white p-3 text-left font-mono text-[10px] text-neutral-600">
                    {content}
                  </pre>
                )}
              </div>
            </div>
          )}
        </div>

        {loading && (
          <div className="absolute inset-0 flex items-center justify-center bg-white/60">
            <span className="text-sm font-semibold text-neutral-500">Loading artifact...</span>
          </div>
        )}

        {error && (
          <div className="absolute inset-0 flex flex-col items-center justify-center bg-white/80 p-6 text-center">
            <Activity className="mb-2 h-8 w-8 text-rose-500" />
            <p className="text-sm font-semibold text-rose-600">Failed to load artifact</p>
            <p className="mt-1 max-w-lg text-xs text-rose-500">{error}</p>
          </div>
        )}
      </main>
    </div>
  );
};
