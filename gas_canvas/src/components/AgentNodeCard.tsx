import React, { useState } from "react";
import { motion } from "motion/react";
import { 
  Play, 
  Trash2, 
  CheckCircle, 
  AlertCircle, 
  Loader2, 
  Map, 
  Database, 
  Compass, 
  Activity, 
  Sliders, 
  FileText,
  Workflow,
  ChevronDown,
  ChevronRight,
  X
} from "lucide-react";
import { AgentNode, TaskArtifact } from "../types";

export interface AgentNodeCardProps {
  node: AgentNode;
  isSelected: boolean;
  inputDatasetsList: { name: string, url: string, type: string, sourceId?: string }[];
  onRemoveInput?: (dataset: { url: string, name: string, type: any, sourceId?: string }) => void;
  onSelect: () => void;
  onDelete: () => void;
  onExecute: () => void;
  onPointerDown: (e: React.PointerEvent) => void;
  onNodePointerUp?: (nodeId: string) => void;
  onPortPointerDown: (nodeId: string, type: "input" | "output", e: React.PointerEvent, artifactName?: string) => void;
  onPortPointerUp: (nodeId: string, type: "input" | "output") => void;
  isConnectingSource: boolean;
  isConnectingTarget: boolean;
  isDrafting: boolean;
  draftType?: "input" | "output" | null;
}

// Map agent types to aesthetic styles and icons for a high-craft feel
export const getAgentAesthetics = (agentId: string) => {
  const id = agentId.toLowerCase();
  
  if (id.includes("retrieval") || id.includes("pasda") || id.includes("earthquake")) {
    return {
      bg: "bg-emerald-50/90 hover:bg-emerald-50/100 dark:bg-emerald-950/20",
      border: "border-emerald-200 dark:border-emerald-900/50",
      selectedBorder: "border-emerald-500 ring-2 ring-emerald-500/20",
      iconColor: "text-emerald-600 dark:text-emerald-400",
      badgeBg: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-300",
      icon: Database,
      category: "Data Source"
    };
  }
  
  if (id.includes("analysis") || id.includes("raster") || id.includes("statistics") || id.includes("vector")) {
    return {
      bg: "bg-sky-50/90 hover:bg-sky-50/100 dark:bg-sky-950/20",
      border: "border-sky-200 dark:border-sky-900/50",
      selectedBorder: "border-sky-500 ring-2 ring-sky-500/20",
      iconColor: "text-sky-600 dark:text-sky-400",
      badgeBg: "bg-sky-100 text-sky-800 dark:bg-sky-900/30 dark:text-sky-300",
      icon: Activity,
      category: "Analysis"
    };
  }

  if (id.includes("mapping") || id.includes("projection") || id.includes("engine")) {
    return {
      bg: "bg-purple-50/90 hover:bg-purple-50/100 dark:bg-purple-950/20",
      border: "border-purple-200 dark:border-purple-900/50",
      selectedBorder: "border-purple-500 ring-2 ring-purple-500/20",
      iconColor: "text-purple-600 dark:text-purple-400",
      badgeBg: "bg-purple-100 text-purple-800 dark:bg-purple-900/30 dark:text-purple-300",
      icon: Map,
      category: "Visualization"
    };
  }

  return {
    bg: "bg-amber-50/90 hover:bg-amber-100/100 dark:bg-amber-950/20",
    border: "border-amber-200 dark:border-amber-900/50",
    selectedBorder: "border-amber-500 ring-2 ring-amber-500/20",
    iconColor: "text-amber-600 dark:text-amber-400",
    badgeBg: "bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-300",
    icon: Compass,
    category: "Workflow"
  };
};

