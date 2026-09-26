#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
MODE="${1:---automated}"
[[ $# -le 1 ]] || { echo "ERROR usage: acceptance.sh [--automated|--live]" >&2; exit 2; }
cd "$ROOT"

case "$MODE" in
  --automated)
    echo "ACCEPTANCE deterministic A1-A11 contract gate"
    bash -n gametable/op/start.sh gametable/op/start-go.sh gametable/op/acceptance.sh
    for file in gametable/web/js/*.js; do node --check "$file"; done
    python3 -m unittest gametable.tests.test_acceptance -v
    echo "PASS automated embodied-VN acceptance gate"
    ;;
  --live)
    command -v opencode >/dev/null || {
      echo "BLOCKED live acceptance: opencode is not installed" >&2
      exit 3
    }
    echo "ACCEPTANCE live LLM smoke"
    "$ROOT/gametable/op/check.sh" --live
    echo "PASS live LLM smoke"
    echo "BLOCKED full scientific/manual acceptance still requires the isolated" >&2
    echo "fresh Motor+Spine research run and human visual escort scenarios documented" >&2
    echo "in docs/reports/embodied-vn-acceptance.md" >&2
    exit 3
    ;;
  *)
    echo "ERROR usage: acceptance.sh [--automated|--live]" >&2
    exit 2
    ;;
esac
