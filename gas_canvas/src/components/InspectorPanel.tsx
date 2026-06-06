import React, { useRef, useEffect, useState } from "react";
import { 
  X, 
  FileText, 
  Terminal, 
  Play, 
  Save, 
  Key, 
  AlertTriangle, 
  Download, 
  Eye, 
  Database, 
  Network,
  Settings,
  HelpCircle
} from "lucide-react";
import { AgentNode, NodeConnection, TaskResult } from "../types";
import { getAgentAesthetics } from "./AgentNodeCard";
import { AGENT_TEMPLATES } from "./SidebarPanel";

interface InspectorPanelProps {
  selectedNode: AgentNode | null;
  connections: NodeConnection[];
  nodes: AgentNode[];
  servers?: { url: string; providerName: string; agents: any[] }[];
  onUpdateNode: (nodeId: string, updates: Partial<AgentNode>) => void;
  onClose: () => void;
  onExecuteNode: (nodeId: string) => void;
  onOpenPreview: (url: string, title: string) => void;
  width: number;
}

export const InspectorPanel: React.FC<InspectorPanelProps> = ({
  selectedNode,
  connections,
  nodes,
  servers = [],
  onUpdateNode,
  onClose,
  onExecuteNode,
  onOpenPreview,
  width,
}) => {
  const logTerminalRef = useRef<HTMLDivElement>(null);
  const [localInstructions, setLocalInstructions] = useState("");
  const [localName, setLocalName] = useState("");
  const [showKeyOverrides, setShowKeyOverrides] = useState(false);

  // Synchronize local states when selection shifts
  useEffect(() => {
    if (selectedNode) {
      setLocalInstructions(selectedNode.instructions);
      setLocalName(selectedNode.name);
    }
  }, [selectedNode?.id]);

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

  const handleSaveNameAndPrompt = () => {
    onUpdateNode(selectedNode.id, {
      name: localName,
      instructions: localInstructions
    });
  };

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
              <p className="text-[10px] text-neutral-400 font-mono truncate">
                {servers.find(s => s.url === selectedNode.serverUrl || s.agents.some((a: any) => a.agent_id === selectedNode.agentId))?.providerName || "Geoinformation and Big Data Research Lab (GIBD)"}
              </p>
            </div>
          </div>
        </div>
        <button
          onClick={onClose}
          className="p-1 hover:bg-neutral-150 rounded dark:hover:bg-neutral-800 text-neutral-500"
        >
          <X className="w-4 h-4" />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {/* EDIT NAME */}
        <div className="space-y-1.5">
          <label className="text-[11px] font-bold text-neutral-500 dark:text-neutral-400 uppercase">
            Step Name
          </label>
          <input
            type="text"
            value={localName}
            onChange={(e) => setLocalName(e.target.value)}
            onBlur={handleSaveNameAndPrompt}
            className="w-full text-xs p-1.5 border border-neutral-300 dark:border-neutral-700 bg-neutral-50 dark:bg-neutral-950 rounded-lg text-neutral-800 dark:text-neutral-100 focus:outline-none focus:ring-1 focus:ring-sky-500"
          />
        </div>

        {/* TASK INSTRUCTIONS / PROMPT FOR LLM */}
        <div className="space-y-1.5">
          <div className="flex items-center justify-between">
            <label className="text-[11px] font-bold text-neutral-500 dark:text-neutral-400 uppercase">
              Agent Task Instructions
            </label>
          </div>
          <textarea
            value={localInstructions}
            onChange={(e) => setLocalInstructions(e.target.value)}
            onBlur={handleSaveNameAndPrompt}
            rows={5}
            placeholder="Tell the agent what to perform..."
            className="w-full text-xs p-2 border border-neutral-300 dark:border-neutral-700 bg-neutral-50 dark:bg-neutral-950 rounded-lg text-neutral-800 dark:text-neutral-100 focus:outline-none focus:ring-1 focus:ring-sky-500 resize-none font-sans leading-relaxed"
          />
          <button 
            onClick={handleSaveNameAndPrompt}
            className="w-full py-1.5 bg-neutral-100 hover:bg-neutral-200 text-neutral-800 dark:bg-neutral-800 dark:hover:bg-neutral-700 dark:text-neutral-200 rounded text-xs font-semibold flex items-center justify-center space-x-1.5"
          >
            <Save className="w-3.5 h-3.5" />
            <span>Apply Changes</span>
          </button>
        </div>

        {/* INPUT BINDINGS OVERVIEW */}
        <div className="space-y-1.5">
          <label className="text-[11px] font-bold text-neutral-500 dark:text-neutral-400 uppercase flex items-center space-x-1">
            <Database className="w-3.5 h-3.5" />
            <span>Active Dataset Inputs</span>
          </label>
          
          {parentNodes.length === 0 ? (
            <div className="p-2 border border-dashed border-neutral-200 dark:border-neutral-800 rounded-lg text-[10px] text-neutral-450 dark:text-neutral-500 italic bg-neutral-50/40">
              No input links connected. Link an output socket from another agent to bind dataset URLs.
            </div>
          ) : (
            <div className="space-y-1">
              {parentNodes.map((parent) => (
                <div 
                  key={parent.id} 
                  className="flex items-center justify-between p-2 rounded-lg bg-emerald-50/40 dark:bg-emerald-950/25 border border-emerald-100 dark:border-emerald-900/50 text-[11px]"
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
              <p className="text-[10px] text-neutral-500 leading-normal">
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
                  className="w-full text-xs p-1.5 border border-neutral-300 dark:border-neutral-700 rounded-md bg-white dark:bg-neutral-900 text-neutral-800 dark:text-neutral-100"
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
            className="w-full h-44 bg-neutral-900 text-neutral-100 p-2.5 rounded-lg font-mono text-[10px] overflow-y-auto scrollbar-thin flex flex-col space-y-1"
          >
            {selectedNode.logs.length === 0 ? (
              <span className="text-neutral-500 italic">No output logged yet. Run step to view connection logs.</span>
            ) : (
              selectedNode.logs.map((log, index) => {
                let colorClass = "text-neutral-300";
                if (log.startsWith("[ERROR]")) {
                  colorClass = "text-rose-400 font-semibold";
                } else if (log.startsWith("[SUCCESS]")) {
                  colorClass = "text-emerald-400 font-semibold";
                } else if (log.startsWith("[EVENT]")) {
                  colorClass = "text-sky-350";
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
            {selectedNode.results && (
              <a
                href={`data:application/json;charset=utf-8,${encodeURIComponent(JSON.stringify(selectedNode.results, null, 2))}`}
                download={`${selectedNode.id}_full_response.json`}
                className="px-2 py-1 bg-neutral-900 hover:bg-neutral-800 text-white dark:bg-neutral-100 dark:hover:bg-neutral-200 dark:text-neutral-900 rounded text-[9px] font-semibold flex items-center space-x-1 transition-colors"
                title="Download Full Response JSON"
              >
                <FileText className="w-3 h-3" />
                <span>Full Response JSON</span>
              </a>
            )}
          </div>

          {!selectedNode.results?.outputs?.artifacts || selectedNode.results.outputs.artifacts.length === 0 ? (
            <div className="text-[10px] text-neutral-450 italic text-center py-2 bg-neutral-50 dark:bg-neutral-950/20 rounded-lg">
              {selectedNode.status === "failed" ? "Agent execution failed." : "No artifacts collected yet. Run connection pipeline."}
            </div>
          ) : (
            <div className="space-y-1.5">
              {selectedNode.results.outputs.artifacts.map((art, index) => {
                const isHtml = art.format?.toLowerCase() === "html" || art.name?.toLowerCase().endsWith(".html");
                return (
                  <div
                    key={index}
                    className="flex flex-col p-2 bg-neutral-50 hover:bg-neutral-100 dark:bg-neutral-950 dark:hover:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 rounded-lg text-xs"
                  >
                    <div className="flex items-center justify-between space-x-2">
                      <span className="font-semibold text-neutral-700 dark:text-neutral-300 truncate font-mono text-[11px] min-w-0">
                        {art.name || `artifact_${index}.${art.format || 'txt'}`}
                      </span>
                      <span className="text-[9px] bg-neutral-200 text-neutral-700 dark:bg-neutral-800 dark:text-neutral-300 px-1 py-0.5 rounded font-bold uppercase shrink-0">
                        {art.format || "FILE"}
                      </span>
                    </div>
                    {art.description && (
                      <p className="text-[10px] text-neutral-500 mt-1">{art.description}</p>
                    )}

                    <div className="mt-2.5 flex items-center justify-end space-x-1.5">
                      <button
                        onClick={() => onOpenPreview(art.url, art.name || "Artifact Preview")}
                        className="px-2 py-1 bg-sky-600 hover:bg-sky-500 text-white rounded text-[10px] font-semibold flex items-center space-x-1 transition-colors"
                      >
                        <Eye className="w-3 h-3" />
                        <span>Preview</span>
                      </button>
                      <a
                        href={art.url}
                        target="_blank"
                        rel="referrer"
                        className="px-2 py-1 bg-neutral-900 hover:bg-neutral-800 text-white dark:bg-neutral-100 dark:hover:bg-neutral-200 dark:text-neutral-900 rounded text-[10px] font-semibold flex items-center space-x-1 transition-colors"
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
        </div>
      </div>

      {/* FOOTER CONTROLS */}
      <div className="p-4 border-t border-neutral-100 dark:border-neutral-800 bg-neutral-50/50 dark:bg-neutral-950/40">
        <button
          onClick={() => onExecuteNode(selectedNode.id)}
          disabled={selectedNode.status === "running"}
          className="w-full flex items-center justify-center space-x-1.5 py-2.5 bg-neutral-900 hover:bg-neutral-800 dark:bg-neutral-100 dark:hover:bg-neutral-200 dark:text-neutral-900 text-white text-xs font-bold rounded-lg shadow-sm disabled:opacity-50 transition-colors"
        >
          <Play className="w-4 h-4 fill-current animate-pulse-slow" />
          <span>{selectedNode.status === "running" ? "Streaming Live..." : "Execute This Agent"}</span>
        </button>
      </div>
    </div>
  );
};
