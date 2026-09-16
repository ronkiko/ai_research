# Contract Versioning

Wire payloads carry `version`. Strict field validation prevents accidental
capability leakage and unknown fields. A breaking field or semantic change
requires a new contract version and corresponding tests; relocation alone does
not change the current version.
