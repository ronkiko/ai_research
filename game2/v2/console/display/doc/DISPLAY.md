# Display Boundary

Display is a read-only Console presentation service. Its private manifest
contains the Engine STATE endpoint, the immutable `world_file`, selected
`mode` (`vision` or `screen`), and private `self_actor_id`, in addition to
`session_id`.

For every valid snapshot Display validates `type == "state"`, the session, the
map ID, and every Actor entry before rendering. Invalid snapshots are
discarded; TELEMETRY and EVENTS are never used as fallbacks. The service keeps
only the latest validated view and does not expose a raw state socket or a
public raw-state object. STATE transport is latest-only: presentation is not
replay, and intermediate visual states may be dropped. A reader continuously
ingests STATE independently of renderer cadence.

Display is a spectator process. A slow, closed, or failed renderer cannot stop
Engine progression, and a closed screen does not send a gameplay command.
