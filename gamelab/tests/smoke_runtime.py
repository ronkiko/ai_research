"""Fast paced acceptance wrapper for the machine regression gate."""
from __future__ import annotations

from pathlib import Path

from gamelab.acceptance import run_paced_acceptance


def main(*, learned_checkpoint: Path | None = None) -> int:
    return run_paced_acceptance(learned_checkpoint=learned_checkpoint)


if __name__ == "__main__":
    raise SystemExit(main())
