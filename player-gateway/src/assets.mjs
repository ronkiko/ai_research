export const ASSET_CATALOG = Object.freeze({
  version: 1,
  assets: {
    "marker.yuki": {kind:"marker",glyph:"P",label:"Юки",fill:"#d96f8f",stroke:"#6f3347"},
    "marker.director": {kind:"marker",glyph:"D",label:"Директор",fill:"#5f87ad",stroke:"#2f4d69"},
    "marker.actor": {kind:"marker",glyph:"•",label:"Actor",fill:"#728378",stroke:"#38463d"},
    "terrain.road": {kind:"terrain",glyph:".",label:"Road",fill:"#53656a"},
    "terrain.boundary": {kind:"terrain",glyph:"!",label:"Boundary",fill:"#8a5a52"},
    "prop.exit": {kind:"prop",glyph:"@",label:"EXIT",fill:"#506f64"},
    "prop.portal": {kind:"prop",glyph:"%",label:"Door",fill:"#6d7f9b"},
    "prop.workstation": {kind:"prop",glyph:"W",label:"Workstation",fill:"#806b57"},
    "prop.training_area": {kind:"prop",glyph:"T",label:"Training lane",fill:"#788550"},
    "background.hallway_day": {kind:"background",label:"Hallway",fill:"#d7e4df",ground:"#829592"},
    "background.laboratory_day": {kind:"background",label:"Laboratory",fill:"#243b45",ground:"#789094"},
    "background.training_flat_run": {kind:"background",label:"Training",fill:"#d8dfd1",ground:"#78866c"},
  },
});

export function entityAsset(entity) {
  if (entity.owner_id === "character.yuki" || entity.entity_id === "entity.yuki") return "marker.yuki";
  if (entity.owner_id === "character.director" || entity.entity_id === "entity.director") return "marker.director";
  return "marker.actor";
}

export function objectAsset(kind) {
  return ({
    exit_marker: "prop.exit",
    portal: "prop.portal",
    workstation: "prop.workstation",
    training_area: "prop.training_area",
  })[kind] || "marker.actor";
}
