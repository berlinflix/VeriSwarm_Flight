#!/usr/bin/env bash
# Abhijan Mac: start Pratik's event ingress/dashboard plus the reverse five-lease link.
set -euo pipefail

REPO="${VERISWARM_RESCUE_REPO:-/Volumes/HyperDrive/Development/VERISWARM_SIH_RESCUE_SECURITY}"
PYTHON="${VERISWARM_PYTHON:-/Volumes/HyperDrive/Development/VERISWARM_SIH/.venv/bin/python}"
POLICY="${VERISWARM_MOVEMENT_AUTH_POLICY:-$REPO/codebase/results/pratik_movement_authorization_policy.json}"

[ -x "$PYTHON" ] || { echo "FAIL Python runtime not executable: $PYTHON" >&2; exit 2; }
[ -f "$REPO/ops/start_abhijan_pratik_mac.sh" ] \
  || { echo "FAIL direct Pratik launcher is missing" >&2; exit 2; }

echo "Resetting all five movement leases to fail-closed HOLD."
cd "$REPO/codebase"
"$PYTHON" -m tools.movement_authorization_link set-policy \
  --file "$POLICY" --node all --decision HOLD --reason startup_fail_closed

export VERISWARM_MOVEMENT_AUTH_POLICY="$POLICY"
export VERISWARM_MOVEMENT_AUTH_TARGET_URL="${VERISWARM_MOVEMENT_AUTH_TARGET_URL:-http://192.168.50.11:8772/authorization-snapshot}"
export VERISWARM_MOVEMENT_AUTH_URL="${VERISWARM_MOVEMENT_AUTH_URL:-http://127.0.0.1:8773}"

exec "$REPO/ops/start_abhijan_pratik_mac.sh"
