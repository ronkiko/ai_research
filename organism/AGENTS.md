# Organism development rules

This directory is the canonical learned physical-control implementation.

- Do not add Brain/Heart/Head, relationship, duality, executive or VN state here.
- GameServer is the only physical authority; never set authoritative x/vx here.
- One live body has one Motor writer. Do not add actuator fallback/teacher logic.
- Policy sensors must be explicit and measurable; renderer or hidden world state
  is not an input.
- Preserve Motor certificate hashes and frozen VERIFY boundaries.
- Use IDs in job/service interfaces; filesystem paths are internal only.
- Unpaced execution must use the same canonical GameServer physics kernel.
- Keep GameLab files as compatibility adapters until the planned cutover.
