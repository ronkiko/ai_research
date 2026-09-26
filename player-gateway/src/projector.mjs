import fs from "node:fs";
import path from "node:path";
import {entityAsset, objectAsset} from "./assets.mjs";

const REST_VELOCITY = 0.05;
const MAX_FRAME_BYTES = 32 * 1024;

export function loadMapCatalog(mapDirectory) {
  const maps = new Map();
  for (const name of fs.readdirSync(mapDirectory).filter((item) => item.endsWith(".json")).sort()) {
    const value = JSON.parse(fs.readFileSync(path.join(mapDirectory, name), "utf8"));
    if (!value || typeof value.map_id !== "string" || !value.map_id) {
      throw new Error(`invalid map manifest: ${name}`);
    }
    if (maps.has(value.map_id)) throw new Error(`duplicate map_id: ${value.map_id}`);
    maps.set(value.map_id, value);
  }
  if (!maps.size) throw new Error("map catalog is empty");
  return maps;
}

function manifestFor(catalog, zoneId) {
  const value = catalog.get(zoneId);
  if (!value) throw new Error(`unknown map: ${zoneId}`);
  return value;
}

function cameraFor(manifest) {
  const bounds = manifest.physics?.bounds;
  const lo = Number(bounds?.x_min);
  const hi = Number(bounds?.x_max);
  if (!Number.isFinite(lo) || !Number.isFinite(hi) || lo >= hi) {
    throw new Error("map camera bounds are invalid");
  }
  return {
    axis: "x",
    world_min: lo,
    world_max: hi,
    cells: Math.trunc(hi - lo) + 1,
    projection: "linear",
  };
}

function quantizeX(x, camera) {
  const value = Number(x);
  if (!Number.isFinite(value) || value < camera.world_min || value > camera.world_max) {
    throw new Error("x is outside camera bounds");
  }
  return Math.min(camera.cells - 1, Math.floor(value - camera.world_min));
}

function screenX(x, camera) {
  const value = Number(x);
  if (!Number.isFinite(value) || value < camera.world_min || value > camera.world_max) {
    throw new Error("x is outside camera bounds");
  }
  return (value - camera.world_min) / (camera.world_max - camera.world_min);
}

export function terrainFor(catalog, zoneId) {
  const manifest = manifestFor(catalog, zoneId);
  const camera = cameraFor(manifest);
  const objects = (manifest.semantics?.objects || []).map((item) => ({
    object_id: item.object_id,
    kind: item.kind,
    x: Number(item.x),
    display_cell: quantizeX(item.x, camera),
    visual_asset: objectAsset(item.kind),
  }));
  return {
    map_id: zoneId,
    map_version: manifest.map_version,
    terrain_revision: manifest.presentation.terrain_revision,
    theme_id: manifest.presentation.theme_id,
    background_asset: "background." + manifest.presentation.theme_id,
    camera,
    base_asset: "terrain.road",
    objects,
  };
}

export function projectWorldSnapshot(snapshot, focusEntityId, catalog, {
  source = "player_gateway_v1",
} = {}) {
  if (!snapshot || typeof snapshot !== "object") throw new Error("world snapshot is required");
  for (const key of ["world_id", "world_epoch", "world_tick", "world_revision", "entities"]) {
    if (!(key in snapshot)) throw new Error("world snapshot is incomplete");
  }
  if (!Number.isInteger(snapshot.world_tick) || snapshot.world_tick < 0) {
    throw new Error("world_tick is invalid");
  }
  if (!Number.isInteger(snapshot.world_revision) || snapshot.world_revision < 0) {
    throw new Error("world_revision is invalid");
  }
  if (!Array.isArray(snapshot.entities)) throw new Error("entities must be an array");
  const focus = snapshot.entities.find((entity) => entity?.entity_id === focusEntityId);
  if (!focus) throw new Error("focus entity is absent from world snapshot");
  const zoneId = focus.zone_id;
  const manifest = manifestFor(catalog, zoneId);
  const camera = cameraFor(manifest);
  const terrain = terrainFor(catalog, zoneId);
  const seen = new Set();
  const entities = [];
  for (const entity of snapshot.entities) {
    if (entity?.zone_id !== zoneId) continue;
    const entityId = entity.entity_id;
    if (typeof entityId !== "string" || !entityId || seen.has(entityId)) {
      throw new Error("snapshot entity IDs must be unique non-empty strings");
    }
    seen.add(entityId);
    const x = Number(entity.x);
    const vx = Number(entity.vx || 0);
    if (!Number.isFinite(vx)) throw new Error("entity vx must be finite");
    entities.push({
      entity_id: entityId,
      transform: {x},
      display_cell: quantizeX(x, camera),
      screen_x: screenX(x, camera),
      visual_asset: entityAsset(entity),
      animation_state: Math.abs(vx) > REST_VELOCITY ? "moving" : "idle",
    });
  }
  const frame = {
    schema_version: 1,
    frame_id: `frame:${snapshot.world_epoch}:${snapshot.world_revision}:${zoneId}`,
    source_world_epoch: String(snapshot.world_epoch),
    source_world_tick: snapshot.world_tick,
    source_world_revision: snapshot.world_revision,
    zone_id: zoneId,
    camera,
    terrain_revision: terrain.terrain_revision,
    entities,
    props: terrain.objects,
    presentation_revision: `${zoneId}@${manifest.map_version}:${manifest.presentation.theme_id}`,
    freshness: {
      state: "current",
      age_ticks: 0,
      authoritative: true,
      source,
      previous_epoch: snapshot.previous_epoch ?? null,
    },
  };
  const size = Buffer.byteLength(JSON.stringify(frame));
  if (size > MAX_FRAME_BYTES) throw new Error(`RenderFrame exceeds ${MAX_FRAME_BYTES} bytes`);
  return frame;
}

export function projectHostState(state, catalog, options = {}) {
  const session = state?.session;
  const snapshot = state?.snapshot;
  const entityId = session?.entity_id;
  if (typeof entityId !== "string" || !entityId) throw new Error("Host session has no entity_id");
  return projectWorldSnapshot(snapshot, entityId, catalog, options);
}

export {MAX_FRAME_BYTES, REST_VELOCITY};
