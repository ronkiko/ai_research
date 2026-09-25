"""Compatibility shim. Canonical implementation lives in organism.verify."""
from organism.verify import *  # noqa: F401,F403

from organism.verify import main as main

if __name__ == "__main__":
    raise SystemExit(main())
