"""Executable entry point for the process-isolated learned Model runtime."""
from game2.v2.model_runtime import ModelRuntime, build_model, main

__all__ = ["ModelRuntime", "build_model", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
