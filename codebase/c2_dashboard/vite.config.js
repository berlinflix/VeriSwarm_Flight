import { promises as fs } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const dashboardRoot = dirname(fileURLToPath(import.meta.url));
const codebaseRoot = resolve(dashboardRoot, "..");
const attackFile = resolve(codebaseRoot, "attack.json");
const eventFile = resolve(codebaseRoot, "results", "live_events.jsonl");
const qualifierUrl = (process.env.VERISWARM_QUALIFIER_URL ?? "").replace(/\/$/, "");
const qualifierToken = process.env.VERISWARM_QUALIFIER_TOKEN ?? "";
const rescueUrl = (process.env.VERISWARM_RESCUE_URL ?? "").replace(/\/$/, "");
const rescueToken = process.env.VERISWARM_RESCUE_TOKEN ?? "";

const clearedAttacks = {
  model_swap: [],
  patch: false,
  collude: [],
  spoof_pose: {},
  replay: false,
  forge: false,
  provisioning: [],
  ota: [],
  rogue_node: [],
};

const attackUpdates = {
  patch: { patch: true },
  model: { model_swap: ["bravo"] },
  provisioning: { provisioning: ["bravo", "charlie"] },
  ota: { ota: ["alpha"] },
  rogue: { rogue_node: ["rogue-01"] },
  replay: { replay: true },
  collude: { collude: ["delta", "echo"] },
  spoof: { spoof_pose: { bravo: [100, 0, 14, 0] } },
};

function sendJson(response, status, payload) {
  response.statusCode = status;
  response.setHeader("Content-Type", "application/json");
  response.end(JSON.stringify(payload));
}

async function callQualifier(path, options = {}) {
  if (!qualifierUrl || !qualifierToken) {
    const error = new Error("qualification backend is not configured");
    error.code = "qualifier_not_configured";
    throw error;
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 45_000);
  try {
    const response = await fetch(`${qualifierUrl}${path}`, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        "X-VeriSwarm-Token": qualifierToken,
        ...(options.headers ?? {}),
      },
      signal: controller.signal,
    });
    const payload = await response.json().catch(() => ({ ok: false, error: "invalid_backend_response" }));
    return { status: response.status, payload };
  } finally {
    clearTimeout(timer);
  }
}

async function callRescue(path) {
  if (!rescueUrl) {
    const error = new Error("rescue backend is not configured");
    error.code = "rescue_not_configured";
    throw error;
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 5_000);
  try {
    const headers = rescueToken
      ? { "X-VeriSwarm-Token": rescueToken }
      : {};
    const response = await fetch(`${rescueUrl}${path}`, {
      headers,
      cache: "no-store",
      signal: controller.signal,
    });
    const payload = await response.json().catch(() => ({ ok: false, error: "invalid_backend_response" }));
    return { status: response.status, payload };
  } finally {
    clearTimeout(timer);
  }
}

function readRequestBody(request) {
  return new Promise((resolveBody, rejectBody) => {
    let body = "";
    request.on("data", (chunk) => {
      body += chunk;
      if (body.length > 16_384) rejectBody(new Error("request body too large"));
    });
    request.on("end", () => resolveBody(body));
    request.on("error", rejectBody);
  });
}

async function readAttackState() {
  try {
    return { ...clearedAttacks, ...JSON.parse(await fs.readFile(attackFile, "utf8")) };
  } catch {
    return { ...clearedAttacks };
  }
}

async function writeAttackState(state) {
  const temporaryFile = `${attackFile}.dashboard.tmp`;
  await fs.writeFile(temporaryFile, `${JSON.stringify(state, null, 2)}\n`, "utf8");
  await fs.rename(temporaryFile, attackFile);
}

async function readRecentEvents() {
  const handle = await fs.open(eventFile, "r");
  try {
    const { size } = await handle.stat();
    const length = Math.min(size, 512 * 1024);
    const buffer = Buffer.alloc(length);
    await handle.read(buffer, 0, length, size - length);
    let text = buffer.toString("utf8");
    if (size > length) text = text.slice(text.indexOf("\n") + 1);
    return text
      .split("\n")
      .filter(Boolean)
      .flatMap((line) => {
        try {
          return [JSON.parse(line)];
        } catch {
          return [];
        }
      });
  } finally {
    await handle.close();
  }
}

