

// ------------------------------------------------------------------
// Custom Error Classes
// ------------------------------------------------------------------
export class GasClientError extends Error {
    constructor(message) {
        super(message);
        this.name = 'GasClientError';
    }
}

export class GasTaskTimeoutError extends Error {
    constructor(message) {
        super(message);
        this.name = 'GasTaskTimeoutError';
    }
}

// ------------------------------------------------------------------
// GasAgentClient Class
// ------------------------------------------------------------------
export class GasAgentClient {
    constructor(client, agentId) {
        this.client = client;
        this.agentId = agentId;
    }

    async describe(refresh = false) {
        return await this.client.describeAgent(this.agentId, refresh);
    }

    async operations() {
        return await this.client.getSupportedOperations(this.agentId);
    }

    async status() {
        return await this.client.getAgentStatus(this.agentId);
    }

    async executeTask(instructions, options = {}) {
        return await this.client.executeTask(this.agentId, instructions, options);
    }

    async runStreamingTask(instructions, options = {}) {
        return await this.client.runStreamingTask(this, instructions, options);
    }

    async executeTaskRequest(requestBody, options = {}) {
        return await this.client.executeTaskRequest(this.agentId, requestBody, options);
    }

    async getTaskStatus(taskId) {
        return await this.client.getTaskStatus(this.agentId, taskId);
    }

    async getTaskResult(taskId) {
        return await this.client.getTaskResult(this.agentId, taskId);
    }

    async waitForTask(taskId, options = {}) {
        return await this.client.waitForTask(this.agentId, taskId, options);
    }

    async cancelTask(taskId) {
        return await this.client.cancelTask(this.agentId, taskId);
    }
}

// ------------------------------------------------------------------
// GasClient Class
// ------------------------------------------------------------------
export class GasClient {
    static TERMINAL_STATUSES = new Set(["successful", "failed", "canceled", "rejected"]);

    constructor(serverUrl, options = {}) {
        this.serverUrl = serverUrl.replace(/\/+$/, "");
        this.defaultCredentials = options.defaultCredentials || {};
        this.artifactDelivery = options.artifactDelivery || "URL";
        this.timeout = options.timeout || 30000; // in milliseconds
        this._capabilities = null;
        this._agentDescriptions = {};

        if (options.loadCapabilities !== false) {
            this.getCapabilities().catch(() => { });
        }
    }

    // Display Helpers
    static _formatDisplayValue(value) {
        if (value === null || value === undefined || value === "" || (Array.isArray(value) && value.length === 0) || (typeof value === 'object' && Object.keys(value).length === 0)) {
            return "-";
        }
        if (typeof value === 'number') {
            return value.toLocaleString();
        }
        return String(value);
    }

    static _formatDurationSeconds(value) {
        if (value === null || value === undefined || value === "") return "-";
        const parsed = parseFloat(value);
        return isNaN(parsed) ? String(value) : `${parsed.toFixed(2)}s`;
    }

    static _streamEventTime(event) {
        const timestamp = event.timestamp;
        if (!timestamp) return "--:--:--";
        try {
            return new Date(timestamp).toLocaleTimeString();
        } catch {
            return String(timestamp);
        }
    }

    static _displayAgentNameFromEvent(event) {
        if (event._display_agent_name) return String(event._display_agent_name);
        const agent = (event.agent && typeof event.agent === 'object') ? event.agent : {};
        return agent.name || agent.id;
    }

    static _formatStreamMessage(event, displayAgentName) {
        const message = String(event.message || event.status || "");
        if (event.event !== "progress") return message;

        if (message.startsWith("The user wants help from ")) return "I received your request.";
        if (message.includes("is still working. Long LLM calls")) {
            return "I am still working. Long LLM calls, code execution, or geospatial file processing can take a little while.";
        }
        if (displayAgentName && message === `The ${displayAgentName} reported a workflow update.`) {
            return "I reported a workflow update.";
        }
        return message;
    }

