import React, { useState, useEffect, useRef } from "react";
import { 
  Network, 
  Settings, 
  HelpCircle, 
  Database, 
  Activity, 
  Key, 
  Layers, 
  MapPin, 
  Plus, 
  Info,
  ChevronRight,
  Sparkles,
  ServerCrash
} from "lucide-react";
import { AgentNode, NodeConnection, SavedWorkflow, TaskResult, TaskArtifact } from "./types";
import { SidebarPanel, AGENT_TEMPLATES } from "./components/SidebarPanel";
import { InspectorPanel } from "./components/InspectorPanel";
import { AgentDescribePanel } from "./components/AgentDescribePanel";
import { CanvasControls } from "./components/CanvasControls";
import { AgentNodeCard } from "./components/AgentNodeCard";
import { LivePreviewModal } from "./components/LivePreviewModal";
import { CredentialsVault } from "./components/CredentialsVault";
import { determineRelevantArtifacts } from "./lib/llmRouting";

const getApiUrl = (path: string) => {
  const pathname = window.location.pathname;
  if (pathname.startsWith("/canvas")) {
    return `/canvas${path}`;
  }
  return path;
};

const STORAGE_KEYS = {
  CREDENTIALS: "gas_canvas_credentials",
  WORKFLOWS: "gas_saved_workflows"
};

