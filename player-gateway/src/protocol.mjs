export const BROWSER_PROTOCOL_VERSION = 1;

function exactKeys(value, expected) {
  const keys = Object.keys(value).sort();
  const wanted = [...expected].sort();
  return keys.length === wanted.length && keys.every((key, index) => key === wanted[index]);
}

export function validateInputState(payload) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    throw new Error("input_state must be an object");
  }
  if (!exactKeys(payload, ["version", "sequence", "axis_x"])) {
    throw new Error("input_state fields mismatch");
  }
  if (payload.version !== BROWSER_PROTOCOL_VERSION) {
    throw new Error("unsupported Player Gateway protocol version");
  }
  if (!Number.isSafeInteger(payload.sequence) || payload.sequence < 0) {
    throw new Error("input_state sequence must be a non-negative integer");
  }
  if (!Number.isInteger(payload.axis_x) || ![-1, 0, 1].includes(payload.axis_x)) {
    throw new Error("axis_x must be -1, 0, or 1");
  }
  return {
    version: BROWSER_PROTOCOL_VERSION,
    sequence: payload.sequence,
    axis_x: payload.axis_x,
  };
}

export function validateControlRequest(payload = {version: BROWSER_PROTOCOL_VERSION}) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    throw new Error("control request must be an object");
  }
  if (!exactKeys(payload, ["version"]) || payload.version !== BROWSER_PROTOCOL_VERSION) {
    throw new Error("invalid control request");
  }
  return {version: BROWSER_PROTOCOL_VERSION};
}

export function publicError(error) {
  const raw = error instanceof Error ? error.message : String(error || "request failed");
  const safe = raw
    .replace(/(?:127\.0\.0\.1|localhost):\d+/gi, "internal service")
    .replace(/\/(?:home|tmp|var)\/[^\s]*/gi, "internal path")
    .slice(0, 240);
  return {ok: false, error: safe || "request rejected"};
}
