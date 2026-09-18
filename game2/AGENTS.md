# Game2 Agent Rules

Game2 V2 (`game2/v2/`) is the sole active Game2 implementation.
V1 is retired and remains available only through Git history.

Current architecture is defined by V2 source and normative V2 architecture
documents.

## Testing policy

Documentation describes the system.
Tests verify the system, not its documentation.

Tests MUST NOT validate documentation or editorial text. This is a mandatory
rule for all future patches.

Forbidden tests include assertions about:

- README wording;
- documentation wording;
- presence of specific sentences;
- presence of specific headings;
- specific documentation links;
- examples in documentation;
- comments;
- docstrings;
- changelog prose;
- retirement wording;
- migration wording;
- historical explanations;
- exact editorial terminology.

Do not read a README or Markdown file and assert that wording, headings, links,
or old wording are present or absent. Do not create equivalent tests for
documentation under another path.

Tests MUST NOT exist solely to prove that a completed historical change
happened. Do not test that an old file no longer exists, an old module was
relocated, an old name was renamed, V1 was removed, a legacy path is absent,
an old protocol field was removed in an earlier patch, or a previous directory
layout is no longer used. The exception is a current runtime or public
contract invariant. For example, a payload MUST NOT expose a private Engine
capability; a file `game2/engine.py` must not exist is a historical migration
test and is forbidden.

Do not use source text grep/string assertions as a substitute for behavioral or
structural testing. Patterns such as the following are forbidden when used to
check architecture or behavior rather than executable semantics:

```python
source = Path(...).read_text()
assert "foo" not in source
assert "bar" in source
```

This checks spelling or source layout, not an executable invariant.

Structured static checks are allowed when the structure itself is an
architectural contract. Prefer AST or parsed structure over grep or
substrings. Allowed examples include AST import/dependency boundaries,
forbidden domain imports, public/private module dependency direction, and
schema or static type structure when necessary. Keep one canonical AST domain
import boundary test; do not create variants of the same boundary test. The
current boundary rules are expressed directly in structured test data and the
test does not read `DEPENDENCY_RULES.md`.

Machine-readable contracts are code and MAY be tested. This includes JSON
schemas, protocol payload fields, strict manifest decoding, config validation,
wire versions, and enum/value validation. README text is not a test target;
`PlayerManifest` fields, Joystick payload schemas, and `SessionConfig`
validation are.

Before adding any regression test, answer:

> What current executable invariant does this test protect?

If the answer is documentation, wording, history, migration, previous patch
structure, or implementation spelling, do not add the test. Valid categories
include runtime behavior, protocol/contract, state transition,
physics/world behavior, realtime invariant, isolation/concurrency,
public/private boundary, failure handling, and structured dependency boundary.

Before adding a regression, search existing tests. If an existing test already
protects the same invariant, extend it if necessary instead of creating a
near-duplicate. Keep one canonical test for one invariant and avoid tests that
prove the same behavior at multiple historical stages.

The default discovered suite is for fast developer feedback. Expensive tests
must not silently enter default discovery. Process-level, multi-process,
graphical, and long-running verticals use explicit smoke modules and run only
when the relevant subsystem changes.

Canonical default suite:

```bash
python -m unittest discover -s game2/v2/tests -v
```

Persistent-server smoke, when it exists:

```bash
python -m game2.v2.tests.server_smoke
```

Do not add tests for this policy or for `AGENTS.md`. In particular, do not
test that `AGENTS.md` exists, contains a phrase or heading, or links to a
document. Do not add a meta-test that greps test files for forbidden
assertions; this governance rule is for agents and reviewers, not unittest.