export default function App() {
  // Master Canvas States
  const [nodes, setNodes] = useState<AgentNode[]>([]);
  const nodesRef = useRef<AgentNode[]>(nodes);
  
  const [connections, setConnections] = useState<NodeConnection[]>([]);
  const connectionsRef = useRef<NodeConnection[]>(connections);
  
  // Sync refs with state
  useEffect(() => {
    nodesRef.current = nodes;
  }, [nodes]);

  useEffect(() => {
    connectionsRef.current = connections;
  }, [connections]);
  
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [selectedConnectionId, setSelectedConnectionId] = useState<string | null>(null);
  const [selectedDescribeAgentInfo, setSelectedDescribeAgentInfo] = useState<any>(null);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;

      if ((e.key === "Delete" || e.key === "Backspace") && selectedConnectionId) {
        setConnections((prev) => prev.filter((c) => c.id !== selectedConnectionId));
        setSelectedConnectionId(null);
      }
    };
    
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [selectedConnectionId]);
  
  // To avoid React batching update delays causing topological sort data races
  const volatileResultsRef = useRef<Record<string, TaskResult>>({});
  
  // Gas servers state
  const [servers, setServers] = useState<{ url: string; providerName: string; agents: any[]; isExpanded?: boolean }[]>([]);
  const [isSyncingServer, setIsSyncingServer] = useState<string | null>(null);

  // Viewport / Zoom scale State
  const [zoom, setZoom] = useState(1);
  const [sidebarWidth, setSidebarWidth] = useState(320);
  const [inspectorWidth, setInspectorWidth] = useState(320);
  const canvasRef = useRef<HTMLDivElement>(null);

  // Dragging states
  const [dragNodeId, setDragNodeId] = useState<string | null>(null);
  const [dragOffset, setDragOffset] = useState({ x: 0, y: 0 });

  // Port connection draft states
  const [connectionDraft, setConnectionDraft] = useState<{
    nodeId: string;
    type: "input" | "output";
    artifactName?: string;
  } | null>(null);
  const [pointerPos, setPointerPos] = useState({ x: 0, y: 0 });

  // Credentials config states
  const [isCredentialsOpen, setIsCredentialsOpen] = useState(false);
  const [credentials, setCredentials] = useState({
    OPENAI_API_KEY: ""
  });

  // Modal map previews states
  const [previewData, setPreviewData] = useState<{
    isOpen: boolean;
    url: string;
    title: string;
  }>({
    isOpen: false,
    url: "",
    title: ""
  });

  // Workflow save/load states
  const [savedWorkflowsList, setSavedWorkflowsList] = useState<Array<{ id: string; name: string }>>([]);

  // Running status
  const [isPipelineRunning, setIsPipelineRunning] = useState(false);

  // Save Prompt State
  const [savePromptOpen, setSavePromptOpen] = useState(false);
  const [workflowNameInput, setWorkflowNameInput] = useState("");

  // Wipe Confirm State
  const [wipeConfirmOpen, setWipeConfirmOpen] = useState(false);

  // Toast
  const [toastMsg, setToastMsg] = useState<string | null>(null);

  const showToast = (msg: string) => {
    setToastMsg(msg);
    setTimeout(() => setToastMsg(null), 3000);
  };

  // Initial load
  useEffect(() => {
    // 1. Recover local credentials if saved
    const savedCreds = localStorage.getItem(STORAGE_KEYS.CREDENTIALS);
    if (savedCreds) {
      try {
        setCredentials(JSON.parse(savedCreds));
      } catch (e) {
        console.error("Failed to parse saved credentials", e);
      }
    }

    // 2. Recover saved workflows list
    updateSavedWorkflowsList();

    // 3. Removed default template load as requested by user
    // loadPresetTemplate("Earthquake");
  }, []);

  const updateSavedWorkflowsList = () => {
    const rawWorkflows = localStorage.getItem(STORAGE_KEYS.WORKFLOWS);
    if (rawWorkflows) {
      try {
        const parsedList: SavedWorkflow[] = JSON.parse(rawWorkflows);
        setSavedWorkflowsList(parsedList.map(item => ({ id: item.id, name: item.name })));
      } catch (e) {
        console.error(e);
      }
    }
  };

  // --- NODE GRAPH MANIPULATION HANDLERS ---

  // Handle adding an agent node instance to the active canvas center
  const handleAddAgentNode = (agentId: string, name: string, serverUrl?: string) => {
    const newId = `agent_${Date.now()}`;
    const defaultPrompts: Record<string, string> = {
      geospatial_data_retrieval_agent: "Retrieve primary spatial datasets.",
      pasda_agent: "Crawl PASDA for Pennsylvania files.",
      usgs_earthquake_agent: "Fetch seismology GeoJSON records from USGS API.",
      spatial_analysis_agent: "Calculate buffer spatial coordinates.",
      spatial_statistics_agent: "Analyze spatial clustering coefficients.",
      raster_agent: "Calculate NDVI elevation statistics.",
      mapping_agent: "Generate high resolution cartographic layout of current elements.",
      web_mapping_app_agent: "Generate an interactive HTML Leaflet map application."
    };

    const newNode: AgentNode = {
      id: newId,
      agentId,
      name: `${name} ${nodes.filter(n => n.agentId === agentId).length + 1}`,
      x: 100 + (nodes.length * 25) % 200,
      y: 120 + (nodes.length * 25) % 200,
      instructions: defaultPrompts[agentId] || "Execute standard geospatial modeling tasks.",
      inputDatasets: [],
      credentials: {},
      serverUrl: serverUrl || "https://www.geospatial-agentic-services.online/",
      status: "idle",
      logs: ["[SYSTEM]: Node initialized in workspace. Connect output to chain dataset feed."]
    };

    setNodes((prev) => [...prev, newNode]);
    setSelectedNodeId(newId);
  };

  // Drag handlers
  const handleNodePointerDown = (nodeId: string, e: React.PointerEvent) => {
    setDragNodeId(nodeId);
    const node = nodes.find(n => n.id === nodeId);
    if (node) {
      // Find offset of pointer relative to node card's top-left corner
      const rect = document.getElementById(`node-${nodeId}`)?.getBoundingClientRect();
      if (rect) {
        setDragOffset({
          x: e.clientX - rect.left,
          y: e.clientY - rect.top
        });
      }
    }
  };

  const handleCanvasPointerMove = (e: React.PointerEvent) => {
    if (!canvasRef.current) return;

    // Read current pointer pos scaled into canvas coordinates
    const rect = canvasRef.current.getBoundingClientRect();
    const currentX = (e.clientX - rect.left) / zoom;
    const currentY = (e.clientY - rect.top) / zoom;
    
    setPointerPos({ x: currentX, y: currentY });

    if (dragNodeId) {
      setNodes((prevNodes) =>
        prevNodes.map((node) => {
          if (node.id === dragNodeId) {
            // Apply coordinates offsets and clamp to canvas edges, undoing the scaling
            const newX = (e.clientX - rect.left - dragOffset.x) / zoom;
            const newY = (e.clientY - rect.top - dragOffset.y) / zoom;
            return {
              ...node,
              x: Math.max(0, Math.min(8000, newX)),
              y: Math.max(0, Math.min(8000, newY))
            };
          }
          return node;
        })
      );
    }
  };

  const handleCanvasPointerUp = () => {
    setDragNodeId(null);
    setConnectionDraft(null);
  };

  const handlePortPointerDown = (nodeId: string, type: "input" | "output", e: React.PointerEvent, artifactName?: string) => {
    if (!canvasRef.current) return;
    const rect = canvasRef.current.getBoundingClientRect();
    const currentX = (e.clientX - rect.left) / zoom;
    const currentY = (e.clientY - rect.top) / zoom;
    
    setPointerPos({ x: currentX, y: currentY });
    setConnectionDraft({ nodeId, type, artifactName });
  };

  const handlePortPointerUp = (nodeId: string, type: "input" | "output") => {
    if (!connectionDraft) return;

    // Complete connection drafting
    const isSelfConnection = connectionDraft.nodeId === nodeId;
    const isOppositeType = connectionDraft.type !== type;

    if (!isSelfConnection && isOppositeType) {
      const sourceId = type === "output" ? nodeId : connectionDraft.nodeId;
      const targetId = type === "input" ? nodeId : connectionDraft.nodeId;
      const artifactName = connectionDraft.artifactName;

      // Check if link already exists
      const linkExists = connections.some(
        (c) => c.sourceId === sourceId && c.targetId === targetId
      );

      setConnections((prev) => {
        const existingIdx = prev.findIndex(
          (c) => c.sourceId === sourceId && c.targetId === targetId
        );

        let newConnections = [...prev];
        let linkAdded = false;

        if (existingIdx >= 0) {
          if (artifactName) {
            const current = prev[existingIdx];
            const arts = current.artifacts ? [...current.artifacts] : [];
            if (!arts.includes(artifactName)) {
              arts.push(artifactName);
            }
            newConnections = [
              ...prev.slice(0, existingIdx),
              { ...current, artifacts: arts },
              ...prev.slice(existingIdx + 1)
            ];
            linkAdded = true;
          }
        } else {
          const newConnection: NodeConnection = {
            id: `connection_${Date.now()}`,
            sourceId,
            targetId,
            artifacts: artifactName ? [artifactName] : undefined
          };
          newConnections = [...prev, newConnection];
          linkAdded = true;
        }

        if (linkAdded) {
          setTimeout(() => {
            setNodes((prevNodes) =>
              prevNodes.map((n) => {
                if (n.id === targetId) {
                  return {
                    ...n,
                    logs: [
                      ...n.logs,
                      `[LINKED]: Output${artifactName ? ` "${artifactName}"` : ''} from parent node "${prevNodes.find((m) => m.id === sourceId)?.name}" attached successfully.`
                    ]
                  };
                }
                return n;
              })
            );
          }, 0);
        }

        return newConnections;
      });
    }

    // Reset link draft
    setConnectionDraft(null);
  };

  // Remove elements
  const handleDeleteNode = (nodeId: string) => {
    setNodes((prev) => prev.filter((n) => n.id !== nodeId));
    setConnections((prev) =>
      prev.filter((c) => c.sourceId !== nodeId && c.targetId !== nodeId)
    );
    if (selectedNodeId === nodeId) {
      setSelectedNodeId(null);
    }
  };

  // --- SYNCHRONIZE AGENTS BLUEPRINTS FROM GetCapabilities ---

  const handleSidebarResize = (e: React.PointerEvent) => {
    e.currentTarget.setPointerCapture(e.pointerId);
    const startX = e.clientX;
    const startWidth = sidebarWidth;

    const onPointerMove = (moveEvent: PointerEvent) => {
      const newWidth = Math.max(200, Math.min(600, startWidth + (moveEvent.clientX - startX)));
      setSidebarWidth(newWidth);
    };

    const onPointerUp = (upEvent: PointerEvent) => {
      document.removeEventListener('pointermove', onPointerMove);
      document.removeEventListener('pointerup', onPointerUp);
      e.currentTarget?.releasePointerCapture?.(upEvent.pointerId);
    };

    document.addEventListener('pointermove', onPointerMove);
    document.addEventListener('pointerup', onPointerUp);
  };

  const handleInspectorResize = (e: React.PointerEvent) => {
    e.currentTarget.setPointerCapture(e.pointerId);
    const startX = e.clientX;
    const startWidth = inspectorWidth;

    const onPointerMove = (moveEvent: PointerEvent) => {
      const newWidth = Math.max(250, Math.min(800, startWidth - (moveEvent.clientX - startX)));
      setInspectorWidth(newWidth);
    };

    const onPointerUp = (upEvent: PointerEvent) => {
      document.removeEventListener('pointermove', onPointerMove);
      document.removeEventListener('pointerup', onPointerUp);
      e.currentTarget?.releasePointerCapture?.(upEvent.pointerId);
    };

    document.addEventListener('pointermove', onPointerMove);
    document.addEventListener('pointerup', onPointerUp);
  };

  const handleAddServer = async (url: string) => {
    setIsSyncingServer(url);
    try {
      console.log("Sycing agent metadata from server URL:", url);
      const res = await fetch(getApiUrl(`/api/gas/capabilities?serverUrl=${encodeURIComponent(url)}`));
      const data = await res.json();
      if (data.error) {
        showToast(`Server Error: ${data.details || data.error}`);
      } else {
        // Log capabilities
        console.log("Fetched live capabilities object:", data.capabilities);
        
        const providerName = data.capabilities?.provider?.name || "Unknown Provider";

        let fetchedAgents: any[] = [];
        if (data.capabilities?.agents && Array.isArray(data.capabilities.agents)) {
          fetchedAgents = data.capabilities.agents;
        } else if (data.agentsList && Array.isArray(data.agentsList)) {
          fetchedAgents = data.agentsList.map((id: string) => ({ agent_id: id }));
        }

        if (fetchedAgents.length > 0) {
          const newTemplates = fetchedAgents.map((agent: any) => {
            const actualId = agent.agent_id || agent.id;
            const existing = AGENT_TEMPLATES.find(t => t.agent_id === actualId);
            return {
              agent_id: actualId,
              name: agent.name || existing?.name || actualId,
              category: existing?.category || "Analysis",
              description: agent.description || existing?.description || "Dynamically discovered agent from GAS server.",
              keywords: existing?.keywords || ["dynamic", "fetched"]
            };
          });

          setServers(prev => {
            const copy = prev.filter(s => s.url !== url);
            return [...copy, { url, providerName, agents: newTemplates, isExpanded: true }];
          });

          showToast(`Successfully connected and fetched ${fetchedAgents.length} agents from ${providerName}!`);
        } else {
          showToast(`Server returned 0 agents in GetCapabilities.`);
        }
      }
    } catch (err: any) {
      console.error(err);
      showToast(`Sync Error: Could not connect to server: ${err.message}`);
    } finally {
      setIsSyncingServer(null);
    }
  };

  const handleRemoveServer = (url: string) => {
    setServers(prev => prev.filter(s => s.url !== url));
  };

  const handleToggleServer = (url: string) => {
    setServers(prev => prev.map(s => s.url === url ? { ...s, isExpanded: !s.isExpanded } : s));
  };

  const handleDescribeAgent = async (serverUrl: string, agentId: string) => {
    try {
      const res = await fetch(getApiUrl(`/api/gas/agent-details?serverUrl=${encodeURIComponent(serverUrl)}&agentId=${encodeURIComponent(agentId)}`));
      const data = await res.json();
      if (data.error) {
        showToast(`Error: ${data.details || data.error}`);
      } else {
        setSelectedDescribeAgentInfo(data);
      }
    } catch (err: any) {
      showToast(`Error fetching agent details: ${err.message}`);
    }
  };

  // --- PIPELINE RUNNER ENGINE (SEQUENTIAL TOP-SORT DAG ORCHESTRATION) ---

  // Executes a single node's task proxying the stream of progress events to its logs
  const executeSingleNode = async (nodeId: string, isAutomatedRun: boolean = false): Promise<TaskResult | null> => {
    const activeNodes = nodesRef.current;
    const activeConnections = connectionsRef.current;

    const node = activeNodes.find((n) => n.id === nodeId);
    if (!node) return null;

    // Mark running
    setNodes((prev) =>
      prev.map((n) => {
        if (n.id === nodeId) {
          return {
            ...n,
            status: "running",
            logs: [...n.logs, `[SYSTEM]: Submitting task execution request to agent [${node.agentId}]...`]
          };
        }
        return n;
      })
    );

    // Compute active datasets input binders:
    // Gather outputs of connected parent nodes
    const parentLinks = activeConnections.filter((c) => c.targetId === nodeId);
    const parentDatasetUrls: string[] = [];

    for (const link of parentLinks) {
      // Prioritize recently completed results avoiding React state batching race-conditions
      const recentResult = volatileResultsRef.current[link.sourceId];
      const parentNode = activeNodes.find((n) => n.id === link.sourceId);
      const artifactsSource = recentResult?.outputs?.artifacts || parentNode?.results?.outputs?.artifacts || [];

      let selectedArtifactNames = link.artifacts || [];

      // If in an automated pipeline run, there are multiple artifacts, and the user hasn't explicitly specified which one(s) to pass
      if (isAutomatedRun && selectedArtifactNames.length === 0 && artifactsSource.length > 1) {
        setNodes((prev) =>
          prev.map((n) => {
            if (n.id === nodeId) {
              return { ...n, logs: [...n.logs, `[SYSTEM]: Using AI routing to determine relevant artifacts from parent agent [${parentNode?.name}]...`] };
            }
            return n;
          })
        );

        selectedArtifactNames = await determineRelevantArtifacts(
          node.credentials.OPENAI_API_KEY || credentials.OPENAI_API_KEY,
          parentNode?.instructions || "",
          node.instructions || "",
          artifactsSource
        );

        setNodes((prev) =>
          prev.map((n) => {
            if (n.id === nodeId) {
              return { ...n, logs: [...n.logs, `[SYSTEM]: AI routed artifacts: ${selectedArtifactNames.join(", ")}`] };
            }
            return n;
          })
        );
      }

      artifactsSource.forEach((art, idx) => {
        const artName = art.name || `Output ${idx + 1}`;
        if (selectedArtifactNames.length === 0 || selectedArtifactNames.includes(artName)) {
          if (art.url) parentDatasetUrls.push(art.url);
        }
      });
    }

    // Merge manual urls + dynamic binders
    const activeInputDatasets = [...node.inputDatasets, ...parentDatasetUrls];

    // Read client key overrides or default system vault credentials
    const finalCredentials: any = {
      OPENAI_API_KEY: node.credentials.OPENAI_API_KEY || credentials.OPENAI_API_KEY,
    };
    const savedSourceCreds = localStorage.getItem("gas_source_credentials");
    if (savedSourceCreds) {
      try {
        const parsed = JSON.parse(savedSourceCreds);
        if (Object.keys(parsed).length > 0) {
          finalCredentials.source_credentials = parsed;
        }
      } catch (e) {
        console.error("Failed to parse gas_source_credentials", e);
      }
    }

    try {
      // Build call parameters matching client SDK guide
      const payload = {
        agentId: node.agentId,
        instructions: node.instructions,
        serverUrl: node.serverUrl || "https://www.geospatial-agentic-services.online/",
        options: {
          mode: "stream",
          inputDatasets: activeInputDatasets,
          credentials: finalCredentials
        }
      };

      console.log(`Firing task payload on node ${nodeId}:`, payload);

      const response = await fetch(getApiUrl("/api/gas/run-task"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });

      if (!response.ok) {
        const errPayload = await response.json();
        throw new Error(errPayload.details || errPayload.error || "Server transaction error");
      }

      // Read chunk-by-chunk stream events
      const reader = response.body?.getReader();
      const decoder = new TextDecoder();
      if (!reader) {
        throw new Error("No readable event stream body returned by proxy.");
      }

      let buffer = "";
      let lastSuccessResult: TaskResult | null = null;

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        
        // Split chunks by double newline (SSE standard delimiter)
        const lines = buffer.split("\n\n");
        // Maintain trailing potential fragment in buffer
        buffer = lines.pop() || "";

        for (const line of lines) {
          const trimmed = line.trim();
          if (!trimmed || !trimmed.startsWith("data: ")) continue;

          let eventObj: any;
          try {
            const rawJson = trimmed.slice(6);
            eventObj = JSON.parse(rawJson);
          } catch (e) {
            console.warn("Skipping parse issue line on SSE:", line);
            continue;
          }

          // Print stream event to node terminal logs
          let logMsg = "";
          let dataUpdated: Partial<AgentNode> = {};

          if (eventObj.event === "task_result" || eventObj.payload?.outputs) {
            const taskResult: TaskResult = eventObj.payload || eventObj;
            lastSuccessResult = taskResult;
            volatileResultsRef.current[nodeId] = taskResult;
            logMsg = `[SUCCESS]: Task Completed successfully! Collected ${taskResult.outputs?.artifacts?.length || 0} artifacts.`;
            
            setNodes((prev) =>
              prev.map((n) => {
                if (n.id === nodeId) {
                  return {
                    ...n,
                    status: "completed",
                    results: taskResult,
                    logs: [...n.logs, logMsg]
                  };
                }
                return n;
              })
            );
          } else if (eventObj.event === "stream_error") {
            throw new Error(eventObj.payload?.message || "Internal Stream Failure");
          } else {
            // Standard debug logs
            const payloadStr = typeof eventObj.payload === 'object' 
              ? JSON.stringify(eventObj.payload) 
              : String(eventObj.payload || '');
            
            logMsg = `[EVENT][${eventObj.event || 'INFO'}]: ${payloadStr}`;

            setNodes((prev) =>
              prev.map((n) => {
                if (n.id === nodeId) {
                  return {
                    ...n,
                    logs: [...n.logs, logMsg]
                  };
                }
                return n;
              })
            );
          }
        }
      }

      if (!lastSuccessResult) {
        // If ended but didn't pay off a result, try querying raw backup or complete manually
        throw new Error("Streaming session completed but no task outcomes returned by server.");
      }

      return lastSuccessResult;

    } catch (err: any) {
      console.error(`Execution error on node ${nodeId}:`, err);
      
      setNodes((prev) =>
        prev.map((n) => {
          if (n.id === nodeId) {
            return {
              ...n,
              status: "error",
              logs: [...n.logs, `[ERROR]: Exec aborted. Reason: ${err.message}`]
            };
          }
          return n;
        })
      );
      return null;
    }
  };

  // Sequentially run all nodes matching dependency hierarchy levels
  const handleRunFullPipeline = async () => {
    setIsPipelineRunning(true);
    
    try {
      // 1. Reset statuses of all nodes to idle / queue
      volatileResultsRef.current = {};
      setNodes((prev) =>
        prev.map((n) => ({
          ...n,
          status: "idle",
          logs: [...n.logs, "[SYSTEM]: Queue reset, starting automated workspace pipeline run."]
        }))
      );

      // We implement a reactive topological scheduler. 
      // Steps are executed dynamically. 
      // In every step, we look for nodes that are "idle", but whose active connected parent nodes are all "completed".
      // We run those in parallel or sequential until no more steps can progress.
      
      let progressMade = true;
      const completedIds = new Set<string>();

      while (progressMade) {
        progressMade = false;

        const activeNodes = nodesRef.current;
        const activeConnections = connectionsRef.current;

        // Find candidate nodes: nodes that are not completed, and all their parents are completed.
        const candidates = activeNodes.filter((n) => {
          if (completedIds.has(n.id)) return false;

          // Find connections leading to this candidate node
          const parentsLinks = activeConnections.filter((c) => c.targetId === n.id);
          
          // Confirms that all parent steps are completed
          const allParentsDone = parentsLinks.every((link) => completedIds.has(link.sourceId));
          
          return allParentsDone;
        });

        if (candidates.length > 0) {
          progressMade = true;
          
          // The user specifically asked: "if more than one agent get input from a agent, they need to be initialaized at the same time."
          // So we should run them in parallel.
          console.log(`Pipeline Scheduler: Dispatching parallel run on: ${candidates.map(c => c.name).join(", ")}`);
          
          const outcomes = await Promise.all(candidates.map(cand => executeSingleNode(cand.id, true)));
          
          let allSuccess = true;
          for (let i = 0; i < candidates.length; i++) {
            if (outcomes[i]) {
              completedIds.add(candidates[i].id);
            } else {
              console.warn(`Scheduler paused on error inside task: ${candidates[i].name}. Downstream execution blocked.`);
              allSuccess = false;
            }
          }
          
          if (!allSuccess) {
            progressMade = false;
            break;
          }
        }
      }

      console.log("Pipeline run complete.");

    } catch (err: any) {
      console.error("Workspace Pipeline crash:", err);
    } finally {
      setIsPipelineRunning(false);
    }
  };

  // --- PRE SET CONFIGURATION LIFECYCLE CHANGER ---

  const loadPresetTemplate = (name: string) => {
    if (name === "Earthquake") {
      const id1 = `agent_eq_retrieval`;
      const id2 = `agent_eq_map_app`;

      const node1: AgentNode = {
        id: id1,
        agentId: "usgs_earthquake_agent",
        name: "Fetch Live Seismology Feed",
        x: 100,
        y: 180,
        instructions: "Extract coordinates, magnitudes, and details from the USGS Earthquake API representing magnitude 4.5+ earthquakes globally over the past 24 hours. Outputs a GeoJSON layer.",
        inputDatasets: [],
        credentials: {},
        status: "completed",
        logs: ["[SYSTEM]: Seismology Node pre-loaded.", "[SUCCESS]: Preset configuration cached successfully."],
        results: {
          task_id: "example_task_1",
          outputs: {
            artifacts: [
              {
                name: "usgs_global_earthquakes.geojson",
                format: "geojson",
                url: "https://www.geospatial-agentic-services.online/artifacts/usgs_global_earthquakes.geojson",
                description: "Clean spatial vector layer representing worldwide tectonic events."
              }
            ]
          }
        }
      };

      const node2: AgentNode = {
        id: id2,
        agentId: "web_mapping_app_agent",
        name: "Build Leaflet Map Viewer",
        x: 520,
        y: 180,
        instructions: "Render a dark midnight visual theme base layer. Embed the earthquake GeoJSON. Bind popups explaining dates and rich event descriptions. Scale marker circle radius proportional to the magnitude metrics.",
        inputDatasets: [],
        credentials: {},
        status: "idle",
        logs: ["[SYSTEM]: Leaflet Builder pre-loaded. Click Execute or Link output."],
        results: {
          task_id: "example_task_2",
          outputs: {
            artifacts: [
              {
                name: "interactive_earthquakes_dashboard.html",
                format: "html",
                url: "https://www.geospatial-agentic-services.online/artifacts/interactive_earthquakes_dashboard.html",
                description: "Responsive browser deployment with geographic layers."
              }
            ]
          }
        }
      };

      const link: NodeConnection = {
        id: `link_eq_pipeline`,
        sourceId: id1,
        targetId: id2
      };

      setNodes([node1, node2]);
      setConnections([link]);
      setSelectedNodeId(id2);
    } else if (name === "PA Buffering") {
      const id1 = `agent_hyd_retrieval`;
      const id2 = `agent_hyd_buffer`;
      const id3 = `agent_hyd_map`;

      const node1: AgentNode = {
        id: id1,
        agentId: "geospatial_data_retrieval_agent",
        name: "Get Fire Hydrants GIS",
        x: 80,
        y: 80,
        instructions: "Download Pennsylvania GIS shapefiles representing fire hydrants and critical public facilities databases inside Centre County, PA.",
        inputDatasets: [],
        credentials: {},
        status: "completed",
        logs: ["[SYSTEM]: Retrieval Agent online."],
        results: {
          task_id: "hyd_task_1",
          outputs: {
            artifacts: [
              {
                name: "centre_county_hydrants.geojson",
                format: "geojson",
                url: "https://www.geospatial-agentic-services.online/artifacts/centre_county_hydrants.geojson",
                description: "Geographic point coordinates vector file."
              }
            ]
          }
        }
      };

      const node2: AgentNode = {
        id: id2,
        agentId: "spatial_analysis_agent",
        name: "Proximity Buffer",
        x: 440,
        y: 120,
        instructions: "Apply standard 1000-ft (300-meter) buffer regions surrounding the point coordinates to outline hydrant service safety limits.",
        inputDatasets: [],
        credentials: {},
        status: "idle",
        logs: ["[SYSTEM]: Waiting for dataset stream."],
        results: {
          task_id: "hyd_task_2",
          outputs: {
            artifacts: [
              {
                name: "hydrant_safe_buffers.geojson",
                format: "geojson",
                url: "https://www.geospatial-agentic-services.online/artifacts/hydrant_safe_buffers.geojson"
              }
            ]
          }
        }
      };

      const node3: AgentNode = {
        id: id3,
        agentId: "mapping_agent",
        name: "Plot Static Map",
        x: 780,
        y: 200,
        instructions: "Generate high contrast overlay print layouts containing polygons representations, transparent color shapes, road maps, and coordinates grid.",
        inputDatasets: [],
        credentials: {},
        status: "idle",
        logs: ["[SYSTEM]: Idle workflow steps."]
      };

      const link1: NodeConnection = {
        id: `link_hyd_1`,
        sourceId: id1,
        targetId: id2
      };

      const link2: NodeConnection = {
        id: `link_hyd_2`,
        sourceId: id2,
        targetId: id3
      };

      setNodes([node1, node2, node3]);
      setConnections([link1, link2]);
      setSelectedNodeId(id1);
    } else {
      // Conflict
      const id1 = `agent_conflict_event`;
      const id2 = `agent_stats_analysis`;

      const node1: AgentNode = {
        id: id1,
        agentId: "spatiotemporal_conflict_event_agent",
        name: "Query Conflict Events",
        x: 120,
        y: 180,
        instructions: "Extract regional safety incidents, spatiotemporal events, and coordinates bounds spanning years 2024-2025.",
        inputDatasets: [],
        credentials: {},
        status: "completed",
        logs: ["[SYSTEM]: Conflict database synced."],
        results: {
          task_id: "conf_1",
          outputs: {
            artifacts: [
              {
                name: "national_conflict_layer.geojson",
                format: "geojson",
                url: "https://www.geospatial-agentic-services.online/artifacts/national_conflict_layer.geojson"
              }
            ]
          }
        }
      };

      const node2: AgentNode = {
        id: id2,
        agentId: "spatial_statistics_agent",
        name: "Clustering Autocorrelation",
        x: 520,
        y: 180,
        instructions: "Execute Local Moran's I (LISA) analysis on point counts to identify statistically significant clusters and cold-spot boundaries.",
        inputDatasets: [],
        credentials: {},
        status: "idle",
        logs: ["[SYSTEM]: Ready."]
      };

      const link: NodeConnection = {
        id: `link_conf`,
        sourceId: id1,
        targetId: id2
      };

      setNodes([node1, node2]);
      setConnections([link]);
      setSelectedNodeId(id2);
    }
  };

  // --- SAVE / LOAD LIFECYCLE ---

  const handleSaveWorkflow = () => {
    setSavePromptOpen(true);
  };
  
  const submitSaveWorkflow = (name: string) => {
    if (!name.trim()) return;

    const newSaved: SavedWorkflow = {
      id: `workflow_${Date.now()}`,
      name,
      description: "Custom user-generated drag-and-drop workflow graph saved locally.",
      nodes,
      connections,
      createdAt: new Date().toISOString()
    };

    const existingWorkflows = localStorage.getItem(STORAGE_KEYS.WORKFLOWS);
    let masterList: SavedWorkflow[] = [];
    if (existingWorkflows) {
      try {
        masterList = JSON.parse(existingWorkflows);
      } catch (e) {}
    }

    masterList.push(newSaved);
    localStorage.setItem(STORAGE_KEYS.WORKFLOWS, JSON.stringify(masterList));
    updateSavedWorkflowsList();
    setSavePromptOpen(false);
    setWorkflowNameInput("");
    showToast(`Successfully stored your pipeline "${name}" into system standard local memory cache!`);
  };

  const handleLoadWorkflow = (id: string) => {
    const rawWorkflows = localStorage.getItem(STORAGE_KEYS.WORKFLOWS);
    if (!rawWorkflows) return;

    try {
      const list: SavedWorkflow[] = JSON.parse(rawWorkflows);
      const match = list.find((item) => item.id === id);
      if (match) {
        setNodes(match.nodes);
        setConnections(match.connections);
        if (match.nodes.length > 0) {
          setSelectedNodeId(match.nodes[0].id);
        }
        showToast(`Successfully loaded saved pipeline: "${match.name}"`);
      }
    } catch (e) {
      console.error(e);
    }
  };

  // Clear Canvas
  const handleClearCanvas = () => {
    setNodes([]);
    setConnections([]);
    setSelectedNodeId(null);
    setSelectedConnectionId(null);
    showToast("Workspace wiped cleanly.");
  };

  // Auto Layout
  const handleAutoLayout = () => {
    if (nodes.length === 0) {
      showToast("Canvas is empty. Nothing to layout.");
      return;
    }

    // Calculate depths
    const nodeDepths = new Map<string, number>();
    
    // Find roots (nodes with no incoming connections)
    const incomingCounts = new Map<string, number>();
    nodes.forEach(n => incomingCounts.set(n.id, 0));
    connections.forEach(c => {
      const current = incomingCounts.get(c.targetId) || 0;
      incomingCounts.set(c.targetId, current + 1);
    });

    let queue = nodes.filter(n => (incomingCounts.get(n.id) || 0) === 0);
    if (queue.length === 0) {
      // Cycle or just take arbitrary node
      queue = [nodes[0]];
    }

    queue.forEach(n => nodeDepths.set(n.id, 0));

    // Simple BFS for depths
    while (queue.length > 0) {
      const current = queue.shift()!;
      const currentDepth = nodeDepths.get(current.id) || 0;

      const outgoing = connections.filter(c => c.sourceId === current.id);
      outgoing.forEach(c => {
        const nextNode = nodes.find(n => n.id === c.targetId);
        if (nextNode) {
          const nextDepth = nodeDepths.get(nextNode.id) || 0;
          if (currentDepth + 1 > nextDepth) {
            nodeDepths.set(nextNode.id, currentDepth + 1);
            queue.push(nextNode); // allow revising max depth
          }
        }
      });
    }

    // fallback for disconnected graphs or nodes not visited
    nodes.forEach(n => {
      if (!nodeDepths.has(n.id)) {
         nodeDepths.set(n.id, 0);
      }
    });

    // Group by depth
    const nodesByDepth = new Map<number, string[]>();
    nodes.forEach(n => {
      const d = nodeDepths.get(n.id)!;
      if (!nodesByDepth.has(d)) nodesByDepth.set(d, []);
      nodesByDepth.get(d)!.push(n.id);
    });

    // Assign positions
    const nodeWidth = 320;
    const nodeHeight = 280;
    const spacingX = 400;
    const spacingY = 320;
    
    const newNodes = [...nodes];

    let minX = Infinity;
    let maxX = -Infinity;
    let minY = Infinity;
    let maxY = -Infinity;

    nodesByDepth.forEach((nodeIds, depth) => {
       const startY = -((nodeIds.length - 1) * spacingY) / 2;
       nodeIds.forEach((id, index) => {
         const nodeIndex = newNodes.findIndex(n => n.id === id);
         if (nodeIndex !== -1) {
            const posX = 50 + depth * spacingX;
            const posY = startY + index * spacingY;
            newNodes[nodeIndex] = {
               ...newNodes[nodeIndex],
               x: posX,
               y: posY
            };

            minX = Math.min(minX, posX);
            maxX = Math.max(maxX, posX + nodeWidth);
            minY = Math.min(minY, posY);
            maxY = Math.max(maxY, posY + nodeHeight);
         }
       });
    });

    // Shift all nodes so that minY starts at a comfortable 100px padding
    const yOffset = minY < 100 ? 100 - minY : 0;
    const finalNodes = newNodes.map(n => ({
      ...n,
      y: n.y + yOffset
    }));

    setNodes(finalNodes);

    if (canvasRef.current && minX !== Infinity) {
      // Find the scroll container (parent of canvasRef)
      const container = canvasRef.current.parentElement;
      if (container) {
        container.scrollTo({ top: 0, left: 0, behavior: 'smooth' });
      }

      const padding = 100;
      const graphW = (maxX - minX) + padding * 2;
      const graphH = (maxY - minY) + padding * 2;
      
      const canvasW = canvasRef.current.parentElement?.clientWidth || window.innerWidth;
      const canvasH = canvasRef.current.parentElement?.clientHeight || window.innerHeight;

      // Ensure graph fits in view area
      const zoomX = canvasW / Math.max(1, graphW);
      const zoomY = canvasH / Math.max(1, graphH);
      
      const newZoom = Math.min(Math.max(0.4, Math.min(zoomX, zoomY)), 1.2);
      setZoom(newZoom);
    }
    
    showToast("Canvas auto-arranged and zoomed to fit.");
  };

  // Credentials changes
  const handleSaveCredentials = (keys: { OPENAI_API_KEY: string }) => {
    setCredentials(keys);
    localStorage.setItem(STORAGE_KEYS.CREDENTIALS, JSON.stringify(keys));
    showToast("Saved API keys local overrides successfully! They'll authorize upcoming stream jobs.");
  };

  // Render variables helper
  const selectedNode = nodes.find((n) => n.id === selectedNodeId) || null;

  return (
    <div className="flex flex-col h-screen w-screen overflow-hidden bg-neutral-100 font-sans text-neutral-800">
      
      {/* GLOBAL MASTER HEADER BAR */}
      <header className="h-14 shrink-0 bg-neutral-900 text-white flex items-center justify-between px-4 border-b border-neutral-800/80 z-20">
        <div className="flex items-center space-x-3">
          <div className="p-2 bg-sky-650 rounded-xl text-white">
            <Network className="w-5 h-5 text-sky-400 rotate-45" />
          </div>
          <div>
            <h1 className="text-sm font-bold tracking-tight text-white flex items-center space-x-1.5">
              <span>GAS Canvas</span>
            </h1>
            <p className="text-[10px] text-neutral-400 leading-normal">
              Geospatial Agentic Services
            </p>
          </div>
        </div>

        {/* Global Action buttons settings */}
        <div className="flex items-center space-x-3 text-sm">
          {/* Instructions disclaimer info banner */}
          <div className="hidden lg:flex items-center space-x-1.5 text-[11px] text-neutral-400 bg-neutral-800/60 border border-neutral-800 rounded px-2.5 py-1">
            <Info className="w-3.5 h-3.5 text-sky-400 shrink-0" />
            <span>Connect agents to form pipelines.</span>
          </div>

          <button
            onClick={() => setIsCredentialsOpen(true)}
            className="flex items-center space-x-1 px-3 py-1.5 bg-neutral-800 hover:bg-neutral-700/80 rounded-lg text-xs font-semibold text-white transition-colors"
          >
            <Key className="w-3.5 h-3.5 text-amber-400" />
            <span>Browser Keys Vault</span>
          </button>
        </div>
      </header>

      {/* WORKFLOW TOOLBAR */}
      <CanvasControls
        onClearCanvas={handleClearCanvas}
        onRunFullPipeline={handleRunFullPipeline}
        isPipelineRunning={isPipelineRunning}
        onLoadPreset={loadPresetTemplate}
        onSaveWorkflow={handleSaveWorkflow}
        onLoadWorkflow={handleLoadWorkflow}
        savedWorkflows={savedWorkflowsList}
        zoom={zoom}
        onZoomIn={() => setZoom(z => Math.min(1.5, z + 0.1))}
        onZoomOut={() => setZoom(z => Math.max(0.3, z - 0.1))}
        onResetZoom={() => setZoom(1)}
        onAutoLayout={handleAutoLayout}
      />

      {/* CORE SPLIT WORKSPACE BODY */}
      <div className="flex-1 flex overflow-hidden min-h-0 relative">
        
        {/* LEFTSIDEBAR: ALL AGENT TEMPLATES */}
        <SidebarPanel
          onAddAgent={handleAddAgentNode}
          onDescribeAgent={handleDescribeAgent}
          servers={servers}
          onAddServer={handleAddServer}
          onRemoveServer={handleRemoveServer}
          onToggleServer={handleToggleServer}
          isSyncingServer={isSyncingServer}
          width={sidebarWidth}
          selectedAgentId={selectedNodeId ? nodes.find(n => n.id === selectedNodeId)?.agentId : null}
        />

        {/* RESIZER DRAG HANDLE LEFT */}
        <div 
          onPointerDown={handleSidebarResize}
          className="w-1.5 hover:bg-sky-400 hover:w-2 active:bg-sky-500 cursor-col-resize shrink-0 bg-neutral-200 dark:bg-neutral-800 transition-colors z-30 flex items-center justify-center group" 
        >
          <div className="h-8 w-0.5 bg-neutral-400 dark:bg-neutral-600 rounded-full group-hover:bg-white" />
        </div>

        {/* INTERACTIVE GRID CANVAS CONTAINER */}
        <div 
          className="flex-1 overflow-auto relative bg-neutral-100 dark:bg-neutral-950/60 relative focus:outline-none flex min-w-0"
          onPointerMove={handleCanvasPointerMove}
          onPointerUp={handleCanvasPointerUp}
          onPointerDown={(e) => {
            if (e.target === e.currentTarget) {
              setSelectedNodeId(null);
              setSelectedDescribeAgentInfo(null);
              setSelectedConnectionId(null);
            }
          }}
          style={{ cursor: dragNodeId ? "grabbing" : "default" }}
        >
          {/* Dot Grid Background */}
          <div 
            ref={canvasRef}
            className="absolute inset-0 min-w-[8000px] min-h-[8000px] bg-[radial-gradient(#d4d4d8_1px,transparent_1px)] [background-size:20px_20px] dark:bg-[radial-gradient(#1e1e24_1px,transparent_1px)]"
            style={{
              transform: `scale(${zoom})`,
              transformOrigin: "top left",
              transition: dragNodeId ? "none" : "transform 0.1s ease-out"
            }}
            onPointerDown={(e) => {
              if (e.target === e.currentTarget) {
                setSelectedNodeId(null);
                setSelectedDescribeAgentInfo(null);
                setSelectedConnectionId(null);
              }
            }}
          >
            {/* SVG LINK REPRESENTATIONS */}
            <svg className="absolute inset-0 w-full h-full pointer-events-none stroke-neutral-500">
              <defs>
                <marker
                  id="arrow"
                  viewBox="0 0 10 10"
                  refX="6"
                  refY="5"
                  markerWidth="6"
                  markerHeight="6"
                  orient="auto-start-reverse"
                >
                  <path d="M 0 0 L 10 5 L 0 10 z" fill="#0ea5e9" />
                </marker>
              </defs>

              {/* Connected Beziers */}
              {connections.map((link) => {
                const source = nodes.find((n) => n.id === link.sourceId);
                const target = nodes.find((n) => n.id === link.targetId);
                if (!source || !target) return null;

                // Source output is on the right edge of the card, target input on the left
                const x1 = source.x + 280;
                const y1 = source.y + 115;
                const x2 = target.x;
                const y2 = target.y + 115;

                // Bezier calculation
                const ctrlX1 = x1 + 100;
                const ctrlY1 = y1;
                const ctrlX2 = x2 - 100;
                const ctrlY2 = y2;

                return (
                  <g 
                    key={link.id} 
                    className="group pointer-events-auto cursor-pointer"
                    onPointerDown={(e) => {
                      e.stopPropagation();
                      setSelectedConnectionId(link.id);
                      setSelectedNodeId(null);
                      setSelectedDescribeAgentInfo(null);
                    }}
                  >
                    {/* Hover and Selection highlights thick guideline helper */}
                    <path
                      d={`M ${x1} ${y1} C ${ctrlX1} ${ctrlY1}, ${ctrlX2} ${ctrlY2}, ${x2} ${y2}`}
                      stroke={selectedConnectionId === link.id ? "#ef4444" : "transparent"}
                      strokeWidth={selectedConnectionId === link.id ? "10" : "20"}
                      fill="none"
                      className="hover:stroke-red-500/50 transition-colors"
                      style={{ opacity: selectedConnectionId === link.id ? 0.3 : 1 }}
                    />
                    
                    {/* Glowing active path connection */}
                    <path
                      d={`M ${x1} ${y1} C ${ctrlX1} ${ctrlY1}, ${ctrlX2} ${ctrlY2}, ${x2} ${y2}`}
                      stroke={selectedConnectionId === link.id ? "#ef4444" : "#0ea5e9"}
                      strokeWidth="3.5"
                      strokeDasharray={source.status === "running" ? "8 6" : "none"}
                      className={source.status === "running" ? "animate-dash" : ""}
                      fill="none"
                      markerEnd={selectedConnectionId === link.id ? "" : "url(#arrow)"}
                    />
                  </g>
                );
              })}

              {/* Connecting draft curve during active drag connect */}
              {connectionDraft && !connectionDraft.artifactName && (
                (() => {
                  const draftNode = nodes.find((n) => n.id === connectionDraft.nodeId);
                  if (!draftNode) return null;

                  // Default port-to-port dashed connector
                  const isOutput = connectionDraft.type === "output";
                  const x1 = isOutput ? draftNode.x + 280 : draftNode.x;
                  const y1 = draftNode.y + 115;
                  const x2 = pointerPos.x;
                  const y2 = pointerPos.y;

                  const ctrlX1 = isOutput ? x1 + 100 : x1 - 100;
                  const ctrlY1 = y1;
                  const ctrlX2 = isOutput ? x2 - 100 : x2 + 100;
                  const ctrlY2 = y2;

                  return (
                    <path
                      d={`M ${x1} ${y1} C ${ctrlX1} ${ctrlY1}, ${ctrlX2} ${ctrlY2}, ${x2} ${y2}`}
                      stroke="#0ea5e9"
                      strokeWidth="2.5"
                      strokeDasharray="6 6"
                      fill="none"
                    />
                  );
                })()
              )}
            </svg>

            {/* FLOATING AGENT INTERACTIVE CARDS */}
            {nodes.map((node) => {
              const parentLinks = connections.filter((c) => c.targetId === node.id);
              const parentDatasetUrls: { name: string, url: string, type: 'dynamic', sourceId: string }[] = [];
              parentLinks.forEach((link) => {
                const parentNode = nodes.find((n) => n.id === link.sourceId);
                const recentResult = volatileResultsRef.current[link.sourceId];
                const artifactsSource = recentResult?.outputs?.artifacts || parentNode?.results?.outputs?.artifacts || [];

                artifactsSource.forEach((art, idx) => {
                  const artName = art.name || `Output ${idx + 1}`;
                  if (!link.artifacts || link.artifacts.length === 0 || link.artifacts.includes(artName)) {
                    if (art.url) parentDatasetUrls.push({ name: artName, url: art.url, type: 'dynamic', sourceId: link.sourceId });
                  }
                });
              });
              const combinedInputs = [...node.inputDatasets.map(url => ({ name: 'Manual Input Dataset', url, type: 'manual' as const, sourceId: '' })), ...parentDatasetUrls].filter(item => !node.excludedInputs?.includes(item.url));

              return (
              <AgentNodeCard
                key={node.id}
                node={node}
                inputDatasetsList={combinedInputs}
                onRemoveInput={(dataset) => {
                  setNodes(prev => prev.map(n => {
                    if (n.id === node.id) {
                      if (dataset.type === 'manual') {
                        return { ...n, inputDatasets: n.inputDatasets.filter(url => url !== dataset.url) };
                      } else {
                        return { ...n, excludedInputs: [...(n.excludedInputs || []), dataset.url] };
                      }
                    }
                    return n;
                  }));
                }}
                isSelected={node.id === selectedNodeId}
                onSelect={() => {
                  setSelectedNodeId(node.id);
                  setSelectedDescribeAgentInfo(null);
                  setSelectedConnectionId(null);
                }}
                onDelete={() => handleDeleteNode(node.id)}
                onExecute={() => executeSingleNode(node.id)}
                onPointerDown={(e) => handleNodePointerDown(node.id, e)}
                onNodePointerUp={(nodeId) => handlePortPointerUp(nodeId, "input")}
                // @ts-ignore
                onPortPointerDown={(nodeId, type, e, artifactName) => handlePortPointerDown(nodeId, type, e, artifactName)}
                onPortPointerUp={(nodeId, type) => handlePortPointerUp(nodeId, type)}
                isConnectingSource={connectionDraft?.nodeId === node.id && connectionDraft?.type === "output"}
                isConnectingTarget={connectionDraft?.nodeId === node.id && connectionDraft?.type === "input"}
                isDrafting={connectionDraft !== null}
                draftType={connectionDraft?.type}
              />
            );})}
          </div>

          {/* Floating Artifact Drag Preview */}
          {connectionDraft && connectionDraft.artifactName && (
            <div 
              style={{ 
                position: 'absolute', 
                left: pointerPos.x * zoom + 15, // Offset from pointer slightly 
                top: pointerPos.y * zoom + 15,
                zIndex: 9999,
                pointerEvents: 'none'
              }}
              className="bg-emerald-50 dark:bg-emerald-950/90 border border-emerald-400 dark:border-emerald-600 rounded shadow-lg p-1.5 flex items-center space-x-1.5 opacity-90 select-none max-w-[180px]"
            >
              <div className="w-4 h-4 text-emerald-600 dark:text-emerald-400 shrink-0">
                 <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z"/><polyline points="14 2 14 8 20 8"/></svg>
              </div>
              <span className="text-[10px] text-emerald-800 dark:text-emerald-200 font-medium truncate leading-none pt-[1px]">{connectionDraft.artifactName}</span>
            </div>
          )}

          {/* Quick empty canvas action helper board */}
          {nodes.length === 0 && (
            <div className="absolute inset-0 flex items-center justify-center p-8 pointer-events-none select-none">
              <div className="text-center space-y-4 max-w-sm bg-white/70 dark:bg-neutral-900/60 backdrop-blur-md p-6 rounded-2xl border border-neutral-200/50 shadow-sm">
                <Network className="w-12 h-12 text-sky-400 mx-auto animate-bounce-slow" />
                <h3 className="text-sm font-bold text-neutral-800 dark:text-neutral-100">
                  Empty Agent Grid Canvas
                </h3>
                <p className="text-xs text-neutral-500 leading-relaxed">
                  Start building your spatial pipeline: click the <strong>Templates presets</strong> above to load a layout, or click on and add individual agents from the <strong>Left Agent Template sidebar palette</strong>!
                </p>
                <div className="flex items-center justify-center space-x-1.5 text-[10px] text-sky-650 font-bold">
                  <span>Powering GIS AI Workflows</span>
                </div>
              </div>
            </div>
          )}
        </div>

        {/* RESIZER DRAG HANDLE RIGHT */}
        <div 
          onPointerDown={handleInspectorResize}
          className="w-1.5 hover:bg-sky-400 hover:w-2 active:bg-sky-500 cursor-col-resize shrink-0 bg-neutral-200 dark:bg-neutral-800 transition-colors z-30 flex items-center justify-center group" 
        >
          <div className="h-8 w-0.5 bg-neutral-400 dark:bg-neutral-600 rounded-full group-hover:bg-white" />
        </div>

        {/* REGION: WORKFLOW VARIABLES & INSPECTOR PANEL */}
        {selectedDescribeAgentInfo ? (
          <AgentDescribePanel 
            agentInfo={selectedDescribeAgentInfo} 
            onClose={() => setSelectedDescribeAgentInfo(null)} 
            width={inspectorWidth} 
          />
        ) : (
          <InspectorPanel
            selectedNode={selectedNode}
            connections={connections}
            nodes={nodes}
            servers={servers}
            onUpdateNode={(nodeId, updates) => {
              setNodes((prev) =>
                prev.map((n) => (n.id === nodeId ? { ...n, ...updates } : n))
              );
            }}
            onClose={() => setSelectedNodeId(null)}
            onExecuteNode={executeSingleNode}
            onOpenPreview={(url, title) => {
              setPreviewData({
                isOpen: true,
                url,
                title
              });
            }}
            width={inspectorWidth}
          />
        )}

      </div>

      {/* CREDENTIALS CONFIG OVERLAY MODAL */}
      <CredentialsVault
        isOpen={isCredentialsOpen}
        onClose={() => setIsCredentialsOpen(false)}
        initialKeys={credentials}
        onSave={handleSaveCredentials}
      />

      {/* DYNAMIC IFRAME WEB MAP PREVIEW DOCK MODAL */}
      <LivePreviewModal
        isOpen={previewData.isOpen}
        onClose={() => setPreviewData({ ...previewData, isOpen: false })}
        url={previewData.url}
        title={previewData.title}
      />

      {/* SAVE PROMPT MODAL */}
      {savePromptOpen && (
        <div className="fixed inset-0 bg-black/60 z-[100] flex items-center justify-center p-4">
          <div className="bg-white dark:bg-neutral-900 rounded-xl shadow-2xl p-6 max-w-sm w-full border border-neutral-200 dark:border-neutral-800">
            <h3 className="text-lg font-bold mb-4">Save Pipeline Workflow</h3>
            <p className="text-xs text-neutral-500 mb-4">Enter a descriptive name to store this node layout graph locally.</p>
            <input 
              type="text" 
              autoFocus
              className="w-full px-3 py-2 bg-neutral-100 dark:bg-neutral-850 dark:text-white rounded border border-neutral-300 dark:border-neutral-700 text-sm mb-5 focus:outline-none focus:ring-2 focus:ring-sky-500" 
              placeholder="e.g. My Custom Analysis..." 
              value={workflowNameInput} 
              onChange={(e) => setWorkflowNameInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') submitSaveWorkflow(workflowNameInput);
                if (e.key === 'Escape') setSavePromptOpen(false);
              }}
            />
            <div className="flex justify-end space-x-2">
              <button onClick={() => setSavePromptOpen(false)} className="px-4 py-1.5 text-xs font-semibold rounded hover:bg-neutral-100 dark:hover:bg-neutral-800 transition-colors">Cancel</button>
              <button 
                onClick={() => submitSaveWorkflow(workflowNameInput)} 
                disabled={!workflowNameInput.trim()}
                className="px-4 py-1.5 text-xs font-semibold rounded bg-sky-500 text-white hover:bg-sky-600 disabled:opacity-50 transition-colors"
              >
                Save
              </button>
            </div>
          </div>
        </div>
      )}

      {/* TOAST SYSTEM ALERTS */}
      {toastMsg && (
        <div className="fixed bottom-6 left-1/2 transform -translate-x-1/2 z-[200] bg-neutral-800 text-white px-6 py-3 rounded-full shadow-2xl text-xs font-medium flex items-center space-x-2 animate-in fade-in slide-in-from-bottom-4 duration-300">
          <div className="w-2 h-2 rounded-full bg-green-400 animate-pulse" />
          <span>{toastMsg}</span>
        </div>
      )}

    </div>
  );
}
