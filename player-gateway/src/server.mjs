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
import {BROWSER_PROTOCOL_VERSION, publicError, validateControlRequest} from "./protocol.mjs";

const sourceDirectory = path.dirname(fileURLToPath(import.meta.url));
const projectDirectory = path.dirname(sourceDirectory);
const repoRoot = path.dirname(projectDirectory);
const publicDirectory = path.join(projectDirectory, "public");
const config = loadConfig();
const catalog = loadMapCatalog(path.join(repoRoot, "world", "maps"));

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

function json(response, status, value) {
  const body = Buffer.from(JSON.stringify(value));
  response.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": body.length,
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
  });
  response.end(body);
}

const mime = new Map([
  [".html", "text/html; charset=utf-8"],
  [".js", "text/javascript; charset=utf-8"],
  [".css", "text/css; charset=utf-8"],
  [".json", "application/json; charset=utf-8"],
]);

function staticResponse(request, response) {
  if (request.method !== "GET") {
    json(response, 405, {error: "method not allowed"});
    return;
  }
  const url = new URL(request.url || "/", "http://localhost");
  if (url.pathname === "/health") {
    json(response, 200, core.status());
    return;
  }
  if (url.pathname === "/api/assets") {
    json(response, 200, ASSET_CATALOG);
    return;
  }
  if (url.pathname.startsWith("/api/terrain/")) {
    try {
      const zoneId = decodeURIComponent(url.pathname.slice("/api/terrain/".length));
      json(response, 200, terrainFor(catalog, zoneId));
    } catch (error) {
      json(response, 404, {error: error.message});
    }
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
  response.writeHead(200, {
    "Content-Type": mime.get(path.extname(target)) || "application/octet-stream",
    "Content-Length": body.length,
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'self'; connect-src 'self' ws: wss:; img-src 'self'; script-src 'self'; style-src 'self'",
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
  next();
});

function ackOk(callback, value = {}) {
  if (typeof callback === "function") callback({ok: true, ...value});
}

function ackError(callback, error) {
  if (typeof callback === "function") callback(publicError(error));
}

io.on("connection", (socket) => {
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
    `Player Gateway: http://${config.bind}:${config.port} (Host[human] 127.0.0.1:${config.hostPort})\n`
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
