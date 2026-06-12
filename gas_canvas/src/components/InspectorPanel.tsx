import React, { useRef, useEffect, useState } from "react";
import { 
  X, 
  FileText, 
  Terminal, 
  Key, 
  AlertTriangle, 
  Download, 
  Eye, 
  FileCode,
  Files,
  Map,
  Database, 
  Network,
  Settings,
  HelpCircle,
  Info
} from "lucide-react";
import { AgentNode, NodeConnection, SourceCredentials, SourceCredentialSpec, TaskArtifact, TaskResult } from "../types";
import { getAgentAesthetics } from "./AgentNodeCard";
import { AGENT_TEMPLATES } from "./SidebarPanel";
import { getArtifactFilename, getArtifactHoverText, getArtifactPreviewTitle, getArtifactSemanticName } from "../lib/artifacts";

const getArtifactExtension = (name = "", format = "") => {
  const normalizedFormat = format.toLowerCase();
  if (normalizedFormat) return normalizedFormat;
  return name.split(".").pop()?.toLowerCase() || "";
};

const isSpatialArtifact = (name = "", format = "") => {
  const extension = getArtifactExtension(name, format);
  return ["geojson", "gpkg", "tif", "tiff", "geotiff", "geotif"].includes(extension);
};

const isHtmlArtifact = (name = "", format = "") => {
  const extension = getArtifactExtension(name, format);
  return extension === "html" || extension === "htm";
};

const utilityButtonClass =
  "px-2 py-1 bg-neutral-100 hover:bg-neutral-200 text-neutral-700 border border-neutral-200 rounded text-[10px] font-semibold flex items-center space-x-1 transition-colors";

interface InspectorPanelProps {
  selectedNode: AgentNode | null;
  connections: NodeConnection[];
  nodes: AgentNode[];
  servers?: { url: string; providerName: string; agents: any[] }[];
  onUpdateNode: (nodeId: string, updates: Partial<AgentNode>) => void;
  onDescribeAgent: (serverUrl: string, agentId: string) => void;
  onClose: () => void;
  onOpenPreview: (url: string, title: string, artifact?: TaskArtifact) => void;
  onViewAllArtifacts: (artifacts: TaskArtifact[]) => void;
  onOpenCredentialsVault: () => void;
  sourceCredentialSpecs: SourceCredentialSpec[];
  sourceCredentials: SourceCredentials;
  width: number;
}

