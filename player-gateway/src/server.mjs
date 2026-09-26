import crypto from "node:crypto";
import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import {fileURLToPath} from "node:url";
import {Server as SocketIOServer} from "socket.io";

import {ASSET_CATALOG} from "./assets.mjs";
import {loadConfig} from "./config.mjs";
import {PlayerGatewayCore} from "./core.mjs";
import {HostProtocolClient} from "./host-client.mjs";
import {loadMapCatalog, terrainFor} from "./projector.mjs";
import {classifyGameTableRoute, proxyGameTable} from "./proxy.mjs";
import {BROWSER_PROTOCOL_VERSION, publicError, validateControlRequest} from "./protocol.mjs";
import {SessionCapabilities} from "./session.mjs";

const sourceDirectory = path.dirname(fileURLToPath(import.meta.url));
const projectDirectory = path.dirname(sourceDirectory);
const repoRoot = path.dirname(projectDirectory);
const publicDirectory = path.join(repoRoot, "gametable", "web");
const config = loadConfig();
const catalog = loadMapCatalog(path.join(repoRoot, "world", "maps"));
const sessionSecret = config.sessionSecret || crypto.randomBytes(32).toString("hex");
const sessions = new SessionCapabilities(sessionSecret, {
  secure: config.tlsMode !== "local_http",
});
const ipConnections = new Map();

const stateClient = new HostProtocolClient({
  clientId: "player-gateway-state",
  host: config.hostBind,
  port: config.hostPort,
  timeoutMs: config.hostTimeoutMs,
});
const controlClient = new HostProtocolClient({
  clientId: "player-gateway-control",
  host: config.hostBind,
  port: config.hostPort,
  timeoutMs: config.hostTimeoutMs,
});
const core = new PlayerGatewayCore({
  stateClient,
  controlClient,
  catalog,
  frameHz: config.frameHz,
  inputHz: config.inputHz,
  inputKeepaliveMs: config.inputKeepaliveMs,
  inputEventsPerSecond: config.inputEventsPerSecond,
});

function json(response, status, value, extraHeaders = {}) {
  const body = Buffer.from(JSON.stringify(value));
  response.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": body.length,
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    ...extraHeaders,
  });
  response.end(body);
}

const mime = new Map([
  [".html", "text/html; charset=utf-8"],
  [".js", "text/javascript; charset=utf-8"],
  [".css", "text/css; charset=utf-8"],
  [".svg", "image/svg+xml"],
  [".json", "application/json; charset=utf-8"],
]);

function hostAllowed(request) {
  const host = request.headers.host;
  return typeof host === "string" && config.allowedHosts.has(host);
}

function originAllowed(request) {
  const origin = request.headers.origin;
  return !origin || config.allowedOrigins.has(origin);
}

function ensureSession(request, response) {
  const current = sessions.fromRequest(request);
  if (current) return current;
  const value = sessions.issue();
  response.setHeader("Set-Cookie", sessions.cookie(value));
  return sessions.validate(value);
}

function requireSession(request, response) {
  const sessionId = sessions.fromRequest(request);
  if (!sessionId) {
    json(response, 401, {error: "Player session required"});
    return null;
  }
  return sessionId;
}

function staticResponse(request, response) {
  if (!hostAllowed(request)) {
    json(response, 421, {error: "host is not allowed"});
    return;
  }
  const url = new URL(request.url || "/", "http://player-gateway.invalid");
  if (url.pathname === "/health") {
    json(response, 200, {
      ...core.status(),
      connections: io?.engine?.clientsCount || 0,
    });
    return;
  }
  if (url.pathname === "/api/assets") {
    if (!requireSession(request, response)) return;
    json(response, 200, ASSET_CATALOG);
    return;
  }
  if (url.pathname.startsWith("/api/terrain/")) {
    if (!requireSession(request, response)) return;
    try {
      const zoneId = decodeURIComponent(url.pathname.slice("/api/terrain/".length));
      json(response, 200, terrainFor(catalog, zoneId));
    } catch {
      json(response, 404, {error: "terrain not found"});
    }
    return;
  }

  const route = classifyGameTableRoute(request.method, url.pathname);
  if (route === "forbidden") {
    json(response, 404, {error: "not found"});
    return;
  }
  if (route === "proxy") {
    if (!requireSession(request, response)) return;
    proxyGameTable(request, response, {
      host: config.gameTableHost,
      port: config.gameTablePort,
      timeoutMs: config.gameTableTimeoutMs,
    });
    return;
  }
  if (request.method !== "GET") {
    json(response, 405, {error: "method not allowed"});
    return;
  }

  const relative = url.pathname === "/" ? "index.html" : url.pathname.replace(/^\/+/, "");
  const target = path.resolve(publicDirectory, relative);
  if (!target.startsWith(publicDirectory + path.sep) && target !== path.join(publicDirectory, "index.html")) {
    json(response, 404, {error: "not found"});
    return;
  }
  let body;
  try {
    body = fs.readFileSync(target);
  } catch {
    json(response, 404, {error: "not found"});
    return;
  }
  if (target.endsWith("index.html")) ensureSession(request, response);
  response.writeHead(200, {
    "Content-Type": mime.get(path.extname(target)) || "application/octet-stream",
    "Content-Length": body.length,
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'self'; connect-src 'self' ws: wss:; img-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
  });
  response.end(body);
}

