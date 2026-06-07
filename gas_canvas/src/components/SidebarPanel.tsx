import React, { useState } from "react";
import { 
  Database, 
  Map, 
  Compass, 
  Activity, 
  Search, 
  Plus, 
  Info,
  Server,
  ChevronDown,
  ChevronRight,
  Trash2
} from "lucide-react";
import { getAgentAesthetics } from "./AgentNodeCard";

type AgentCategory = "Data Access" | "Analysis" | "Visualization" | "Domain";

export interface GasServerData {
  url: string;
  providerName: string;
  provider?: any;
  agents: any[];
  isExpanded?: boolean;
}

interface SidebarPanelProps {
  onAddAgent: (agentId: string, name: string, serverUrl: string) => void;
  onDescribeAgent: (serverUrl: string, agentId: string) => void;
  servers: GasServerData[];
  onAddServer: (url: string) => void;
  onRemoveServer: (url: string) => void;
  onToggleServer: (url: string) => void;
  isSyncingServer: string | null;
  width: number;
  selectedAgentId?: string | null;
  selectedServerUrl?: string | null;
}

// Full specifications of the published agents fetched from GetCapabilities
export const AGENT_TEMPLATES = [
  {
    agent_id: "geospatial_data_retrieval_agent",
    name: "Geospatial Data Retrieval Agent",
    category: "Data Access",
    description: "Downloads vector boundaries, tabular datasets, or sensor values (e.g. US Census public boundaries, state resources).",
    keywords: ["Census Bureau", "boundary", "download", "JSON", "GeoJSON", "CSV"]
  },
  {
    agent_id: "pasda_agent",
    name: "PASDA Discovery Agent",
    category: "Data Access",
    description: "Penn State-hosted Pennsylvania Spatial Data Access (PASDA) directory tool to crawl, discover, and index GIS data files.",
    keywords: ["PASDA", "Pennsylvania", "PennDOT", "raster", "search", "datasets"]
  },
  {
    agent_id: "usgs_earthquake_agent",
    name: "USGS Earthquake Agent",
    category: "Domain",
    description: "Searches and extracts real-time seismological data directly from the USGS API based on bounding box and magnitude thresholds.",
    keywords: ["USGS", "earthquake", "seismology", "magnitude", "feed", "live data"]
  },
  {
    agent_id: "spatial_analysis_agent",
    name: "Spatial Analysis Agent",
    category: "Analysis",
    description: "Runs buffers, intersections, overlays, and bounding polygon processing on primary geospatial vector structures.",
    keywords: ["buffer", "intersection", "centroids", "dissolve", "geopandas"]
  },
  {
    agent_id: "spatial_statistics_agent",
    name: "Spatial Statistics Agent",
    category: "Analysis",
    description: "Computes spatial clustering coefficients, spatial autocorrelation (Moran's I), hot spots, and spatial distribution stats.",
    keywords: ["Moran's I", "LISA", "hotspot", "clustering", "autocorrelation", "stats"]
  },
  {
    agent_id: "raster_agent",
    name: "Raster Agent",
    category: "Analysis",
    description: "Performs pixel-level calculations (NDVI formulas, raster clip, cell algebra, DEM contours, slope processing).",
    keywords: ["NDVI", "rasterio", "elevation", "dem", "tiff", "satellite"]
  },
  {
    agent_id: "vector_analysis_agent",
    name: "Vector Analysis Agent",
    category: "Analysis",
    description: "Performs advanced spatial topology analysis, point-in-polygon queries, and spatial joins of complex feature classes.",
    keywords: ["spatial join", "overlay", "shapely", "dissolve", "polygon", "point"]
  },
  {
    agent_id: "exploratory_spatial_data_analysis_agent",
    name: "ESDA Agent",
    category: "Analysis",
    description: "Explores shapefiles and CSV catalogs, generating statistical descriptions and distribution plots.",
    keywords: ["histogram", "scatter", "distribution", "analysis", "pandas", "esda"]
  },
  {
    agent_id: "mapping_agent",
    name: "Mapping Agent",
    category: "Visualization",
    description: "Generates static map layouts, thematic choropleths, graduated symbols, and maps ready for digital display.",
    keywords: ["matplotlib", "geopandas", "choropleth", "static map", "legend", "pdf"]
  },
  {
    agent_id: "web_mapping_app_agent",
    name: "Web Mapping App Agent",
    category: "Visualization",
    description: "Generates fully dynamic, responsive HTML and JavaScript maps using Folium, Leaflet, layer toggle, and sidebars.",
    keywords: ["Leaflet", "interactive map", "Folium", "HTML", "layer control", "legend"]
  },
  {
    agent_id: "google_earth_engine_agent",
    name: "Google Earth Engine Agent",
    category: "Domain",
    description: "Queries high-volume Earth Engine catalog assets (Sentinel, Landsat) to synthesize multi-temporal remote sensing maps.",
    keywords: ["GEE", "ee", "landsat", "sentinel", "modis", "imagery", "satellite"]
  },
  {
    agent_id: "map_projection_agent",
    name: "Map Projection Agent",
    category: "Analysis",
    description: "Configures or transforms map features between spatial reference systems (e.g., reprojecting from EPSG:4326 to State Plane).",
    keywords: ["EPSG", "reproject", "CRS", "pyproj", "coordinate transformation"]
  },
  {
    agent_id: "geospatial_workflow_planning_agent",
    name: "Geospatial Workflow Agent",
    category: "Analysis",
    description: "Generates comprehensive blueprint diagrams and step-by-step instructions to chain and coordinate complex geospatial tasks.",
    keywords: ["planning", "workflow", "orchestration", "DAG", "steps"]
  },
  {
    agent_id: "geospatial_data_inspection_agent",
    name: "Data Inspection Agent",
    category: "Analysis",
    description: "Inspects shapefile schemas, projection codes, column values, and structural bounds before executing analytics.",
    keywords: ["inspect", "schema", "column types", "null values", "bounding box"]
  },
  {
    agent_id: "spatiotemporal_conflict_event_agent",
    name: "Conflict Event Agent",
    category: "Domain",
    description: "Tracks spatiotemporal conflicts or safety occurrences, filtering dataset records across spatial regions and timeline ranges.",
    keywords: ["event layer", "spatiotemporal", "ACLED", "conflict", "timeline"]
  }
];

