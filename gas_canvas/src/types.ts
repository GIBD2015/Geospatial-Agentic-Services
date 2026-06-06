export interface GasAgentInfo {
  agent_id: string;
  name: string;
  DescribeAgent: string;
}

export interface GasCapabilities {
  title: string;
  description: string;
  version: string;
  base_url: string;
  provider: {
    name: string;
    website: string;
    contact?: {
      name?: string;
      email?: string;
    };
  };
  operations: Array<{
    operation_id: string;
    name: string;
    method: string;
    url: string;
    description: string;
  }>;
  agents: GasAgentInfo[];
}

export interface WebMappingAppArtifact {
  type: string;
  format: string;
  url: string;
  filename?: string;
  description?: string;
}

export interface TaskArtifact {
  name: string;
  format: string;
  url: string;
  description?: string;
}

export interface TaskResult {
  task_id?: string;
  status?: string;
  outputs?: {
    artifacts?: TaskArtifact[];
    summary?: string;
  };
  provenance?: {
    llm_calls?: number;
    duration?: number;
    token_usage?: {
      prompt_tokens?: number;
      completion_tokens?: number;
      total_tokens?: number;
    };
  };
  error?: string;
}

export interface StreamEvent {
  event: string;
  payload?: any;
  timestamp?: string;
}

export interface NodeConnection {
  id: string;
  sourceId: string;
  targetId: string;
  artifacts?: string[]; // Specific artifact names mapped
}

export interface AgentNode {
  id: string;
  agentId: string;
  name: string;
  x: number;
  y: number;
  instructions: string;
  inputDatasets: string[]; // URLs or files
  credentials: {
    OPENAI_API_KEY?: string;
  };
  excludedInputs?: string[];
  serverUrl?: string;
  status: "idle" | "running" | "completed" | "error";
  logs: string[];
  results?: TaskResult;
}

export interface SavedWorkflow {
  id: string;
  name: string;
  description: string;
  nodes: AgentNode[];
  connections: NodeConnection[];
  createdAt: string;
}
