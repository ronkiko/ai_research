"""Compatibility shim. Canonical implementation lives in organism.training."""
from organism.training import *  # noqa: F401,F403
from organism.training import _measure_curriculum_frontier, _prepare_reward_config, _sample_curriculum_task

from organism.training import main as main

if __name__ == "__main__":
    raise SystemExit(main())