function latest(events, predicate) {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    if (predicate(events[index])) return events[index];
  }
  return null;
}

function dashboardApi() {
  return {
    name: "veriswarm-dashboard-api",
    configureServer(server) {
      server.middlewares.use("/api/qualification", async (request, response) => {
        try {
          if (request.method === "GET" && request.url === "/status") {
            const result = await callQualifier("/health");
            sendJson(response, result.status, result.payload);
            return;
          }
          if (request.method === "POST" && request.url === "/run") {
            const body = JSON.parse((await readRequestBody(request)) || "{}");
            if (!['clean', 'model_swap'].includes(body.case)) {
              sendJson(response, 400, { ok: false, error: "unsupported_case" });
              return;
            }
            const result = await callQualifier("/runs", {
              method: "POST",
              body: JSON.stringify({ case: body.case }),
            });
            sendJson(response, result.status, result.payload);
            return;
          }
          sendJson(response, 404, { ok: false, error: "not_found" });
        } catch (error) {
          const message = error.name === "AbortError"
            ? "qualification_timeout"
            : error.code ?? error.message;
          sendJson(response, 503, { ok: false, error: message });
        }
      });

      server.middlewares.use("/api/attacks", async (request, response) => {
        try {
          if (request.method === "GET") {
            sendJson(response, 200, { ok: true, state: await readAttackState() });
            return;
          }
          if (request.method !== "POST") {
            sendJson(response, 405, { ok: false, error: "method_not_allowed" });
            return;
          }

          const body = JSON.parse((await readRequestBody(request)) || "{}");
          if (body.action === "clear") {
            await writeAttackState({ ...clearedAttacks });
            sendJson(response, 200, { ok: true, action: "clear", state: clearedAttacks });
            return;
          }

          const update = attackUpdates[body.action];
          if (!update) {
            sendJson(response, 400, { ok: false, error: "unknown_attack" });
            return;
          }

          const state = { ...(await readAttackState()), ...update };
          await writeAttackState(state);
          sendJson(response, 200, { ok: true, action: body.action, state });
        } catch (error) {
          sendJson(response, 500, { ok: false, error: error.message });
        }
      });

      server.middlewares.use("/api/telemetry", async (request, response) => {
        if (request.method !== "GET") {
          sendJson(response, 405, { ok: false, error: "method_not_allowed" });
          return;
        }
        try {
          const events = await readRecentEvents();
          const verdicts = events
            .filter((event) => event.type === "verdict" && event.target === "alpha")
            .slice(-28);
          const pose = latest(events, (event) => event.type === "pose" && event.node === "alpha");
          const reputation = latest(events, (event) => event.type === "reputation" && event.node === "alpha");
          const action = latest(events, (event) => event.type === "safe_action" && event.node === "alpha");
          const newestEvent = events.at(-1);
          sendJson(response, 200, {
            ok: true,
            source: "results/live_events.jsonl",
            updatedAt: newestEvent?.t ?? null,
            altitude: pose?.xyz?.[2] ?? null,
            reputation: reputation?.value ?? null,
            action: action?.action ?? null,
            outcome: action?.outcome ?? null,
            consensus: verdicts.map((event) => Number(event.consensus_ms ?? 0)),
            semanticAcks: verdicts.map((event) => Number(event.semantic_acks ?? 0)),
          });
        } catch (error) {
          sendJson(response, 503, { ok: false, error: error.message });
        }
      });

      server.middlewares.use("/api/rescue", async (request, response) => {
        if (request.method !== "GET") {
          sendJson(response, 405, { ok: false, error: "method_not_allowed" });
          return;
        }
        const routes = {
          "/status": "/health",
          "/state": "/state",
          "/report": "/report",
        };
        const target = routes[request.url];
        if (!target) {
          sendJson(response, 404, { ok: false, error: "not_found" });
          return;
        }
        try {
          const result = await callRescue(target);
          sendJson(response, result.status, result.payload);
        } catch (error) {
          const message = error.name === "AbortError"
            ? "rescue_timeout"
            : error.code ?? error.message;
          sendJson(response, 503, { ok: false, error: message });
        }
      });
    },
  };
}

export default defineConfig({
  plugins: [react(), dashboardApi()],
});