export const SidebarPanel: React.FC<SidebarPanelProps> = ({
  onAddAgent,
  onDescribeAgent,
  servers,
  onAddServer,
  onRemoveServer,
  onToggleServer,
  isSyncingServer,
  width,
  selectedAgentId,
  selectedServerUrl,
}) => {
  const [search, setSearch] = useState("");
  const [activeTab, setActiveTab] = useState<"all" | AgentCategory>("all");
  const [isUrlEditing, setIsUrlEditing] = useState(false);
  const [newServerUrl, setNewServerUrl] = useState("https://www.geospatial-agentic-services.online/");

  return (
    <div style={{ width: `${width}px` }} className="border-r border-neutral-200 dark:border-neutral-800 bg-neutral-50/50 dark:bg-neutral-950 flex flex-col h-full shrink-0">
      {/* SERVER INTEGRATION SETTINGS */}
      <div className="px-3 py-2 border-b border-neutral-200 dark:border-neutral-800 bg-white dark:bg-neutral-900">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center space-x-1.5">
            <Server className="w-4 h-4 text-neutral-600 dark:text-neutral-400" />
            <span className="text-sm font-semibold text-neutral-700 dark:text-neutral-300">GAS Servers</span>
          </div>
          <button 
            onClick={() => setIsUrlEditing(!isUrlEditing)}
            className="text-xs border border-neutral-200 bg-white hover:bg-neutral-50 text-neutral-700 dark:border-neutral-800 dark:bg-neutral-900 dark:hover:bg-neutral-950 dark:text-neutral-300 font-semibold px-3 py-1.5 rounded-lg transition-colors"
          >
            {isUrlEditing ? "Cancel" : "Add Server"}
          </button>
        </div>

        {isUrlEditing && (
          <div className="space-y-1.5 mt-2">
            <input
              type="text"
              value={newServerUrl}
              onChange={(e) => setNewServerUrl(e.target.value)}
              placeholder="Server URL"
              className="w-full text-xs border border-neutral-300 dark:border-neutral-700 rounded-md p-1.5 font-mono focus:ring-1 focus:ring-sky-500 bg-white dark:bg-neutral-950 text-neutral-800 dark:text-neutral-100 focus:outline-none"
            />
            <div className="flex justify-end space-x-1">
              <button 
                onClick={() => {
                  const trimmedUrl = newServerUrl.trim();
                  if (!trimmedUrl) return;
                  if (servers.some(s => s.url === trimmedUrl)) {
                    setIsUrlEditing(false);
                    return;
                  }
                  onAddServer(trimmedUrl);
                  setIsUrlEditing(false);
                }}
                className="text-[10px] bg-sky-600 text-white px-2 py-1 rounded hover:bg-sky-500 font-bold flex items-center"
              >
                {isSyncingServer === newServerUrl ? "Connecting..." : "Add"}
              </button>
            </div>
          </div>
        )}
      </div>

      {/* FILTER & SEARCH OVERLAY */}
      <div className="p-3 border-b border-neutral-200/80 dark:border-neutral-800/80 space-y-2">
        {/* Search input */}
        <div className="relative">
          <Search className="absolute left-2.5 top-2.5 w-4 h-4 text-neutral-400" />
          <input
            type="text"
            placeholder="Search agents..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full pl-9 pr-3 py-2 text-xs border border-neutral-300/80 dark:border-neutral-700 rounded-lg focus:outline-none focus:ring-1 focus:ring-sky-500 bg-white dark:bg-neutral-900 text-neutral-800 dark:text-neutral-100 placeholder-neutral-400"
          />
        </div>

        {/* Tab categories */}
        <div className="flex flex-wrap items-center gap-1">
          {[
            { id: "all", label: "All" },
            { id: "Data Access", label: "Data" },
            { id: "Analysis", label: "Analyze" },
            { id: "Visualization", label: "Visualize" },
            { id: "Domain", label: "Domain" }
          ].map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id as any)}
              className={`px-2 py-1 rounded text-[11px] font-medium transition-colors shrink-0 ${
                activeTab === tab.id
                  ? "bg-sky-600 text-white"
                  : "bg-white border border-neutral-200 text-neutral-600 hover:bg-neutral-100 dark:bg-neutral-900 dark:border-neutral-800 dark:text-neutral-400"
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>
      </div>

      {/* AGENT ITEMS LIST */}
      <div className="flex-1 overflow-y-auto p-3 space-y-3 select-none">
        {servers.length === 0 ? (
          <div className="text-center py-8 px-4">
            <Database className="w-8 h-8 mx-auto text-neutral-300 dark:text-neutral-600 mb-2" />
            <p className="text-xs text-neutral-500 font-semibold mb-1">No servers connected.</p>
            <p className="text-[10px] text-neutral-400 mt-1">Add a GAS server above to fetch available mapping agents.</p>
          </div>
        ) : (
          servers.map(server => {
            const filteredAgents = server.agents.filter((tpl) => {
              const matchesSearch = 
                tpl.name.toLowerCase().includes(search.toLowerCase()) || 
                tpl.description.toLowerCase().includes(search.toLowerCase());
                
              const matchesTab = activeTab === "all" || tpl.category === activeTab;
              
              return matchesSearch && matchesTab;
            });

            return (
              <div key={server.url} className="bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 rounded-lg overflow-hidden">
                <div 
                  className="px-3 py-2.5 flex items-center justify-between bg-neutral-100 dark:bg-neutral-900 cursor-pointer border-b border-neutral-200/70 dark:border-neutral-800"
                  onClick={() => onToggleServer(server.url)}
                >
                  <div className="flex items-center space-x-2 min-w-0">
                    {server.isExpanded ? <ChevronDown className="w-3.5 h-3.5 text-neutral-400" /> : <ChevronRight className="w-3.5 h-3.5 text-neutral-400" />}
                    <Server className="w-4 h-4 text-sky-600 shrink-0" />
                    <span
                      className="text-sm font-semibold text-neutral-800 dark:text-neutral-200 truncate"
                      title={server.providerName}
                    >
                      {server.providerName}
                    </span>
                  </div>
                  <div className="flex items-center space-x-2">
                    <span className="text-[11px] text-neutral-600 bg-white dark:bg-neutral-800 dark:text-neutral-300 px-1.5 py-0.5 rounded border border-neutral-200 dark:border-neutral-700">{server.agents.length}</span>
                    <button 
                      onClick={(e) => {
                        e.stopPropagation();
                        onRemoveServer(server.url);
                      }}
                      className="text-neutral-400 hover:text-red-500 transition-colors"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </div>
                </div>

                {server.isExpanded && (
                  <div className="p-2 space-y-2">
                    {filteredAgents.length === 0 ? (
                      <p className="text-[10px] text-neutral-500 text-center py-2">No tools match your filter.</p>
                    ) : (
                      filteredAgents.map((tpl) => {
                        const { bg, border, iconColor, icon: Icon } = getAgentAesthetics(tpl.agent_id);
                        const isSelected = selectedAgentId === tpl.agent_id && selectedServerUrl === server.url;
                        return (
                          <div
                            key={tpl.agent_id}
                            onClick={() => onDescribeAgent(server.url, tpl.agent_id)}
                            onDoubleClick={(e) => {
                              e.stopPropagation();
                              onAddAgent(tpl.agent_id, tpl.name, server.url);
                            }}
                            className={`p-2.5 rounded-lg border bg-white dark:bg-neutral-900/60 cursor-pointer shadow-none transition-all relative overflow-hidden group ${
                              isSelected 
                                ? "border-sky-500 ring-1 ring-sky-500/50 shadow-sm" 
                                : "border-neutral-100 hover:border-sky-200 dark:border-neutral-800 dark:hover:border-neutral-700 hover:shadow-xs"
                            }`}
                          >
                            <div className="flex items-start justify-between gap-1">
                              <div className="flex items-start space-x-2 overflow-hidden flex-1">
                                <Icon className={`w-4 h-4 mt-0.5 shrink-0 ${iconColor}`} />
                                <div className="min-w-0">
                                  <h5 className="text-[13px] font-bold text-neutral-800 dark:text-neutral-200 leading-tight truncate" title={tpl.name}>
                                    {tpl.name}
                                  </h5>
                                </div>
                              </div>
                              
                              <div className="flex items-center space-x-1 shrink-0">
                                <button 
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    onDescribeAgent(server.url, tpl.agent_id);
                                  }}
                                  className="p-0.5 rounded opacity-0 group-hover:opacity-100 bg-neutral-100 dark:bg-neutral-800 hover:bg-neutral-900 hover:text-white dark:hover:bg-neutral-100 dark:hover:text-black transition-all cursor-pointer"
                                  title="Describe Agent"
                                >
                                  <Info className="w-3.5 h-3.5" />
                                </button>
                                <button 
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    onAddAgent(tpl.agent_id, tpl.name, server.url);
                                  }}
                                  className="p-0.5 rounded opacity-0 group-hover:opacity-100 bg-neutral-100 dark:bg-neutral-800 hover:bg-neutral-900 hover:text-white dark:hover:bg-neutral-100 dark:hover:text-black transition-all cursor-pointer"
                                  title="Add Agent to Canvas"
                                >
                                  <Plus className="w-3.5 h-3.5" />
                                </button>
                              </div>
                            </div>

                            <p className="text-[12px] text-neutral-600 dark:text-neutral-400 mt-2 leading-relaxed line-clamp-2">
                              {tpl.description}
                            </p>
                          </div>
                        );
                      })
                    )}
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>

      <div className="p-3 border-t border-neutral-200 dark:border-neutral-800 bg-neutral-100/50 dark:bg-neutral-900/40 text-center text-xs text-neutral-600 font-mono">
        Copyright 2026 &copy;{" "}
        <a
          href="https://giscience.psu.edu/"
          target="_blank"
          rel="noreferrer"
          className="font-semibold text-neutral-700 underline-offset-2 hover:text-sky-700 hover:underline"
        >
          GIBD
        </a>
      </div>
    </div>
  );
};
