#!/usr/bin/env bash
# Mac-only recovery launcher. No Windows, Unreal, CoSys, AirSim or Ethernet required.
set -uo pipefail

REPO="${VERISWARM_RESCUE_REPO:-/Volumes/HyperDrive/Development/VERISWARM_SIH_RESCUE_SECURITY}"
PYTHON="${VERISWARM_PYTHON:-/Volumes/HyperDrive/Development/VERISWARM_SIH/.venv/bin/python}"
MISSION_ID=OP-VARUNA-001
RESCUE_PORT="${VERISWARM_RESCUE_PORT:-8770}"
DASHBOARD_PORT="${VERISWARM_DASHBOARD_PORT:-5175}"
RUN_STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RESCUE_LOG="${VERISWARM_RESCUE_LOG:-$REPO/codebase/results/mac_fallback_rescue_events.$RUN_STAMP.jsonl}"
COLLECTOR_PID=""

RED=$'\e[31m'; GRN=$'\e[32m'; CYN=$'\e[36m'; OFF=$'\e[0m'
ok()  { printf "  ${GRN}PASS${OFF}  %s\n" "$1"; }
die() { printf "  ${RED}FAIL${OFF}  %s\n" "$1"; exit 1; }

cleanup() {
  if [ -n "$COLLECTOR_PID" ]; then
    kill -TERM "$COLLECTOR_PID" 2>/dev/null || true
    wait "$COLLECTOR_PID" 2>/dev/null || true
  fi
  echo
  echo "${CYN}Mac fallback dashboard and collector stopped.${OFF}"
}
trap cleanup EXIT INT TERM

echo "=== 1. Mac fallback preflight ==="
[ -f "$REPO/codebase/tools/mac_fallback_rescue_simulator.py" ] \
  || die "fallback simulator missing"
[ -f "$REPO/codebase/tools/rescue_event_collector.py" ] \
  || die "rescue collector missing"
[ -f "$REPO/codebase/c2_dashboard/package.json" ] \
  || die "dashboard missing"
[ -x "$PYTHON" ] || die "Python runtime not executable: $PYTHON"
ok "contract-driven fallback files present"

for port in "$RESCUE_PORT" "$DASHBOARD_PORT"; do
  if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    die "local port $port is already in use; stop the earlier VeriSwarm process"
  fi
done

echo "=== 2. Fresh retained-event collector ==="
mkdir -p "$(dirname "$RESCUE_LOG")"
cd "$REPO/codebase" || die "cannot enter codebase"
unset VERISWARM_RESCUE_TOKEN
"$PYTHON" -m tools.rescue_event_collector serve \
  --bind 127.0.0.1 --port "$RESCUE_PORT" \
  --mission-id "$MISSION_ID" --log "$RESCUE_LOG" &
COLLECTOR_PID=$!
for _ in 1 2 3 4 5; do
  curl -fsS "http://127.0.0.1:$RESCUE_PORT/health" >/dev/null 2>&1 && break
  sleep 1
done
curl -fsS "http://127.0.0.1:$RESCUE_PORT/health" >/dev/null \
  && ok "collector on 127.0.0.1:$RESCUE_PORT" || die "collector failed to start"

echo "=== 3. Clearly labeled command-centre dashboard ==="
export VERISWARM_RESCUE_URL="http://127.0.0.1:$RESCUE_PORT"
export VITE_RESCUE_DATA_MODE=MAC_FALLBACK
unset VERISWARM_QUALIFIER_URL VERISWARM_QUALIFIER_TOKEN VERISWARM_MOVEMENT_AUTH_URL
echo
echo "${GRN}Ready.${OFF} Open ${CYN}http://127.0.0.1:$DASHBOARD_PORT${OFF}"
echo "In a second Mac terminal run:"
echo "  ${CYN}cd '$REPO'${OFF}"
echo "  ${CYN}VERISWARM_RESCUE_PORT=$RESCUE_PORT ./ops/run_mac_fallback_mission.sh nominal${OFF}"
echo
echo "Fallback evidence log: ${CYN}$RESCUE_LOG${OFF}"
echo "This mode is synthetic/contract-driven and is not presented as CoSys or Unreal evidence."
echo "Jetson model-hash qualification remains intentionally separate."
echo "Ctrl+C stops the dashboard and collector."
echo

cd "$REPO/codebase/c2_dashboard" || die "dashboard directory missing"
[ -d node_modules ] || npm ci || die "npm dependency installation failed"
npm run dev -- --host 127.0.0.1 --port "$DASHBOARD_PORT"
