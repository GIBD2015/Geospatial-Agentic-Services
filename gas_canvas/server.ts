import express from "express";
import { execFile } from "child_process";
import fs from "fs";
import os from "os";
import path from "path";
import { createRequire } from "module";
import { createServer as createViteServer } from "vite";
import { GasClient } from "@gibd/gas-client";
import { promisify } from "util";

const require = createRequire(path.join(process.cwd(), "server.ts"));
const Database = require("better-sqlite3");
const wkx = require("wkx");
const execFileAsync = promisify(execFile);

const GPKG_PREVIEW_FEATURE_LIMIT = 5000;
const GEOTIFF_PREVIEW_TIMEOUT_MS = 120000;

function getPythonExecutable() {
  const projectVenvPython = path.resolve(process.cwd(), "..", ".venv", "Scripts", "python.exe");
  if (fs.existsSync(projectVenvPython)) return projectVenvPython;
  return process.env.PYTHON || "python";
}

function quoteSqlIdentifier(identifier: string) {
  return `"${identifier.replace(/"/g, '""')}"`;
}

function normalizeGpkgValue(value: unknown) {
  if (Buffer.isBuffer(value)) return "[binary]";
  if (typeof value === "bigint") return Number(value);
  return value;
}

function extractGpkgWkb(geometryValue: Buffer) {
  if (geometryValue.length <= 8 || geometryValue[0] !== 0x47 || geometryValue[1] !== 0x50) {
    return geometryValue;
  }

  const flags = geometryValue[3];
  const envelopeIndicator = (flags & 0x0e) >> 1;
  const envelopeByteLengths: Record<number, number> = {
    0: 0,
    1: 32,
    2: 48,
    3: 48,
    4: 64
  };
  const headerLength = 8 + (envelopeByteLengths[envelopeIndicator] || 0);
  return geometryValue.subarray(headerLength);
}

function parseGpkgGeometry(geometryValue: unknown) {
  if (!Buffer.isBuffer(geometryValue)) return null;

  const wkb = extractGpkgWkb(geometryValue);
  if (!wkb.length) return null;

  try {
    return wkx.Geometry.parse(wkb).toGeoJSON();
  } catch (err) {
    console.warn("Unable to parse GeoPackage geometry:", err);
    return null;
  }
}

