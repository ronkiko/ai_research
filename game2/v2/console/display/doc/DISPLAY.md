# Display Boundary

Display is a read-only Console presentation service. Its private manifest
contains exactly the Engine STATE endpoint, the immutable `world_file`, and the
selected `mode` (`vision` or `screen`), in addition to `session_id`.

For every valid snapshot Display validates `type == "state"`, the session, the
map ID, and the avatar coordinates before rendering. Invalid snapshots are
discarded; TELEMETRY and EVENTS are never used as fallbacks. The service keeps
only the latest validated view and does not expose a raw state socket or a
public raw-state object.

Display is a spectator process. A slow, closed, or failed renderer cannot stop
Engine progression, and a closed screen does not send a gameplay command.
