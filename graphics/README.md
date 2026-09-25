# Graphics

`graphics/` projects versioned world snapshots into browser-facing
`RenderFrame` values. It does not own physics, decide portal transfers, send
Motor commands, or provide policy sensors.

The first renderer is a flat side view for `flat_1d`: continuous authoritative
X stays in GameServer; display cells are the inclusive 0…1000 projection (1001
cells); terrain is static per `terrain_revision`; frame traffic contains sparse
entities/props; multiple entities may occupy one display cell. A seated
workstation pose requires an interaction record, not merely a location ID.

`FrameHub` is latest-frame/coalescing transport state. Slow browsers lose
intermediate frames and never backpressure physics. Same-epoch stale revisions
are rejected; an epoch change must name the previous epoch.

Before stage 09, GameTable uses `LegacyVNGraphics` only so its shell already
consumes the RenderFrame contract. Those frames are marked
`authoritative=false` and `source=legacy_vn_compat`; they are not evidence of
physical movement. Stage 09 replaces this compatibility source with the real
world snapshot source.

Run `./graphics/op/check.sh`.
