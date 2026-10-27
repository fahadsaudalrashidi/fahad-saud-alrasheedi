import { existsSync } from "node:fs";
import { spawn, type ChildProcess } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import app from "./app";
import { logger } from "./lib/logger";

const rawPort = process.env["PORT"];

if (!rawPort) {
  throw new Error(
    "PORT environment variable is required but was not provided.",
  );
}

const port = Number(rawPort);

if (Number.isNaN(port) || port <= 0) {
  throw new Error(`Invalid PORT value: "${rawPort}"`);
}

function findWorkspaceRoot(): string {
  const starts = [process.cwd(), path.dirname(fileURLToPath(import.meta.url))];
  for (const start of starts) {
    let candidate = start;
    for (let depth = 0; depth < 6; depth += 1) {
      if (existsSync(path.join(candidate, "support_backend", "main.py"))) {
        return candidate;
      }
      const parent = path.dirname(candidate);
      if (parent === candidate) break;
      candidate = parent;
    }
  }
  throw new Error("Could not locate the workspace support_backend package.");
}

const workspaceRoot = findWorkspaceRoot();
const pythonPort = Number(process.env.PYTHON_PORT ?? "8031");
const pythonUrl = process.env.PYTHON_SERVICE_URL ?? `http://127.0.0.1:${pythonPort}`;
const bundledPython = path.resolve(workspaceRoot, ".pythonlibs/bin/python");
const pythonExecutable =
  process.env.PYTHON_EXECUTABLE ??
  (existsSync(bundledPython) ? bundledPython : "python3");
let pythonProcess: ChildProcess | undefined;

async function startPythonBackend(): Promise<void> {
  if (
    process.env.SKIP_PYTHON_BACKEND === "true" ||
    process.env.PYTHON_SERVICE_URL
  ) return;

  pythonProcess = spawn(
    pythonExecutable,
    [
      "-m",
      "uvicorn",
      "support_backend.main:app",
      "--host",
      "127.0.0.1",
      "--port",
      String(pythonPort),
    ],
    {
      cwd: workspaceRoot,
      env: {
        ...process.env,
        PYTHONPATH: [workspaceRoot, process.env.PYTHONPATH]
          .filter(Boolean)
          .join(path.delimiter),
      },
      stdio: "inherit",
    },
  );
  pythonProcess.on("error", (err) => {
    logger.error({ err }, "Unable to start the Python support backend");
  });
  pythonProcess.on("exit", (code, signal) => {
    if (code !== 0 && code !== null) {
      logger.error({ code, signal }, "Python support backend exited unexpectedly");
    }
  });

  const deadline = Date.now() + 60_000;
  let lastError: unknown;
  while (Date.now() < deadline) {
    if (pythonProcess.exitCode !== null) {
      throw new Error(
        `Python backend exited before becoming ready (code ${pythonProcess.exitCode}).`,
      );
    }
    try {
      const response = await fetch(`${pythonUrl}/api/healthz`);
      if (response.ok) {
        logger.info({ pythonUrl }, "Python support backend is ready");
        return;
      }
      lastError = new Error(`Python backend health check returned ${response.status}`);
    } catch (err) {
      lastError = err;
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error(`Python backend did not become ready within 60 seconds: ${String(lastError)}`);
}

async function start(): Promise<void> {
  await startPythonBackend();
  app.listen(port, (err) => {
    if (err) {
      logger.error({ err }, "Error listening on port");
      process.exit(1);
    }
    logger.info({ port }, "Server listening");
  });
}

function stop(): void {
  pythonProcess?.kill("SIGTERM");
}

process.on("SIGTERM", stop);
process.on("SIGINT", stop);

start().catch((err) => {
  logger.error({ err }, "Unable to start API services");
  stop();
  process.exit(1);
});
