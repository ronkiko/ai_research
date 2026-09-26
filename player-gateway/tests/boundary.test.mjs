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

test("Stage 13 refuses public bind by default contract", () => {
  const previous = process.env.PLAYER_GATEWAY_BIND;
  process.env.PLAYER_GATEWAY_BIND = "0.0.0.0";
  try {
    assert.throws(() => loadConfig(), /loopback only/);
  } finally {
    if (previous == null) delete process.env.PLAYER_GATEWAY_BIND;
    else process.env.PLAYER_GATEWAY_BIND = previous;
  }
});
