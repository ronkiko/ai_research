"""Compatibility shim. Canonical implementation lives in organism.motor_school."""
from organism.motor_school import *  # noqa: F401,F403

from organism.motor_school import main as main

if __name__ == "__main__":
    raise SystemExit(main())
