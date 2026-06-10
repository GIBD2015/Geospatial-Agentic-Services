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
  ChevronLeft,
  ChevronRight,
  Sparkles,
  ServerCrash,
  Github,
  Map as MapIcon,
  FileCode,
  FileText,
  Files,
  Trash2,
  Save,
  FolderOpen,
  Upload,
  ZoomIn,
  ZoomOut,
  RotateCcw,
  LayoutGrid,
  Maximize2,
  X,
  Copy,
  ClipboardPaste,
  CopyPlus,
  Play,
  Pencil
} from "lucide-react";
import { AgentNode, NodeConnection, SavedWorkflow, TaskResult, TaskArtifact } from "./types";
import { SidebarPanel, AGENT_TEMPLATES } from "./components/SidebarPanel";
import { InspectorPanel } from "./components/InspectorPanel";
import { AgentDescribePanel } from "./components/AgentDescribePanel";
import { CanvasControls } from "./components/CanvasControls";
import { AgentNodeCard, getAgentCategory } from "./components/AgentNodeCard";
import { MapView } from "./components/MapView";
import { HtmlView } from "./components/HtmlView";
import { ArtifactsView } from "./components/ArtifactsView";
import { CredentialsVault } from "./components/CredentialsVault";
import { determineRelevantArtifacts } from "./lib/llmRouting";
import {
  artifactMatchesSelection,
  getArtifactFilename,
  getArtifactPreviewTitle,
  getArtifactHoverText,
  normalizeTaskArtifacts,
  normalizeTaskResultArtifacts
} from "./lib/artifacts";

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

const getArtifactExtension = (url: string, title: string) => {
  const fromTitle = title.split(".").pop()?.toLowerCase();
  if (fromTitle && fromTitle !== title.toLowerCase()) return fromTitle;

  try {
    const pathname = new URL(url, window.location.href).pathname;
    return pathname.split(".").pop()?.toLowerCase() || "";
  } catch {
    return url.split("?")[0].split(".").pop()?.toLowerCase() || "";
  }
};

const isSpatialArtifact = (url: string, title: string) =>
  ["geojson", "gpkg"].includes(getArtifactExtension(url, title));

const isHtmlArtifact = (url: string, title: string) =>
  ["html", "htm"].includes(getArtifactExtension(url, title));

const DEFAULT_TASK_INSTRUCTIONS = "No task instructions defined yet. Double click to edit.";
const CANVAS_SIZE = 8000;
const NODE_CARD_WIDTH = 280;
const NODE_CARD_HEIGHT = 260;

const isPlaceholderInstruction = (instructions: string) =>
  !instructions.trim() || instructions.trim() === DEFAULT_TASK_INSTRUCTIONS;

const getTaskResultError = (result: TaskResult & { success?: boolean }) => {
  const status = String(result.status || "").toLowerCase();
  if (result.error) return result.error;
  if (result.success === false) return "Agent returned an unsuccessful task result.";
  if (["failed", "failure", "error", "cancelled", "canceled"].includes(status)) {
    return `Agent returned status: ${result.status}`;
  }
  return "";
};

const formatDisplayValue = (value: any) => {
  if (value === null || value === undefined || value === "") return "-";
  if (Array.isArray(value) && value.length === 0) return "-";
  if (typeof value === "object" && Object.keys(value).length === 0) return "-";
  if (typeof value === "number") return value.toLocaleString();
  return String(value);
};

const formatErrorDetail = (value: any) => {
  if (value instanceof Error) return value.message;
  if (typeof value === "string") return value;
  if (value === null || value === undefined) return "";
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
};

const streamEventTime = (event: any) => {
  const timestamp = event?.timestamp;
  const date = timestamp ? new Date(timestamp) : new Date();
  if (Number.isNaN(date.getTime())) return String(timestamp || "--:--:--");
  return date.toLocaleTimeString([], { hour12: false });
};

const displayAgentNameFromEvent = (event: any, fallbackName?: string) => {
  if (event?._display_agent_name) return String(event._display_agent_name);
  const agent = event?.agent && typeof event.agent === "object" ? event.agent : {};
  return agent.name || agent.id || fallbackName;
};

const formatStreamMessage = (event: any, displayAgentName?: string) => {
  const payload = event?.payload && typeof event.payload === "object" ? event.payload : {};
  const message = String(event?.message || payload.message || event?.status || payload.status || "");
  if (event?.event !== "progress") return message;

  if (message.startsWith("The user wants help from ")) return "I received your request.";
  if (message.includes("is still working. Long LLM calls")) {
    return "I am still working. Long LLM calls, code execution, or geospatial file processing can take a little while.";
  }
  if (displayAgentName && message === `The ${displayAgentName} reported a workflow update.`) {
    return "I reported a workflow update.";
  }
  return message;
};

const formatStreamLogLine = (event: any, fallbackAgentName?: string) => {
  const eventType = event?.event || "INFO";
  const timeText = streamEventTime(event);
  const displayAgentName = displayAgentNameFromEvent(event, fallbackAgentName);
  const message = formatStreamMessage(event, displayAgentName);

  if (eventType === "task_result") {
    const payload = event?.payload && typeof event.payload === "object" ? event.payload : {};
    const task = payload.task && typeof payload.task === "object" ? payload.task : {};
    const taskId = task.id || payload.task_id || payload.id || "";
    return `[${timeText}] task_result: final task received ${taskId}`.trim();
  }

  if (eventType === "stream_done" && !message) {
    return `[${timeText}] stream_done: Streaming session closed.`;
  }

  const label = eventType === "progress" && displayAgentName ? displayAgentName : formatDisplayValue(eventType);
  return `[${timeText}] ${label}: ${message}`.trim();
};

const formatLocalLogLine = (label: string, message: string) =>
  `[${new Date().toLocaleTimeString([], { hour12: false })}] ${label}: ${message}`;

