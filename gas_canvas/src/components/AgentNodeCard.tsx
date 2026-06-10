import React, { useEffect, useRef, useState } from "react";
import { motion } from "motion/react";
import { 
  Play, 
  Trash2, 
  CheckCircle, 
  AlertCircle, 
  Map, 
  Database, 
  Compass, 
  Activity, 
  Sliders, 
  FileText,
  Workflow,
  ChevronDown,
  ChevronRight,
  Download,
  Eye,
  X
} from "lucide-react";
import { AgentNode, TaskArtifact } from "../types";
import { getArtifactFilename, getArtifactHoverText, getArtifactSelectionKey, getArtifactSemanticName } from "../lib/artifacts";

const AnalysisLayersIcon: React.FC<React.SVGProps<SVGSVGElement>> = ({ className, ...props }) => (
  <svg
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2.2"
    strokeLinecap="round"
    strokeLinejoin="round"
    className={className}
    {...props}
  >
    <path d="M12 3.5 21 8 12 12.5 3 8 12 3.5Z" />
    <path d="m3 13 9 4.5 9-4.5" />
    <path d="m3 17.5 9 4.5 9-4.5" />
  </svg>
);

export interface AgentNodeCardProps {
  node: AgentNode;
  isSelected: boolean;
  inputDatasetsList: { name: string, url: string, type: string, sourceId?: string, description?: string }[];
  onRemoveInput?: (dataset: { url: string, name: string, type: any, sourceId?: string, description?: string }) => void;
  onSelect: () => void;
  onDelete: () => void;
  onExecute: () => void;
  onCancel: () => void;
  onUpdateNode: (nodeId: string, updates: Partial<AgentNode>) => void;
  onOpenArtifact?: (artifact: TaskArtifact) => void;
  outputsExpanded?: boolean;
  onOutputsExpandedChange?: (nodeId: string, expanded: boolean) => void;
  renameRequestId?: number;
  editInstructionsRequestId?: number;
  onPointerDown: (e: React.PointerEvent) => void;
  onNodePointerUp?: (nodeId: string) => void;
  onContextMenu?: (nodeId: string, e: React.MouseEvent) => void;
  onPortPointerDown: (nodeId: string, type: "input" | "output", e: React.PointerEvent, artifactName?: string) => void;
  onPortPointerUp: (nodeId: string, type: "input" | "output") => void;
  isConnectingSource: boolean;
  isConnectingTarget: boolean;
  isDrafting: boolean;
  draftType?: "input" | "output" | null;
}

// Map agent types to aesthetic styles and icons for a high-craft feel
export const getAgentCategory = (agentId: string) => {
  const id = agentId.toLowerCase();

  if (id.includes("earthquake") || id.includes("conflict") || id.includes("event") || id.includes("earth_engine")) return "Domain";
  if (id.includes("retrieval") || id.includes("pasda")) return "Data Access";
  if (id.includes("mapping") || id.includes("engine")) return "Visualization";
  if (id.includes("analysis") || id.includes("raster") || id.includes("statistics") || id.includes("vector") || id.includes("projection") || id.includes("inspection") || id.includes("planning") || id.includes("workflow")) return "Analysis";
  return "Other";
};