    static _printStreamEvent(event) {
        const timeText = GasClient._streamEventTime(event);
        const eventType = event.event;
        const eventName = GasClient._formatDisplayValue(eventType);
        const displayAgentName = GasClient._displayAgentNameFromEvent(event);
        const message = GasClient._formatStreamMessage(event, displayAgentName);

        if (eventType === "task_result") {
            const payload = (event.payload && typeof event.payload === 'object') ? event.payload : {};
            const task = (payload.task && typeof payload.task === 'object') ? payload.task : {};
            console.log(`[${timeText}] task_result: final task received ${task.id || ''}`.trim());
            return;
        }

        const label = (eventType === "progress" && displayAgentName) ? displayAgentName : eventName;
        console.log(`[${timeText}] ${label}: ${message}`.trim());
    }

    static _printTaskSummary(taskResult) {
        const task = taskResult.task || {};
        const agent = taskResult.agent || {};
        const outputs = taskResult.outputs || {};
        const execution = taskResult.execution || {};
        const provenance = taskResult.provenance || {};
        const diagnostics = taskResult.diagnostics || {};
        const tokenUsage = provenance.token_usage || {};
        const artifacts = Array.isArray(outputs.artifacts) ? outputs.artifacts : [];

        let inputTokens = tokenUsage.input_tokens;
        let outputTokens = tokenUsage.output_tokens;
        let totalTokens = tokenUsage.total_tokens;
        if ((totalTokens === null || totalTokens === undefined || totalTokens === "") && typeof inputTokens === 'number' && typeof outputTokens === 'number') {
            totalTokens = inputTokens + outputTokens;
        }

        console.log("\n" + "=".repeat(72));
        console.log("GAS Task Summary");
        console.log("=".repeat(72));
        console.log(`Task         : ${GasClient._formatDisplayValue(task.id)}`);
        console.log(`Status       : ${GasClient._formatDisplayValue(task.status)}`);
        console.log(`Agent        : ${GasClient._formatDisplayValue(agent.name || agent.id)}`);
        console.log(`Version      : ${GasClient._formatDisplayValue(agent.version)}`);
        console.log(`Model        : ${GasClient._formatDisplayValue(agent.model)}`);
        console.log(`Duration     : ${GasClient._formatDurationSeconds(execution.duration_seconds)}`);
        console.log(`Iterations   : ${GasClient._formatDisplayValue(execution.iterations)}`);

        console.log("\nUsage");
        console.log("-----");
        console.log(`LLM calls    : ${GasClient._formatDisplayValue(provenance.llm_calls)}`);
        console.log(`Tool calls   : ${GasClient._formatDisplayValue(provenance.tool_calls)}`);
        console.log(`Input tokens : ${GasClient._formatDisplayValue(inputTokens)}`);
        console.log(`Output tokens: ${GasClient._formatDisplayValue(outputTokens)}`);
        console.log(`Total tokens : ${GasClient._formatDisplayValue(totalTokens)}`);

        console.log("\nOutputs");
        console.log("-------");
        console.log(`Summary      : ${GasClient._formatDisplayValue(outputs.summary)}`);
        console.log(`Artifacts    : ${artifacts.length}`);

        artifacts.forEach((artifact, i) => {
            if (!artifact || typeof artifact !== 'object') return;
            const spatialMetadata = artifact.spatial_metadata || {};
            const semanticName = artifact.name;
            const filename = artifact.filename;
            const role = artifact.role;
            const label = artifact.label || role;
            const originalFilename = artifact.original_filename;
            const description = artifact.description;
            const artifactType = artifact.type || spatialMetadata.type;
            const artifactFormat = artifact.format || artifact.mime_type;
            const sizeBytes = artifact.size_bytes;
            const displayName = semanticName || label || filename || `artifact_${i + 1}`;

            console.log(`  ${i + 1}. ${displayName}`);
            console.log(`     type=${GasClient._formatDisplayValue(artifactType)} format=${GasClient._formatDisplayValue(artifactFormat)} size=${GasClient._formatDisplayValue(sizeBytes)} bytes`);
            if (role) console.log(`     role=${role}`);
            if (filename) console.log(`     filename=${filename}`);
            if (originalFilename) console.log(`     original=${originalFilename}`);
            if (description) console.log(`     description=${description}`);
            if (artifact.url) console.log(`     url=${artifact.url}`);
        });

        console.log("\nDiagnostics");
        console.log("-----------");
        console.log(`Has error    : ${GasClient._formatDisplayValue(diagnostics.has_error)}`);
        if (diagnostics.error) console.log(`Error        : ${diagnostics.error}`);

        const warnings = Array.isArray(diagnostics.warnings) ? diagnostics.warnings : [];
        if (warnings.length > 0) {
            console.log("Warnings     :");
            warnings.forEach(w => console.log(`  - ${w}`));
        } else {
            console.log("Warnings     : -");
        }
        console.log("=".repeat(72));
    }