const extractTaskIdFromEvent = (event: any) => {
  const payload = event?.payload && typeof event.payload === "object" ? event.payload : {};
  const task = payload.task && typeof payload.task === "object" ? payload.task : {};
  return (
    event?.task_id ||
    event?.taskId ||
    event?.id ||
    payload.task_id ||
    payload.taskId ||
    payload.id ||
    task.id ||
    ""
  );
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
  const [expandedOutputNodeIds, setExpandedOutputNodeIds] = useState<Set<string>>(new Set());
  const [connectionContextMenu, setConnectionContextMenu] = useState<{
    connectionId: string;
    x: number;
    y: number;
  } | null>(null);
  const [nodeContextMenu, setNodeContextMenu] = useState<{
    nodeId: string;
    x: number;
    y: number;
  } | null>(null);
  const [canvasContextMenu, setCanvasContextMenu] = useState<{
    x: number;
    y: number;
    canvasX: number;
    canvasY: number;
  } | null>(null);
  const [copiedNode, setCopiedNode] = useState<AgentNode | null>(null);
  const [renameNodeRequest, setRenameNodeRequest] = useState<{
    nodeId: string;
    requestId: number;
  } | null>(null);
  const [editInstructionsRequest, setEditInstructionsRequest] = useState<{
    nodeId: string;
    requestId: number;
  } | null>(null);

  const deleteConnection = (connectionId: string) => {
    const nextConnections = connectionsRef.current.filter((connection) => connection.id !== connectionId);
    connectionsRef.current = nextConnections;
    setConnections(nextConnections);
    setSelectedConnectionId((currentId) => (currentId === connectionId ? null : currentId));
    setConnectionContextMenu(null);
  };

  const closeContextMenus = () => {
    setConnectionContextMenu(null);
    setNodeContextMenu(null);
    setCanvasContextMenu(null);
  };

  const copyNodeToClipboard = (nodeId: string) => {
    const node = nodesRef.current.find((item) => item.id === nodeId);
    if (!node) return;

    setCopiedNode({ ...node });
    setSelectedNodeId(node.id);
    setSelectedDescribeAgentInfo(null);
    closeContextMenus();
    showToast(`Copied ${node.name}.`);
  };

  const pasteCopiedNode = (position: { x: number; y: number }) => {
    if (!copiedNode) return;

    const newId = `agent_${Date.now()}`;
    const nextNode: AgentNode = {
      ...copiedNode,
      id: newId,
      name: `${copiedNode.name} Copy`,
      x: Math.max(0, Math.min(CANVAS_SIZE - NODE_CARD_WIDTH, position.x - NODE_CARD_WIDTH / 2)),
      y: Math.max(0, Math.min(CANVAS_SIZE - NODE_CARD_HEIGHT, position.y - 36)),
      status: "idle",
      logs: ["[SYSTEM]: Node pasted in workspace."],
      currentTaskId: undefined,
      lastRequest: undefined,
      results: undefined
    };

    setNodes((prev) => [...prev, nextNode]);
    setSelectedNodeId(newId);
    setSelectedDescribeAgentInfo(null);
    closeContextMenus();
  };

  const duplicateNode = (nodeId: string) => {
    const node = nodesRef.current.find((item) => item.id === nodeId);
    if (!node) return;

    const newId = `agent_${Date.now()}`;
    const nextNode: AgentNode = {
      ...node,
      id: newId,
      name: `${node.name} Copy`,
      x: Math.max(0, Math.min(CANVAS_SIZE - NODE_CARD_WIDTH, node.x + 36)),
      y: Math.max(0, Math.min(CANVAS_SIZE - NODE_CARD_HEIGHT, node.y + 36)),
      status: "idle",
      logs: ["[SYSTEM]: Node duplicated in workspace."],
      currentTaskId: undefined,
      lastRequest: undefined,
      results: undefined
    };

    setNodes((prev) => [...prev, nextNode]);
    setSelectedNodeId(newId);
    setSelectedDescribeAgentInfo(null);
    closeContextMenus();
  };

  const requestRenameNode = (nodeId: string) => {
    setSelectedNodeId(nodeId);
    setSelectedDescribeAgentInfo(null);
    setRenameNodeRequest({ nodeId, requestId: Date.now() });
    closeContextMenus();
  };

  const requestEditNodeInstructions = (nodeId: string) => {
    setSelectedNodeId(nodeId);
    setSelectedDescribeAgentInfo(null);
    setEditInstructionsRequest({ nodeId, requestId: Date.now() });
    closeContextMenus();
  };

  const updateOutputExpansion = (nodeId: string, expanded: boolean) => {
    setExpandedOutputNodeIds((prev) => {
      const next = new Set(prev);
      if (expanded) {
        next.add(nodeId);
      } else {
        next.delete(nodeId);
      }
      return next;
    });
  };

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;

      if ((e.key === "Delete" || e.key === "Backspace") && selectedConnectionId) {
        deleteConnection(selectedConnectionId);
      }
    };

    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [selectedConnectionId]);

  useEffect(() => {
    if (!connectionContextMenu && !nodeContextMenu && !canvasContextMenu) return;

    const closeMenu = () => closeContextMenus();
    window.addEventListener("pointerdown", closeMenu);
    window.addEventListener("blur", closeMenu);

    return () => {
      window.removeEventListener("pointerdown", closeMenu);
      window.removeEventListener("blur", closeMenu);
    };
  }, [connectionContextMenu, nodeContextMenu, canvasContextMenu]);

  // To avoid React batching update delays causing topological sort data races
  const volatileResultsRef = useRef<Record<string, TaskResult>>({});
  const runningTaskControllersRef = useRef<Record<string, AbortController>>({});
  const workflowCancelRequestedRef = useRef(false);

  // Gas servers state
  const [servers, setServers] = useState<{ url: string; providerName: string; provider?: any; agents: any[]; isExpanded?: boolean }[]>([]);
  const [isSyncingServer, setIsSyncingServer] = useState<string | null>(null);

  // Viewport / Zoom scale State
  const [zoom, setZoom] = useState(1);
  const zoomRef = useRef(zoom);
  const [sidebarWidth, setSidebarWidth] = useState(320);
  const [inspectorWidth, setInspectorWidth] = useState(320);
  const [isSidebarVisible, setIsSidebarVisible] = useState(true);
  const [isInspectorVisible, setIsInspectorVisible] = useState(true);
  const canvasScrollRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLDivElement>(null);
  const canvasContextImportInputRef = useRef<HTMLInputElement>(null);
  const hasCenteredCanvasRef = useRef(false);
  const savedCanvasViewportRef = useRef({
    scrollLeft: 0,
    scrollTop: 0,
    hasValue: false
  });

  // Dragging states
  const [dragNodeId, setDragNodeId] = useState<string | null>(null);
  const [dragOffset, setDragOffset] = useState({ x: 0, y: 0 });
  const [isCanvasPanning, setIsCanvasPanning] = useState(false);
  const [isWheelZooming, setIsWheelZooming] = useState(false);
  const canvasPanRef = useRef({
    active: false,
    startX: 0,
    startY: 0,
    scrollLeft: 0,
    scrollTop: 0
  });
  const wheelZoomTimeoutRef = useRef<number | null>(null);

  const getCanvasViewportCenter = () => {
    const container = canvasScrollRef.current;
    if (!container) {
      return { x: CANVAS_SIZE / 2, y: CANVAS_SIZE / 2 };
    }

    return {
      x: (container.scrollLeft + container.clientWidth / 2) / zoom,
      y: (container.scrollTop + container.clientHeight / 2) / zoom
    };
  };

  const centerCanvasViewport = (behavior: ScrollBehavior = "auto") => {
    const container = canvasScrollRef.current;
    if (!container) return;

    container.scrollTo({
      left: Math.max(0, (container.scrollWidth - container.clientWidth) / 2),
      top: Math.max(0, (container.scrollHeight - container.clientHeight) / 2),
      behavior
    });
  };

  const saveCanvasViewport = () => {
    const container = canvasScrollRef.current;
    if (!container) return;

    savedCanvasViewportRef.current = {
      scrollLeft: container.scrollLeft,
      scrollTop: container.scrollTop,
      hasValue: true
    };
  };

  const focusCanvasOnNodes = (targetNodes: AgentNode[], behavior: ScrollBehavior = "smooth") => {
    if (targetNodes.length === 0) {
      centerCanvasViewport(behavior);
      return;
    }

    const container = canvasScrollRef.current;
    if (!container) return;

    const bounds = targetNodes.reduce(
      (acc, node) => ({
        minX: Math.min(acc.minX, node.x),
        minY: Math.min(acc.minY, node.y),
        maxX: Math.max(acc.maxX, node.x + NODE_CARD_WIDTH),
        maxY: Math.max(acc.maxY, node.y + NODE_CARD_HEIGHT)
      }),
      { minX: Infinity, minY: Infinity, maxX: -Infinity, maxY: -Infinity }
    );
    if (!Number.isFinite(bounds.minX)) return;

    const padding = 100;
    const graphWidth = Math.max(1, bounds.maxX - bounds.minX) + padding * 2;
    const graphHeight = Math.max(1, bounds.maxY - bounds.minY) + padding * 2;
    const fitZoom = Math.min(
      1.2,
      Math.max(
        0.4,
        Math.min(
          container.clientWidth / graphWidth,
          container.clientHeight / graphHeight
        )
      )
    );
    const nextZoom = Number.isFinite(fitZoom) ? fitZoom : zoom;
    const centerX = (bounds.minX + bounds.maxX) / 2;
    const centerY = (bounds.minY + bounds.maxY) / 2;

    setZoom(nextZoom);
    window.requestAnimationFrame(() => {
      container.scrollTo({
        left: Math.max(0, centerX * nextZoom - container.clientWidth / 2),
        top: Math.max(0, centerY * nextZoom - container.clientHeight / 2),
        behavior
      });
    });
  };

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

  // Workspace artifact preview states
  const [activeWorkspaceTab, setActiveWorkspaceTab] = useState<"canvas" | "map" | "html" | "artifacts">("canvas");
  const [mapArtifact, setMapArtifact] = useState<{
    url: string;
    title: string;
  } | null>(null);
  const [mapArtifacts, setMapArtifacts] = useState<Array<{
    url: string;
    title: string;
  }>>([]);
  const [htmlArtifact, setHtmlArtifact] = useState<{
    url: string;
    title: string;
  } | null>(null);
  const [artifactViewItems, setArtifactViewItems] = useState<TaskArtifact[]>([]);
  const [selectedArtifactUrl, setSelectedArtifactUrl] = useState("");

  const handleOpenArtifactPreview = (url: string, title: string, sourceArtifact?: TaskArtifact) => {
    if (isSpatialArtifact(url, title)) {
      setMapArtifact({ url, title });
      setMapArtifacts((prev) => [{ url, title }, ...prev.filter((item) => item.url !== url)]);
      setActiveWorkspaceTab("map");
      return;
    }

    if (isHtmlArtifact(url, title)) {
      setHtmlArtifact({ url, title });
      setActiveWorkspaceTab("html");
      return;
    }

    const artifact = normalizeTaskArtifacts([
      {
        ...sourceArtifact,
        name: sourceArtifact?.name || title,
        filename: sourceArtifact?.filename || getArtifactFilename({ url }, title),
        format: sourceArtifact?.format || getArtifactExtension(url, title) || "file",
        url,
      }
    ])[0];
    setArtifactViewItems((prev) => [artifact, ...prev.filter((item) => item.url !== url)]);
    setSelectedArtifactUrl(url);
    setActiveWorkspaceTab("artifacts");
  };

  const handleViewAllArtifacts = (artifacts: TaskArtifact[]) => {
    if (!artifacts.length) return;

    const normalizedArtifacts = normalizeTaskArtifacts(artifacts);

    const spatialArtifacts = normalizedArtifacts
      .filter((artifact) => isSpatialArtifact(artifact.url, getArtifactFilename(artifact)))
      .map((artifact) => ({ url: artifact.url, title: getArtifactPreviewTitle(artifact) }));
    const htmlArtifacts = normalizedArtifacts.filter((artifact) => isHtmlArtifact(artifact.url, getArtifactFilename(artifact)));
    const otherArtifacts = normalizedArtifacts.filter(
      (artifact) => !isSpatialArtifact(artifact.url, getArtifactFilename(artifact)) && !isHtmlArtifact(artifact.url, getArtifactFilename(artifact))
    );

    if (spatialArtifacts.length > 0) {
      setMapArtifact(spatialArtifacts[0]);
      setMapArtifacts((prev) => [
        ...spatialArtifacts,
        ...prev.filter((item) => !spatialArtifacts.some((artifact) => artifact.url === item.url))
      ]);
    }

    if (htmlArtifacts.length > 0) {
      setHtmlArtifact({ url: htmlArtifacts[0].url, title: getArtifactPreviewTitle(htmlArtifacts[0]) });
    }

    setArtifactViewItems((prev) => [
      ...otherArtifacts,
      ...prev.filter((item) => !otherArtifacts.some((artifact) => artifact.url === item.url))
    ]);
    setSelectedArtifactUrl(otherArtifacts[0]?.url || "");
    setActiveWorkspaceTab(spatialArtifacts.length > 0 ? "map" : htmlArtifacts.length > 0 ? "html" : "artifacts");
  };

  const handleDeleteArtifactViewItem = (url: string) => {
    setArtifactViewItems((prev) => {
      const next = prev.filter((artifact) => artifact.url !== url);
      if (selectedArtifactUrl === url) {
        setSelectedArtifactUrl(next[0]?.url || "");
      }
      return next;
    });
  };

  const handleRemoveMapArtifact = (url: string) => {
    setMapArtifacts((prev) => prev.filter((artifact) => artifact.url !== url));
    setMapArtifact((prev) => (prev?.url === url ? null : prev));
  };

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

  useEffect(() => {
    zoomRef.current = zoom;
  }, [zoom]);

  // Handle adding an agent node instance to the active canvas center or a requested canvas point
  const handleAddAgentNode = (agentId: string, name: string, serverUrl?: string, position?: { x: number; y: number }) => {
    const newId = `agent_${Date.now()}`;
    const viewportCenter = getCanvasViewportCenter();
    const stagger = (nodes.length * 25) % 200;
    const requestedX = position ? position.x - NODE_CARD_WIDTH / 2 : viewportCenter.x - NODE_CARD_WIDTH / 2 + stagger;
    const requestedY = position ? position.y - 36 : viewportCenter.y - NODE_CARD_HEIGHT / 2 + stagger;

    const newNode: AgentNode = {
      id: newId,
      agentId,
      name: `${name} ${nodes.filter(n => n.agentId === agentId).length + 1}`,
      x: Math.max(0, Math.min(CANVAS_SIZE - NODE_CARD_WIDTH, requestedX)),
      y: Math.max(0, Math.min(CANVAS_SIZE - NODE_CARD_HEIGHT, requestedY)),
      instructions: DEFAULT_TASK_INSTRUCTIONS,
      inputDatasets: [],
      credentials: {},
      serverUrl: serverUrl || "https://www.geospatial-agentic-services.online/",
      status: "idle",
      logs: ["[SYSTEM]: Node initialized in workspace. Connect output to chain dataset feed."]
    };

    setNodes((prev) => [...prev, newNode]);
    setSelectedNodeId(newId);
    setSelectedDescribeAgentInfo(null);
    setActiveWorkspaceTab("canvas");
  };

  const handleCanvasDragOver = (e: React.DragEvent<HTMLDivElement>) => {
    if (!e.dataTransfer.types.includes("application/x-gas-agent")) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "copy";
  };

  const handleCanvasDrop = (e: React.DragEvent<HTMLDivElement>) => {
    const rawAgent = e.dataTransfer.getData("application/x-gas-agent");
    if (!rawAgent || !canvasRef.current) return;

    e.preventDefault();

    try {
      const agent = JSON.parse(rawAgent) as { agentId?: string; name?: string; serverUrl?: string };
      if (!agent.agentId || !agent.name) return;

      const canvasRect = canvasRef.current.getBoundingClientRect();
      const dropPoint = {
        x: (e.clientX - canvasRect.left) / zoom,
        y: (e.clientY - canvasRect.top) / zoom
      };
      handleAddAgentNode(agent.agentId, agent.name, agent.serverUrl, dropPoint);
    } catch (err) {
      console.error("Failed to parse dropped GAS agent", err);
    }
  };

  // Drag handlers
  const handleNodePointerDown = (nodeId: string, e: React.PointerEvent) => {
    if ((e.target as HTMLElement).closest(".no-drag")) return;
    setDragNodeId(nodeId);
    const node = nodes.find(n => n.id === nodeId);
    const canvasRect = canvasRef.current?.getBoundingClientRect();
    if (node && canvasRect) {
      const pointerCanvasX = (e.clientX - canvasRect.left) / zoom;
      const pointerCanvasY = (e.clientY - canvasRect.top) / zoom;
      setDragOffset({
        x: pointerCanvasX - node.x,
        y: pointerCanvasY - node.y
      });
    }
    e.currentTarget.setPointerCapture(e.pointerId);
  };

  const handleCanvasPointerMove = (e: React.PointerEvent) => {
    const panState = canvasPanRef.current;
    if (panState.active && canvasScrollRef.current) {
      canvasScrollRef.current.scrollLeft = panState.scrollLeft - (e.clientX - panState.startX);
      canvasScrollRef.current.scrollTop = panState.scrollTop - (e.clientY - panState.startY);
      return;
    }

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
            const newX = currentX - dragOffset.x;
            const newY = currentY - dragOffset.y;
            return {
              ...node,
              x: Math.max(0, Math.min(CANVAS_SIZE - NODE_CARD_WIDTH, newX)),
              y: Math.max(0, Math.min(CANVAS_SIZE - NODE_CARD_HEIGHT, newY))
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
    canvasPanRef.current.active = false;
    setIsCanvasPanning(false);
  };

  const handleCanvasPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    const target = e.target as HTMLElement;
    const isEmptyCanvasTarget = e.target === e.currentTarget || target === canvasRef.current;

    if (!isEmptyCanvasTarget) return;

    closeContextMenus();
    setSelectedNodeId(null);
    setSelectedDescribeAgentInfo(null);
    setSelectedConnectionId(null);

    if (e.button !== 0 || !canvasScrollRef.current) return;

    canvasPanRef.current = {
      active: true,
      startX: e.clientX,
      startY: e.clientY,
      scrollLeft: canvasScrollRef.current.scrollLeft,
      scrollTop: canvasScrollRef.current.scrollTop
    };
    setIsCanvasPanning(true);
    e.currentTarget.setPointerCapture(e.pointerId);
  };

  const handleNodeContextMenu = (nodeId: string, e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setSelectedNodeId(nodeId);
    setSelectedDescribeAgentInfo(null);
    setSelectedConnectionId(null);
    setConnectionContextMenu(null);
    setCanvasContextMenu(null);
    setNodeContextMenu({
      nodeId,
      x: e.clientX,
      y: e.clientY
    });
  };

  const handleCanvasContextMenu = (e: React.MouseEvent<HTMLDivElement>) => {
    const target = e.target as HTMLElement;
    const isEmptyCanvasTarget = e.target === e.currentTarget || target === canvasRef.current;
    if (!isEmptyCanvasTarget || !canvasRef.current) return;

    e.preventDefault();
    const canvasRect = canvasRef.current.getBoundingClientRect();
    setSelectedNodeId(null);
    setSelectedDescribeAgentInfo(null);
    setSelectedConnectionId(null);
    setConnectionContextMenu(null);
    setNodeContextMenu(null);
    setCanvasContextMenu({
      x: e.clientX,
      y: e.clientY,
      canvasX: (e.clientX - canvasRect.left) / zoom,
      canvasY: (e.clientY - canvasRect.top) / zoom
    });
  };

  const handleCanvasWheel = (e: WheelEvent) => {
    const target = e.target as HTMLElement;
    if (target.closest("input, textarea, select, [contenteditable='true']")) return;
    if (!canvasScrollRef.current) return;

    e.preventDefault();

    const container = canvasScrollRef.current;
    const currentZoom = zoomRef.current;
    const rect = container.getBoundingClientRect();
    const viewportX = e.clientX - rect.left;
    const viewportY = e.clientY - rect.top;
    const canvasX = (container.scrollLeft + viewportX) / currentZoom;
    const canvasY = (container.scrollTop + viewportY) / currentZoom;
    const zoomFactor = Math.exp(-e.deltaY * 0.001);
    const nextZoom = Math.min(1.5, Math.max(0.3, currentZoom * zoomFactor));

    zoomRef.current = nextZoom;
    setIsWheelZooming(true);
    setZoom(nextZoom);
    window.setTimeout(() => {
      container.scrollLeft = canvasX * nextZoom - viewportX;
      container.scrollTop = canvasY * nextZoom - viewportY;
    }, 0);

    if (wheelZoomTimeoutRef.current) {
      window.clearTimeout(wheelZoomTimeoutRef.current);
    }
    wheelZoomTimeoutRef.current = window.setTimeout(() => {
      setIsWheelZooming(false);
      wheelZoomTimeoutRef.current = null;
    }, 120);
  };

  useEffect(() => {
    if (activeWorkspaceTab !== "canvas") return;

    const container = canvasScrollRef.current;
    if (!container) return;

    container.addEventListener("wheel", handleCanvasWheel, { passive: false });
    return () => {
      container.removeEventListener("wheel", handleCanvasWheel);
    };
  }, [activeWorkspaceTab]);

  useEffect(() => {
    if (activeWorkspaceTab !== "canvas") return;

    const frame = window.requestAnimationFrame(() => {
      if (!hasCenteredCanvasRef.current) {
        centerCanvasViewport();
        hasCenteredCanvasRef.current = true;
        return;
      }

      if (savedCanvasViewportRef.current.hasValue && canvasScrollRef.current) {
        canvasScrollRef.current.scrollTo({
          left: savedCanvasViewportRef.current.scrollLeft,
          top: savedCanvasViewportRef.current.scrollTop,
          behavior: "auto"
        });
      }
    });

    return () => window.cancelAnimationFrame(frame);
  }, [activeWorkspaceTab]);

  useEffect(() => {
    if (!dragNodeId && !connectionDraft && !isCanvasPanning) return;

    const releasePointerState = () => {
      setDragNodeId(null);
      setConnectionDraft(null);
      canvasPanRef.current.active = false;
      setIsCanvasPanning(false);
    };

    window.addEventListener("pointerup", releasePointerState);
    window.addEventListener("pointercancel", releasePointerState);
    window.addEventListener("blur", releasePointerState);

    return () => {
      window.removeEventListener("pointerup", releasePointerState);
      window.removeEventListener("pointercancel", releasePointerState);
      window.removeEventListener("blur", releasePointerState);
    };
  }, [dragNodeId, connectionDraft, isCanvasPanning]);

  const handlePortPointerDown = (nodeId: string, type: "input" | "output", e: React.PointerEvent, artifactName?: string) => {
    e.preventDefault();
    e.stopPropagation();
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
          connectionsRef.current = newConnections;
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

        const provider = data.capabilities?.provider || {};
        const providerName = provider?.name || "Unknown Provider";

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
              category: existing?.category || getAgentCategory(actualId),
              description: agent.description || existing?.description || "Dynamically discovered agent from GAS server.",
              keywords: existing?.keywords || ["dynamic", "fetched"]
            };
          });

          setServers(prev => {
            const copy = prev.filter(s => s.url !== url);
            return [...copy, { url, providerName, provider, agents: newTemplates, isExpanded: true }];
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

  const handleRemoveServerAgent = (serverUrl: string, agentId: string) => {
    setServers((prev) =>
      prev.map((server) =>
        server.url === serverUrl
          ? { ...server, agents: server.agents.filter((agent) => agent.agent_id !== agentId) }
          : server
      )
    );
    setSelectedDescribeAgentInfo((current) => {
      const currentAgentId = current?.profile?.agent_id || current?.agent_id;
      const currentServerUrl = current?._server?.url;
      return currentAgentId === agentId && currentServerUrl === serverUrl ? null : current;
    });
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
        const server = servers.find((item) => item.url === serverUrl);
        setSelectedDescribeAgentInfo({
          ...data,
          _server: {
            url: serverUrl,
            providerName: server?.providerName,
            provider: server?.provider,
            describeUrl: `${serverUrl.replace(/\/+$/, "")}/?SERVICE=GAS&VERSION=1.0.0&REQUEST=DescribeAgent&agent_id=${encodeURIComponent(agentId)}`,
            getCapabilitiesUrl: `${serverUrl.replace(/\/+$/, "")}/?SERVICE=GAS&VERSION=1.0.0&REQUEST=GetCapabilities`,
          }
        });
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
    const template = AGENT_TEMPLATES.find((tpl) => tpl.agent_id === node.agentId);
    const streamAgentName = template?.name || node.name;
    const parentLinks = activeConnections.filter((c) => c.targetId === nodeId);
    const unfinishedParents = parentLinks
      .map((link) => activeNodes.find((n) => n.id === link.sourceId))
      .filter((parent): parent is AgentNode => !parent || parent.status !== "completed");

    if (unfinishedParents.length > 0) {
      const upstreamNames = unfinishedParents
        .map((parent) => parent?.name || "an upstream agent")
        .join(", ");
      const message = `Cannot run this agent yet. Waiting for upstream agent${unfinishedParents.length > 1 ? "s" : ""} to finish: ${upstreamNames}.`;
      const blockedNodes = nodesRef.current.map((n) => {
        if (n.id === nodeId) {
          return {
            ...n,
            status: "waiting" as const,
            logs: [...n.logs, formatLocalLogLine("task_blocked", message)]
          };
        }
        return n;
      });
      nodesRef.current = blockedNodes;
      setNodes(blockedNodes);
      showToast(message);
      return null;
    }

    if (isPlaceholderInstruction(node.instructions)) {
      const message = "Task instructions are not defined yet. Double click the agent box to enter instructions before running this agent.";
      setNodes((prev) =>
        prev.map((n) => {
          if (n.id === nodeId) {
            return {
              ...n,
              status: "error",
              logs: [...n.logs, formatLocalLogLine("task_rejected", message)]
            };
          }
          return n;
        })
      );
      return null;
    }

    delete volatileResultsRef.current[nodeId];

    // Mark running and clear stale runtime output from any previous execution.
    const runningNodes = nodesRef.current.map((n) => {
      if (n.id === nodeId) {
        return {
          ...n,
          status: "running" as const,
          currentTaskId: undefined,
          lastRequest: undefined,
          results: undefined,
          logs: [formatLocalLogLine("task_submitting", `Submitting task execution request to ${streamAgentName}.`)]
        };
      }
      return n;
    });
    nodesRef.current = runningNodes;
    setNodes(runningNodes);

    // Compute active datasets input binders:
    // Gather outputs of connected parent nodes
    const parentDatasetUrls: string[] = [];

    for (const link of parentLinks) {
      // Prioritize recently completed results avoiding React state batching race-conditions
      const recentResult = volatileResultsRef.current[link.sourceId];
      const parentNode = activeNodes.find((n) => n.id === link.sourceId);
      const artifactsSource = recentResult?.outputs?.artifacts || parentNode?.results?.outputs?.artifacts || [];

      let selectedArtifactNames = artifactsSource.length === 1 ? [] : link.artifacts || [];

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
        if (artifactMatchesSelection(art, selectedArtifactNames, idx)) {
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

      const nextNodes = nodesRef.current.map((n) => {
        if (n.id === nodeId) {
          return {
            ...n,
            lastRequest: payload
          };
        }
        return n;
      });
      nodesRef.current = nextNodes;
      setNodes(nextNodes);

      console.log(`Firing task payload on node ${nodeId}:`, payload);
      const controller = new AbortController();
      runningTaskControllersRef.current[nodeId] = controller;

      const response = await fetch(getApiUrl("/api/gas/run-task"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        signal: controller.signal
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

          const streamTaskId = extractTaskIdFromEvent(eventObj);
          if (streamTaskId) {
            const nextNodes = nodesRef.current.map((n) =>
              n.id === nodeId ? { ...n, currentTaskId: streamTaskId } : n
            );
            nodesRef.current = nextNodes;
            setNodes(nextNodes);
          }

          if (eventObj.event === "task_result" || eventObj.payload?.outputs) {
            const taskResult: TaskResult = normalizeTaskResultArtifacts(eventObj.payload || eventObj);
            const resultError = getTaskResultError(taskResult);
            const logMsg = formatStreamLogLine(eventObj, streamAgentName);

            if (resultError) {
              const nextNodes = nodesRef.current.map((n) => {
                if (n.id === nodeId) {
                  return {
                    ...n,
                    status: "error" as const,
                    results: taskResult,
                    logs: [...n.logs, logMsg, `[${streamEventTime(eventObj)}] task_failed: ${resultError}`]
                  };
                }
                return n;
              });
              nodesRef.current = nextNodes;
              setNodes(nextNodes);
              throw new Error(resultError);
            }

            lastSuccessResult = taskResult;
            volatileResultsRef.current[nodeId] = taskResult;

            const nextNodes = nodesRef.current.map((n) => {
              if (n.id === nodeId) {
                return {
                  ...n,
                  status: "completed" as const,
                  currentTaskId: undefined,
                  results: taskResult,
                  logs: [...n.logs, logMsg]
                };
              }
              return n;
            });
            nodesRef.current = nextNodes;
            setNodes(nextNodes);
          } else if (eventObj.event === "stream_error") {
            const logMsg = formatStreamLogLine(eventObj, streamAgentName);
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
            throw new Error(eventObj.payload?.message || "Internal Stream Failure");
          } else {
            const logMsg = formatStreamLogLine(eventObj, streamAgentName);

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

      if (err?.name === "AbortError") {
        return null;
      }

      setNodes((prev) =>
        prev.map((n) => {
          if (n.id === nodeId) {
            return {
              ...n,
              status: "error",
              logs: [...n.logs, formatLocalLogLine("task_failed", `Execution aborted. Reason: ${err.message}`)]
            };
          }
          return n;
        })
      );
      return null;
    } finally {
      delete runningTaskControllersRef.current[nodeId];
    }
  };

  const cancelNodeExecution = async (nodeId: string) => {
    const activeNode = nodesRef.current.find((n) => n.id === nodeId);
    if (!activeNode || activeNode.status !== "running") return;

    const taskId = activeNode.currentTaskId;
    runningTaskControllersRef.current[nodeId]?.abort();
    delete runningTaskControllersRef.current[nodeId];

    const cancelRequestedLog = taskId
      ? `Cancel requested for task ${taskId}.`
      : "Cancel requested before the GAS task id was received. The local stream was closed.";

    const getDownstreamNodeIds = (startNodeId: string) => {
      const downstreamIds = new Set<string>();
      const queue = [startNodeId];

      while (queue.length > 0) {
        const currentId = queue.shift();
        if (!currentId) continue;

        connectionsRef.current
          .filter((connection) => connection.sourceId === currentId)
          .forEach((connection) => {
            if (!downstreamIds.has(connection.targetId)) {
              downstreamIds.add(connection.targetId);
              queue.push(connection.targetId);
            }
          });
      }

      return downstreamIds;
    };

    const markDownstreamCanceled = (startNodeId: string, reason: string) => {
      const downstreamIds = getDownstreamNodeIds(startNodeId);
      if (downstreamIds.size === 0) return downstreamIds;

      downstreamIds.forEach((downstreamId) => {
        runningTaskControllersRef.current[downstreamId]?.abort();
        delete runningTaskControllersRef.current[downstreamId];
      });

      const nextNodes = nodesRef.current.map((n) => {
        if (downstreamIds.has(n.id) && (n.status === "waiting" || n.status === "running")) {
          return {
            ...n,
            status: "canceled" as const,
            currentTaskId: undefined,
            logs: [...n.logs, formatLocalLogLine("task_cancelled", reason)]
          };
        }
        return n;
      });

      nodesRef.current = nextNodes;
      setNodes(nextNodes);
      return downstreamIds;
    };

    const markCanceled = (extraLog?: string) => {
      const nextNodes = nodesRef.current.map((n) => {
        if (n.id === nodeId) {
          return {
            ...n,
            status: "canceled" as const,
            currentTaskId: undefined,
            logs: [
              ...n.logs,
              formatLocalLogLine("task_cancel_requested", cancelRequestedLog),
              ...(extraLog ? [extraLog] : [])
            ]
          };
        }
        return n;
      });
      nodesRef.current = nextNodes;
      setNodes(nextNodes);
      markDownstreamCanceled(nodeId, `Canceled because upstream agent "${activeNode.name}" was canceled.`);
    };

    if (!taskId) {
      markCanceled();
      return;
    }

    markCanceled(formatLocalLogLine("task_cancel_pending", "Requesting server-side task cancellation."));

    try {
      const response = await fetch(getApiUrl("/api/gas/cancel-task"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          agentId: activeNode.agentId,
          taskId,
          serverUrl: activeNode.serverUrl || "https://www.geospatial-agentic-services.online/"
        })
      });

      if (!response.ok) {
        const errPayload = await response.json().catch(() => ({}));
        const detail = formatErrorDetail(errPayload.details || errPayload.error || "Server cancellation request failed.");
        if (!detail.includes("already closed") && !detail.includes("TASK_CLOSED")) {
          throw new Error(detail);
        }
      }

      const cancelResult = await response.json().catch(() => null);
      const nextNodes = nodesRef.current.map((n) => {
        if (n.id === nodeId) {
          return {
            ...n,
            results: cancelResult || n.results,
            logs: [...n.logs, formatLocalLogLine("task_cancelled", "Server confirmed task cancellation.")]
          };
        }
        return n;
      });
      nodesRef.current = nextNodes;
      setNodes(nextNodes);
    } catch (err: any) {
      const nextNodes = nodesRef.current.map((n) => {
        if (n.id === nodeId) {
          return {
            ...n,
            logs: [...n.logs, formatLocalLogLine("task_cancel_warning", "Local stream was closed, but server cancellation was not confirmed.")]
          };
        }
        return n;
      });
      nodesRef.current = nextNodes;
      setNodes(nextNodes);
    }
  };

  const cancelFullPipeline = async () => {
    if (!isPipelineRunning) return;

    workflowCancelRequestedRef.current = true;
    const runningNodes = nodesRef.current.filter((node) => node.status === "running");
    const waitingNodeIds = new Set(
      nodesRef.current
        .filter((node) => node.status === "waiting")
        .map((node) => node.id)
    );

    if (waitingNodeIds.size > 0) {
      const nextNodes = nodesRef.current.map((node) =>
        waitingNodeIds.has(node.id)
          ? {
              ...node,
              status: "canceled" as const,
              currentTaskId: undefined,
              logs: [...node.logs, formatLocalLogLine("workflow_cancelled", "Workflow cancellation requested before this agent started.")]
            }
          : node
      );
      nodesRef.current = nextNodes;
      setNodes(nextNodes);
    }

    showToast("Canceling workflow...");
    await Promise.all(runningNodes.map((node) => cancelNodeExecution(node.id)));
  };

  // Sequentially run all nodes matching dependency hierarchy levels
  const handleRunFullPipeline = async () => {
    workflowCancelRequestedRef.current = false;
    setIsPipelineRunning(true);

    try {
      // 1. Reset statuses of all nodes to idle / queue
      volatileResultsRef.current = {};
      const currentConnections = connectionsRef.current;
      const resetNodes: AgentNode[] = nodesRef.current.map((n) => ({
        ...n,
        status: currentConnections.some((connection) => connection.targetId === n.id) ? "waiting" : "idle",
        currentTaskId: undefined,
        lastRequest: undefined,
        results: undefined,
        logs: ["[SYSTEM]: Queue reset, starting automated workspace pipeline run."]
      }));
      nodesRef.current = resetNodes;
      setNodes(resetNodes);

      const completedIds = new Set<string>();
      const failedIds = new Set<string>();
      const inFlight = new Map<string, Promise<{ node: AgentNode; outcome: TaskResult | null; error?: any }>>();

      const getReadyNodes = () => {
        if (workflowCancelRequestedRef.current) return [];

        const activeNodes = nodesRef.current;
        const activeConnections = connectionsRef.current;

        return activeNodes.filter((n) => {
          if (n.status === "canceled" || n.status === "error") return false;
          if (completedIds.has(n.id)) return false;
          if (failedIds.has(n.id)) return false;
          if (inFlight.has(n.id)) return false;
          const parentsLinks = activeConnections.filter((c) => c.targetId === n.id);
          const allParentsDone = parentsLinks.every((link) => completedIds.has(link.sourceId));
          return allParentsDone;
        });
      };

      const launchReadyNodes = () => {
        const readyNodes = getReadyNodes();
        if (readyNodes.length === 0) return;

        console.log(`Pipeline Scheduler: Dispatching run on: ${readyNodes.map((node) => node.name).join(", ")}`);
        readyNodes.forEach((node) => {
          inFlight.set(
            node.id,
            executeSingleNode(node.id, true)
              .then((outcome) => ({ node, outcome }))
              .catch((error) => ({ node, outcome: null, error }))
          );
        });
      };

      launchReadyNodes();

      while (inFlight.size > 0) {
        const finished = await Promise.race(Array.from(inFlight.values()));
        inFlight.delete(finished.node.id);

        if (workflowCancelRequestedRef.current) {
          failedIds.add(finished.node.id);
          continue;
        }

        if (finished.outcome) {
          completedIds.add(finished.node.id);
          launchReadyNodes();
        } else {
          failedIds.add(finished.node.id);
          console.warn(`Scheduler paused on error inside task: ${finished.node.name}. Downstream execution blocked.`, finished.error);
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

  const sanitizeWorkflowFileName = (name: string) =>
    name.trim().replace(/[^a-z0-9-_]+/gi, "_").replace(/^_+|_+$/g, "") || "gas_workflow";

  const buildCurrentWorkflow = (name: string): SavedWorkflow => ({
    id: `workflow_${Date.now()}`,
    name,
    description: "Custom user-generated drag-and-drop workflow graph.",
    nodes: nodesRef.current,
    connections: connectionsRef.current,
    createdAt: new Date().toISOString()
  });

  const isSavedWorkflow = (value: any): value is SavedWorkflow =>
    value &&
    typeof value === "object" &&
    typeof value.name === "string" &&
    Array.isArray(value.nodes) &&
    Array.isArray(value.connections);

  const loadWorkflowIntoCanvas = (workflow: SavedWorkflow) => {
    nodesRef.current = workflow.nodes;
    connectionsRef.current = workflow.connections;
    setActiveWorkspaceTab("canvas");
    setNodes(workflow.nodes);
    setConnections(workflow.connections);
    setSelectedDescribeAgentInfo(null);
    setSelectedConnectionId(null);
    setSelectedNodeId(workflow.nodes.length > 0 ? workflow.nodes[0].id : null);
    window.requestAnimationFrame(() => {
      focusCanvasOnNodes(workflow.nodes);
    });
  };

  const handleSaveWorkflow = () => {
    setSavePromptOpen(true);
  };

  const submitSaveWorkflow = (name: string) => {
    if (!name.trim()) return;

    const newSaved = buildCurrentWorkflow(name);

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
        loadWorkflowIntoCanvas(match);
        showToast(`Successfully loaded saved pipeline: "${match.name}"`);
      }
    } catch (e) {
      console.error(e);
    }
  };

  const handleDeleteWorkflow = (id: string) => {
    const rawWorkflows = localStorage.getItem(STORAGE_KEYS.WORKFLOWS);
    if (!rawWorkflows) return;

    try {
      const list: SavedWorkflow[] = JSON.parse(rawWorkflows);
      const match = list.find((item) => item.id === id);
      if (!match) return;

      const shouldDelete = window.confirm(`Delete saved workflow "${match.name}"?`);
      if (!shouldDelete) return;

      const nextList = list.filter((item) => item.id !== id);
      localStorage.setItem(STORAGE_KEYS.WORKFLOWS, JSON.stringify(nextList));
      updateSavedWorkflowsList();
      showToast(`Deleted saved workflow: "${match.name}"`);
    } catch (e) {
      console.error(e);
      showToast("Could not delete saved workflow.");
    }
  };

  const handleRenameWorkflow = (id: string) => {
    const rawWorkflows = localStorage.getItem(STORAGE_KEYS.WORKFLOWS);
    if (!rawWorkflows) return;

    try {
      const list: SavedWorkflow[] = JSON.parse(rawWorkflows);
      const match = list.find((item) => item.id === id);
      if (!match) return;

      const nextName = window.prompt("Rename saved workflow:", match.name);
      if (!nextName || !nextName.trim()) return;

      const nextList = list.map((item) =>
        item.id === id ? { ...item, name: nextName.trim() } : item
      );
      localStorage.setItem(STORAGE_KEYS.WORKFLOWS, JSON.stringify(nextList));
      updateSavedWorkflowsList();
      showToast(`Renamed saved workflow to "${nextName.trim()}"`);
    } catch (e) {
      console.error(e);
      showToast("Could not rename saved workflow.");
    }
  };

  const handleDownloadWorkflow = () => {
    const workflow = buildCurrentWorkflow(workflowNameInput.trim() || "GAS Canvas Workflow");
    const blob = new Blob([JSON.stringify(workflow, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${sanitizeWorkflowFileName(workflow.name)}.gas-workflow.json`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
    showToast(`Downloaded workflow file: "${workflow.name}"`);
  };

  const handleImportWorkflowFile = (file: File) => {
    const reader = new FileReader();

    reader.onload = () => {
      try {
        const parsed = JSON.parse(String(reader.result || ""));
        const workflow: SavedWorkflow = isSavedWorkflow(parsed)
          ? {
              ...parsed,
              id: parsed.id || `workflow_${Date.now()}`,
              description: parsed.description || "Imported GAS Canvas workflow.",
              createdAt: parsed.createdAt || new Date().toISOString()
            }
          : {
              id: `workflow_${Date.now()}`,
              name: file.name.replace(/\.gas-workflow\.json$|\.json$/i, "") || "Imported Workflow",
              description: "Imported GAS Canvas workflow.",
              nodes: Array.isArray(parsed.nodes) ? parsed.nodes : [],
              connections: Array.isArray(parsed.connections) ? parsed.connections : [],
              createdAt: new Date().toISOString()
            };

        if (!Array.isArray(workflow.nodes) || !Array.isArray(workflow.connections)) {
          throw new Error("Workflow file must contain nodes and connections arrays.");
        }

        loadWorkflowIntoCanvas(workflow);
        showToast(`Loaded workflow from file: "${workflow.name}"`);
      } catch (err: any) {
        console.error(err);
        showToast(`Could not load workflow file: ${err.message}`);
      }
    };

    reader.onerror = () => {
      showToast("Could not read workflow file.");
    };

    reader.readAsText(file);
  };

  // Clear Canvas
  const handleClearCanvas = () => {
    const shouldClear = window.confirm("Wipe the current canvas? This removes all nodes and connections from the workspace.");
    if (!shouldClear) return;

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
    const nodeWidth = NODE_CARD_WIDTH;
    const nodeHeight = 280;
    const spacingX = 560;
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

  const handleZoomToFit = () => {
    if (nodesRef.current.length === 0) {
      centerCanvasViewport("smooth");
      showToast("Canvas is empty. Centered the workspace.");
      return;
    }

    focusCanvasOnNodes(nodesRef.current);
  };

  // Credentials changes
  const handleSaveCredentials = (keys: { OPENAI_API_KEY: string }) => {
    setCredentials(keys);
    localStorage.setItem(STORAGE_KEYS.CREDENTIALS, JSON.stringify(keys));
    showToast("Saved API keys local overrides successfully! They'll authorize upcoming stream jobs.");
  };

  // Render variables helper
  const selectedNode = nodes.find((n) => n.id === selectedNodeId) || null;
  const nodeContextMenuNode = nodeContextMenu ? nodes.find((node) => node.id === nodeContextMenu.nodeId) || null : null;
  const updateNode = (nodeId: string, updates: Partial<AgentNode>) => {
    const nextNodes = nodesRef.current.map((n) => (n.id === nodeId ? { ...n, ...updates } : n));
    nodesRef.current = nextNodes;
    setNodes(nextNodes);
  };

  return (
    <div className="flex flex-col h-screen w-screen overflow-hidden bg-neutral-100 font-sans text-neutral-800">

      {/* GLOBAL MASTER HEADER BAR */}
      <header className="h-14 shrink-0 bg-white text-neutral-800 flex items-center justify-between px-4 border-b border-neutral-200 z-20 shadow-sm">
        <div className="flex items-center space-x-3">
          <div className="p-2 bg-sky-50 border border-sky-100 rounded-xl text-sky-600">
            <Network className="w-5 h-5 text-sky-600 rotate-45" />
          </div>
          <div className="flex items-center gap-3 min-w-0">
            <h1 className="text-2xl font-bold tracking-tight text-neutral-900 leading-tight">
              GAS Canvas
            </h1>
            <p className="translate-y-0.5 text-sm font-medium text-neutral-500 leading-tight truncate">
              Build Autonomous GIS Applications with Geospatial Agentic Services
            </p>
          </div>
        </div>

        {/* Global Action buttons settings */}
        <div className="flex items-center space-x-3 text-sm">
          <button
            onClick={() => setIsCredentialsOpen(true)}
            className="flex items-center space-x-1 px-3 py-1.5 bg-white hover:bg-neutral-50 rounded-lg text-xs font-semibold text-neutral-700 border border-neutral-200 transition-colors"
          >
            <Key className="w-3.5 h-3.5 text-amber-500" />
            <span>API Credentials</span>
          </button>
          <a
            href="https://github.com/GIBD2015/geospatial-agentic-services"
            target="_blank"
            rel="noreferrer"
            title="Open GIBD2015/geospatial-agentic-services on GitHub"
            className="flex items-center space-x-2 rounded-full bg-neutral-900 px-4 py-2 text-xs font-bold text-white shadow-sm transition-colors hover:bg-neutral-800"
          >
            <Github className="h-4 w-4" />
            <span>GAS Project</span>
          </a>
        </div>
      </header>

      {/* CORE SPLIT WORKSPACE BODY */}
      <div className="flex-1 flex overflow-hidden min-h-0 relative">

        {isSidebarVisible ? (
          <>
            {/* LEFTSIDEBAR: ALL AGENT TEMPLATES */}
            <SidebarPanel
              onAddAgent={handleAddAgentNode}
              onDescribeAgent={handleDescribeAgent}
              servers={servers}
              onAddServer={handleAddServer}
              onRemoveServer={handleRemoveServer}
              onRemoveAgent={handleRemoveServerAgent}
              onToggleServer={handleToggleServer}
              isSyncingServer={isSyncingServer}
              width={sidebarWidth}
            />

            {/* RESIZER DRAG HANDLE LEFT */}
            <div
              onPointerDown={handleSidebarResize}
              className="relative w-1 hover:bg-sky-400 active:bg-sky-500 cursor-col-resize shrink-0 bg-neutral-200 dark:bg-neutral-800 transition-colors z-30 flex items-center justify-center group"
            >
              <div className="h-8 w-px bg-neutral-400 dark:bg-neutral-600 rounded-full group-hover:bg-white" />
              <button
                type="button"
                title="Hide GAS Servers panel"
                onPointerDown={(e) => e.stopPropagation()}
                onClick={() => setIsSidebarVisible(false)}
                className="absolute top-3 left-1/2 -translate-x-1/2 flex h-6 w-5 items-center justify-center rounded-full border border-neutral-200 bg-white text-neutral-600 shadow-sm hover:bg-neutral-50 hover:text-sky-700"
              >
                <ChevronLeft className="h-3.5 w-3" />
              </button>
            </div>
          </>
        ) : (
          <div className="w-10 shrink-0 border-r border-neutral-200 bg-white flex justify-center pt-3">
            <button
              type="button"
              title="Show GAS Servers panel"
              onClick={() => setIsSidebarVisible(true)}
              className="flex h-7 w-7 items-center justify-center rounded-lg border border-neutral-200 bg-white text-neutral-600 shadow-sm hover:bg-neutral-50 hover:text-sky-700"
            >
              <ChevronRight className="h-4 w-4" />
            </button>
          </div>
        )}

        {/* CENTRAL CANVAS WORKSPACE */}
        <div
          className="relative flex-1 min-w-0 flex flex-col bg-neutral-100"
        >
          {activeWorkspaceTab === "canvas" ? (
            <>
              {/* WORKFLOW TOOLBAR */}
              <CanvasControls
                onClearCanvas={handleClearCanvas}
                onRunFullPipeline={handleRunFullPipeline}
                onCancelFullPipeline={cancelFullPipeline}
                isPipelineRunning={isPipelineRunning}
                onLoadPreset={loadPresetTemplate}
                onSaveWorkflow={handleSaveWorkflow}
                onLoadWorkflow={handleLoadWorkflow}
                onDeleteWorkflow={handleDeleteWorkflow}
                onRenameWorkflow={handleRenameWorkflow}
                onImportWorkflowFile={handleImportWorkflowFile}
                savedWorkflows={savedWorkflowsList}
                zoom={zoom}
                onZoomIn={() => setZoom(z => Math.min(1.5, z + 0.1))}
                onZoomOut={() => setZoom(z => Math.max(0.3, z - 0.1))}
                onResetZoom={() => setZoom(1)}
                onAutoLayout={handleAutoLayout}
                onZoomToFit={handleZoomToFit}
              />

              {/* INTERACTIVE GRID CANVAS CONTAINER */}
              <div
                ref={canvasScrollRef}
                className="gas-canvas-scroll flex-1 overflow-auto relative bg-neutral-100 dark:bg-neutral-950/60 focus:outline-none min-w-0"
                onPointerMove={handleCanvasPointerMove}
                onPointerUp={handleCanvasPointerUp}
                onPointerCancel={handleCanvasPointerUp}
                onPointerDown={handleCanvasPointerDown}
                onContextMenu={handleCanvasContextMenu}
                onDragOver={handleCanvasDragOver}
                onDrop={handleCanvasDrop}
                onScroll={saveCanvasViewport}
                style={{ cursor: dragNodeId || isCanvasPanning ? "grabbing" : "grab" }}
              >
                {/* Dot Grid Background */}
                <div
                  ref={canvasRef}
                  className="absolute inset-0 min-w-[8000px] min-h-[8000px] bg-[radial-gradient(#d4d4d8_1px,transparent_1px)] [background-size:20px_20px] dark:bg-[radial-gradient(#1e1e24_1px,transparent_1px)]"
                  style={{
                    transform: `scale(${zoom})`,
                    transformOrigin: "top left",
                    transition: dragNodeId || isWheelZooming ? "none" : "transform 0.1s ease-out"
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
                    const isSourceRunning = source.status === "running";
                    const isTargetWaiting = target.status === "waiting";
                    const dashPattern = isSourceRunning ? "8 6" : isTargetWaiting ? "2 7" : "none";
                    const pathClassName = isSourceRunning ? "animate-dash cursor-pointer" : "cursor-pointer";

                    return (
                      <g
                        key={link.id}
                        className="group pointer-events-auto"
                        onPointerDown={(e) => {
                          e.stopPropagation();
                          setSelectedConnectionId(link.id);
                          setSelectedNodeId(null);
                          setSelectedDescribeAgentInfo(null);
                        }}
                        onContextMenu={(e) => {
                          e.preventDefault();
                          e.stopPropagation();
                          const menuWidth = 172;
                          const menuHeight = 44;
                          setSelectedConnectionId(link.id);
                          setSelectedNodeId(null);
                          setSelectedDescribeAgentInfo(null);
                          setConnectionContextMenu({
                            connectionId: link.id,
                            x: Math.min(e.clientX, window.innerWidth - menuWidth - 8),
                            y: Math.min(e.clientY, window.innerHeight - menuHeight - 8)
                          });
                        }}
                      >
                        {/* Hover and Selection highlights thick guideline helper */}
                        <path
                          d={`M ${x1} ${y1} C ${ctrlX1} ${ctrlY1}, ${ctrlX2} ${ctrlY2}, ${x2} ${y2}`}
                          stroke={selectedConnectionId === link.id ? "#ef4444" : "transparent"}
                          strokeWidth={selectedConnectionId === link.id ? "10" : "20"}
                          fill="none"
                          className="cursor-pointer hover:stroke-red-500/50 transition-colors"
                          style={{ opacity: selectedConnectionId === link.id ? 0.3 : 1 }}
                        />

                        {/* Glowing active path connection */}
                        <path
                          d={`M ${x1} ${y1} C ${ctrlX1} ${ctrlY1}, ${ctrlX2} ${ctrlY2}, ${x2} ${y2}`}
                          stroke={selectedConnectionId === link.id ? "#ef4444" : "#0ea5e9"}
                          strokeWidth="3.5"
                          strokeDasharray={dashPattern}
                          strokeLinecap={isTargetWaiting && !isSourceRunning ? "round" : "butt"}
                          className={pathClassName}
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
                  const parentDatasetUrls: { name: string, url: string, type: 'dynamic', sourceId: string, description?: string }[] = [];
                  parentLinks.forEach((link) => {
                    const parentNode = nodes.find((n) => n.id === link.sourceId);
                    const recentResult = volatileResultsRef.current[link.sourceId];
                    const artifactsSource = recentResult?.outputs?.artifacts || parentNode?.results?.outputs?.artifacts || [];

                    const selectedArtifacts = artifactsSource.length === 1 ? [] : link.artifacts || [];
                    artifactsSource.forEach((art, idx) => {
                      if (artifactMatchesSelection(art, selectedArtifacts, idx)) {
                        if (art.url) {
                          parentDatasetUrls.push({
                            name: getArtifactFilename(art, `Output ${idx + 1}`),
                            url: art.url,
                            type: 'dynamic',
                            sourceId: link.sourceId,
                            description: getArtifactHoverText(art)
                          });
                        }
                      }
                    });
                  });
                  const combinedInputs = [...node.inputDatasets.map(url => ({ name: getArtifactFilename({ url }, 'Manual Input Dataset'), url, type: 'manual' as const, sourceId: '', description: url })), ...parentDatasetUrls].filter(item => !node.excludedInputs?.includes(item.url));

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
                    onCancel={() => cancelNodeExecution(node.id)}
                    onUpdateNode={(nodeId, updates) => {
                      updateNode(nodeId, updates);
                    }}
                    onOpenArtifact={(artifact) => {
                      handleOpenArtifactPreview(artifact.url, getArtifactPreviewTitle(artifact), artifact);
                    }}
                    outputsExpanded={expandedOutputNodeIds.has(node.id)}
                    onOutputsExpandedChange={updateOutputExpansion}
                    renameRequestId={renameNodeRequest?.nodeId === node.id ? renameNodeRequest.requestId : 0}
                    editInstructionsRequestId={editInstructionsRequest?.nodeId === node.id ? editInstructionsRequest.requestId : 0}
                    onPointerDown={(e) => handleNodePointerDown(node.id, e)}
                    onNodePointerUp={(nodeId) => handlePortPointerUp(nodeId, "input")}
                    onContextMenu={handleNodeContextMenu}
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
                      Empty Workflow Canvas
                    </h3>
                    <p className="text-xs text-neutral-500 leading-relaxed">
                      Start building a workflow by adding agents from the <strong>GAS Servers</strong> panel on the left, or load an existing workflow with <strong>From Storage</strong> or <strong>From File</strong>.
                    </p>
                    <div className="flex items-center justify-center space-x-1.5 text-[10px] text-sky-650 font-bold">
                      <span>Connect GAS services to build Autonomous GIS workflows.</span>
                    </div>
                  </div>
                </div>
              )}
              </div>
            </>
          ) : (
            <div className="relative h-full min-h-0 flex-1" />
          )}

          <div
            className={`absolute inset-0 transition-opacity ${
              activeWorkspaceTab === "map"
                ? "z-10 opacity-100"
                : "hidden pointer-events-none z-0 opacity-0"
            }`}
          >
            <MapView
              artifact={mapArtifact}
              artifacts={mapArtifacts}
              isVisible={activeWorkspaceTab === "map"}
              resizeKey={`${isInspectorVisible}-${inspectorWidth}-${isSidebarVisible}-${sidebarWidth}`}
              onRemoveArtifact={handleRemoveMapArtifact}
            />
          </div>

          <div
            className={`absolute inset-0 bg-white transition-opacity ${
              activeWorkspaceTab === "html"
                ? "z-10 opacity-100"
                : "hidden pointer-events-none z-0 opacity-0"
            }`}
          >
            <HtmlView artifact={htmlArtifact} isVisible={activeWorkspaceTab === "html"} />
          </div>

          <div
            className={`absolute inset-0 bg-white transition-opacity ${
              activeWorkspaceTab === "artifacts"
                ? "z-10 opacity-100"
                : "hidden pointer-events-none z-0 opacity-0"
            }`}
          >
            <ArtifactsView
              artifacts={artifactViewItems}
              selectedUrl={selectedArtifactUrl}
              onSelectArtifact={setSelectedArtifactUrl}
              onDeleteArtifact={handleDeleteArtifactViewItem}
            />
          </div>

          {connectionContextMenu && activeWorkspaceTab === "canvas" && (
            <div
              onPointerDown={(event) => event.stopPropagation()}
              className="fixed z-[900] w-44 overflow-hidden rounded-lg border border-neutral-200 bg-white text-xs shadow-xl"
              style={{ left: connectionContextMenu.x, top: connectionContextMenu.y }}
            >
              <button
                type="button"
                onClick={() => deleteConnection(connectionContextMenu.connectionId)}
                className="flex w-full items-center gap-2 px-3 py-2 text-left font-semibold text-rose-600 hover:bg-rose-50"
              >
                <Trash2 className="h-3.5 w-3.5" />
                <span>Delete Connection</span>
              </button>
            </div>
          )}

          {nodeContextMenu && nodeContextMenuNode && activeWorkspaceTab === "canvas" && (
            <div
              onPointerDown={(event) => event.stopPropagation()}
              className="fixed z-[900] w-44 overflow-hidden rounded-lg border border-neutral-200 bg-white text-xs shadow-xl"
              style={{ left: nodeContextMenu.x, top: nodeContextMenu.y }}
            >
              <button
                type="button"
                disabled={nodeContextMenuNode.status === "running"}
                onClick={() => {
                  closeContextMenus();
                  executeSingleNode(nodeContextMenu.nodeId);
                }}
                className="flex w-full items-center gap-2 px-3 py-2 text-left font-semibold text-neutral-700 hover:bg-neutral-50 disabled:cursor-not-allowed disabled:text-neutral-400 disabled:hover:bg-white"
              >
                <Play className={`h-3.5 w-3.5 ${nodeContextMenuNode.status === "running" ? "text-neutral-400" : "text-emerald-600"}`} />
                <span>Run Agent</span>
              </button>
              <button
                type="button"
                onClick={() => duplicateNode(nodeContextMenu.nodeId)}
                className="flex w-full items-center gap-2 px-3 py-2 text-left font-semibold text-neutral-700 hover:bg-neutral-50"
              >
                <CopyPlus className="h-3.5 w-3.5 text-sky-600" />
                <span>Duplicate</span>
              </button>
              <button
                type="button"
                onClick={() => copyNodeToClipboard(nodeContextMenu.nodeId)}
                className="flex w-full items-center gap-2 px-3 py-2 text-left font-semibold text-neutral-700 hover:bg-neutral-50"
              >
                <Copy className="h-3.5 w-3.5 text-sky-600" />
                <span>Copy</span>
              </button>
              <button
                type="button"
                onClick={() => requestRenameNode(nodeContextMenu.nodeId)}
                className="flex w-full items-center gap-2 px-3 py-2 text-left font-semibold text-neutral-700 hover:bg-neutral-50"
              >
                <Pencil className="h-3.5 w-3.5 text-neutral-500" />
                <span>Rename</span>
              </button>
              <button
                type="button"
                onClick={() => requestEditNodeInstructions(nodeContextMenu.nodeId)}
                className="flex w-full items-center gap-2 px-3 py-2 text-left font-semibold text-neutral-700 hover:bg-neutral-50"
              >
                <FileText className="h-3.5 w-3.5 text-neutral-500" />
                <span>Edit Task Instruction</span>
              </button>
              <button
                type="button"
                onClick={() => {
                  closeContextMenus();
                  handleDescribeAgent(nodeContextMenuNode.serverUrl || "", nodeContextMenuNode.agentId);
                }}
                className="flex w-full items-center gap-2 px-3 py-2 text-left font-semibold text-neutral-700 hover:bg-neutral-50"
              >
                <Info className="h-3.5 w-3.5 text-sky-600" />
                <span>View Details</span>
              </button>
              <div className="h-px bg-neutral-100" />
              <button
                type="button"
                onClick={() => {
                  closeContextMenus();
                  handleDeleteNode(nodeContextMenu.nodeId);
                }}
                className="flex w-full items-center gap-2 px-3 py-2 text-left font-semibold text-rose-600 hover:bg-rose-50"
              >
                <Trash2 className="h-3.5 w-3.5" />
                <span>Delete</span>
              </button>
            </div>
          )}

          {canvasContextMenu && activeWorkspaceTab === "canvas" && (
            <div
              onPointerDown={(event) => event.stopPropagation()}
              className="fixed z-[900] w-48 overflow-visible rounded-lg border border-neutral-200 bg-white py-1 text-xs shadow-xl"
              style={{ left: canvasContextMenu.x, top: canvasContextMenu.y }}
            >
              <button
                type="button"
                disabled={isPipelineRunning || nodes.length === 0}
                onClick={() => {
                  closeContextMenus();
                  handleRunFullPipeline();
                }}
                className="flex w-full items-center gap-2 px-3 py-2 text-left font-semibold text-neutral-700 hover:bg-neutral-50 disabled:cursor-not-allowed disabled:text-neutral-400 disabled:hover:bg-white"
              >
                <Play className={`h-3.5 w-3.5 ${isPipelineRunning || nodes.length === 0 ? "text-neutral-400" : "text-emerald-600"}`} />
                <span>Run Workflow</span>
              </button>
              <button
                type="button"
                disabled={!isPipelineRunning}
                onClick={() => {
                  closeContextMenus();
                  cancelFullPipeline();
                }}
                className="flex w-full items-center gap-2 px-3 py-2 text-left font-semibold text-neutral-700 hover:bg-neutral-50 disabled:cursor-not-allowed disabled:text-neutral-400 disabled:hover:bg-white"
              >
                <X className={`h-3.5 w-3.5 ${isPipelineRunning ? "text-rose-600" : "text-neutral-400"}`} />
                <span>Cancel Workflow</span>
              </button>
              <div className="my-1 h-px bg-neutral-100" />
              <button
                type="button"
                disabled={!copiedNode}
                onClick={() => pasteCopiedNode({ x: canvasContextMenu.canvasX, y: canvasContextMenu.canvasY })}
                className="flex w-full items-center gap-2 px-3 py-2 text-left font-semibold text-neutral-700 hover:bg-neutral-50 disabled:cursor-not-allowed disabled:text-neutral-400 disabled:hover:bg-white"
              >
                <ClipboardPaste className={`h-3.5 w-3.5 ${copiedNode ? "text-sky-600" : "text-neutral-400"}`} />
                <span>Paste</span>
              </button>
              <div className="my-1 h-px bg-neutral-100" />
              <button
                type="button"
                onClick={() => {
                  closeContextMenus();
                  handleSaveWorkflow();
                }}
                className="flex w-full items-center gap-2 px-3 py-2 text-left font-semibold text-neutral-700 hover:bg-neutral-50"
              >
                <Save className="h-3.5 w-3.5 text-sky-600" />
                <span>Save Workflow</span>
              </button>
              <div className="group relative">
                <button
                  type="button"
                  disabled={savedWorkflowsList.length === 0}
                  className="flex w-full items-center gap-2 px-3 py-2 text-left font-semibold text-neutral-700 hover:bg-neutral-50 disabled:cursor-not-allowed disabled:text-neutral-400 disabled:hover:bg-white"
                >
                  <FolderOpen className={`h-3.5 w-3.5 ${savedWorkflowsList.length > 0 ? "text-sky-600" : "text-neutral-400"}`} />
                  <span className="flex-1">Load Workflow</span>
                  <ChevronRight className="h-3.5 w-3.5 text-neutral-400" />
                </button>
                {savedWorkflowsList.length > 0 && (
                  <div className="absolute left-full top-0 hidden w-56 overflow-hidden rounded-lg border border-neutral-200 bg-white text-xs shadow-xl group-hover:block">
                    {savedWorkflowsList.map((workflow) => (
                      <button
                        key={workflow.id}
                        type="button"
                        onClick={() => {
                          closeContextMenus();
                          handleLoadWorkflow(workflow.id);
                        }}
                        className="flex w-full items-center gap-2 px-3 py-2 text-left font-semibold text-neutral-700 hover:bg-neutral-50"
                      >
                        <Network className="h-3.5 w-3.5 shrink-0 text-sky-600" />
                        <span className="truncate">{workflow.name}</span>
                      </button>
                    ))}
                  </div>
                )}
              </div>
              <button
                type="button"
                onClick={() => {
                  closeContextMenus();
                  canvasContextImportInputRef.current?.click();
                }}
                className="flex w-full items-center gap-2 px-3 py-2 text-left font-semibold text-neutral-700 hover:bg-neutral-50"
              >
                <Upload className="h-3.5 w-3.5 text-sky-600" />
                <span>Import Workflow</span>
              </button>
              <div className="my-1 h-px bg-neutral-100" />
              <button
                type="button"
                disabled={nodes.length === 0}
                onClick={() => {
                  closeContextMenus();
                  handleAutoLayout();
                }}
                className="flex w-full items-center gap-2 px-3 py-2 text-left font-semibold text-neutral-700 hover:bg-neutral-50 disabled:cursor-not-allowed disabled:text-neutral-400 disabled:hover:bg-white"
              >
                <LayoutGrid className={`h-3.5 w-3.5 ${nodes.length > 0 ? "text-neutral-500" : "text-neutral-400"}`} />
                <span>Auto Layout</span>
              </button>
              <button
                type="button"
                onClick={() => {
                  closeContextMenus();
                  handleZoomToFit();
                }}
                className="flex w-full items-center gap-2 px-3 py-2 text-left font-semibold text-neutral-700 hover:bg-neutral-50"
              >
                <Maximize2 className="h-3.5 w-3.5 text-neutral-500" />
                <span>Zoom to Fit</span>
              </button>
              <button
                type="button"
                onClick={() => {
                  setZoom((z) => Math.min(1.5, z + 0.1));
                  closeContextMenus();
                }}
                className="flex w-full items-center gap-2 px-3 py-2 text-left font-semibold text-neutral-700 hover:bg-neutral-50"
              >
                <ZoomIn className="h-3.5 w-3.5 text-neutral-500" />
                <span>Zoom In</span>
              </button>
              <button
                type="button"
                onClick={() => {
                  setZoom((z) => Math.max(0.3, z - 0.1));
                  closeContextMenus();
                }}
                className="flex w-full items-center gap-2 px-3 py-2 text-left font-semibold text-neutral-700 hover:bg-neutral-50"
              >
                <ZoomOut className="h-3.5 w-3.5 text-neutral-500" />
                <span>Zoom Out</span>
              </button>
              <button
                type="button"
                onClick={() => {
                  setZoom(1);
                  closeContextMenus();
                }}
                className="flex w-full items-center gap-2 px-3 py-2 text-left font-semibold text-neutral-700 hover:bg-neutral-50"
              >
                <RotateCcw className="h-3.5 w-3.5 text-neutral-500" />
                <span>Reset Zoom</span>
              </button>
              <div className="my-1 h-px bg-neutral-100" />
              <button
                type="button"
                onClick={() => {
                  closeContextMenus();
                  handleClearCanvas();
                }}
                className="flex w-full items-center gap-2 px-3 py-2 text-left font-semibold text-rose-600 hover:bg-rose-50"
              >
                <Trash2 className="h-3.5 w-3.5" />
                <span>Clear Canvas</span>
              </button>
            </div>
          )}

          <input
            ref={canvasContextImportInputRef}
            type="file"
            accept=".json,.gas-workflow.json,application/json"
            className="hidden"
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) {
                handleImportWorkflowFile(file);
                event.target.value = "";
              }
            }}
          />

          <div className="absolute bottom-2 left-2 z-40 flex rounded-lg border border-neutral-200 bg-white/95 p-1 shadow-md backdrop-blur">
            <button
              type="button"
              onClick={() => setActiveWorkspaceTab("canvas")}
              className={`flex h-8 items-center gap-1.5 rounded-md px-3 text-xs font-bold transition-colors ${
                activeWorkspaceTab === "canvas"
                  ? "bg-neutral-900 text-white"
                  : "text-neutral-600 hover:bg-neutral-100 hover:text-neutral-900"
              }`}
            >
              <Network className="h-3.5 w-3.5" />
              <span>Canvas</span>
            </button>
            <button
              type="button"
              onClick={() => setActiveWorkspaceTab("map")}
              className={`flex h-8 items-center gap-1.5 rounded-md px-3 text-xs font-bold transition-colors ${
                activeWorkspaceTab === "map"
                  ? "bg-neutral-900 text-white"
                  : "text-neutral-600 hover:bg-neutral-100 hover:text-neutral-900"
              }`}
            >
              <MapIcon className="h-3.5 w-3.5" />
              <span>Map</span>
            </button>
            <button
              type="button"
              onClick={() => setActiveWorkspaceTab("html")}
              className={`flex h-8 items-center gap-1.5 rounded-md px-3 text-xs font-bold transition-colors ${
                activeWorkspaceTab === "html"
                  ? "bg-neutral-900 text-white"
                  : "text-neutral-600 hover:bg-neutral-100 hover:text-neutral-900"
              }`}
            >
              <FileCode className="h-3.5 w-3.5" />
              <span>HTML</span>
            </button>
            <button
              type="button"
              onClick={() => setActiveWorkspaceTab("artifacts")}
              className={`flex h-8 items-center gap-1.5 rounded-md px-3 text-xs font-bold transition-colors ${
                activeWorkspaceTab === "artifacts"
                  ? "bg-neutral-900 text-white"
                  : "text-neutral-600 hover:bg-neutral-100 hover:text-neutral-900"
              }`}
            >
              <Files className="h-3.5 w-3.5" />
              <span>Artifacts</span>
            </button>
          </div>

        </div>

        {isInspectorVisible ? (
          <>
            {/* RESIZER DRAG HANDLE RIGHT */}
            <div
              onPointerDown={handleInspectorResize}
              className="relative w-1 hover:bg-sky-400 active:bg-sky-500 cursor-col-resize shrink-0 bg-neutral-200 dark:bg-neutral-800 transition-colors z-30 flex items-center justify-center group"
            >
              <div className="h-8 w-px bg-neutral-400 dark:bg-neutral-600 rounded-full group-hover:bg-white" />
              <button
                type="button"
                title="Hide details panel"
                onPointerDown={(e) => e.stopPropagation()}
                onClick={() => setIsInspectorVisible(false)}
                className="absolute top-3 left-1/2 -translate-x-1/2 flex h-6 w-5 items-center justify-center rounded-full border border-neutral-200 bg-white text-neutral-600 shadow-sm hover:bg-neutral-50 hover:text-sky-700"
              >
                <ChevronRight className="h-3.5 w-3" />
              </button>
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
                  updateNode(nodeId, updates);
                }}
                onDescribeAgent={(serverUrl, agentId) => handleDescribeAgent(serverUrl, agentId)}
                onClose={() => setSelectedNodeId(null)}
                onOpenPreview={handleOpenArtifactPreview}
                onViewAllArtifacts={handleViewAllArtifacts}
                width={inspectorWidth}
              />
            )}
          </>
        ) : (
          <div className="w-10 shrink-0 border-l border-neutral-200 bg-white flex justify-center pt-3">
            <button
              type="button"
              title="Show details panel"
              onClick={() => setIsInspectorVisible(true)}
              className="flex h-7 w-7 items-center justify-center rounded-lg border border-neutral-200 bg-white text-neutral-600 shadow-sm hover:bg-neutral-50 hover:text-sky-700"
            >
              <ChevronLeft className="h-4 w-4" />
            </button>
          </div>
        )}

      </div>

      {/* CREDENTIALS CONFIG OVERLAY MODAL */}
      <CredentialsVault
        isOpen={isCredentialsOpen}
        onClose={() => setIsCredentialsOpen(false)}
        initialKeys={credentials}
        onSave={handleSaveCredentials}
      />

      {/* SAVE PROMPT MODAL */}
      {savePromptOpen && (
        <div className="fixed inset-0 bg-black/60 z-[100] flex items-center justify-center p-4">
          <div className="bg-white dark:bg-neutral-900 rounded-xl shadow-2xl p-6 max-w-md w-full border border-neutral-200 dark:border-neutral-800">
            <h3 className="text-lg font-bold mb-2">Save Workflow</h3>
            <p className="text-xs text-neutral-500 mb-4">Name this workflow, then choose where to save it.</p>
            <input
              type="text"
              autoFocus
              className="w-full px-3 py-2 bg-neutral-100 dark:bg-neutral-850 dark:text-white rounded border border-neutral-300 dark:border-neutral-700 text-sm mb-4 focus:outline-none focus:ring-2 focus:ring-sky-500"
              placeholder="e.g. My Custom Analysis..."
              value={workflowNameInput}
              onChange={(e) => setWorkflowNameInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') submitSaveWorkflow(workflowNameInput);
                if (e.key === 'Escape') setSavePromptOpen(false);
              }}
            />

            <div className="space-y-2">
              <button
                onClick={() => submitSaveWorkflow(workflowNameInput)}
                disabled={!workflowNameInput.trim()}
                className="w-full rounded-lg border border-sky-200 bg-sky-50 px-3 py-3 text-left transition-colors hover:bg-sky-100 disabled:opacity-50 disabled:hover:bg-sky-50"
              >
                <span className="block text-sm font-bold text-sky-800">Save to Browser Storage</span>
                <span className="mt-1 block text-xs leading-relaxed text-sky-700">
                  Stores this workflow in this browser on this computer. It will appear under From Storage, but it is not available in other browsers or after browser storage is cleared.
                </span>
              </button>

              <button
                onClick={() => {
                  handleDownloadWorkflow();
                  setSavePromptOpen(false);
                  setWorkflowNameInput("");
                }}
                disabled={!workflowNameInput.trim()}
                className="w-full rounded-lg border border-neutral-200 bg-white px-3 py-3 text-left transition-colors hover:bg-neutral-50 disabled:opacity-50 disabled:hover:bg-white"
              >
                <span className="block text-sm font-bold text-neutral-800">Save as JSON File</span>
                <span className="mt-1 block text-xs leading-relaxed text-neutral-500">
                  Downloads a portable .gas-workflow.json file that can be moved to another machine or loaded later with From File.
                </span>
              </button>
            </div>

            <div className="mt-4 flex justify-end">
              <button onClick={() => setSavePromptOpen(false)} className="px-4 py-1.5 text-xs font-semibold rounded hover:bg-neutral-100 dark:hover:bg-neutral-800 transition-colors">Cancel</button>
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
