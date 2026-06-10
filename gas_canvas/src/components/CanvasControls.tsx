import React from "react";
import {
  Play,
  Trash2,
  Save,
  FolderOpen,
  Upload,
  Network,
  Pencil,
  ZoomIn,
  ZoomOut,
  RotateCcw,
  LayoutGrid,
  Maximize2,
  X
} from "lucide-react";

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
  zoom,
  onZoomIn,
  onZoomOut,
  onResetZoom,
  onAutoLayout,
  onZoomToFit,
}) => {
  const fileInputRef = React.useRef<HTMLInputElement>(null);

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

        <button
          onClick={onSaveWorkflow}
          className="flex items-center space-x-1.5 px-3 py-1.5 border border-neutral-200 dark:border-neutral-800 hover:bg-neutral-50 dark:hover:bg-neutral-950 rounded-lg text-xs font-semibold text-neutral-700 dark:text-neutral-300 transition-colors"
        >
          <Save className="w-3.5 h-3.5" />
          <span>Save Workflow</span>
        </button>

        {savedWorkflows.length > 0 && (
          <div className="relative group">
            <button className="flex items-center space-x-1 px-3 py-1.5 border border-neutral-200 dark:border-neutral-800 hover:bg-neutral-50 dark:hover:bg-neutral-950 rounded-lg text-xs font-semibold text-neutral-700 dark:text-neutral-300 transition-colors">
              <FolderOpen className="w-3.5 h-3.5" />
              <span>From Storage</span>
            </button>
            <div className="absolute left-0 top-full pt-1 hidden group-hover:block z-50">
              <div className="w-56 bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 rounded-lg shadow-lg overflow-hidden">
                {savedWorkflows.map((flow) => (
                  <div
                    key={flow.id}
                    className="flex items-center border-b last:border-0 border-neutral-100 dark:border-neutral-800/80"
                  >
                    <button
                      onClick={() => onLoadWorkflow(flow.id)}
                      className="min-w-0 flex-1 text-left px-3 py-2 text-xs hover:bg-neutral-50 dark:hover:bg-neutral-950 text-neutral-700 dark:text-neutral-300 font-medium transition-colors pointer-events-auto flex items-center space-x-2"
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
                      className="m-1 mr-0 flex h-6 w-6 shrink-0 items-center justify-center rounded text-neutral-400 hover:bg-sky-50 hover:text-sky-600 transition-colors"
                    >
                      <Pencil className="w-3.5 h-3.5" />
                    </button>
                    <button
                      onClick={(event) => {
                        event.stopPropagation();
                        onDeleteWorkflow(flow.id);
                      }}
                      title={`Delete saved workflow ${flow.name}`}
                      className="m-1 flex h-6 w-6 shrink-0 items-center justify-center rounded text-neutral-400 hover:bg-rose-50 hover:text-rose-600 transition-colors"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </div>
                ))}
              </div>
            </div>
          </div>
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
        <button
          onClick={() => fileInputRef.current?.click()}
          className="flex items-center space-x-1.5 px-3 py-1.5 border border-neutral-200 dark:border-neutral-800 hover:bg-neutral-50 dark:hover:bg-neutral-950 rounded-lg text-xs font-semibold text-neutral-700 dark:text-neutral-300 transition-colors"
        >
          <Upload className="w-3.5 h-3.5" />
          <span>From File</span>
        </button>

        <button
          onClick={onClearCanvas}
          className="flex items-center space-x-1 px-3 py-1.5 border border-neutral-200 dark:border-neutral-800 hover:bg-neutral-50 dark:hover:bg-neutral-950 rounded-lg text-xs font-semibold text-neutral-700 dark:text-neutral-300 transition-colors"
        >
          <Trash2 className="w-3.5 h-3.5" />
          <span>Clear Canvas</span>
        </button>
      </div>

      <div className="flex items-center space-x-1.5 border-l pl-3 border-neutral-200 dark:border-neutral-850">
        <span className="text-[10px] text-neutral-400 font-mono pr-1.5">Zoom: {Math.round(zoom * 100)}%</span>

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
