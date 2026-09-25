#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"

usage() {
  cat <<'EOF'
GameLab — one operator command

Usage:
  ./gamelab/op/gamelab.sh check [--existing-server]
  ./gamelab/op/gamelab.sh check --mcp-startup
  ./gamelab/op/gamelab.sh check --full [--seeds 1,2,3]

  ./gamelab/op/gamelab.sh train motor [options]
  ./gamelab/op/gamelab.sh train spine [options]

  ./gamelab/op/gamelab.sh verify [options]
  ./gamelab/op/gamelab.sh run --target X [options]
  ./gamelab/op/gamelab.sh serve

Actions:
  check
      Fast compile/unit/integration gate.
      --mcp-startup verifies the exact stdio MCP startup used by OpenCode.
      --full runs the complete multi-seed learned research gate.

  train motor
      Default: construct a new Motor, train to stable BEST, then certify.
      --resume UUID       resume an interrupted pre-certification Motor
      --quick             short PASS-oriented diagnostic training
      --no-certify        train for the requested budget without certification
      --certify UUID      certify an existing development-qualified BEST
      Other options such as --seed, --episodes and --architecture pass through.

  train spine
      Train Spine. Defaults: --motor best --mode unpaced.
      Override with --motor UUID and/or --mode realtime when needed.
      Other options such as --fresh, --episodes, --seed and --target pass through.

  verify
      Frozen acceptance of the saved organism. No learning.

  run
      Run the saved organism toward a realtime target.

  serve
      Start the GameLab MCP service for OpenCode/agents.

Examples:
  ./gamelab/op/gamelab.sh check
  ./gamelab/op/gamelab.sh check --full
  ./gamelab/op/gamelab.sh train motor
  ./gamelab/op/gamelab.sh train motor --resume <uuid>
  ./gamelab/op/gamelab.sh train spine --fresh
  ./gamelab/op/gamelab.sh train spine --mode realtime --motor <uuid> --fresh
  ./gamelab/op/gamelab.sh verify --target 987 --runs 3
  ./gamelab/op/gamelab.sh run --target 987
  ./gamelab/op/gamelab.sh serve
EOF
}

die() {
  echo "ERROR $*" >&2
  echo >&2
  usage >&2
  exit 2
}

load_env() {
  # shellcheck source=/dev/null
  source "$ROOT/gamelab/op/_env.sh"
  cd "$ROOT"
}

quick_check() {
  local existing_server=0
  if [[ "${1:-}" == "--existing-server" ]]; then
    existing_server=1
    shift
  fi
  [[ $# -eq 0 ]] || die "check accepts only --existing-server or --full"

  if [[ "$existing_server" -eq 1 ]]; then
    export GAMELAB_TEST_EXISTING_SERVER=1
  fi

  echo "CHECK GameLab compile"
  "$GAMELAB_PY" -m compileall -q -x '/\.venv/' gamelab

  echo "CHECK GameLab unit tests"
  "$GAMELAB_PY" -m unittest discover -s gamelab/tests -p 'test_*.py' -v

  echo "CHECK real GameLab model -> Host -> GameServer smoke"
  "$GAMELAB_PY" -m gamelab.tests.smoke_runtime

  echo "CHECK real OpenCode-facing MCP goal smoke"
  "$GAMELAB_PY" -m gamelab.tests.smoke_mcp

  echo "PASS gamelab checks"
}

train_motor() {
  local scenario="auto"
  local motor_id=""
  local special=0
  local -a forwarded=()

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --resume)
        [[ $# -ge 2 ]] || die "train motor --resume requires UUID"
        [[ -z "$motor_id" ]] || die "Motor instance was specified more than once"
        motor_id="$2"
        shift 2
        ;;
      --certify)
        [[ $# -ge 2 ]] || die "train motor --certify requires UUID"
        [[ -z "$motor_id" ]] || die "Motor instance was specified more than once"
        [[ "$special" -eq 0 ]] || die "choose only one of --quick, --no-certify, --certify"
        scenario="certify"
        special=1
        motor_id="$2"
        shift 2
        ;;
      --quick)
        [[ "$special" -eq 0 ]] || die "choose only one of --quick, --no-certify, --certify"
        scenario="quick"
        special=1
        shift
        ;;
      --no-certify)
        [[ "$special" -eq 0 ]] || die "choose only one of --quick, --no-certify, --certify"
        scenario="train"
        special=1
        shift
        ;;
      --motor|--motor=*)
        die "use --resume UUID for an existing Motor, or --certify UUID"
        ;;
      *)
        forwarded+=("$1")
        shift
        ;;
    esac
  done

  [[ -z "$motor_id" ]] || forwarded+=(--motor "$motor_id")
  exec "$GAMELAB_PY" -m gamelab.motor_school "$scenario" "${forwarded[@]}"
}

train_spine() {
  local have_motor=0
  local have_mode=0
  local arg
  local -a defaults=()

  for arg in "$@"; do
    case "$arg" in
      --motor|--motor=*) have_motor=1 ;;
      --mode|--mode=*) have_mode=1 ;;
    esac
  done

  [[ "$have_motor" -eq 1 ]] || defaults+=(--motor best)
  [[ "$have_mode" -eq 1 ]] || defaults+=(--mode unpaced)

  exec "$GAMELAB_PY" -m gamelab.training "${defaults[@]}" "$@"
}

if [[ $# -eq 0 ]]; then
  usage
  exit 0
fi

command="$1"
shift

case "$command" in
  help|-h|--help)
    usage
    ;;
  check)
    if [[ "${1:-}" == "--full" ]]; then
      shift
      load_env
      exec "$GAMELAB_PY" -m gamelab.research "$@"
    fi
    if [[ "${1:-}" == "--mcp-startup" ]]; then
      shift
      [[ $# -eq 0 ]] || die "check --mcp-startup accepts no additional arguments"
      load_env
      exec "$GAMELAB_PY" -m gamelab.tests.smoke_mcp_startup
    fi
    load_env
    quick_check "$@"
    ;;
  train)
    [[ $# -ge 1 ]] || die "train requires motor or spine"
    subject="$1"
    shift
    load_env
    case "$subject" in
      motor) train_motor "$@" ;;
      spine) train_spine "$@" ;;
      *) die "unknown training subject '$subject'; expected motor or spine" ;;
    esac
    ;;
  verify)
    load_env
    exec "$GAMELAB_PY" -m gamelab.verify "$@"
    ;;
  run)
    load_env
    exec "$GAMELAB_PY" -m gamelab.runtime "$@"
    ;;
  serve)
    # stdio MCP owns stdout; all bootstrap diagnostics stay on stderr.
    load_env 1>&2
    export PYTHONUNBUFFERED=1
    exec "$GAMELAB_PY" -m gamelab.mcp "$@"
    ;;
  *)
    die "unknown action '$command'"
    ;;
esac
