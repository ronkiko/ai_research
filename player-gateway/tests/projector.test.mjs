import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import {fileURLToPath} from "node:url";

import {loadMapCatalog, projectWorldSnapshot} from "../src/projector.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(here, "../..");
const fixture = JSON.parse(
  fs.readFileSync(path.join(repoRoot, "graphics/tests/fixtures/player_gateway_parity.json"), "utf8")
);
const catalog = loadMapCatalog(path.join(repoRoot, "world/maps"));

test("Node projector matches shared Python RenderFrame parity fixture", () => {
  const frame = projectWorldSnapshot(
    fixture.snapshot,
    fixture.focus_entity_id,
    catalog,
    {source: fixture.source},
  );
  assert.deepEqual(frame, fixture.expected);
});