function parseGpkgFile(filePath: string) {
  const db = new Database(filePath, { readonly: true, fileMustExist: true });

  try {
    const tables = db
      .prepare("SELECT table_name FROM gpkg_contents WHERE data_type = 'features'")
      .all()
      .map((row: any) => String(row.table_name));

    const features: any[] = [];
    const tableSummaries: any[] = [];

    for (const tableName of tables) {
      const geometryRow = db
        .prepare("SELECT column_name FROM gpkg_geometry_columns WHERE table_name = ?")
        .get(tableName) as { column_name?: string } | undefined;

      if (!geometryRow?.column_name) continue;

      const geometryColumn = geometryRow.column_name;
      const columns = db
        .prepare(`PRAGMA table_info(${quoteSqlIdentifier(tableName)})`)
        .all()
        .map((row: any) => String(row.name));

      const remaining = GPKG_PREVIEW_FEATURE_LIMIT - features.length;
      if (remaining <= 0) break;

      const rows = db
        .prepare(`SELECT * FROM ${quoteSqlIdentifier(tableName)} LIMIT ?`)
        .all(remaining);

      for (const row of rows as Record<string, unknown>[]) {
        const properties: Record<string, unknown> = {};

        for (const column of columns) {
          if (column !== geometryColumn) {
            properties[column] = normalizeGpkgValue(row[column]);
          }
        }

        features.push({
          type: "Feature",
          geometry: parseGpkgGeometry(row[geometryColumn]),
          properties: {
            ...properties,
            _gpkg_table: tableName
          }
        });
      }

      tableSummaries.push({
        table: tableName,
        geometryColumn,
        previewedFeatures: rows.length
      });
    }

    return {
      type: "FeatureCollection",
      features,
      metadata: {
        tables: tableSummaries,
        featureLimit: GPKG_PREVIEW_FEATURE_LIMIT,
        truncated: features.length >= GPKG_PREVIEW_FEATURE_LIMIT
      }
    };
  } finally {
    db.close();
  }
}

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
          const upstreamController = new AbortController();
          const timeoutId = setTimeout(
            () => upstreamController.abort(),
            requestOptions.timeout || client.timeout || 300000
          );
          try {
            res.on("close", () => {
              if (!res.writableEnded) {
                upstreamController.abort();
              }
            });

            const response = await fetch(url, {
                method: 'POST',
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(body),
                signal: upstreamController.signal
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
          } finally {
            clearTimeout(timeoutId);
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
      if (err?.name === "AbortError") {
        console.log(`Execution stream aborted for agent ${agentId}.`);
        if (!res.headersSent) {
          res.status(499).json({ error: `Execution stream aborted for agent ${agentId}` });
        } else if (!res.writableEnded) {
          res.end();
        }
        return;
      }

      console.error(`Error executing task on agent ${agentId}:`, err);
      if (!res.headersSent) {
        res.status(500).json({ error: `Execution error on agent ${agentId}`, details: err.message });
      } else {
        res.write(`data: ${JSON.stringify({ event: "stream_error", payload: { message: err.message } })}\n\n`);
        res.end();
      }
    }
  });

  app.post("/api/gas/cancel-task", async (req, res) => {
    const { agentId, taskId, serverUrl } = req.body;

    if (!agentId || !taskId) {
      res.status(400).json({ error: "agentId and taskId are required parameters." });
      return;
    }

    try {
      const client = getGasClient(serverUrl);
      const cancelUrl = `${client.serverUrl}/agents/${encodeURIComponent(agentId)}/tasks/${encodeURIComponent(taskId)}/cancel`;
      const cancelResponse = await fetch(cancelUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        signal: AbortSignal.timeout(client.timeout || 300000)
      });

      const text = await cancelResponse.text();
      let result: any = {};
      try {
        result = text ? JSON.parse(text) : {};
      } catch {
        result = { message: text };
      }

      if (!cancelResponse.ok) {
        const detail = result?.details || result?.message || result?.error || text || `HTTP ${cancelResponse.status}`;
        throw new Error(detail);
      }

      res.json(result);
    } catch (err: any) {
      console.error(`Error canceling task ${taskId} on agent ${agentId}:`, err);
      res.status(500).json({ error: `Cancel error on agent ${agentId}`, details: err.message });
    }
  });

  // Fetch remote artifacts through the local app so previews can avoid iframe/CORS header issues.
  app.get("/api/fetch-artifact", async (req, res) => {
    const url = req.query.url as string;
    if (!url) {
      res.status(400).send("url is required");
      return;
    }

    try {
      const response = await fetch(url, {
        signal: AbortSignal.timeout(60000)
      });

      if (!response.ok) {
        throw new Error(`Failed to fetch artifact: HTTP ${response.status}`);
      }

      const contentType = response.headers.get("content-type") || "text/html; charset=utf-8";
      const text = await response.text();
      res.type(contentType);
      res.send(text);
    } catch (err: any) {
      console.error("Failed to fetch artifact preview:", err);
      res.status(500).send(err.message || "Failed to fetch artifact");
    }
  });

  // Proxy parse GPKG
  app.get("/api/parse-gpkg", async (req, res) => {
    const url = req.query.url as string;
    if (!url) {
      res.status(400).json({ error: "url is required" });
      return;
    }

    let tempFilePath = "";

    try {
      const response = await fetch(url, {
        signal: AbortSignal.timeout(60000)
      });

      if (!response.ok) {
        throw new Error(`Failed to fetch GPKG file: HTTP ${response.status}`);
      }

      const arrayBuffer = await response.arrayBuffer();
      tempFilePath = path.join(
        os.tmpdir(),
        `gas-canvas-preview-${Date.now()}-${Math.random().toString(16).slice(2)}.gpkg`
      );
      fs.writeFileSync(tempFilePath, Buffer.from(arrayBuffer));

      res.json(parseGpkgFile(tempFilePath));
    } catch (err: any) {
      console.error("Failed to parse GPKG preview:", err);
      res.status(500).json({
        error: "Failed to parse GPKG",
        details: err.message,
        url
      });
    } finally {
      if (tempFilePath) {
        fs.rmSync(tempFilePath, { force: true });
      }
    }
  });

  app.get("/api/parse-geotiff", async (req, res) => {
    const url = req.query.url as string;
    if (!url) {
      res.status(400).json({ error: "url is required" });
      return;
    }

    let tempFilePath = "";

    try {
      const response = await fetch(url, {
        signal: AbortSignal.timeout(60000)
      });

      if (!response.ok) {
        throw new Error(`Failed to fetch GeoTIFF file: HTTP ${response.status}`);
      }

      const arrayBuffer = await response.arrayBuffer();
      tempFilePath = path.join(
        os.tmpdir(),
        `gas-canvas-preview-${Date.now()}-${Math.random().toString(16).slice(2)}.tif`
      );
      fs.writeFileSync(tempFilePath, Buffer.from(arrayBuffer));

      const scriptPath = path.join(process.cwd(), "scripts", "render_geotiff_preview.py");
      const { stdout, stderr } = await execFileAsync(
        getPythonExecutable(),
        [scriptPath, tempFilePath],
        {
          timeout: GEOTIFF_PREVIEW_TIMEOUT_MS,
          maxBuffer: 24 * 1024 * 1024
        }
      );

      if (stderr.trim()) {
        console.warn("GeoTIFF preview warnings:", stderr.trim());
      }

      res.json(JSON.parse(stdout));
    } catch (err: any) {
      console.error("Failed to parse GeoTIFF preview:", err);
      res.status(500).json({
        error: "Failed to parse GeoTIFF",
        details: err.message,
        url
      });
    } finally {
      if (tempFilePath) {
        fs.rmSync(tempFilePath, { force: true });
      }
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
