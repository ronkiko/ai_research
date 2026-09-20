# UI Boundary

The future management UI is an operator interface, not Display and not a
runtime bridge. It must not forward Joystick actions, Engine STATE, or model
inference through a management process.


The future Bot Profiler follows the same boundary. It edits and inspects
versioned Bot Profiles through `BotProfilerBackend`; it does not import
PyTorch, Planner, Motor, Critic, Optimizer, or Trainer implementation classes.

The silhouette is a Management visualization of the real profile hierarchy:
Research Strategist / LLM at the brain, Planner / CNN at the spinal cord, and
named reflex Motors at body actuators. A click on a callout selects a profile
component for the settings panel. The UI must obtain topology/configuration
metadata from the backend rather than hardcoding values such as `5-8-3`.
