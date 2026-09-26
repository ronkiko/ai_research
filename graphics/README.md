# Graphics

`graphics/` projects versioned authoritative world snapshots into browser
`RenderFrame` values. It does not own physics, decide portal transfers, send
Motor commands, or provide policy sensors.

The flat renderer projects continuous GameServer X into inclusive 0…1000 display
cells. Terrain is static per `terrain_revision`; frame traffic contains sparse
entities/props. A seated workstation pose requires an interaction record, not
merely a location ID.

`EmbodiedWorldGraphics` is the production GameTable source after cutover 09.
It reads one Host snapshot/observation, projects it through `SceneProjector`,
and marks frames `authoritative=true`, `source=embodied_world_v1`.
`LegacyVNGraphics` remains only for isolated compatibility tests/history and
must never be treated as movement evidence.

`FrameHub` is latest-frame/coalescing transport. Slow browsers lose
intermediate frames and never backpressure physics. Same-epoch stale revisions
are rejected; an epoch change names the previous epoch.

Run `./graphics/op/check.sh`.
