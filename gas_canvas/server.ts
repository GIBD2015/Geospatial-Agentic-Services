import express from "express";
import path from "path";
import { createServer as createViteServer } from "vite";
import { GasClient } from "@gibd/gas-client";

async function startServer() {
  const app = express();
  const PORT = 3000;

  // Middleware to parse JSON payloads
  app.use(express.json());

  // Default GAS server url
  const DEFAULT_GAS_SERVER = "https://www.geospatial-agentic-services.online/";

  // Retrieve a configured client instance helper
  function getGasClient(customServerUrl?: string) {
    const serverUrl = customServerUrl || DEFAULT_GAS_SERVER;
    // Merge server keys if available
    const defaultCredentials: Record<string, string> = {};
    // Also support custom key settings if defined in background environment variables
    if (process.env.OPENAI_API_KEY) {
      defaultCredentials["OPENAI_API_KEY"] = process.env.OPENAI_API_KEY;
    }

    return new GasClient(serverUrl, { defaultCredentials, timeout: 300000 });
  }

  // --- API Routes ---

  // Health check
  app.get("/api/health", (req, res) => {
    res.json({ status: "ok", time: new Date().toISOString() });
  });

  // Fetch Capabilities
  app.get("/api/gas/capabilities", async (req, res) => {
    const { serverUrl } = req.query;
    try {
      const client = getGasClient(serverUrl as string | undefined);
      const capabilities = await client.getCapabilities();
      const agentsList = await client.listAgents();
      res.json({ capabilities, agentsList });
    } catch (err: any) {
      console.error("Error fetching GAS capabilities:", err);
      res.status(500).json({ error: "Failed to fetch GAS server capabilities", details: err.message });
    }
  });

  // Describe a Specific Agent
  app.get("/api/gas/agent-details", async (req, res) => {
    const { agentId, serverUrl } = req.query;
    if (!agentId) {
      res.status(400).json({ error: "agentId parameter is required" });
      return;
    }
    try {
      const client = getGasClient(serverUrl as string | undefined);
      const description = await client.describeAgent(agentId as string);
      res.json(description);
    } catch (err: any) {
      console.error(`Error describing agent ${agentId}:`, err);
      res.status(500).json({ error: `Failed to fetch description for agent ${agentId}`, details: err.message });
    }
  });

  // Execute Task (Supports Sync / Streaming Proxy)
  app.post("/api/gas/run-task", async (req, res) => {
    const { agentId, instructions, options, serverUrl } = req.body;

    if (!agentId || !instructions) {
      res.status(400).json({ error: "agentId and instructions are required parameters." });
      return;
    }

    try {
      const client = getGasClient(serverUrl);
      const agentInstance = client.agent(agentId);

      // Merge environment API keys into options credentials if they are missing
      const requestOptions = { ...(options || {}) };
      requestOptions.credentials = {
        ...(requestOptions.credentials || {})
      };

      // Add server-side environment credentials as high priority fallbacks if not supplied by user
      if (!requestOptions.credentials["OPENAI_API_KEY"] && process.env.OPENAI_API_KEY) {
        requestOptions.credentials["OPENAI_API_KEY"] = process.env.OPENAI_API_KEY;
      }

      const isStream = requestOptions.mode === "stream";

      if (isStream) {
        // Configure Server-Sent Events headers
        res.setHeader("Content-Type", "text/event-stream");
        res.setHeader("Cache-Control", "no-cache");
        res.setHeader("Connection", "keep-alive");
        res.flushHeaders(); // Ensure headers are flushed immediately

        console.log(`Starting stream for agent ${agentId}: "${instructions}"`);
        
        try {
          const url = `${client.serverUrl}/agents/${agentId}/tasks`;
          const body = client.buildExecuteTaskRequest(instructions, requestOptions);
          
          const response = await fetch(url, {
              method: 'POST',
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify(body),
              signal: AbortSignal.timeout(requestOptions.timeout || client.timeout || 300000)
          });

          if (!response.ok) {
             const errTxt = await response.text();
             throw new Error(`Upstream returned ${response.status}: ${errTxt}`);
          }

          if (response.body) {
             const decoder = new TextDecoder();
             let buffer = "";
             // @ts-ignore
             for await (const chunk of response.body) {
                 buffer += decoder.decode(chunk, { stream: true });
                 const lines = buffer.split("\n");
                 buffer = lines.pop() || "";

                 for (let line of lines) {
                     line = line.trim();
                     if (line) {
                         // Fix invalid JSON values like NaN
                         const sanitizedLine = line.replace(/:\s*NaN/g, ': null').replace(/\[\s*NaN/g, '[null').replace(/,\s*NaN/g, ', null');
                         try {
                             const event = JSON.parse(sanitizedLine);
                             res.write(`data: ${JSON.stringify(event)}\n\n`);
                         } catch (e) {
                             console.warn("Skipping invalid JSON from upstream:", sanitizedLine);
                         }
                     }
                 }
             }
          }
          res.write(`data: ${JSON.stringify({ event: "stream_done", payload: { success: true } })}\n\n`);
          res.end();
        } catch (err: any) {
             throw err;
        }
      } else {
        // Sync Mode Execution
        console.log(`Executing sync job with agent ${agentId}: "${instructions}"`);
        const result = await agentInstance.executeTask(instructions, requestOptions);
        res.json(result);
      }
    } catch (err: any) {
      console.error(`Error executing task on agent ${agentId}:`, err);
      if (!res.headersSent) {
        res.status(500).json({ error: `Execution error on agent ${agentId}`, details: err.message });
      } else {
        res.write(`data: ${JSON.stringify({ event: "stream_error", payload: { message: err.message } })}\n\n`);
        res.end();
      }
    }
  });

  // Proxy parse GPKG
  app.get("/api/parse-gpkg", async (req, res) => {
    const url = req.query.url as string;
    if (!url) {
      res.status(400).json({ error: "url is required" });
      return;
    }
    
    try {
      const { GeoPackageAPI } = await import('@ngageoint/geopackage');
      
      const resp = await fetch(url);
      if (!resp.ok) throw new Error("Failed to fetch gpkg file");
      
      const arrayBuffer = await resp.arrayBuffer();
      const uint8Array = new Uint8Array(arrayBuffer);
      
      const geoPackage = await GeoPackageAPI.open(uint8Array);
      const featureTables = geoPackage.getFeatureTables();
      
      const geojsonData = { type: 'FeatureCollection', features: [] as any[] };
      
      for (const table of featureTables) {
        const iterator = geoPackage.iterateGeoJSONFeatures(table);
        for (const feature of iterator) {
          geojsonData.features.push(feature);
        }
      }
      
      geoPackage.close();
      res.json(geojsonData);
    } catch (err: any) {
      console.error("GPKG parse error:", err);
      res.status(500).json({ error: "Failed to parse GPKG", details: err.message });
    }
  });

  // --- Vite & Client App Static Serving ---

  if (process.env.NODE_ENV !== "production") {
    // Mount Vite middleware in development
    const vite = await createViteServer({
      server: { middlewareMode: true },
      appType: "spa",
    });
    app.use(vite.middlewares);
  } else {
    // Serve build artifacts in production
    const distPath = path.join(process.cwd(), "dist");
    app.use(express.static(distPath));
    app.get("*", (req, res) => {
      res.sendFile(path.join(distPath, "index.html"));
    });
  }

  app.listen(PORT, "0.0.0.0", () => {
    console.log(`[GAS Server] Ready on http://localhost:${PORT} [ENV: ${process.env.NODE_ENV || 'dev'}]`);
  });
}

startServer();
