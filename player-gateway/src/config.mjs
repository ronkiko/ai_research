const LOOPBACK_BINDS = new Set(["127.0.0.1", "localhost"]);

function integerEnv(name, fallback, {min = 1, max = Number.MAX_SAFE_INTEGER} = {}) {
  const raw = process.env[name];
  const value = raw == null || raw === "" ? fallback : Number(raw);
  if (!Number.isInteger(value) || value < min || value > max) {
    throw new Error(`${name} must be an integer within [${min},${max}]`);
  }
  return value;
}

function originsEnv() {
  const raw = process.env.PLAYER_GATEWAY_ALLOWED_ORIGINS;
  if (!raw) {
    return new Set([
      "http://127.0.0.1:17881",
      "http://localhost:17881",
    ]);
  }
  const values = raw.split(",").map((value) => value.trim()).filter(Boolean);
  if (!values.length) throw new Error("PLAYER_GATEWAY_ALLOWED_ORIGINS is empty");
  return new Set(values);
}

export function loadConfig() {
  const bind = process.env.PLAYER_GATEWAY_BIND || "127.0.0.1";
  if (!LOOPBACK_BINDS.has(bind)) {
    throw new Error("Stage 13 Player Gateway must bind to loopback only");
  }
  return Object.freeze({
    bind,
    port: integerEnv("PLAYER_GATEWAY_PORT", 17881, {min: 1, max: 65535}),
    hostBind: "127.0.0.1",
    hostPort: integerEnv("PLAYER_GATEWAY_HOST_PORT", 17701, {min: 1, max: 65535}),
    hostTimeoutMs: integerEnv("PLAYER_GATEWAY_HOST_TIMEOUT_MS", 1000, {min: 100, max: 10000}),
    frameHz: integerEnv("PLAYER_GATEWAY_FRAME_HZ", 25, {min: 1, max: 60}),
    inputHz: integerEnv("PLAYER_GATEWAY_INPUT_HZ", 20, {min: 1, max: 60}),
    inputKeepaliveMs: integerEnv("PLAYER_GATEWAY_INPUT_KEEPALIVE_MS", 500, {min: 100, max: 1500}),
    inputEventsPerSecond: integerEnv("PLAYER_GATEWAY_INPUT_EVENTS_PER_SECOND", 120, {min: 10, max: 1000}),
    maxConnections: integerEnv("PLAYER_GATEWAY_MAX_CONNECTIONS", 64, {min: 1, max: 10000}),
    maxHttpBufferSize: integerEnv("PLAYER_GATEWAY_MAX_PAYLOAD_BYTES", 65536, {min: 1024, max: 1048576}),
    allowedOrigins: originsEnv(),
  });
}

export {LOOPBACK_BINDS};
