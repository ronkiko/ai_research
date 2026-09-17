# Scheduling

Controller uses the latest Engine TELEMETRY entry for its manifest `actor_id` to
place commands ahead of the global `world_tick` and applies the configured
finite hold. Each private `ActionCommand` carries `actor_id` and global
`target_world_tick`; a previous target keeps commands monotonic. Missing
decisions do not create an infinite hold and do not pause Engine time. This
document does not define a new timing contract for Joystick v1.
