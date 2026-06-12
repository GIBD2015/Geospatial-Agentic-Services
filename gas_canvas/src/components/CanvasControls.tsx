import React from "react";
import {
  Play,
  Trash2,
  Save,
  FolderOpen,
  Upload,
  Network,
  Pencil,
  ChevronDown,
  ZoomIn,
  ZoomOut,
  RotateCcw,
  LayoutGrid,
  Maximize2,
  X
} from "lucide-react";
import { AgentNode } from "../types";

const ThreeNodeWorkflowIcon: React.FC<React.SVGProps<SVGSVGElement>> = ({ className, ...props }) => (
  <svg
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
    className={className}
    {...props}
  >
    <rect x="4" y="4" width="6" height="6" rx="1.2" />
    <rect x="14" y="14" width="6" height="6" rx="1.2" />
    <rect x="4" y="14" width="5" height="5" rx="1" />
    <path d="M10 7h3.5a2.5 2.5 0 0 1 2.5 2.5V14" />
    <path d="M9 16.5h5" />
  </svg>
);

interface CanvasControlsProps {
  onClearCanvas: () => void;
  onRunFullPipeline: () => void;
  onCancelFullPipeline: () => void;
  isPipelineRunning: boolean;
  onLoadPreset: (presetName: string) => void;
  onSaveWorkflow: () => void;
  onLoadWorkflow: (workflowId: string) => void;
  onDeleteWorkflow: (workflowId: string) => void;
  onRenameWorkflow: (workflowId: string) => void;
  onImportWorkflowFile: (file: File) => void;
  savedWorkflows: Array<{ id: string; name: string }>;
  statusCounts: Partial<Record<AgentNode["status"], number>>;
  zoom: number;
  onZoomIn: () => void;
  onZoomOut: () => void;
  onResetZoom: () => void;
  onAutoLayout: () => void;
  onZoomToFit: () => void;
}

