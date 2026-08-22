#!/usr/bin/env bash
# Set the desired simulation movement authorization on Abhijan's Mac.
set -euo pipefail

REPO="${VERISWARM_RESCUE_REPO:-/Volumes/HyperDrive/Development/VERISWARM_SIH_RESCUE_SECURITY}"
PYTHON="${VERISWARM_PYTHON:-/Volumes/HyperDrive/Development/VERISWARM_SIH/.venv/bin/python}"
POLICY="${VERISWARM_MOVEMENT_AUTH_POLICY:-$REPO/codebase/results/pratik_movement_authorization_policy.json}"

DECISION="${1:-}"
REASON="${2:-}"
NODE="${3:-all}"

if [ -z "$DECISION" ] || [ -z "$REASON" ]; then
  echo "usage: $0 <ALLOW|HOLD|QUARANTINE> <reason> [alpha|bravo|charlie|delta|echo|all]" >&2
  exit 2
fi

cd "$REPO/codebase"
"$PYTHON" -m tools.movement_authorization_link set-policy \
  --file "$POLICY" --node "$NODE" --decision "$DECISION" --reason "$REASON"
