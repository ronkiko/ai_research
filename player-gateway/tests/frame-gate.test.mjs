import assert from "node:assert/strict";
import test from "node:test";
import {FrameGate} from "../src/frame-gate.mjs";

function frame(epoch, revision, tick, previous = null) {
  return {
    frame_id: `frame:${epoch}:${revision}`,
    source_world_epoch: epoch,
    source_world_revision: revision,
    source_world_tick: tick,
    freshness: {previous_epoch: previous},
  };
}

test("frame gate keeps latest revision and fences old epochs", () => {
  const gate = new FrameGate();
  assert.deepEqual(gate.accept(frame("e1", 5, 5)), {accepted: true, reset: false});
  assert.deepEqual(gate.accept(frame("e1", 4, 6)), {accepted: false, reset: false});
  assert.deepEqual(gate.accept(frame("e2", 1, 1, "e1")), {accepted: true, reset: true});
  assert.deepEqual(gate.accept(frame("e1", 6, 6)), {accepted: false, reset: false});
});
