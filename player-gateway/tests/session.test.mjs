import assert from "node:assert/strict";
import test from "node:test";
import {SessionCapabilities} from "../src/session.mjs";

test("server-issued player capability is signed and HttpOnly", () => {
  const sessions = new SessionCapabilities("s".repeat(40), {secure: true});
  const value = sessions.issue();
  assert.ok(sessions.validate(value));
  assert.equal(sessions.validate(value + "x"), null);
  const cookie = sessions.cookie(value);
  assert.match(cookie, /HttpOnly/);
  assert.match(cookie, /SameSite=Strict/);
  assert.match(cookie, /Secure/);
});
