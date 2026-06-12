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
  name?: string;
  filename?: string;
  original_filename?: string;
  label?: string;
  role?: string;
  format?: string;
  mime_type?: string;
  type?: string;
  url: string;
  description?: string;
  size_bytes?: number;
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

export type SourceCredentials = Record<string, Record<string, string>>;

export interface SourceCredentialSpec {
  sourceId: string;
  name: string;
  description?: string;
  fields: string[];
  registrationUrl?: string;
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
    source_credentials?: SourceCredentials;
  };
  excludedInputs?: string[];
  serverUrl?: string;
  status: "idle" | "waiting" | "running" | "completed" | "error" | "canceled";
  logs: string[];
  currentTaskId?: string;
  lastRequest?: any;
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
