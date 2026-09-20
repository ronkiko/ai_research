#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$ROOT"

PYTHON_BIN="${PYTHON:-python3}"
case "${1:-}" in
  model|engine|unpaced|all)
    exec "$PYTHON_BIN" -m game2.v2.performance_probe "$@"
    ;;
  synthetic)
    shift
    exec "$PYTHON_BIN" -m game2.v2.cnn_ppo_probe "$@"
    ;;
  *)
    exec "$PYTHON_BIN" -m game2.v2.cnn_ppo_probe "$@"
    ;;
esac
