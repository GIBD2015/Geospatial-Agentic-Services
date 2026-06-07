import React, { useEffect, useState } from "react";
import { Activity, ExternalLink, FileCode, RefreshCw } from "lucide-react";

const getApiUrl = (path: string) => {
  const pathname = window.location.pathname;
  if (pathname.startsWith("/canvas")) {
    return `/canvas${path}`;
  }
  return path;
};

const addBaseTag = (html: string, artifactUrl: string) => {
  const baseTag = `<base href="${artifactUrl}">`;
  if (/<base\s/i.test(html)) return html;
  if (/<head[^>]*>/i.test(html)) {
    return html.replace(/<head([^>]*)>/i, `<head$1>${baseTag}`);
  }
  return `${baseTag}${html}`;
};

interface HtmlViewProps {
  artifact: {
    url: string;
    title: string;
  } | null;
  isVisible?: boolean;
}

export const HtmlView: React.FC<HtmlViewProps> = ({ artifact, isVisible = true }) => {
  const [html, setHtml] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;

    const loadHtml = async () => {
      if (!artifact) {
        setHtml("");
        setError(null);
        setLoading(false);
        return;
      }

      setLoading(true);
      setError(null);

      try {
        const response = await fetch(getApiUrl(`/api/fetch-artifact?url=${encodeURIComponent(artifact.url)}`));
        if (!response.ok) {
          const detail = await response.text().catch(() => "");
          throw new Error(detail || `Failed to fetch HTML artifact: HTTP ${response.status}`);
        }

        const text = await response.text();
        if (!cancelled) {
          setHtml(addBaseTag(text, artifact.url));
          setLoading(false);
        }
      } catch (err: any) {
        if (!cancelled) {
          setError(err.message || "Failed to load HTML artifact.");
          setLoading(false);
        }
      }
    };

    loadHtml();

    return () => {
      cancelled = true;
    };
  }, [artifact, reloadKey]);

  return (
    <div className="relative h-full min-h-[360px] bg-white">
      <div className="absolute right-4 top-4 z-20 flex items-center gap-2">
        {artifact && (
          <a
            href={artifact.url}
            target="_blank"
            rel="referrer"
            title="Open HTML artifact"
            className="flex h-9 w-9 items-center justify-center rounded-lg border border-neutral-200 bg-white/95 text-neutral-600 shadow-sm backdrop-blur hover:bg-white hover:text-sky-700"
          >
            <ExternalLink className="h-4 w-4" />
          </a>
        )}
        <button
          type="button"
          title="Reload HTML artifact"
          onClick={() => setReloadKey((key) => key + 1)}
          className="flex h-9 w-9 items-center justify-center rounded-lg border border-neutral-200 bg-white/95 text-neutral-600 shadow-sm backdrop-blur hover:bg-white hover:text-sky-700"
        >
          <RefreshCw className="h-4 w-4" />
        </button>
      </div>

      {artifact && html && isVisible && (
        <iframe
          key={`${artifact.url}-${reloadKey}`}
          title={artifact.title}
          srcDoc={html}
          className="h-full w-full border-0 bg-white"
          sandbox="allow-same-origin allow-scripts allow-forms allow-popups allow-downloads"
        />
      )}

      {!artifact && (
        <div className="flex h-full items-center justify-center bg-neutral-50 p-8 text-center">
          <div className="max-w-sm space-y-2">
            <FileCode className="mx-auto h-10 w-10 text-neutral-300" />
            <h3 className="text-sm font-bold text-neutral-700">No HTML Artifact</h3>
            <p className="text-xs leading-relaxed text-neutral-500">
              Preview an HTML artifact from the inspector to display it here.
            </p>
          </div>
        </div>
      )}

      {loading && (
        <div className="absolute inset-0 z-10 flex items-center justify-center bg-white/70">
          <span className="text-sm font-semibold text-neutral-500">Loading HTML...</span>
        </div>
      )}

      {error && (
        <div className="absolute inset-0 z-10 flex flex-col items-center justify-center bg-white/80 p-6 text-center">
          <Activity className="mb-2 h-8 w-8 text-rose-500" />
          <p className="text-sm font-semibold text-rose-600">Failed to load HTML artifact</p>
          <p className="mt-1 max-w-lg text-xs text-rose-500">{error}</p>
        </div>
      )}
    </div>
  );
};
