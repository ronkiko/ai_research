import assert from "node:assert/strict";
import test from "node:test";
import {classifyGameTableRoute} from "../src/proxy.mjs";

test("only narrative GameTable routes cross the Player Gateway proxy", () => {
  assert.equal(classifyGameTableRoute("GET", "/api/state"), "proxy");
  assert.equal(classifyGameTableRoute("GET", "/api/events"), "proxy");
  assert.equal(classifyGameTableRoute("GET", "/api/audit/turn-1"), "proxy");
  assert.equal(classifyGameTableRoute("POST", "/api/turn"), "proxy");
  assert.equal(classifyGameTableRoute("POST", "/api/story/escort-response"), "proxy");
  assert.equal(classifyGameTableRoute("GET", "/api/frames"), "forbidden");
  assert.equal(classifyGameTableRoute("POST", "/api/director/input"), "forbidden");
  assert.equal(classifyGameTableRoute("POST", "/api/director/control/acquire"), "forbidden");
  assert.equal(classifyGameTableRoute("GET", "/api/graphics/assets"), "none");
});
