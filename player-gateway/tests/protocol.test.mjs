import assert from "node:assert/strict";
import test from "node:test";
import {validateInputState} from "../src/protocol.mjs";

test("input protocol accepts only normalized axis state", () => {
  assert.deepEqual(
    validateInputState({version: 1, sequence: 7, axis_x: -1}),
    {version: 1, sequence: 7, axis_x: -1},
  );
  assert.throws(
    () => validateInputState({version: 1, sequence: 8, axis_x: 1, x: 999}),
    /fields mismatch/,
  );
  assert.throws(
    () => validateInputState({version: 1, sequence: 9, axis_x: 2}),
    /axis_x/,
  );
});
