import React from "react";
import { 
  Play, 
  Trash2, 
  Save, 
  FolderOpen, 
  Sparkles, 
  ZoomIn, 
  ZoomOut, 
  RotateCcw,
  Workflow,
  HelpCircle,
  FileCheck,
  LayoutGrid
} from "lucide-react";
import { AgentNode, NodeConnection } from "../types";

interface CanvasControlsProps {
  onClearCanvas: () => void;
  onRunFullPipeline: () => void;
  isPipelineRunning: boolean;
  onLoadPreset: (presetName: string) => void;
  onSaveWorkflow: () => void;
  onLoadWorkflow: (workflowId: string) => void;
  savedWorkflows: Array<{ id: string; name: string }>;
  zoom: number;
  onZoomIn: () => void;
  onZoomOut: () => void;
  onResetZoom: () => void;
  onAutoLayout: () => void;
}

export const CanvasControls: React.FC<CanvasControlsProps> = ({
  onClearCanvas,
  onRunFullPipeline,
  isPipelineRunning,
  onLoadPreset,
  onSaveWorkflow,
  onLoadWorkflow,
  savedWorkflows,
  zoom,
  onZoomIn,
  onZoomOut,
  onResetZoom,
  onAutoLayout,
}) => {
  return (
    <div className="relative z-40 bg-white dark:bg-neutral-900 border-b border-neutral-200 dark:border-neutral-800 p-3 flex flex-wrap items-center justify-between gap-3 select-none">
      {/* LEFT: EXECUTION RUNNERS & WORKFLOW STATE */}
      <div className="flex items-center space-x-2">
        <button
          onClick={onRunFullPipeline}
          disabled={isPipelineRunning}
          className="flex items-center space-x-2 px-4 py-2 bg-neutral-950 hover:bg-neutral-800 dark:bg-neutral-100 dark:hover:bg-neutral-200 dark:text-neutral-900 text-white rounded-lg text-sm font-bold shadow-sm transition-colors disabled:opacity-50"
        >
          <Play className="w-4 h-4 fill-current animate-pulse-slow" />
          <span>{isPipelineRunning ? "Running Pipeline..." : "Execute Workspace Pipeline"}</span>
        </button>

        <button
          onClick={onSaveWorkflow}
          className="flex items-center space-x-1.5 px-3 py-2 border border-neutral-200 dark:border-neutral-800 hover:bg-neutral-50 dark:hover:bg-neutral-950 rounded-lg text-xs font-semibold text-neutral-700 dark:text-neutral-300 transition-colors"
        >
          <Save className="w-4 h-4" />
          <span>Save Pipeline</span>
        </button>

        {/* Load dropdown */}
        {savedWorkflows.length > 0 && (
          <div className="relative group">
            <button className="flex items-center space-x-1 px-3 py-2 border border-neutral-200 dark:border-neutral-800 hover:bg-neutral-50 dark:hover:bg-neutral-950 rounded-lg text-xs font-semibold text-neutral-700 dark:text-neutral-300 transition-colors">
              <FolderOpen className="w-4 h-4" />
              <span>Load Saved</span>
            </button>
            <div className="absolute left-0 top-full pt-1 hidden group-hover:block z-50">
              <div className="w-48 bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 rounded-lg shadow-lg overflow-hidden">
                {savedWorkflows.map((flow) => (
                  <button
                    key={flow.id}
                    onClick={() => onLoadWorkflow(flow.id)}
                    className="w-full text-left px-3 py-2 text-xs hover:bg-neutral-50 dark:hover:bg-neutral-950 text-neutral-700 dark:text-neutral-300 truncate font-medium border-b last:border-0 border-neutral-100 dark:border-neutral-800/80 transition-colors pointer-events-auto"
                  >
                    📁 {flow.name}
                  </button>
                ))}
              </div>
            </div>
          </div>
        )}

        <button
          onClick={onClearCanvas}
          className="flex items-center space-x-1 px-3 py-2 border border-rose-100 dark:border-rose-950/20 text-rose-600 hover:bg-rose-50 dark:hover:bg-rose-950/25 rounded-lg text-xs font-semibold transition-colors"
        >
          <Trash2 className="w-4 h-4" />
          <span>Wipe Canvas</span>
        </button>
      </div>



      {/* RIGHT: VIEWPORT ZOOM / PAN CONTROL PANEL */}
      <div className="flex items-center space-x-1.5 border-l pl-3 border-neutral-200 dark:border-neutral-850">
        <span className="text-[10px] text-neutral-400 font-mono pr-1.5">Zoom: {Math.round(zoom * 100)}%</span>
        
        <button
          onClick={onAutoLayout}
          title="Auto-layout & Fit View"
          className="p-1.5 hover:bg-neutral-50 border border-neutral-200 dark:border-neutral-800 dark:hover:bg-neutral-950 text-neutral-600 dark:text-neutral-400 rounded-lg transition-colors"
        >
          <LayoutGrid className="w-4 h-4" />
        </button>

        <button
          onClick={onZoomOut}
          title="Zoom out"
          className="p-1.5 hover:bg-neutral-50 border border-neutral-200 dark:border-neutral-800 dark:hover:bg-neutral-950 text-neutral-600 dark:text-neutral-400 rounded-lg transition-colors"
        >
          <ZoomOut className="w-4 h-4" />
        </button>
        
        <button
          onClick={onZoomIn}
          title="Zoom in"
          className="p-1.5 hover:bg-neutral-50 border border-neutral-200 dark:border-neutral-800 dark:hover:bg-neutral-950 text-neutral-600 dark:text-neutral-400 rounded-lg transition-colors"
        >
          <ZoomIn className="w-4 h-4" />
        </button>
        
        <button
          onClick={onResetZoom}
          title="Reset canvas viewport scale"
          className="p-1.5 hover:bg-neutral-50 border border-neutral-200 dark:border-neutral-800 dark:hover:bg-neutral-950 text-neutral-650 dark:text-neutral-300 rounded-lg transition-colors"
        >
          <RotateCcw className="w-4 h-4" />
        </button>
      </div>
    </div>
  );
};