const httpServer = http.createServer(staticResponse);
const io = new SocketIOServer(httpServer, {
  maxHttpBufferSize: config.maxHttpBufferSize,
  serveClient: true,
  cors: {
    origin(origin, callback) {
      if (!origin || config.allowedOrigins.has(origin)) callback(null, true);
      else callback(new Error("origin is not allowed"));
    },
    methods: ["GET", "POST"],
  },
  allowRequest(request, callback) {
    callback(null, hostAllowed(request) && originAllowed(request));
  },
});

io.use((socket, next) => {
  if (io.engine.clientsCount > config.maxConnections) {
    next(new Error("Player Gateway connection capacity reached"));
    return;
  }
  const origin = socket.handshake.headers.origin;
  if (origin && !config.allowedOrigins.has(origin)) {
    next(new Error("origin is not allowed"));
    return;
  }
  const sessionId = sessions.fromRequest(socket.request);
  if (!sessionId) {
    next(new Error("player session required"));
    return;
  }
  const address = socket.handshake.address || "unknown";
  if ((ipConnections.get(address) || 0) >= config.maxConnectionsPerIp) {
    next(new Error("connection rate limit reached"));
    return;
  }
  socket.data.sessionId = sessionId;
  socket.data.address = address;
  next();
});

function ackOk(callback, value = {}) {
  if (typeof callback === "function") callback({ok: true, ...value});
}

function ackError(callback, error) {
  if (typeof callback === "function") callback(publicError(error));
}

io.on("connection", (socket) => {
  const address = socket.data.address;
  ipConnections.set(address, (ipConnections.get(address) || 0) + 1);

  socket.emit("gateway.status", {
    protocol_version: BROWSER_PROTOCOL_VERSION,
    ...core.status(),
    control: core.controlStatus(socket.id),
  });
  if (core.latestFrame) {
    socket.emit("frame.latest", {version: 1, frame: core.latestFrame, reset: false});
  }

  socket.on("control.acquire", async (payload, callback) => {
    try {
      validateControlRequest(payload);
      const control = await core.acquire(socket.id);
      socket.emit("gateway.status", {...core.status(), control});
      ackOk(callback, {control});
    } catch (error) {
      ackError(callback, error);
    }
  });

  socket.on("input_state", (payload, callback) => {
    try {
      const accepted = core.offerInput(socket.id, payload);
      ackOk(callback, {accepted});
    } catch (error) {
      ackError(callback, error);
    }
  });

  socket.on("control.release", async (payload, callback) => {
    try {
      validateControlRequest(payload);
      const released = await core.release(socket.id);
      ackOk(callback, {released});
    } catch (error) {
      ackError(callback, error);
    }
  });

  socket.on("disconnect", () => {
    const current = Math.max(0, (ipConnections.get(address) || 1) - 1);
    if (current) ipConnections.set(address, current);
    else ipConnections.delete(address);
    void core.release(socket.id);
  });
});

core.start({
  onFrame(frame, reset) {
    io.volatile.emit("frame.latest", {version: 1, frame, reset});
  },
  onStatus(status) {
    io.emit("gateway.status", status);
  },
  onInput(value) {
    io.emit("input.applied", value);
  },
});

httpServer.listen(config.port, config.bind, () => {
  process.stdout.write(
    `Player Gateway: http://${config.bind}:${config.port} · GameTable proxy 127.0.0.1:${config.gameTablePort}\n`
  );
});

let stopping = false;
async function stop() {
  if (stopping) return;
  stopping = true;
  await core.stop();
  await new Promise((resolve) => io.close(resolve));
  await new Promise((resolve) => httpServer.close(resolve));
}
for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => {
    void stop().finally(() => process.exit(0));
  });
}

export {core, httpServer, io};
