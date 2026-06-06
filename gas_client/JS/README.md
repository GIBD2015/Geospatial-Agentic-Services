# @gibd/gas-client

JavaScript SDK for Geospatial Agentic Services (GAS).

This package contains only the lightweight client layer. It does not install a GAS server or heavy geospatial runtime dependencies. It runs natively in Node.js (18+), browsers, and Edge environments with zero external dependencies.

## Install

Install from npm:

```bash
npm install @gibd/gas-client
```

## Quick Start

```javascript
import { GasClient } from '@gibd/gas-client';

const client = new GasClient("https://your-gas-server.com");

// List available agents
console.log(await client.listAgents());

const agent = client.agent("geospatial_data_retrieval_agent");

// Execute a task synchronously
const result = await agent.executeTask(
    "Download Pennsylvania county boundaries from Census Bureau.",
    {
        mode: "sync",
        credentials: { "OPENAI_API_KEY": "YOUR_OPENAI_API_KEY" }
    }
);

// Print a clean, formatted summary of execution metrics and outputs to console
client.printTaskSummary(result);
```

## Accessing Artifacts Natively

The package includes `client.printTaskSummary(result)`, which automatically logs details about generated artifacts, errors, and warnings to the console. To access or manipulate the generated artifacts directly in your JavaScript code, you can inspect the canonical JSON response object natively:

```javascript
// Extract all artifacts
const artifacts = result.outputs?.artifacts || [];

// Filter for specific formats (e.g., CSV)
const csvArtifacts = artifacts.filter(a => a.format?.toLowerCase() === 'csv');

// Grab specific artifact URLs
const csvUrls = csvArtifacts.map(a => a.url).filter(Boolean);
```

Client-level credentials are optional defaults. You can omit them at client creation and pass credentials per task, or provide `defaultCredentials` with the provider-specific keys expected by your server, such as `GEMINI_API_KEY`. Before choosing a credential field name, users and orchestrating agents should inspect the selected agent's `DescribeAgent` JSON and use the key name that agent advertises. Task-level credentials override client defaults when needed.

```javascript
const client = new GasClient(
    "https://your-gas-server.com",
    {
        defaultCredentials: {
            "GEMINI_API_KEY": "YOUR_GEMINI_API_KEY",
        }
    }
);
```

## Streaming Tasks

For real-time insight into long-running tasks, use the stream mode. The client handles chunk decoding automatically and exposes events via a native JavaScript Async Generator:

```javascript
const eventStream = await agent.executeTask(
    "Download Pennsylvania county boundaries from Census Bureau.",
    { mode: "stream" }
);

for await (const event of eventStream) {
    client.printStreamEvent(event);
    
    if (event.event === "task_result") {
        const result = event.payload;
        client.printTaskSummary(result);
    }
}
```

The SDK also provides a convenient, automated wrapper that handles iterating, printing live progress events, and returning the final summary payoff all in a single call:

```javascript
// Using the client directly with an agent instance
const result = await client.runStreamingTask(
    agent,
    "Download Pennsylvania county boundaries from Census Bureau."
);

// Or using an agent-bound client instance directly
const result = await agent.runStreamingTask(
    "Download Pennsylvania county boundaries from Census Bureau."
);
```

## Canonical GAS Request Body

Credential requirements are defined by each service's `DescribeAgent` capability document. Inspect the selected agent before submitting a task: one service may require an OpenAI key, another may use a different model provider, another may require data-source credentials, and deterministic services may not need an LLM key.

```javascript
const requestBody = client.buildExecuteTaskRequest(
    "Create a web mapping app.",
    {
        mode: "stream",
        inputDatasets: [
            "https://example.com/counties.geojson",
        ],
        artifactDelivery: "URL",
        // Optional: include credentials here only when this call needs a key
        // and the client was not created with suitable default credentials.
        credentials: {
            "OPENAI_API_KEY": "YOUR_OPENAI_API_KEY",
        }
    }
);

const eventStream = await client.agent("web_mapping_app_agent").executeTaskRequest(requestBody);

for await (const event of eventStream) {
    client.printStreamEvent(event);
}
```

## Public API

```javascript
import {
    GasClient,
    GasAgentClient,
    GasClientError,
    GasTaskTimeoutError
} from '@gibd/gas-client';
```

### Important Methods

#### GasClient

- `getCapabilities(refresh)`
- `listAgents(refresh)`
- `describeAgent(agentId, refresh)`
- `agent(agentId)`
- `executeTask(agentId, instructions, options)`
- `executeTaskRequest(agentId, requestBody, options)`
- `runStreamingTask(agentOrAgentId, instructions, options)`
- `getTaskStatus(agentId, taskId, options)`
- `getTaskResult(agentId, taskId, options)`
- `waitForTask(agentId, taskId, options)`
- `cancelTask(agentId, taskId)`
- `buildExecuteTaskRequest(instructions, options)`
- `encodeDatasetFileBuffer(filename, buffer)`
- `getTaskId(taskResponse)`
- `getTaskStatusValue(taskResponse)`
- `printStreamEvent(event, options)`
- `printTaskSummary(taskResult)`

#### GasAgentClient

- `describe(refresh)`
- `operations()`
- `status()`
- `executeTask(instructions, options)`
- `runStreamingTask(instructions, options)`
- `executeTaskRequest(requestBody, options)`
- `getTaskStatus(taskId)`
- `getTaskResult(taskId)`
- `waitForTask(taskId, options)`
- `cancelTask(taskId)`

## License

ISC
