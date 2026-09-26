import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import {fileURLToPath} from "node:url";

import {loadConfig} from "../src/config.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const project = path.resolve(here, "..");

test("Player Gateway runtime has no direct GameServer port and frames are volatile latest-only", () => {
  const runtime = ["src/server.mjs", "src/core.mjs", "src/config.mjs", "src/host-client.mjs"]
    .map((name) => fs.readFileSync(path.join(project, name), "utf8"))
    .join("\n");
  assert.equal(runtime.includes("17600"), false);
  assert.equal(/\bGATEWAY_PORT\b/.test(runtime), false);
  const server = fs.readFileSync(path.join(project, "src/server.mjs"), "utf8");
  assert.match(server, /io\.volatile\.emit\("frame\.latest"/);
});

function withEnv(values, fn) {
  const previous = new Map();
  for (const [key, value] of Object.entries(values)) {
    previous.set(key, process.env[key]);
    if (value == null) delete process.env[key];
    else process.env[key] = value;
  }
  try { return fn(); }
  finally {
    for (const [key, value] of previous) {
      if (value == null) delete process.env[key];
      else process.env[key] = value;
    }
  }
}

test("public bind requires explicit public mode and TLS/auth deployment contract", () => {
  withEnv({
    PLAYER_GATEWAY_BIND: "0.0.0.0",
    PLAYER_GATEWAY_PUBLIC: null,
  }, () => assert.throws(() => loadConfig(), /PLAYER_GATEWAY_PUBLIC=1/));

  withEnv({
    PLAYER_GATEWAY_BIND: "0.0.0.0",
    PLAYER_GATEWAY_PUBLIC: "1",
    PLAYER_GATEWAY_ALLOWED_ORIGINS: "https://game.example",
    PLAYER_GATEWAY_ALLOWED_HOSTS: "game.example",
    PLAYER_GATEWAY_SESSION_SECRET: "x".repeat(40),
    PLAYER_GATEWAY_TLS_MODE: null,
  }, () => assert.throws(() => loadConfig(), /TLS termination contract/));

  withEnv({
    PLAYER_GATEWAY_BIND: "0.0.0.0",
    PLAYER_GATEWAY_PUBLIC: "1",
    PLAYER_GATEWAY_ALLOWED_ORIGINS: "https://game.example",
    PLAYER_GATEWAY_ALLOWED_HOSTS: "game.example",
    PLAYER_GATEWAY_SESSION_SECRET: "x".repeat(40),
    PLAYER_GATEWAY_TLS_MODE: "reverse_proxy",
  }, () => {
    const config = loadConfig();
    assert.equal(config.publicMode, true);
    assert.equal(config.tlsMode, "reverse_proxy");
  });
});