    printStreamEvent(event, options = {}) {
        if (options.agentName && event && typeof event === 'object') {
            const displayEvent = { ...event };
            if (!displayEvent._display_agent_name) displayEvent._display_agent_name = options.agentName;
            GasClient._printStreamEvent(displayEvent);
            return;
        }
        GasClient._printStreamEvent(event);
    }

    printTaskSummary(taskResult) {
        GasClient._printTaskSummary(taskResult);
    }

    // API Discoverability Operations
    async getCapabilities(refresh = false) {
        if (this._capabilities !== null && !refresh) return this._capabilities;
        const response = await fetch(this._capabilitiesUrl(), { signal: AbortSignal.timeout(this.timeout) });
        this._capabilities = await this._jsonOrRaise(response);
        this._agentDescriptions = {};
        return this._capabilities;
    }

    async listAgents(refresh = false) {
        const capabilities = await this.getCapabilities(refresh);
        const agents = capabilities.agents || [];
        return agents
            .filter(a => a && typeof a === 'object' && (a.agent_id || a.name))
            .map(a => String(a.agent_id || a.name));
    }

    async describeAgent(agentId, refresh = false) {
        const resolvedId = await this.resolveAgentId(agentId);
        if (this._agentDescriptions[resolvedId] && !refresh) return this._agentDescriptions[resolvedId];

        const agentEntry = await this._agentEntry(resolvedId);
        let describeUrl = agentEntry.DescribeAgent;
        if (!describeUrl || typeof describeUrl !== 'string') {
            const describeTemplate = await this._capabilityOperationUrl("describe_agent");
            if (!describeTemplate || !describeTemplate.includes("{agent_id}")) {
                throw new GasClientError(`No DescribeAgent URL advertised for agent '${resolvedId}'.`);
            }
            describeUrl = describeTemplate.replace("{agent_id}", resolvedId);
        }

        const response = await fetch(this._absoluteUrl(describeUrl), {
            headers: this._headers(),
            signal: AbortSignal.timeout(this.timeout)
        });
        const description = await this._jsonOrRaise(response);
        this._agentDescriptions[resolvedId] = description;
        return description;
    }

    async resolveAgentId(agentId) {
        const requested = agentId.replace(/^\/+|\/+$/g, "");
        const advertisedList = await this.listAgents();
        for (const advertised of advertisedList) {
            if (requested === advertised) return advertised;
        }
        throw new GasClientError(`Unknown agent_id '${agentId}'. Available agents: ${advertisedList.join(', ') || '(none)'}`);
    }

    agent(agentId) {
        return new GasAgentClient(this, agentId);
    }

    // Task Interactions
    async executeTask(agentId, instructions, options = {}) {
        const mode = options.mode || "sync";
        if (!["sync", "async", "stream"].includes(mode)) {
            throw new Error("mode must be one of 'sync', 'async', or 'stream'.");
        }

        const url = await this._operationUrl(agentId, "execute_task", "tasks");
        const body = this.buildExecuteTaskRequest(instructions, options);

        const response = await fetch(url, {
            method: 'POST',
            headers: this._headers(),
            body: JSON.stringify(body),
            signal: AbortSignal.timeout(options.timeout || this.timeout)
        });

        if (mode === "stream") {
            await this._raiseForStatus(response);
            return this._streamEvents(response, await this._agentDisplayName(agentId));
        }
        return await this._jsonOrRaise(response);
    }

    async executeTaskRequest(agentId, requestBody, options = {}) {
        if (!requestBody || typeof requestBody !== 'object') throw new Error("requestBody must be an object.");
        const task = requestBody.task || {};
        const mode = String(task.mode || "sync").trim().toLowerCase();
        if (!["sync", "async", "stream"].includes(mode)) {
            throw new Error("requestBody.task.mode must be one of 'sync', 'async', or 'stream'.");
        }

        const url = await this._operationUrl(agentId, "execute_task", "tasks");
        const response = await fetch(url, {
            method: 'POST',
            headers: this._headers(),
            body: JSON.stringify(requestBody),
            signal: AbortSignal.timeout(options.timeout || this.timeout)
        });

        if (mode === "stream") {
            await this._raiseForStatus(response);
            return this._streamEvents(response, await this._agentDisplayName(agentId));
        }
        return await this._jsonOrRaise(response);
    }

