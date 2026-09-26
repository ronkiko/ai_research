#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"

usage() {
  cat <<'EOF'
Organism — learned body operator

Usage:
  ./organism/op/organism.sh prepare
  ./organism/op/organism.sh check
  ./organism/op/organism.sh train motor [motor-school options]
  ./organism/op/organism.sh train spine [spine-school options]
  ./organism/op/organism.sh verify [options]
  ./organism/op/organism.sh run --target X [options]
  ./organism/op/organism.sh serve [learning|navigation]
EOF
}

[[ $# -gt 0 ]] || { usage; exit 0; }
source "$ROOT/organism/op/_env.sh"
cd "$ROOT"
command="$1"; shift

case "$command" in
  prepare)
    [[ $# -eq 0 ]] || { usage >&2; exit 2; }
    echo "Organism runtime ready"
    ;;
  check)
    [[ $# -eq 0 ]] || { usage >&2; exit 2; }
    echo "CHECK Organism + embodied world"
    "$ORGANISM_PY" -m compileall -q -x '(^|/)\.venv(/|$)' organism world
    "$ORGANISM_PY" -m unittest discover -s organism/tests -p 'test_*.py' -v
    "$ORGANISM_PY" -m unittest discover -s world/tests -p 'test_*.py' -v
    "$ORGANISM_PY" -c 'import organism.mcp, world.mcp'
    echo "PASS organism checks"
    ;;
  train)
    [[ $# -ge 1 ]] || { usage >&2; exit 2; }
    subject="$1"; shift
    case "$subject" in
      motor)
        exec "$ORGANISM_PY" -m organism.motor_school "$@"
        ;;
      spine)
        have_motor=0
        have_mode=0
        for arg in "$@"; do
          case "$arg" in
            --motor|--motor=*) have_motor=1 ;;
            --mode|--mode=*) have_mode=1 ;;
          esac
        done
        extra=()
        [[ "$have_motor" -eq 1 ]] || extra+=(--motor best)
        [[ "$have_mode" -eq 1 ]] || extra+=(--mode realtime)
        exec "$ORGANISM_PY" -m organism.training "${extra[@]}" "$@"
        ;;
      *) echo "ERROR train requires motor or spine" >&2; exit 2 ;;
    esac
    ;;
  verify)
    exec "$ORGANISM_PY" -m organism.verify "$@"
    ;;
  run)
    exec "$ORGANISM_PY" -m organism.runtime "$@"
    ;;
  serve)
    service="${1:-learning}"
    [[ $# -le 1 ]] || { usage >&2; exit 2; }
    export PYTHONUNBUFFERED=1
    if [[ -n "${GAMETABLE_MCP_BRIDGE_URL:-}" ]]; then
      case "$service" in learning|navigation) ;; *) exit 2 ;; esac
      exec "$ORGANISM_PY" -m gametable.roleplay.scoped_mcp "$service"
    fi
    case "$service" in
      learning) exec "$ORGANISM_PY" -m organism.mcp ;;
      navigation) exec "$ORGANISM_PY" -m world.mcp ;;
      *) echo "ERROR serve expects learning or navigation" >&2; exit 2 ;;
    esac
    ;;
  help|-h|--help) usage ;;
  *) usage >&2; exit 2 ;;
esac