export const InspectorPanel: React.FC<InspectorPanelProps> = ({
  selectedNode,
  connections,
  nodes,
  servers = [],
  onUpdateNode,
  onDescribeAgent,
  onClose,
  onOpenPreview,
  onViewAllArtifacts,
  onOpenCredentialsVault,
  sourceCredentialSpecs,
  sourceCredentials,
  width,
}) => {
  const logTerminalRef = useRef<HTMLDivElement>(null);
  const instructionsTextareaRef = useRef<HTMLTextAreaElement>(null);
  const [draftInstructions, setDraftInstructions] = useState("");
  const [isEditingInstructions, setIsEditingInstructions] = useState(false);
  const [showKeyOverrides, setShowKeyOverrides] = useState(false);

  const resizeInstructionsTextarea = () => {
    const textarea = instructionsTextareaRef.current;
    if (!textarea) return;

    const maxHeight = 152;
    textarea.style.height = "auto";
    const nextHeight = Math.min(textarea.scrollHeight, maxHeight);
    textarea.style.height = `${nextHeight}px`;
    textarea.style.overflowY = textarea.scrollHeight > maxHeight ? "auto" : "hidden";
  };

  // Keep a local textarea draft so large prompts do not update the canvas on every keystroke.
  useEffect(() => {
    if (selectedNode && !isEditingInstructions) {
      setDraftInstructions(selectedNode.instructions);
    }
  }, [isEditingInstructions, selectedNode?.id, selectedNode?.instructions]);

  useEffect(() => {
    resizeInstructionsTextarea();
  }, [draftInstructions, selectedNode?.id]);

  // Handle auto-scroll inside the logs terminal matching SSE stream increments
  useEffect(() => {
    if (logTerminalRef.current) {
      logTerminalRef.current.scrollTop = logTerminalRef.current.scrollHeight;
    }
  }, [selectedNode?.logs?.length]);

  if (!selectedNode) {
    return (
      <div style={{ width: `${width}px` }} className="border-l border-neutral-200 dark:border-neutral-850 bg-neutral-50/50 dark:bg-neutral-950 flex flex-col items-center justify-center text-center p-6 shrink-0 h-full">
        <Network className="w-10 h-10 text-neutral-300 dark:text-neutral-700 mb-2.5 animate-pulse" />
        <h4 className="text-sm font-semibold text-neutral-600 dark:text-neutral-400">No agent selected</h4>
        <p className="text-[11px] text-neutral-450 dark:text-neutral-500 max-w-[200px] mt-1.5 leading-relaxed">
          Select an agent on the canvas to inspect variables, configure target prompts, track logs, or test execute.
        </p>
      </div>
    );
  }

  // Find parent nodes supplying input datasets (bindings)
  const incomingLinks = connections.filter((c) => c.targetId === selectedNode.id);
  const outgoingLinks = connections.filter((c) => c.sourceId === selectedNode.id);
  const parentNodes = incomingLinks.map((link) => nodes.find((n) => n.id === link.sourceId)).filter(Boolean) as AgentNode[];
  const childNodes = outgoingLinks.map((link) => nodes.find((n) => n.id === link.targetId)).filter(Boolean) as AgentNode[];

  // Retrieve templates
  const template = AGENT_TEMPLATES.find((tpl) => tpl.agent_id === selectedNode.agentId);
  const { iconColor, icon: Icon } = getAgentAesthetics(selectedNode.agentId);
  const selectedServerProviderName =
    servers.find((server) => server.url === selectedNode.serverUrl)?.providerName ||
    selectedNode.serverUrl ||
    "Unknown GAS Server";
  const totalSourceCredentialFields = sourceCredentialSpecs.reduce((count, spec) => count + spec.fields.length, 0);
  const configuredSourceCredentialFields = sourceCredentialSpecs.reduce(
    (count, spec) =>
      count + spec.fields.filter((field) => Boolean(sourceCredentials[spec.sourceId]?.[field]?.trim())).length,
    0
  );
  const missingSourceCredentialLabels = sourceCredentialSpecs.flatMap((spec) =>
    spec.fields
      .filter((field) => !sourceCredentials[spec.sourceId]?.[field]?.trim())
      .map((field) => `${spec.name} ${field}`)
  );

  return (
    <div style={{ width: `${width}px` }} className="border-l border-neutral-200 dark:border-neutral-800 bg-white dark:bg-neutral-900 flex flex-col h-full shrink-0 shadow-xl overflow-hidden">
      {/* HEADER SECTION */}
      <div className="p-4 border-b border-neutral-100 dark:border-neutral-800 flex items-center justify-between">
        <div className="flex items-center space-x-2.5 overflow-hidden">
          <Icon className={`w-5 h-5 shrink-0 ${iconColor}`} />
          <div className="overflow-hidden">
            <h4 className="text-sm font-bold text-neutral-800 dark:text-neutral-200 truncate">
              {selectedNode.name}
            </h4>
            <div className="mt-1 space-y-0.5">
              <p
                className="text-[10px] text-neutral-500 truncate"
                title={selectedServerProviderName}
              >
                {selectedServerProviderName}
              </p>
            </div>
          </div>
        </div>
        <div className="flex items-center gap-1">
          <button
            onClick={() => onDescribeAgent(selectedNode.serverUrl || "", selectedNode.agentId)}
            title="View agent details"
            className="p-1 hover:bg-neutral-150 rounded dark:hover:bg-neutral-800 text-neutral-500 hover:text-sky-600 cursor-pointer"
          >
            <Info className="w-4 h-4" />
          </button>
          <button
            onClick={onClose}
            className="p-1 hover:bg-neutral-150 rounded dark:hover:bg-neutral-800 text-neutral-500"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {/* TASK INSTRUCTIONS / PROMPT FOR LLM */}
        <div className="space-y-1.5">
          <div className="flex items-center justify-between">
            <label className="text-[11px] font-bold text-neutral-500 dark:text-neutral-400 uppercase">
              Agent Task Instructions
            </label>
          </div>
          <textarea
            ref={instructionsTextareaRef}
            value={draftInstructions}
            onChange={(e) => {
              const nextInstructions = e.target.value;
              setDraftInstructions(nextInstructions);
              onUpdateNode(selectedNode.id, { instructions: nextInstructions });
              requestAnimationFrame(resizeInstructionsTextarea);
            }}
            onFocus={() => setIsEditingInstructions(true)}
            onBlur={() => {
              if (draftInstructions !== selectedNode.instructions) {
                onUpdateNode(selectedNode.id, { instructions: draftInstructions });
              }
              setIsEditingInstructions(false);
            }}
            rows={2}
            placeholder="Tell the agent what to perform..."
            className="w-full min-h-[56px] max-h-[152px] text-sm p-2.5 border border-neutral-300 dark:border-neutral-700 bg-neutral-50 dark:bg-neutral-950 rounded-lg text-neutral-800 dark:text-neutral-100 focus:outline-none focus:ring-1 focus:ring-sky-500 resize-none font-sans leading-relaxed"
          />
        </div>

        {/* INPUT BINDINGS OVERVIEW */}
        <div className="space-y-1.5">
          <label className="text-[11px] font-bold text-neutral-500 dark:text-neutral-400 uppercase flex items-center space-x-1">
            <Database className="w-3.5 h-3.5" />
            <span>Active Dataset Inputs</span>
          </label>
          
          {parentNodes.length === 0 ? (
            <div className="p-2.5 border border-dashed border-neutral-200 dark:border-neutral-800 rounded-lg text-xs leading-relaxed text-neutral-600 dark:text-neutral-500 italic bg-neutral-50/40">
              No input links connected. Link an output socket from another agent to bind dataset URLs.
            </div>
          ) : (
            <div className="space-y-1">
              {parentNodes.map((parent) => (
                <div 
                  key={parent.id} 
                  className="flex items-center justify-between p-2 rounded-lg bg-emerald-50/40 dark:bg-emerald-950/25 border border-emerald-100 dark:border-emerald-900/50 text-xs"
                >
                  <span className="font-semibold text-emerald-800 dark:text-emerald-400 truncate max-w-[140px]">
                    📦 {parent.name}
                  </span>
                  <span className="text-[9px] text-neutral-450 dark:text-neutral-500 bg-emerald-100 dark:bg-emerald-900/30 px-1 py-0.5 rounded font-mono">
                    {parent.results?.outputs?.artifacts?.length || 0} Artifacts Connected
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>

        {sourceCredentialSpecs.length > 0 && (
          <div className="space-y-2 rounded-lg border border-amber-200 bg-amber-50/70 p-2.5 dark:border-amber-900/70 dark:bg-amber-950/20">
            <div className="flex items-center justify-between gap-2">
              <div className="min-w-0">
                <p className="text-[11px] font-bold uppercase text-amber-800 dark:text-amber-300">
                  Data Source Keys
                </p>
                <p className="mt-0.5 text-xs font-semibold text-neutral-700 dark:text-neutral-300">
                  {configuredSourceCredentialFields} of {totalSourceCredentialFields} configured
                </p>
              </div>
              <button
                type="button"
                onClick={onOpenCredentialsVault}
                className="shrink-0 rounded-md bg-sky-600 px-2.5 py-1.5 text-[10px] font-bold text-white transition-colors hover:bg-sky-500"
              >
                Manage Keys
              </button>
            </div>
            {missingSourceCredentialLabels.length > 0 ? (
              <p className="text-[10px] leading-relaxed text-amber-800 dark:text-amber-300">
                Missing: {missingSourceCredentialLabels.slice(0, 3).join(", ")}
                {missingSourceCredentialLabels.length > 3 ? `, +${missingSourceCredentialLabels.length - 3} more` : ""}
              </p>
            ) : (
              <p className="text-[10px] font-semibold text-emerald-700 dark:text-emerald-400">
                All declared source credentials are configured.
              </p>
            )}
          </div>
        )}

        {/* SECURE KEY OVERRIDES */}
        <div className="space-y-1.5 pt-2 border-t border-neutral-100 dark:border-neutral-800">
          <button
            onClick={() => setShowKeyOverrides(!showKeyOverrides)}
            className="flex items-center justify-between w-full text-left text-neutral-500 text-[11px] font-bold uppercase hover:text-neutral-700 transition-colors"
          >
            <span className="flex items-center space-x-1.5">
              <Key className="w-3.5 h-3.5" />
              <span>Agent Credentials Overrides</span>
            </span>
            <span>{showKeyOverrides ? "[-]" : "[+]"}</span>
          </button>

          {showKeyOverrides && (
            <div className="space-y-2 p-2 bg-neutral-50/65 dark:bg-neutral-950/40 rounded-lg border border-neutral-200 dark:border-neutral-800/80">
              <p className="text-xs text-neutral-500 leading-relaxed">
                Override default keys specifically for this agent instance block if required.
              </p>
              
              <div className="space-y-1">
                <label className="text-[9px] font-bold text-neutral-500">OPENAI_API_KEY</label>
                <input
                  type="password"
                  placeholder="sk-..."
                  value={selectedNode.credentials.OPENAI_API_KEY || ""}
                  onChange={(e) => onUpdateNode(selectedNode.id, {
                    credentials: { ...selectedNode.credentials, OPENAI_API_KEY: e.target.value }
                  })}
                  className="w-full text-sm p-2 border border-neutral-300 dark:border-neutral-700 rounded-md bg-white dark:bg-neutral-900 text-neutral-800 dark:text-neutral-100"
                />
              </div>

            </div>
          )}
        </div>

        {/* LOG TERMINAL / PROGRESS BLOCK */}
        <div className="space-y-1.5 pt-2 border-t border-neutral-100 dark:border-neutral-800">
          <label className="text-[11px] font-bold text-neutral-500 dark:text-neutral-400 uppercase flex items-center space-x-1">
            <Terminal className="w-3.5 h-3.5" />
            <span>Execution Console Stream</span>
          </label>
          
          <div 
            ref={logTerminalRef}
            className="w-full h-44 bg-neutral-100 text-black border border-neutral-200 p-3 rounded-lg font-mono text-xs leading-relaxed overflow-y-auto scrollbar-thin flex flex-col space-y-1"
          >
            {selectedNode.logs.length === 0 ? (
              <span className="text-neutral-700 italic">No output logged yet. Run step to view connection logs.</span>
            ) : (
              selectedNode.logs.map((log, index) => {
                let colorClass = "text-black";
                if (log.startsWith("[ERROR]") || log.includes("stream_error:") || log.includes("task_failed:") || log.includes("task_rejected:")) {
                  colorClass = "text-rose-700 font-semibold";
                } else if (log.startsWith("[SUCCESS]") || log.includes("task_succeeded:") || log.includes("task_result:")) {
                  colorClass = "text-emerald-700 font-semibold";
                } else if (log.includes("task_cancel")) {
                  colorClass = "text-amber-700 font-semibold";
                } else if (log.startsWith("[EVENT]") || log.includes("stream_connected:") || log.includes("task_accepted:") || log.includes("task_submitting:")) {
                  colorClass = "text-sky-700";
                }
                
                return (
                  <div key={index} className={colorClass}>
                    {log}
                  </div>
                );
              })
            )}
          </div>
        </div>

        {/* GENERATED ARTIFACTS LIST */}
        <div className="space-y-2 pt-2 border-t border-neutral-100 dark:border-neutral-800">
          <div className="flex items-center justify-between">
            <label className="text-[11px] font-bold text-neutral-500 dark:text-neutral-400 uppercase flex items-center space-x-1">
              <FileText className="w-3.5 h-3.5" />
              <span>Output & Response</span>
            </label>
          </div>

          {!selectedNode.results?.outputs?.artifacts || selectedNode.results.outputs.artifacts.length === 0 ? (
            <div className="text-xs text-neutral-500 italic text-center py-2.5 bg-neutral-50 dark:bg-neutral-950/20 rounded-lg">
              {selectedNode.status === "error"
                ? "Agent execution failed."
                : selectedNode.status === "canceled"
                  ? "Agent execution was canceled."
                  : "No artifacts collected yet. Run connection pipeline."}
            </div>
          ) : (
            <div className="space-y-1.5">
              {selectedNode.results.outputs.artifacts.map((art, index) => {
                const filename = getArtifactFilename(art, `artifact_${index}.${art.format || "file"}`);
                const displayName = getArtifactSemanticName(art, filename);
                const previewTitle = getArtifactPreviewTitle(art, filename);
                const isSpatial = isSpatialArtifact(filename, art.format);
                const isHtmlOutput = isHtmlArtifact(filename, art.format);
                return (
                  <div
                    key={index}
                    className="flex flex-col p-2.5 bg-neutral-50 hover:bg-neutral-100 dark:bg-neutral-950 dark:hover:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 rounded-lg text-sm"
                    title={getArtifactHoverText(art)}
                  >
                    <div className="flex items-center justify-between space-x-2">
                      <span className="font-semibold text-neutral-700 dark:text-neutral-300 truncate font-mono text-xs min-w-0">
                        {displayName}
                      </span>
                      <span className="text-[9px] bg-neutral-200 text-neutral-700 dark:bg-neutral-800 dark:text-neutral-300 px-1 py-0.5 rounded font-bold uppercase shrink-0">
                        {art.format || "FILE"}
                      </span>
                    </div>
                    {art.description && (
                      <p className="text-xs text-neutral-500 mt-1 leading-relaxed">{art.description}</p>
                    )}

                    <div className="mt-2.5 flex items-center justify-end space-x-1.5">
                      <button
                        onClick={() => onOpenPreview(art.url, previewTitle, art)}
                        className="px-2 py-1 bg-sky-600 hover:bg-sky-500 text-white rounded text-[10px] font-semibold flex items-center space-x-1 transition-colors"
                      >
                        {isSpatial ? (
                          <Map className="w-3 h-3" />
                        ) : isHtmlOutput ? (
                          <FileCode className="w-3 h-3" />
                        ) : (
                          <Eye className="w-3 h-3" />
                        )}
                        <span>{isSpatial ? "Add to Map" : isHtmlOutput ? "Open HTML" : "View Artifact"}</span>
                      </button>
                      <a
                        href={art.url}
                        target="_blank"
                        rel="referrer"
                        className="px-2 py-1 bg-neutral-100 hover:bg-neutral-200 text-neutral-700 border border-neutral-200 rounded text-[10px] font-semibold flex items-center space-x-1 transition-colors"
                      >
                        <Download className="w-3 h-3" />
                        <span>Download</span>
                      </a>
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          {(selectedNode.lastRequest || selectedNode.results) && (
            <div className="flex items-center justify-end gap-1.5 pt-1">
              {selectedNode.results?.outputs?.artifacts && selectedNode.results.outputs.artifacts.length > 0 && (
                <button
                  type="button"
                  onClick={() => onViewAllArtifacts(selectedNode.results?.outputs?.artifacts || [])}
                  className={utilityButtonClass}
                  title="View all artifacts in workspace tabs"
                >
                  <Files className="w-3 h-3" />
                  <span>View All Artifacts</span>
                </button>
              )}
              {selectedNode.lastRequest && (
                <a
                  href={`data:application/json;charset=utf-8,${encodeURIComponent(JSON.stringify(selectedNode.lastRequest, null, 2))}`}
                  download={`${selectedNode.id}_request.json`}
                  className={utilityButtonClass}
                  title="Download Request JSON"
                >
                  <FileText className="w-3 h-3" />
                  <span>Request JSON</span>
                </a>
              )}
              {selectedNode.results && (
                <a
                  href={`data:application/json;charset=utf-8,${encodeURIComponent(JSON.stringify(selectedNode.results, null, 2))}`}
                  download={`${selectedNode.id}_response.json`}
                  className={utilityButtonClass}
                  title="Download Response JSON"
                >
                  <FileText className="w-3 h-3" />
                  <span>Response JSON</span>
                </a>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
