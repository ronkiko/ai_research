#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

bash -n   op/process.sh   gameserver/v1/op/server.sh   gameclient/v1/op/host.sh   gameclient/v1/op/director-host.sh   gameclient/v1/op/gui.sh   gametable/op/start.sh   player-gateway/op/gateway.sh

for script in   gameserver/v1/op/server.sh   gameclient/v1/op/host.sh   gameclient/v1/op/director-host.sh   gameclient/v1/op/gui.sh   gametable/op/start.sh   player-gateway/op/gateway.sh
do
  grep -q -- '--start' "$script"
  grep -q -- '--stop' "$script"
  grep -q -- '--restart' "$script"
  grep -q -- '--status' "$script"
done

# Yuki and Director run the same Python module, so their managed-process
# fallback markers must include distinct fixed ports. Otherwise one Host can
# be mistaken for the other during status/restart.
grep -q -- 'gameclient.v1.host.server --port 17700' gameclient/v1/op/host.sh
grep -q -- 'gameclient.v1.host.server --port 17701' gameclient/v1/op/director-host.sh
grep -q -- 'occupied by an unmanaged or foreign process' gameclient/v1/op/host.sh
grep -q -- 'occupied by an unmanaged or foreign process' gameclient/v1/op/director-host.sh
grep -q -- 'player-gateway/src/server.mjs' player-gateway/op/gateway.sh

# shellcheck source=/dev/null
source "$ROOT/op/process.sh"

TAG="process-smoke-$$"
MARKER="ai-research-process-smoke"
LABEL="Managed process smoke"
STARTER=""

cleanup() {
  op_stop_process "$TAG" "$ROOT" "$MARKER" "$ROOT" "$LABEL" >/dev/null 2>&1 || true
  if [[ -n "$STARTER" ]]; then
    wait "$STARTER" 2>/dev/null || true
  fi
}
trap cleanup EXIT

(
  # shellcheck source=/dev/null
  source "$ROOT/op/process.sh"
  op_managed_process     start     "$TAG"     "$ROOT"     "$MARKER"     "$ROOT"     "$LABEL"     bash -c 'exec -a ai-research-process-smoke sleep 30'
) &
STARTER=$!

for ((i=0; i<100; i++)); do
  if op_status_process "$TAG" "$ROOT" "$MARKER" "$ROOT" "$LABEL" >/dev/null 2>&1; then
    break
  fi
  sleep 0.02
done

op_status_process "$TAG" "$ROOT" "$MARKER" "$ROOT" "$LABEL" >/dev/null

if (
  source "$ROOT/op/process.sh"
  op_managed_process     start     "$TAG"     "$ROOT"     "$MARKER"     "$ROOT"     "$LABEL"     bash -c 'exec -a ai-research-process-smoke sleep 30'
) >/dev/null 2>&1; then
  echo "ERROR duplicate managed start unexpectedly succeeded" >&2
  exit 1
fi

op_stop_process "$TAG" "$ROOT" "$MARKER" "$ROOT" "$LABEL" >/dev/null
wait "$STARTER" 2>/dev/null || true
STARTER=""

if op_status_process "$TAG" "$ROOT" "$MARKER" "$ROOT" "$LABEL" >/dev/null 2>&1; then
  echo "ERROR managed process still reported running after stop" >&2
  exit 1
fi


# Legacy launchers could leave the same managed module running with cwd inside a
# checkout subdirectory. The current manager must reclaim that process instead
# of claiming it is stopped and then colliding on the fixed Host port.
LEGACY_TAG="process-legacy-$"
LEGACY_MARKER="ai-research-process-legacy-$"
LEGACY_LABEL="Legacy managed process smoke"
LEGACY_DIR="$(mktemp -d "$ROOT/.process-smoke.XXXXXX")"
LEGACY_PID=""

cleanup_legacy() {
  if [[ -n "$LEGACY_PID" ]]; then
    kill -KILL "$LEGACY_PID" 2>/dev/null || true
    wait "$LEGACY_PID" 2>/dev/null || true
  fi
  rm -rf "$LEGACY_DIR"
}
trap 'cleanup_legacy; cleanup' EXIT

(
  cd "$LEGACY_DIR"
  exec -a "$LEGACY_MARKER" sleep 30
) &
LEGACY_PID=$!

for ((i=0; i<100; i++)); do
  if op_status_process "$LEGACY_TAG" "$ROOT" "$LEGACY_MARKER" "$ROOT" "$LEGACY_LABEL" >/dev/null 2>&1; then
    break
  fi
  sleep 0.02
done

op_status_process "$LEGACY_TAG" "$ROOT" "$LEGACY_MARKER" "$ROOT" "$LEGACY_LABEL" >/dev/null
op_stop_process "$LEGACY_TAG" "$ROOT" "$LEGACY_MARKER" "$ROOT" "$LEGACY_LABEL" >/dev/null
wait "$LEGACY_PID" 2>/dev/null || true
LEGACY_PID=""
rm -rf "$LEGACY_DIR"

echo "PASS managed operator process smoke"