    async runStreamingTask(agent, instructions, options = {}) {
        const boundAgent = (agent instanceof GasAgentClient) ? agent : this.agent(String(agent));
        let finalResult = null;

        const eventStream = await boundAgent.executeTask(instructions, {
            ...options,
            mode: "stream"
        });

        for await (const event of eventStream) {
            if (options.printEvents !== false) {
                this.printStreamEvent(event);
            }
            if (event.event === "task_result") {
                finalResult = event.payload;
            }
        }

        if (options.printSummary !== false && finalResult !== null) {
            this.printTaskSummary(finalResult);
        }
        return finalResult;
    }

    async getTaskStatus(agentId, taskId, options = {}) {
        const url = await this._taskOperationUrl(agentId, "get_task_status", taskId);
        const response = await fetch(url, { headers: this._headers(), signal: AbortSignal.timeout(options.timeout || this.timeout) });
        return await this._jsonOrRaise(response);
    }

    async getTaskResult(agentId, taskId, options = {}) {
        const url = await this._taskOperationUrl(agentId, "get_task_result", taskId);
        const response = await fetch(url, { headers: this._headers(), signal: AbortSignal.timeout(options.timeout || this.timeout) });
        return await this._jsonOrRaise(response);
    }

    async waitForTask(agentId, taskId, options = {}) {
        const pollInterval = options.pollInterval || 5000;
        const timeoutSeconds = options.timeoutSeconds || 900;
        const started = performance.now();

        while (true) {
            const taskStatus = await this.getTaskStatus(agentId, taskId);
            const statusValue = this.getTaskStatusValue(taskStatus);

            if (GasClient.TERMINAL_STATUSES.has(statusValue)) {
                return await this.getTaskResult(agentId, taskId);
            }
            if ((performance.now() - started) / 1000 > timeoutSeconds) {
                throw new GasTaskTimeoutError(`Task '${taskId}' did not finish within ${timeoutSeconds} seconds.`);
            }
            await new Promise(resolve => setTimeout(resolve, pollInterval));
        }
    }

    async cancelTask(agentId, taskId) {
        const url = await this._taskOperationUrl(agentId, "cancel_task", taskId);
        const response = await fetch(url, { method: 'POST', headers: this._headers(), signal: AbortSignal.timeout(this.timeout) });
        return await this._jsonOrRaise(response);
    }

    buildExecuteTaskRequest(instructions, options = {}) {
        const mode = options.mode || "sync";
        const artifactDelivery = options.artifactDelivery || this.artifactDelivery;

        const payload = {
            task: { instructions, mode },
            outputs: {
                artifact_delivery: artifactDelivery.toLowerCase() === "encoded" ? "Encoded" : "URL"
            }
        };

        if (options.inputDatasets !== undefined && options.inputDatasets !== null) {
            payload.inputs = {
                input_datasets: Array.isArray(options.inputDatasets) ? options.inputDatasets : [options.inputDatasets]
            };
        }

        const requestParams = { ...options.parameters };
        if (options.model) requestParams.model = options.model;
        if (Object.keys(requestParams).length > 0) payload.parameters = requestParams;

        const requestCreds = { ...this.defaultCredentials, ...options.credentials };
        if (Object.keys(requestCreds).length > 0) payload.credentials = requestCreds;
        if (options.metadata) payload.metadata = { ...options.metadata };

        return payload;
    }

    // Base64 helper for target system environment (Node.js runtime snippet context)
    encodeDatasetFileBuffer(filename, buffer) {
        return {
            filename: filename,
            encoding: "base64",
            data: buffer.toString('base64')
        };
    }

    getTaskId(taskResponse) {
        const taskId = taskResponse?.task?.id;
        if (!taskId) throw new GasClientError("Response did not include task.id.");
        return String(taskId);
    }

    getTaskStatusValue(taskResponse) {
        return taskResponse?.task?.status ? String(taskResponse.task.status) : null;
    }

