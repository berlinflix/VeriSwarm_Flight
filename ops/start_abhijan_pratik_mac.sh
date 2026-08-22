#!/usr/bin/env bash
# Abhijan's Mac: simulation-only direct Pratik -> Mac collector/dashboard path.
# This intentionally does not require Jetson Alpha, Bravo, Charlie or Suyash's laptop.
set -uo pipefail

REPO="${VERISWARM_RESCUE_REPO:-/Volumes/HyperDrive/Development/VERISWARM_SIH_RESCUE_SECURITY}"
PYTHON="${VERISWARM_PYTHON:-/Volumes/HyperDrive/Development/VERISWARM_SIH/.venv/bin/python}"
MISSION_ID=OP-VARUNA-001
RESCUE_PORT=8770
PRATIK_INGRESS_PORT=8771
DASHBOARD_PORT=5175
RESCUE_LOG="${VERISWARM_RESCUE_LOG:-$REPO/codebase/results/rescue_events.integration.jsonl}"
PRATIK_IP="${VERISWARM_PRATIK_IP:-}"
MAC_ETHERNET_IP="${VERISWARM_MAC_ETHERNET_IP:-}"
COLLECTOR_PID=""
INGRESS_PID=""

RED=$'\e[31m'; GRN=$'\e[32m'; CYN=$'\e[36m'; OFF=$'\e[0m'
ok()  { printf "  ${GRN}PASS${OFF}  %s\n" "$1"; }
die() { printf "  ${RED}FAIL${OFF}  %s\n" "$1"; exit 1; }

cleanup() {
  if [ -n "$INGRESS_PID" ]; then
    kill -TERM "$INGRESS_PID" 2>/dev/null || true
    wait "$INGRESS_PID" 2>/dev/null || true
  fi
  if [ -n "$COLLECTOR_PID" ]; then
    kill -TERM "$COLLECTOR_PID" 2>/dev/null || true
    wait "$COLLECTOR_PID" 2>/dev/null || true
  fi
  echo
  echo "${CYN}Simulation dashboard, ingress and collector stopped.${OFF}"
}
trap cleanup EXIT INT TERM

echo "=== 1. Direct simulation checkout ==="
[ -f "$REPO/codebase/tools/rescue_event_collector.py" ] || die "rescue collector missing"
[ -f "$REPO/codebase/tools/rescue_ethernet_ingress.py" ] || die "Ethernet ingress missing"
[ -x "$PYTHON" ] || die "Python runtime not executable: $PYTHON"
ok "direct simulation integration files present"

echo "=== 2. Discover Pratik Ethernet route ==="
if [ -z "$PRATIK_IP" ]; then
  PRATIK_IP=192.168.50.11
fi
[ "$PRATIK_IP" = "192.168.50.11" ] \
  || die "Pratik must use the frozen private address 192.168.50.11; refusing $PRATIK_IP"
ping -c1 -t2 "$PRATIK_IP" >/dev/null 2>&1 || die "Pratik is unreachable at $PRATIK_IP"

if [ -z "$MAC_ETHERNET_IP" ]; then
  PRATIK_INTERFACE=$(route -n get "$PRATIK_IP" 2>/dev/null | awk '/interface:/{print $2; exit}')
  [ -n "$PRATIK_INTERFACE" ] || die "cannot determine Pratik's Ethernet interface"
  [ "$PRATIK_INTERFACE" != "en0" ] \
    || die "route to Pratik uses Wi-Fi en0; configure the wired adapter first"
  MAC_ETHERNET_IP=$(ipconfig getifaddr "$PRATIK_INTERFACE" 2>/dev/null || true)
fi
[ -n "$MAC_ETHERNET_IP" ] || die "Mac has no IPv4 address on Pratik's Ethernet interface"
[ "$MAC_ETHERNET_IP" = "192.168.50.14" ] \
  || die "Mac wired address must be 192.168.50.14; found $MAC_ETHERNET_IP"
ok "direct route $PRATIK_IP -> $MAC_ETHERNET_IP"

for port in "$RESCUE_PORT" "$PRATIK_INGRESS_PORT" "$DASHBOARD_PORT"; do
  if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    die "local port $port is already in use; stop the earlier VeriSwarm process"
  fi
done

echo "=== 3. Loopback collector ==="
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

echo "=== 4. Source-locked Ethernet ingress ==="
"$PYTHON" -m tools.rescue_ethernet_ingress serve \
  --bind "$MAC_ETHERNET_IP" --port "$PRATIK_INGRESS_PORT" \
  --peer-ip "$PRATIK_IP" --collector-url "http://127.0.0.1:$RESCUE_PORT" &
INGRESS_PID=$!
for _ in 1 2 3 4 5; do
  curl -fsS "http://$MAC_ETHERNET_IP:$PRATIK_INGRESS_PORT/health" >/dev/null 2>&1 && break
  sleep 1
done
curl -fsS "http://$MAC_ETHERNET_IP:$PRATIK_INGRESS_PORT/health" >/dev/null \
  && ok "ingress $MAC_ETHERNET_IP:$PRATIK_INGRESS_PORT accepts only $PRATIK_IP" \
  || die "Ethernet ingress failed to start"

echo "=== 5. Dashboard ==="
export VERISWARM_RESCUE_URL="http://127.0.0.1:$RESCUE_PORT"
unset VERISWARM_RESCUE_TOKEN VERISWARM_QUALIFIER_URL VERISWARM_QUALIFIER_TOKEN
echo
echo "${GRN}Ready.${OFF} Open ${CYN}http://127.0.0.1:$DASHBOARD_PORT${OFF}"
echo "Pratik sender endpoint: ${CYN}http://$MAC_ETHERNET_IP:$PRATIK_INGRESS_PORT${OFF}"
echo "Model-hash qualification is intentionally separate/offline in this launcher."
echo "Ctrl+C stops the dashboard, ingress and collector."
echo

cd "$REPO/codebase/c2_dashboard" || die "dashboard directory missing"
[ -d node_modules ] || npm ci || die "npm dependency installation failed"
npm run dev -- --host 127.0.0.1 --port "$DASHBOARD_PORT"
