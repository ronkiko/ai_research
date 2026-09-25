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

- `learning_v1` is the only new LLM-facing learning boundary; keep its arguments
  ID-based and bounded.
- Do not expose checkpoint/filesystem paths, arbitrary Python, actor/entity IDs,
  coordinates or Motor effort through learning MCP.
- A physical training/verify job must own the same `BodyLease` as navigation.
- Motor certification is one-shot per UUID/generation; interruption/cancel must
  not create an automatic second exam.
- Candidate weights do not become the mounted production skill until frozen
  verification passes and `skill_select` succeeds at an idle boundary.
- `training_prepare` authority is minted server-side, never by the MCP caller.