export const AgentNodeCard: React.FC<AgentNodeCardProps> = ({
  node,
  isSelected,
  inputDatasetsList,
  onRemoveInput,
  onSelect,
  onDelete,
  onExecute,
  onPointerDown,
  onNodePointerUp,
  onPortPointerDown,
  onPortPointerUp,
  isConnectingSource,
  isConnectingTarget,
  isDrafting,
  draftType,
}) => {
  const { bg, border, selectedBorder, iconColor, badgeBg, icon: Icon, category } = getAgentAesthetics(node.agentId);
  const [showInputs, setShowInputs] = useState(false);
  const [showOutputs, setShowOutputs] = useState(false);

  // Status visual configurations
  const statusConfig = {
    idle: {
      color: "text-gray-400",
      bg: "bg-gray-100 dark:bg-gray-800",
      label: "Ready",
      indicator: "bg-gray-300 dark:bg-gray-700"
    },
    running: {
      color: "text-blue-500",
      bg: "bg-blue-50 dark:bg-blue-950/30",
      label: "Streaming",
      indicator: "bg-blue-500 animate-pulse"
    },
    completed: {
      color: "text-emerald-500",
      bg: "bg-emerald-50 dark:bg-emerald-950/30",
      label: "Success",
      indicator: "bg-emerald-500 animate-ping"
    },
    error: {
      color: "text-rose-500",
      bg: "bg-rose-50 dark:bg-rose-950/30",
      label: "Failed",
      indicator: "bg-rose-500"
    }
  }[node.status];

  return (
    <div
      id={`node-${node.id}`}
      style={{
        position: "absolute",
        left: `${node.x}px`,
        top: `${node.y}px`,
        width: "280px",
        zIndex: isSelected ? 30 : 10,
      }}
      className={`rounded-xl border shadow-sm transition-shadow duration-150 relative select-none ${bg} ${border} ${
        isSelected ? selectedBorder : "hover:shadow-md"
      }`}
      onPointerDown={(e) => {
        // Prevent trigger selection on clicking buttons or interactive fields
        const target = e.target as HTMLElement;
        if (target.closest(".no-drag")) return;
        onSelect();
        onPointerDown(e);
      }}
      onPointerUp={(e) => {
        if (onNodePointerUp) {
          // Allow bubbling so canvas can unset dragging
          onNodePointerUp(node.id);
        }
      }}
    >
      {/* Sockets/Ports */}
      {/* Input Socket (Left Pin) */}
      <div
        className={`absolute -left-4 top-1/2 -translate-y-1/2 z-40 no-drag cursor-crosshair group transition-opacity duration-200 ${
          isSelected || isConnectingTarget || (isDrafting && draftType === "output") ? "opacity-100 pointer-events-auto" : "opacity-0 pointer-events-none"
        }`}
        onPointerDown={(e) => {
          e.stopPropagation();
          onPortPointerDown(node.id, "input", e);
        }}
        onPointerUp={(e) => {
          e.stopPropagation();
          onPortPointerUp(node.id, "input");
        }}
        title="Connect Output data URL here"
      >
        <div className={`w-6 h-6 flex items-center justify-center transition-all ${
          isConnectingTarget 
            ? "text-sky-500 scale-125 drop-shadow-[0_0_8px_rgba(14,165,233,0.8)]"
            : (isDrafting && draftType === "output") ? "text-sky-400 animate-pulse drop-shadow-[0_0_5px_rgba(14,165,233,0.5)] hover:text-sky-300" 
            : "text-neutral-400 dark:text-neutral-500 hover:text-sky-400"
        }`}>
          <svg width="24" height="24" viewBox="0 0 24 24">
            <path d="M8 5v14l11-7z" fill="currentColor" />
          </svg>
        </div>
        <span className="absolute left-6 top-1/2 -translate-y-1/2 ml-1 text-[10px] bg-neutral-900 text-white rounded px-1 group-hover:block hidden whitespace-nowrap">
          Input Dataset Link
        </span>
      </div>

      {/* Output Socket (Right Pin) */}
      <div
        className={`absolute -right-4 top-1/2 -translate-y-1/2 z-40 no-drag cursor-crosshair group transition-opacity duration-200 ${
          isSelected || isConnectingSource || (isDrafting && draftType === "input") ? "opacity-100 pointer-events-auto" : "opacity-0 pointer-events-none"
        }`}
        onPointerDown={(e) => {
          e.stopPropagation();
          onPortPointerDown(node.id, "output", e);
        }}
        onPointerUp={(e) => {
          e.stopPropagation();
          onPortPointerUp(node.id, "output");
        }}
        title="Link generated artifacts as input to another agent"
      >
        <div className={`w-6 h-6 flex items-center justify-center transition-all ${
          isConnectingSource 
            ? "text-sky-500 scale-125 drop-shadow-[0_0_8px_rgba(14,165,233,0.8)]"
            : (isDrafting && draftType === "input") ? "text-sky-400 animate-pulse drop-shadow-[0_0_5px_rgba(14,165,233,0.5)] hover:text-sky-300"
            : "text-neutral-400 dark:text-neutral-500 hover:text-sky-400"
        }`}>
          <svg width="24" height="24" viewBox="0 0 24 24">
            <path d="M8 5v14l11-7z" fill="currentColor" />
          </svg>
        </div>
        <span className="absolute right-6 top-1/2 -translate-y-1/2 mr-1 text-[10px] bg-neutral-900 text-white rounded px-1 group-hover:block hidden whitespace-nowrap">
          Output Bindings
        </span>
      </div>

      {/* Card Header Drag-handle visual */}
      <div className="h-2 rounded-t-xl bg-neutral-200/50 dark:bg-neutral-800/30 flex items-center justify-center space-x-1 cursor-grab active:cursor-grabbing border-b border-neutral-100 dark:border-neutral-900/50">
        <div className="w-1.5 h-1.5 rounded-full bg-neutral-300" />
        <div className="w-1.5 h-1.5 rounded-full bg-neutral-300" />
        <div className="w-1.5 h-1.5 rounded-full bg-neutral-300" />
      </div>

      <div className="p-4">
        {/* Category & Status Indicator */}
        <div className="flex items-center justify-between mb-2">
          <span className={`text-[10px] uppercase font-bold px-2 py-0.5 rounded ${badgeBg}`}>
            {category}
          </span>
          
          <div className="flex items-center space-x-1.5">
            <span className={`w-2 h-2 rounded-full ${statusConfig.indicator}`} />
            <span className="text-[10px] font-medium text-neutral-500">{statusConfig.label}</span>
          </div>
        </div>

        {/* Title */}
        <div className="flex items-start space-x-2.5">
          <Icon className={`w-5 h-5 mt-0.5 shrink-0 ${iconColor}`} />
          <div className="overflow-hidden">
            <h4 className="text-sm font-semibold text-neutral-800 dark:text-neutral-200 truncate leading-tight">
              {node.name}
            </h4>
            <p className="text-[10px] text-neutral-500 font-mono truncate">
              ID: {node.agentId}
            </p>
          </div>
        </div>

        {/* Preview Instructions */}
        <div className="mt-3 bg-white/70 dark:bg-neutral-900/20 border border-neutral-100 dark:border-neutral-900/30 rounded-lg p-2 text-xs text-neutral-600 dark:text-neutral-400 min-h-[48px] line-clamp-3 leading-relaxed">
          {node.instructions || <span className="text-neutral-400 italic">No instructions defined yet. Click to configure.</span>}
        </div>

        {/* Inputs Dropdown */}
        {inputDatasetsList && inputDatasetsList.length > 0 && (
          <div className="mt-2.5">
            <button
              onClick={(e) => {
                e.stopPropagation();
                setShowInputs(!showInputs);
              }}
              className="flex items-center space-x-1 w-full text-left text-[11px] text-sky-600 dark:text-sky-400 font-medium hover:text-sky-700 dark:hover:text-sky-300 transition-colors"
            >
              <div className="w-4 flex justify-center">
                {showInputs ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
              </div>
              <Database className="w-3.5 h-3.5" />
              <span>INPUT DATASETS ({inputDatasetsList.length})</span>
            </button>
            {showInputs && (
              <div className="mt-1 ml-5 space-y-1">
                {inputDatasetsList.map((ds, idx) => (
                  <div key={idx} className="group text-[10px] text-neutral-600 dark:text-neutral-400 truncate pr-1 flex items-center space-x-1.5 no-drag bg-neutral-100 dark:bg-neutral-800/50 p-1 rounded relative">
                    <span className="truncate flex-1">{ds.name || "Unnamed Dataset"}</span>
                    {onRemoveInput && (
                      <button 
                        onClick={(e) => { e.stopPropagation(); onRemoveInput(ds); }}
                        className="opacity-0 group-hover:opacity-100 p-0.5 text-neutral-400 hover:text-red-500 transition-all rounded hover:bg-neutral-200 dark:hover:bg-neutral-700"
                      >
                        <X className="w-3 h-3" />
                      </button>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Outputs Dropdown */}
        {node.results?.outputs?.artifacts && node.results.outputs.artifacts.length > 0 && (
          <div className="mt-2.5">
            <button
              onClick={(e) => {
                e.stopPropagation();
                setShowOutputs(!showOutputs);
              }}
              className="flex items-center space-x-1 w-full text-left text-[11px] text-emerald-600 dark:text-emerald-400 font-medium hover:text-emerald-700 dark:hover:text-emerald-300 transition-colors"
            >
              <div className="w-4 flex justify-center">
                {showOutputs ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
              </div>
              <FileText className="w-3.5 h-3.5" />
              <span>OUTPUT DATASETS ({node.results.outputs.artifacts.length})</span>
            </button>
            {showOutputs && (
              <div className="mt-1 ml-5 space-y-1">
                {node.results.outputs.artifacts.map((art, idx) => (
                  <div 
                    key={idx} 
                    onPointerDown={(e) => {
                      e.stopPropagation();
                      // @ts-ignore (we passed the extra arg)
                      onPortPointerDown(node.id, "output", e, art.name || `Output ${idx + 1}`);
                    }}
                    className="group cursor-grab active:cursor-grabbing text-[10px] text-neutral-600 dark:text-neutral-400 truncate flex items-center space-x-1.5 no-drag bg-emerald-50 dark:bg-emerald-950/30 border border-emerald-100 dark:border-emerald-900/50 p-1 rounded hover:bg-emerald-100 dark:hover:bg-emerald-900/50 transition-colors"
                    title="Drag to another agent to pass this output as input"
                  >
                    <span className="truncate flex-1 pr-2">{art.name || `Output ${idx + 1}`}</span>
                    <Workflow className="w-3 h-3 text-emerald-500/50 group-hover:text-emerald-500" />
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Action Panel */}
        <div className="mt-4 pt-3 border-t border-neutral-100 dark:border-neutral-900/40 flex items-center justify-between no-drag">
          <button
            onClick={onExecute}
            disabled={node.status === "running"}
            className="flex items-center space-x-1 px-3 py-1.5 bg-neutral-900 hover:bg-neutral-800 text-white rounded-lg text-xs font-semibold shadow-sm transition-colors disabled:opacity-50"
          >
            {node.status === "running" ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <Play className="w-3.5 h-3.5 fill-current" />
            )}
            <span>{node.status === "running" ? "Streaming" : "Run Step"}</span>
          </button>

          <button
            onClick={onDelete}
            title="Delete agent node"
            className="p-1.5 hover:bg-rose-50 hover:text-rose-600 text-neutral-400 rounded-lg transition-colors border border-transparent hover:border-rose-100"
          >
            <Trash2 className="w-4 h-4" />
          </button>
        </div>
      </div>
    </div>
  );
};
