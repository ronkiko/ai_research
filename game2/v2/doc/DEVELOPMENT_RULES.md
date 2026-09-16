# Development Rules

Before implementing any Game2 V2 feature, verify that it preserves the realtime
invariants defined in [REALTIME_SYSTEM.md](REALTIME_SYSTEM.md). Any patch that
blocks the Engine on model inference, ties a physics tick to inference
completion, or turns world progression into request/response stepping is an
architectural violation unless the normative realtime contract is explicitly
changed in the same patch.

Assign every new module to one concrete V2 domain before writing it. Keep
domain-private implementation details inside that domain.

When independent domains need to communicate, define or revise a formal public
contract first. Add its versioning and boundary tests in `contracts/` and
`tests/`; do not solve the dependency by importing a runtime implementation.

Preserve the Console Engine's world authority, fixed-step clock, Controller
scheduling, finite input decisions, ACK mapping, and process ownership unless a
separate normative change explicitly updates the Console specification.

Every architectural directory has a short `README.md` and terminal local
documentation in `doc/`. Keep navigation documents short and put detailed
semantics in the owning domain.
