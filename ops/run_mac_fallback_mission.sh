#!/usr/bin/env bash
# Emit one visible five-drone mission into the Mac-local rescue collector.
set -euo pipefail

REPO="${VERISWARM_RESCUE_REPO:-/Volumes/HyperDrive/Development/VERISWARM_SIH_RESCUE_SECURITY}"
PYTHON="${VERISWARM_PYTHON:-/Volumes/HyperDrive/Development/VERISWARM_SIH/.venv/bin/python}"
MODE="${1:-nominal}"
RESCUE_PORT="${VERISWARM_RESCUE_PORT:-8770}"

case "$MODE" in
  nominal|hold|quarantine) ;;
  *) echo "Usage: $0 [nominal|hold|quarantine]" >&2; exit 2 ;;
esac

curl -fsS "http://127.0.0.1:$RESCUE_PORT/health" >/dev/null \
  || { echo "FAIL collector is not running; start ./ops/start_mac_fallback_dashboard.sh first" >&2; exit 2; }

cd "$REPO/codebase"
exec "$PYTHON" -m tools.mac_fallback_rescue_simulator \
  --collector-url "http://127.0.0.1:$RESCUE_PORT" \
  --mode "$MODE" --interval 0.25 --steps 24
