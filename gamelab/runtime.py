"""Compatibility shim. Canonical implementation lives in organism.runtime."""
from organism.runtime import *  # noqa: F401,F403

from organism.runtime import main as main

if __name__ == "__main__":
    raise SystemExit(main())