export const getAgentAesthetics = (agentId: string) => {
  const category = getAgentCategory(agentId);

  if (category === "Data Access") {
    return {
      bg: "bg-emerald-50/90 hover:bg-emerald-50/100 dark:bg-emerald-950/20",
      border: "border-emerald-200 dark:border-emerald-900/50",
      selectedBorder: "border-emerald-500 ring-2 ring-emerald-500/20",
      iconColor: "text-emerald-600 dark:text-emerald-400",
      badgeBg: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-300",
      icon: Database,
      category
    };
  }

  if (category === "Analysis") {
    return {
      bg: "bg-sky-50/90 hover:bg-sky-50/100 dark:bg-sky-950/20",
      border: "border-sky-200 dark:border-sky-900/50",
      selectedBorder: "border-sky-500 ring-2 ring-sky-500/20",
      iconColor: "text-sky-600 dark:text-sky-400",
      badgeBg: "bg-sky-100 text-sky-800 dark:bg-sky-900/30 dark:text-sky-300",
      icon: AnalysisLayersIcon,
      category
    };
  }

  if (category === "Visualization") {
    return {
      bg: "bg-purple-50/90 hover:bg-purple-50/100 dark:bg-purple-950/20",
      border: "border-purple-200 dark:border-purple-900/50",
      selectedBorder: "border-purple-500 ring-2 ring-purple-500/20",
      iconColor: "text-purple-600 dark:text-purple-400",
      badgeBg: "bg-purple-100 text-purple-800 dark:bg-purple-900/30 dark:text-purple-300",
      icon: Map,
      category
    };
  }

  if (category === "Domain") {
    return {
      bg: "bg-cyan-50/90 hover:bg-cyan-50/100 dark:bg-cyan-950/20",
      border: "border-cyan-200 dark:border-cyan-900/50",
      selectedBorder: "border-cyan-500 ring-2 ring-cyan-500/20",
      iconColor: "text-cyan-700 dark:text-cyan-400",
      badgeBg: "bg-cyan-100 text-cyan-800 dark:bg-cyan-900/30 dark:text-cyan-300",
      icon: Compass,
      category
    };
  }

  return {
    bg: "bg-neutral-50/90 hover:bg-neutral-100/100 dark:bg-neutral-950/20",
    border: "border-neutral-200 dark:border-neutral-800/50",
    selectedBorder: "border-neutral-500 ring-2 ring-neutral-500/20",
    iconColor: "text-neutral-600 dark:text-neutral-400",
    badgeBg: "bg-neutral-100 text-neutral-700 dark:bg-neutral-800/60 dark:text-neutral-300",
    icon: Sliders,
    category
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
  onCancel,
  onUpdateNode,
  onOpenArtifact,
  outputsExpanded,
  onOutputsExpandedChange,
  renameRequestId = 0,
  editInstructionsRequestId = 0,
  onPointerDown,
  onNodePointerUp,
  onContextMenu,
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
  const [isEditingName, setIsEditingName] = useState(false);
  const [draftName, setDraftName] = useState(node.name);
  const [isEditingInstructions, setIsEditingInstructions] = useState(false);
  const [draftInstructions, setDraftInstructions] = useState(node.instructions);
  const [artifactContextMenu, setArtifactContextMenu] = useState<{
    artifact: TaskArtifact;
  } | null>(null);
  const nameInputRef = useRef<HTMLInputElement>(null);
  const instructionsInputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (!isEditingName) setDraftName(node.name);
  }, [isEditingName, node.name]);

  useEffect(() => {
    if (!isEditingInstructions) setDraftInstructions(node.instructions);
  }, [isEditingInstructions, node.instructions]);

  useEffect(() => {
    if (isEditingName) {
      nameInputRef.current?.focus();
      nameInputRef.current?.select();
    }
  }, [isEditingName]);

  useEffect(() => {
    if (!renameRequestId) return;
    setIsEditingName(true);
  }, [renameRequestId]);

  useEffect(() => {
    if (!editInstructionsRequestId) return;
    setIsEditingInstructions(true);
  }, [editInstructionsRequestId]);

  useEffect(() => {
    if (isEditingInstructions) {
      instructionsInputRef.current?.focus();
      instructionsInputRef.current?.select();
    }
  }, [isEditingInstructions]);

  useEffect(() => {
    if (outputsExpanded !== undefined) {
      setShowOutputs(outputsExpanded);
    }
  }, [outputsExpanded]);

  useEffect(() => {
    if (!artifactContextMenu) return;

    const closeMenu = () => setArtifactContextMenu(null);
    window.addEventListener("pointerdown", closeMenu);
    window.addEventListener("blur", closeMenu);

    return () => {
      window.removeEventListener("pointerdown", closeMenu);
      window.removeEventListener("blur", closeMenu);
    };
  }, [artifactContextMenu]);

  const stopInlineEditPointer = (e: React.PointerEvent) => {
    e.stopPropagation();
  };

  const commitName = () => {
    const nextName = draftName.trim();
    if (nextName && nextName !== node.name) {
      onUpdateNode(node.id, { name: nextName });
    } else {
      setDraftName(node.name);
    }
    setIsEditingName(false);
  };

  const cancelName = () => {
    setDraftName(node.name);
    setIsEditingName(false);
  };

  const commitInstructions = () => {
    if (draftInstructions !== node.instructions) {
      onUpdateNode(node.id, { instructions: draftInstructions });
    }
    setIsEditingInstructions(false);
  };

  const cancelInstructions = () => {
    setDraftInstructions(node.instructions);
    setIsEditingInstructions(false);
  };

  // Status visual configurations
  const statusConfig = {
    idle: {
      color: "text-gray-400",
      bg: "bg-gray-100 dark:bg-gray-800",
      label: "Ready",
      indicator: "bg-gray-300 dark:bg-gray-700"
    },
    waiting: {
      color: "text-amber-600",
      bg: "bg-amber-50 dark:bg-amber-950/30",
      label: "Waiting",
      indicator: "bg-amber-400"
    },
    running: {
      color: "text-blue-500",
      bg: "bg-blue-50 dark:bg-blue-950/30",
      label: "Streaming",
      indicator: "bg-blue-500"
    },
    completed: {
      color: "text-emerald-500",
      bg: "bg-emerald-50 dark:bg-emerald-950/30",
      label: "Success",
      indicator: "bg-emerald-500"
    },
    error: {
      color: "text-rose-500",
      bg: "bg-rose-50 dark:bg-rose-950/30",
      label: "Failed",
      indicator: "bg-rose-500"
    },
    canceled: {
      color: "text-amber-500",
      bg: "bg-amber-50 dark:bg-amber-950/30",
      label: "Canceled",
      indicator: "bg-amber-400"
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
      className={`rounded-xl border shadow-sm transition-shadow duration-150 relative select-none cursor-default ${bg} ${border} ${
        isSelected ? selectedBorder : "hover:shadow-md"
      }`}
      onPointerDown={(e) => {
        const target = e.target as HTMLElement;
        onSelect();
        // Prevent drag initiation on buttons or interactive fields, but still select the node.
        if (target.closest(".no-drag")) return;
        onPointerDown(e);
      }}
      onPointerUp={(e) => {
        if (onNodePointerUp) {
          // Allow bubbling so canvas can unset dragging
          onNodePointerUp(node.id);
        }
      }}
      onContextMenu={(e) => onContextMenu?.(node.id, e)}
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
          
          <div className="flex items-center space-x-2">
            {node.status === "running" ? (
              <span className="flex h-4.5 w-5 items-end justify-center gap-0.5" aria-hidden="true">
                <span className="h-2 w-1 rounded-full bg-blue-500 animate-[streamBar_0.75s_ease-in-out_infinite]" />
                <span className="h-4 w-1 rounded-full bg-blue-500 animate-[streamBar_0.75s_ease-in-out_0.12s_infinite]" />
                <span className="h-2.5 w-1 rounded-full bg-blue-500 animate-[streamBar_0.75s_ease-in-out_0.24s_infinite]" />
              </span>
            ) : node.status === "waiting" ? (
              <span className="relative flex h-3 w-3" aria-hidden="true">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-amber-300 opacity-60" />
                <span className={`relative inline-flex h-3 w-3 rounded-full ${statusConfig.indicator}`} />
              </span>
            ) : (
              <span className={`w-2.5 h-2.5 rounded-full ${statusConfig.indicator}`} />
            )}
            <span className="text-[11px] font-semibold text-neutral-500">{statusConfig.label}</span>
          </div>
        </div>

        {/* Title */}
        <div className="flex items-start space-x-2.5">
          <Icon className={`w-5 h-5 mt-0.5 shrink-0 ${iconColor}`} />
          <div className="overflow-hidden flex-1">
            {isEditingName ? (
              <input
                ref={nameInputRef}
                value={draftName}
                onChange={(e) => setDraftName(e.target.value)}
                onBlur={commitName}
                onPointerDown={stopInlineEditPointer}
                onDoubleClick={(e) => e.stopPropagation()}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    commitName();
                  }
                  if (e.key === "Escape") {
                    e.preventDefault();
                    cancelName();
                  }
                }}
                className="no-drag w-full rounded-md border border-sky-300 bg-white px-1.5 py-1 text-sm font-semibold leading-tight text-neutral-900 shadow-sm outline-none ring-2 ring-sky-100"
                aria-label="Edit step name"
              />
            ) : (
              <h4
                className="no-drag text-sm font-semibold text-neutral-800 dark:text-neutral-200 truncate leading-tight cursor-text"
                title="Double-click to edit step name"
                onDoubleClick={(e) => {
                  e.stopPropagation();
                  onSelect();
                  setIsEditingName(true);
                }}
              >
                {node.name}
              </h4>
            )}
            <p className="text-[10px] text-neutral-500 font-mono truncate">
              ID: {node.agentId}
            </p>
          </div>
        </div>

        {/* Preview Instructions */}
        {isEditingInstructions ? (
          <textarea
            ref={instructionsInputRef}
            value={draftInstructions}
            onChange={(e) => {
              const nextInstructions = e.target.value;
              setDraftInstructions(nextInstructions);
              onUpdateNode(node.id, { instructions: nextInstructions });
            }}
            onBlur={commitInstructions}
            onPointerDown={stopInlineEditPointer}
            onDoubleClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => {
              if (e.key === "Escape") {
                e.preventDefault();
                cancelInstructions();
              }
              if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
                e.preventDefault();
                commitInstructions();
              }
            }}
            className="no-drag mt-3 min-h-[72px] w-full resize-none rounded-lg border border-sky-300 bg-white p-2 text-xs leading-relaxed text-neutral-900 shadow-sm outline-none ring-2 ring-sky-100"
            aria-label="Edit agent task instructions"
          />
        ) : (
          <div
            className="no-drag mt-3 bg-white/70 dark:bg-neutral-900/20 border border-neutral-100 dark:border-neutral-900/30 rounded-lg p-2 text-xs text-neutral-600 dark:text-neutral-400 min-h-[48px] line-clamp-3 leading-relaxed cursor-text"
            title="Double-click to edit task instructions"
            onDoubleClick={(e) => {
              e.stopPropagation();
              onSelect();
              setIsEditingInstructions(true);
            }}
          >
            {node.instructions || <span className="text-neutral-400 italic">No task instructions defined yet. Double click to edit.</span>}
          </div>
        )}

        {/* Inputs Dropdown */}
        {inputDatasetsList && inputDatasetsList.length > 0 && (
          <div className="mt-2.5">
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                setShowInputs(!showInputs);
              }}
              className="no-drag flex items-center space-x-1 w-full text-left text-[11px] text-sky-600 dark:text-sky-400 font-medium hover:text-sky-700 dark:hover:text-sky-300 transition-colors"
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
                  <div
                    key={idx}
                    className="group text-[10px] text-neutral-600 dark:text-neutral-400 truncate pr-1 flex items-center space-x-1.5 no-drag bg-neutral-100 dark:bg-neutral-800/50 p-1 rounded relative"
                    title={ds.description || ds.name || "Input dataset"}
                  >
                    <span className="truncate flex-1">{ds.name || "Unnamed Dataset"}</span>
                    {onRemoveInput && (
                      <button 
                        type="button"
                        onClick={(e) => { e.stopPropagation(); onRemoveInput(ds); }}
                        className="no-drag opacity-0 group-hover:opacity-100 p-0.5 text-neutral-400 hover:text-red-500 transition-all rounded hover:bg-neutral-200 dark:hover:bg-neutral-700"
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
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                const nextShowOutputs = !showOutputs;
                setShowOutputs(nextShowOutputs);
                onOutputsExpandedChange?.(node.id, nextShowOutputs);
              }}
              className="no-drag flex items-center space-x-1 w-full text-left text-[11px] text-emerald-600 dark:text-emerald-400 font-medium hover:text-emerald-700 dark:hover:text-emerald-300 transition-colors"
            >
              <div className="w-4 flex justify-center">
                {showOutputs ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
              </div>
              <FileText className="w-3.5 h-3.5" />
              <span>OUTPUT DATASETS ({node.results.outputs.artifacts.length})</span>
            </button>
            {showOutputs && (
              <div className="mt-1 ml-5 space-y-1">
                {node.results.outputs.artifacts.map((art, idx) => {
                  const filename = getArtifactFilename(art, `Output ${idx + 1}`);
                  const displayName = getArtifactSemanticName(art, filename);
                  const selectionKey = getArtifactSelectionKey(art, idx);
                  return (
                  <div 
                    key={idx} 
                    onPointerDown={(e) => {
                      e.stopPropagation();
                      if (e.button !== 0) return;
                      // @ts-ignore (we passed the extra arg)
                      onPortPointerDown(node.id, "output", e, selectionKey);
                    }}
                    onContextMenu={(e) => {
                      e.preventDefault();
                      e.stopPropagation();
                      setArtifactContextMenu({
                        artifact: art
                      });
                    }}
                    className="group relative cursor-grab active:cursor-grabbing text-[10px] text-neutral-600 dark:text-neutral-400 flex items-center space-x-1.5 no-drag bg-emerald-50 dark:bg-emerald-950/30 border border-emerald-100 dark:border-emerald-900/50 p-1 rounded hover:bg-emerald-100 dark:hover:bg-emerald-900/50 transition-colors"
                    title={getArtifactHoverText(art)}
                  >
                    <span className="truncate flex-1 pr-2">{displayName}</span>
                    <Workflow className="w-3 h-3 text-emerald-500/50 group-hover:text-emerald-500" />
                    {artifactContextMenu?.artifact.url === art.url && (
                      <div
                        onPointerDown={(event) => event.stopPropagation()}
                        className="absolute left-2 top-7 z-[900] w-36 overflow-hidden rounded-lg border border-neutral-200 bg-white text-xs shadow-xl"
                      >
                        <button
                          type="button"
                          onClick={() => {
                            onOpenArtifact?.(artifactContextMenu.artifact);
                            setArtifactContextMenu(null);
                          }}
                          className="flex w-full items-center gap-2 px-3 py-2 text-left font-semibold text-neutral-700 hover:bg-neutral-50"
                        >
                          <Eye className="h-3.5 w-3.5 text-sky-600" />
                          <span>View</span>
                        </button>
                        <a
                          href={artifactContextMenu.artifact.url}
                          download
                          rel="referrer"
                          onClick={() => setArtifactContextMenu(null)}
                          className="flex w-full items-center gap-2 px-3 py-2 text-left font-semibold text-neutral-700 hover:bg-neutral-50"
                        >
                          <Download className="h-3.5 w-3.5 text-emerald-600" />
                          <span>Download</span>
                        </a>
                      </div>
                    )}
                  </div>
                )})}
              </div>
            )}
          </div>
        )}

        {/* Action Panel */}
        <div className="mt-4 pt-3 border-t border-neutral-100 dark:border-neutral-900/40 flex items-center justify-between">
          <div className="flex items-center space-x-1.5">
            <button
              onClick={onExecute}
              disabled={node.status === "running"}
              className={`no-drag flex items-center space-x-1 px-3 py-1.5 text-white rounded-lg text-xs font-semibold shadow-sm transition-colors disabled:cursor-default ${
                node.status === "running"
                  ? "bg-emerald-600 hover:bg-emerald-600"
                  : "bg-sky-600 hover:bg-sky-500"
              }`}
            >
              {node.status === "running" ? (
                <Activity className="w-3.5 h-3.5 animate-pulse" />
              ) : (
                <Play className="w-3.5 h-3.5 fill-current" />
              )}
              <span>{node.status === "running" ? "Streaming" : "Run Agent"}</span>
            </button>

            {node.status === "running" && (
              <button
                onClick={onCancel}
                title="Cancel running task"
                className="no-drag flex h-7 w-7 items-center justify-center rounded-lg border border-rose-200 bg-white text-rose-600 shadow-sm transition-colors hover:bg-rose-50 hover:text-rose-700"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            )}
          </div>

          <button
            onClick={onDelete}
            title="Delete agent node"
            className="no-drag p-1.5 hover:bg-rose-50 hover:text-rose-600 text-neutral-400 rounded-lg transition-colors border border-transparent hover:border-rose-100"
          >
            <Trash2 className="w-4 h-4" />
          </button>
        </div>
      </div>

    </div>
  );
};
