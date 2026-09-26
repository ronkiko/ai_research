import assert from "node:assert/strict";
import test from "node:test";
import {HumanInputBuffer} from "../src/input-buffer.mjs";

test("repeated browser events coalesce to one upstream state at one instant", () => {
  const buffer = new HumanInputBuffer({
    maxUpstreamHz: 20,
    keepaliveMs: 500,
    eventBudgetPerSecond: 20000,
    nowMs: 1000,
  });
  let sends = 0;
  for (let sequence = 0; sequence < 10000; sequence += 1) {
    buffer.receive({version: 1, sequence, axis_x: 1}, 1000);
    if (buffer.consume(1000) != null) sends += 1;
  }
  assert.equal(sends, 1);
  assert.equal(buffer.metrics().coalesced, 9999);
});

test("input changes obey upstream cadence and release is explicit zero", () => {
  const buffer = new HumanInputBuffer({maxUpstreamHz: 20, eventBudgetPerSecond: 100});
  buffer.receive({version: 1, sequence: 1, axis_x: 1}, 1000);
  assert.equal(buffer.consume(1000), 1);
  buffer.receive({version: 1, sequence: 2, axis_x: -1}, 1010);
  assert.equal(buffer.consume(1010), null);
  assert.equal(buffer.consume(1050), -1);
  assert.equal(buffer.release(1060), 0);
});

test("browser event budget is bounded independently of upstream cadence", () => {
  const buffer = new HumanInputBuffer({
    maxUpstreamHz: 20,
    eventBudgetPerSecond: 10,
    nowMs: 0,
  });
  for (let sequence = 0; sequence < 10; sequence += 1) {
    buffer.receive({version: 1, sequence, axis_x: 1}, 0);
  }
  assert.throws(
    () => buffer.receive({version: 1, sequence: 10, axis_x: 1}, 0),
    /rate limit/,
  );
});
