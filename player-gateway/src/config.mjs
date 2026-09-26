const LOOPBACK_BINDS = new Set(["127.0.0.1", "localhost"]);

function integerEnv(name, fallback, {min = 1, max = Number.MAX_SAFE_INTEGER} = {}) {
  const raw = process.env[name];
  const value = raw == null || raw === "" ? fallback : Number(raw);
  if (!Number.isInteger(value) || value < min || value > max) {
    throw new Error(`${name} must be an integer within [${min},${max}]`);
  }
  return value;
}

function listEnv(name, fallback = []) {
  const raw = process.env[name];
  const values = raw == null
    ? fallback
    : raw.split(",").map((value) => value.trim()).filter(Boolean);
  return new Set(values);
}

export function loadConfig() {
  const bind = process.env.PLAYER_GATEWAY_BIND || "127.0.0.1";
  const port = integerEnv("PLAYER_GATEWAY_PORT", 17881, {min: 1, max: 65535});
  const publicMode = process.env.PLAYER_GATEWAY_PUBLIC === "1";
  const loopback = LOOPBACK_BINDS.has(bind);
  if (!loopback && !publicMode) {
    throw new Error("public Player Gateway bind requires PLAYER_GATEWAY_PUBLIC=1");
  }

  const defaultOrigins = loopback
    ? [`http://127.0.0.1:${port}`, `http://localhost:${port}`]
    : [];
  const defaultHosts = loopback
    ? [`127.0.0.1:${port}`, `localhost:${port}`]
    : [];
  const allowedOrigins = listEnv("PLAYER_GATEWAY_ALLOWED_ORIGINS", defaultOrigins);
  const allowedHosts = listEnv("PLAYER_GATEWAY_ALLOWED_HOSTS", defaultHosts);
  if (!allowedOrigins.size || !allowedHosts.size) {
    throw new Error("Player Gateway allowed origins/hosts must be configured");
  }

  const tlsMode = process.env.PLAYER_GATEWAY_TLS_MODE || (loopback ? "local_http" : "");
  if (!loopback && !["reverse_proxy", "native_https"].includes(tlsMode)) {
    throw new Error("public Player Gateway requires TLS termination contract");
  }

  const sessionSecret = process.env.PLAYER_GATEWAY_SESSION_SECRET || null;
  if (!loopback && (!sessionSecret || sessionSecret.length < 32)) {
    throw new Error("public Player Gateway requires PLAYER_GATEWAY_SESSION_SECRET");
  }

  return Object.freeze({
    bind,
    port,
    publicMode,
    tlsMode,
    sessionSecret,
    allowedOrigins,
    allowedHosts,
    hostBind: "127.0.0.1",
    hostPort: integerEnv("PLAYER_GATEWAY_HOST_PORT", 17701, {min: 1, max: 65535}),
    hostTimeoutMs: integerEnv("PLAYER_GATEWAY_HOST_TIMEOUT_MS", 1000, {min: 100, max: 10000}),
    gameTableHost: "127.0.0.1",
    gameTablePort: integerEnv("PLAYER_GATEWAY_GAMETABLE_PORT", 17880, {min: 1, max: 65535}),
    gameTableTimeoutMs: integerEnv("PLAYER_GATEWAY_GAMETABLE_TIMEOUT_MS", 5000, {min: 500, max: 30000}),
    frameHz: integerEnv("PLAYER_GATEWAY_FRAME_HZ", 25, {min: 1, max: 60}),
    inputHz: integerEnv("PLAYER_GATEWAY_INPUT_HZ", 20, {min: 1, max: 60}),
    inputKeepaliveMs: integerEnv("PLAYER_GATEWAY_INPUT_KEEPALIVE_MS", 500, {min: 100, max: 1500}),
    inputEventsPerSecond: integerEnv("PLAYER_GATEWAY_INPUT_EVENTS_PER_SECOND", 120, {min: 10, max: 1000}),
    maxConnections: integerEnv("PLAYER_GATEWAY_MAX_CONNECTIONS", 64, {min: 1, max: 10000}),
    maxConnectionsPerIp: integerEnv("PLAYER_GATEWAY_MAX_CONNECTIONS_PER_IP", 8, {min: 1, max: 1000}),
    maxHttpBufferSize: integerEnv("PLAYER_GATEWAY_MAX_PAYLOAD_BYTES", 65536, {min: 1024, max: 1048576}),
  });
}

export {LOOPBACK_BINDS};