    // Internal Utility Operations
    _capabilitiesUrl() {
        if (this.serverUrl.includes("REQUEST=GetCapabilities")) return this.serverUrl;
        const params = new URLSearchParams({ SERVICE: 'GAS', VERSION: '1.0.0', REQUEST: 'GetCapabilities' });
        return `${this.serverUrl}/?${params.toString()}`;
    }

    async _agentEntry(agentId) {
        const capabilities = await this.getCapabilities();
        const agents = capabilities.agents || [];
        for (const entry of agents) {
            if (entry && typeof entry === 'object' && (entry.agent_id === agentId || entry.name === agentId)) {
                return entry;
            }
        }
        throw new GasClientError(`Agent '${agentId}' is not advertised by GetCapabilities.`);
    }

    async _capabilityOperationUrl(operationId) {
        const capabilities = await this.getCapabilities();
        const operations = capabilities.operations || [];
        for (const op of operations) {
            if (op && typeof op === 'object' && op.operation_id === operationId) {
                const urlVal = op.url || op.path;
                return urlVal ? String(urlVal) : null;
            }
        }
        return null;
    }

    async _operationUrl(agentId, operationId, fallbackPath) {
        await this._agentEntry(agentId);
        const urlTemplate = await this._capabilityOperationUrl(operationId);
        if (urlTemplate) {
            return this._absoluteAgentUrl(agentId, urlTemplate.replace("{agent_id}", agentId));
        }
        return this._absoluteAgentUrl(agentId, fallbackPath);
    }

    async _taskOperationUrl(agentId, operationId, taskId) {
        const fallbacks = {
            get_task_status: `tasks/${taskId}/status`,
            get_task_result: `tasks/${taskId}/result`,
            cancel_task: `tasks/${taskId}/cancel`
        };
        await this._agentEntry(agentId);
        const urlTemplate = await this._capabilityOperationUrl(operationId);
        if (urlTemplate) {
            const formatted = urlTemplate
                .replace("{agent_id}", agentId)
                .replace("<task_id>", taskId)
                .replace("{task_id}", taskId);
            return this._absoluteAgentUrl(agentId, formatted);
        }
        return this._absoluteAgentUrl(agentId, fallbacks[operationId]);
    }

    _absoluteAgentUrl(agentId, endpointUrl) {
        const clean = endpointUrl.trim();
        if (clean.startsWith("http://") || clean.startsWith("https://")) return clean;
        if (clean.startsWith("/agents/")) return this._absoluteUrl(clean);
        return `${this._absoluteUrl(`/agents/${agentId}`).replace(/\/+$/, "")}/${clean.replace(/^\/+/, "")}`;
    }

    _absoluteUrl(pathOrUrl) {
        if (pathOrUrl.startsWith("http://") || pathOrUrl.startsWith("https://")) return pathOrUrl;
        const root = this.serverUrl.startsWith("http") ? new URL(this.serverUrl).origin : this.serverUrl;
        return `${root.replace(/\/+$/, "")}/${pathOrUrl.replace(/^\/+/, "")}`;
    }

    async _agentDisplayName(agentId) {
        let description = {};
        try { description = await this.describeAgent(agentId); } catch { }
        const profile = description.profile || {};
        return String(profile.name || description.name || agentId.replace(/_/g, " "));
    }

    _headers() {
        return { "Content-Type": "application/json" };
    }

    async _jsonOrRaise(response) {
        await this._raiseForStatus(response);
        const txt = await response.text();
        try {
            return JSON.parse(txt);
        } catch (err) {
            throw new GasClientError(`Response was not valid JSON: ${txt}`);
        }
    }

    async _raiseForStatus(response) {
        if (response.status < 400) return;
        const txt = await response.text();
        let payload;
        try {
            payload = JSON.parse(txt);
        } catch {
            payload = txt;
        }
        throw new GasClientError(`GAS request failed with HTTP ${response.status}: ${JSON.stringify(payload)}`);
    }

    // Async Generator managing raw HTTP fetch chunks parsing Line-Delimited stream JSON packets
    async * _streamEvents(response, displayAgentName) {
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split("\n");
            buffer = lines.pop() || ""; // Retain partial trailing string fragment

            for (const line of lines) {
                if (line.trim()) {
                    const event = JSON.parse(line);
                    if (displayAgentName && event && typeof event === 'object') {
                        if (!event._display_agent_name) event._display_agent_name = displayAgentName;
                    }
                    yield event;
                }
            }
        }
    }
}
