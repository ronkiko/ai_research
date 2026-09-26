import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import {fileURLToPath} from "node:url";

import {PlayerGatewayCore} from "../src/core.mjs";
import {loadMapCatalog} from "../src/projector.mjs";

class FakeClient {
  constructor(handler) {
    this.handler = handler;
    this.requests = [];
  }
  async request(type, fields = {}) {
    this.requests.push({type, fields});
    return this.handler(type, fields);
  }
  close() {}
}

const here = path.dirname(fileURLToPath(import.meta.url));
const catalog = loadMapCatalog(path.resolve(here, "../../world/maps"));

test("transient Host stale publishes a matching recovery status", async () => {
  const fixture = JSON.parse(
    fs.readFileSync(
      path.resolve(here, "../../graphics/tests/fixtures/player_gateway_parity.json"),
      "utf8",
    ),
  );
  let call = 0;
  const state = new FakeClient(() => {
    call += 1;
    return {
      session: {entity_id: fixture.focus_entity_id},
      snapshot: fixture.snapshot,
      freshness: {
        source: "world_state_hub",
        stale: call === 1,
        state: call === 1 ? "stale" : "current",
        age_seconds: call === 1 ? 1.1 : 0.01,
      },
    };
  });
  const control = new FakeClient(() => { throw new Error("unused"); });
  const core = new PlayerGatewayCore({stateClient: state, controlClient: control, catalog});
  const statuses = [];
  core.onStatus = (value) => statuses.push(value);
  assert.equal(await core.pollFrame(), null);
  assert.equal(statuses.at(-1).status, "degraded");
  assert.equal(statuses.at(-1).error, "authoritative Host state is stale");
  const frame = await core.pollFrame();
  assert.ok(frame);
  assert.equal(statuses.at(-1).status, "ready");
  assert.equal(statuses.at(-1).error, null);
  assert.equal(statuses.at(-1).host_freshness.stale, false);
});

test("only one browser session can own the Host manual lease", async () => {
  const state = new FakeClient(() => { throw new Error("unused"); });
  const control = new FakeClient((type) => {
    if (type === "control_acquire") return {lease: {lease_id: "lease.1"}};
    if (type === "control_release") return {lease: {released: true}};
    if (type === "input") return {sequence: 1};
    throw new Error(type);
  });
  const core = new PlayerGatewayCore({stateClient: state, controlClient: control, catalog});
  await core.acquire("browser-a");
  await assert.rejects(() => core.acquire("browser-b"), /already owned/);
  assert.equal(core.controlStatus("browser-a").owned_by_you, true);
  await core.release("browser-a");
});

test("repeated browser input remains bounded before Host", async () => {
  let now = 1000;
  const state = new FakeClient(() => { throw new Error("unused"); });
  let hostSequence = 0;
  const control = new FakeClient((type) => {
    if (type === "control_acquire") return {lease: {lease_id: "lease.1"}};
    if (type === "input") return {sequence: ++hostSequence};
    if (type === "control_release") return {lease: {released: true}};
    throw new Error(type);
  });
  const core = new PlayerGatewayCore({
    stateClient: state,
    controlClient: control,
    catalog,
    inputEventsPerSecond: 20000,
    clock: () => now,
  });
  await core.acquire("browser-a");
  for (let sequence = 0; sequence < 10000; sequence += 1) {
    core.offerInput("browser-a", {version: 1, sequence, axis_x: 1});
  }
  await core.controlQueue;
  assert.equal(control.requests.filter((value) => value.type === "input").length, 1);
  now += 500;
  await core.flushInput();
  await core.controlQueue;
  assert.equal(control.requests.filter((value) => value.type === "input").length, 2);
  await core.release("browser-a");
});
