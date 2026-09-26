#!/usr/bin/env bash
# Parse GameTable operator lifecycle flags without consuming backend arguments.
# Source this file and call gametable_parse_cli "$@".

gametable_parse_cli() {
  GAMETABLE_ACTION="start"
  GAMETABLE_FRESH=0
  GAMETABLE_FREE_PORTS=0
  GAMETABLE_BACKEND_ARGS=()
  local action_explicit=0
  local requested

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --start|--restart|--stop|--status)
        requested="${1#--}"
        if [[ "$action_explicit" -eq 1 && "$GAMETABLE_ACTION" != "$requested" ]]; then
          echo "ERROR conflicting lifecycle actions: --$GAMETABLE_ACTION and --$requested" >&2
          return 2
        fi
        GAMETABLE_ACTION="$requested"
        action_explicit=1
        ;;
      --fresh)
        GAMETABLE_FRESH=1
        ;;
      --free-ports)
        GAMETABLE_FREE_PORTS=1
        ;;
      *)
        GAMETABLE_BACKEND_ARGS+=("$1")
        ;;
    esac
    shift
  done

  if [[ "$GAMETABLE_ACTION" == "stop" || "$GAMETABLE_ACTION" == "status" ]]; then
    if [[ "$GAMETABLE_FRESH" -eq 1 ]]; then
      echo "ERROR --fresh cannot be combined with --$GAMETABLE_ACTION" >&2
      return 2
    fi
    if [[ "$GAMETABLE_FREE_PORTS" -eq 1 ]]; then
      echo "ERROR --free-ports cannot be combined with --$GAMETABLE_ACTION" >&2
      return 2
    fi
  fi
}