export const CanvasControls: React.FC<CanvasControlsProps> = ({
  onClearCanvas,
  onRunFullPipeline,
  onCancelFullPipeline,
  isPipelineRunning,
  onSaveWorkflow,
  onLoadWorkflow,
  onDeleteWorkflow,
  onRenameWorkflow,
  onImportWorkflowFile,
  savedWorkflows,
  statusCounts,
  zoom,
  onZoomIn,
  onZoomOut,
  onResetZoom,
  onAutoLayout,
  onZoomToFit,
}) => {
  const fileInputRef = React.useRef<HTMLInputElement>(null);
  const allStatusItems: Array<{ status: AgentNode["status"]; label: string; dot: string }> = [
    { status: "running", label: "running", dot: "bg-sky-500 animate-pulse" },
    { status: "completed", label: "completed", dot: "bg-emerald-500" },
    { status: "waiting", label: "waiting", dot: "bg-neutral-400" },
    { status: "error", label: "error", dot: "bg-rose-500" },
    { status: "canceled", label: "canceled", dot: "bg-amber-500" },
    { status: "idle", label: "idle", dot: "bg-neutral-300" }
  ];
  const statusItems = allStatusItems.filter((item) => (statusCounts[item.status] || 0) > 0);

  return (
    <div className="relative z-40 bg-white dark:bg-neutral-900 border-b border-neutral-200 dark:border-neutral-800 px-3 py-2 flex flex-wrap items-center justify-between gap-2 select-none">
      <div className="flex items-center space-x-2">
        <button
          onClick={onRunFullPipeline}
          disabled={isPipelineRunning}
          className="flex items-center space-x-1.5 px-3 py-1.5 bg-sky-600 hover:bg-sky-500 text-white rounded-lg text-xs font-semibold shadow-sm transition-colors disabled:opacity-50"
        >
          <Play className="w-3.5 h-3.5 fill-current animate-pulse-slow" />
          <span>{isPipelineRunning ? "Running Workflow..." : "Run Workflow"}</span>
        </button>

        {isPipelineRunning && (
          <button
            onClick={onCancelFullPipeline}
            title="Cancel running workflow"
            className="flex h-7 w-7 items-center justify-center rounded-lg border border-rose-200 bg-white text-rose-600 shadow-sm transition-colors hover:bg-rose-50 hover:text-rose-700"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        )}

        <input
          ref={fileInputRef}
          type="file"
          accept=".json,.gas-workflow.json,application/json"
          className="hidden"
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) {
              onImportWorkflowFile(file);
              event.target.value = "";
            }
          }}
        />

        <div className="relative group/workflow">
          <button className="flex items-center space-x-1.5 px-3 py-1.5 border border-neutral-200 dark:border-neutral-800 hover:bg-neutral-50 dark:hover:bg-neutral-950 rounded-lg text-xs font-semibold text-neutral-700 dark:text-neutral-300 transition-colors">
            <ThreeNodeWorkflowIcon className="w-3.5 h-3.5" />
            <span>Workflow</span>
            <ChevronDown className="w-3 h-3 text-neutral-400" />
          </button>
          <div className="absolute left-0 top-full z-50 hidden pt-1 group-hover/workflow:block">
            <div className="w-52 overflow-visible rounded-lg border border-neutral-200 bg-white py-1 text-xs shadow-lg dark:border-neutral-800 dark:bg-neutral-900">
              <button
                onClick={onSaveWorkflow}
                className="flex w-full items-center space-x-2 px-3 py-2 text-left font-semibold text-neutral-700 transition-colors hover:bg-neutral-50 dark:text-neutral-300 dark:hover:bg-neutral-950"
              >
                <Save className="w-3.5 h-3.5 text-sky-600" />
                <span>Save Workflow</span>
              </button>
              <div className="my-1 h-px bg-neutral-100 dark:bg-neutral-800" />
              <div className="group/storage relative">
                <button
                  disabled={savedWorkflows.length === 0}
                  className="flex w-full items-center space-x-2 px-3 py-2 text-left font-semibold text-neutral-700 transition-colors hover:bg-neutral-50 disabled:cursor-not-allowed disabled:text-neutral-400 disabled:hover:bg-white dark:text-neutral-300 dark:hover:bg-neutral-950 dark:disabled:hover:bg-neutral-900"
                >
                  <FolderOpen className={`w-3.5 h-3.5 ${savedWorkflows.length > 0 ? "text-sky-600" : "text-neutral-400"}`} />
                  <span className="flex-1">Load from Storage</span>
                  <ChevronDown className="w-3 h-3 -rotate-90 text-neutral-400" />
                </button>
                {savedWorkflows.length > 0 && (
                  <div className="absolute left-full top-0 hidden w-56 overflow-hidden rounded-lg border border-neutral-200 bg-white text-xs shadow-lg group-hover/storage:block dark:border-neutral-800 dark:bg-neutral-900">
                    {savedWorkflows.map((flow) => (
                      <div
                        key={flow.id}
                        className="flex items-center border-b border-neutral-100 last:border-0 dark:border-neutral-800/80"
                      >
                        <button
                          onClick={() => onLoadWorkflow(flow.id)}
                          className="pointer-events-auto flex min-w-0 flex-1 items-center space-x-2 px-3 py-2 text-left text-xs font-medium text-neutral-700 transition-colors hover:bg-neutral-50 dark:text-neutral-300 dark:hover:bg-neutral-950"
                        >
                          <Network className="w-3.5 h-3.5 shrink-0 text-sky-600" />
                          <span className="truncate">{flow.name}</span>
                        </button>
                        <button
                          onClick={(event) => {
                            event.stopPropagation();
                            onRenameWorkflow(flow.id);
                          }}
                          title={`Rename saved workflow ${flow.name}`}
                          className="m-1 mr-0 flex h-6 w-6 shrink-0 items-center justify-center rounded text-neutral-400 transition-colors hover:bg-sky-50 hover:text-sky-600"
                        >
                          <Pencil className="w-3.5 h-3.5" />
                        </button>
                        <button
                          onClick={(event) => {
                            event.stopPropagation();
                            onDeleteWorkflow(flow.id);
                          }}
                          title={`Delete saved workflow ${flow.name}`}
                          className="m-1 flex h-6 w-6 shrink-0 items-center justify-center rounded text-neutral-400 transition-colors hover:bg-rose-50 hover:text-rose-600"
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    ))}
                  </div>
                )}
              </div>
              <button
                onClick={() => fileInputRef.current?.click()}
                className="flex w-full items-center space-x-2 px-3 py-2 text-left font-semibold text-neutral-700 transition-colors hover:bg-neutral-50 dark:text-neutral-300 dark:hover:bg-neutral-950"
              >
                <Upload className="w-3.5 h-3.5 text-sky-600" />
                <span>Import from File</span>
              </button>
            </div>
          </div>
        </div>

        <button
          onClick={onClearCanvas}
          className="flex items-center space-x-1 px-3 py-1.5 border border-neutral-200 dark:border-neutral-800 hover:bg-neutral-50 dark:hover:bg-neutral-950 rounded-lg text-xs font-semibold text-neutral-700 dark:text-neutral-300 transition-colors"
        >
          <Trash2 className="w-3.5 h-3.5" />
          <span>Clear Canvas</span>
        </button>
      </div>

      {statusItems.length > 0 && (
        <div className="hidden">
          <div className="flex max-w-full items-center gap-2 overflow-hidden rounded-lg border border-neutral-200 bg-neutral-50 px-2.5 py-1.5 text-[11px] font-semibold text-neutral-600 dark:border-neutral-800 dark:bg-neutral-950 dark:text-neutral-300">
            {statusItems.map((item, index) => (
              <React.Fragment key={item.status}>
                {index > 0 && <span className="text-neutral-300 dark:text-neutral-700">·</span>}
                <span className="flex shrink-0 items-center gap-1.5">
                  <span className={`h-2 w-2 rounded-full ${item.dot}`} />
                  <span>{statusCounts[item.status]}</span>
                  <span>{item.label}</span>
                </span>
              </React.Fragment>
            ))}
          </div>
        </div>
      )}

      <div className="flex items-center space-x-1.5 border-l pl-3 border-neutral-200 dark:border-neutral-850">
        <span className="text-[11px] text-neutral-500 font-mono pr-1.5">Zoom: {Math.round(zoom * 100)}%</span>

        <button
          onClick={onAutoLayout}
          title="Auto-layout & Fit View"
          className="p-1.5 hover:bg-neutral-50 border border-neutral-200 dark:border-neutral-800 dark:hover:bg-neutral-950 text-neutral-600 dark:text-neutral-400 rounded-md transition-colors"
        >
          <LayoutGrid className="w-3.5 h-3.5" />
        </button>

        <button
          onClick={onZoomToFit}
          title="Zoom to fit"
          className="p-1.5 hover:bg-neutral-50 border border-neutral-200 dark:border-neutral-800 dark:hover:bg-neutral-950 text-neutral-600 dark:text-neutral-400 rounded-md transition-colors"
        >
          <Maximize2 className="w-3.5 h-3.5" />
        </button>

        <button
          onClick={onZoomOut}
          title="Zoom out"
          className="p-1.5 hover:bg-neutral-50 border border-neutral-200 dark:border-neutral-800 dark:hover:bg-neutral-950 text-neutral-600 dark:text-neutral-400 rounded-md transition-colors"
        >
          <ZoomOut className="w-3.5 h-3.5" />
        </button>

        <button
          onClick={onZoomIn}
          title="Zoom in"
          className="p-1.5 hover:bg-neutral-50 border border-neutral-200 dark:border-neutral-800 dark:hover:bg-neutral-950 text-neutral-600 dark:text-neutral-400 rounded-md transition-colors"
        >
          <ZoomIn className="w-3.5 h-3.5" />
        </button>

        <button
          onClick={onResetZoom}
          title="Reset canvas viewport scale"
          className="p-1.5 hover:bg-neutral-50 border border-neutral-200 dark:border-neutral-800 dark:hover:bg-neutral-950 text-neutral-650 dark:text-neutral-300 rounded-md transition-colors"
        >
          <RotateCcw className="w-3.5 h-3.5" />
        </button>
      </div>
    </div>
  );
};
