# Player Adapters

Adapters may transform only data already granted by a public Player capability.

The current Proprioception path is: private Engine TELEMETRY → Console-owned whitelist/filter → public ProprioceptionFrame → Player receiver → learned-model calibration/normalization → Motor/Critic tensors.

The adapter boundary may normalize units or arrange tensors. It may not recover private Engine fields, query world geometry, enumerate other Actors, or turn simulator-only knowledge into a sensor.

The physical-admissibility test is normative: every Proprioception quantity must correspond to something a contemporary real humanoid could instrument on its own body. External-world sensing requires its own explicit physical sensor surface.
