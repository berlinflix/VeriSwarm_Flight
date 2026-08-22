#!/usr/bin/env bash
# Abhijan's Mac: run the integrated rescue collector and dashboard while securely
# tunnelling the frozen qualification API from Jetson Alpha.
set -uo pipefail

REPO="${VERISWARM_RESCUE_REPO:-/Volumes/HyperDrive/Development/VERISWARM_SIH_RESCUE_SECURITY}"
PYTHON="${VERISWARM_PYTHON:-/Volumes/HyperDrive/Development/VERISWARM_SIH/.venv/bin/python}"
JETSON_USER="${VERISWARM_JETSON_USER:-akaberlinflix}"
JETSON_IP=192.168.50.10
MISSION_ID=OP-VARUNA-001
QUALIFIER_PORT=8765
RESCUE_PORT=8770
PRATIK_INGRESS_PORT=8771
TOKEN_PATH='~/.veriswarm/dashboard_token'
CTRL="$HOME/.ssh/vs-rescue-ctl"
RESCUE_LOG="${VERISWARM_RESCUE_LOG:-$REPO/codebase/results/rescue_events.integration.jsonl}"
COLLECTOR_PID=""
INGRESS_PID=""
PRATIK_IP="${VERISWARM_PRATIK_IP:-}"
MAC_ETHERNET_IP="${VERISWARM_MAC_ETHERNET_IP:-}"

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
  ssh -S "$CTRL" -O exit "$JETSON_USER@$JETSON_IP" 2>/dev/null || true
  rm -f "$CTRL"
  echo
  echo "${CYN}Dashboard, collector and tunnel stopped.${OFF}"
}
trap cleanup EXIT INT TERM

echo "=== 1. Integrated rescue checkout ==="
[ -d "$REPO/codebase" ] || die "rescue repo not found: $REPO"
[ -f "$REPO/codebase/tools/rescue_event_collector.py" ] \
  || die "rescue collector missing from $REPO"
[ -f "$REPO/codebase/tools/rescue_ethernet_ingress.py" ] \
  || die "Pratik Ethernet ingress missing from $REPO"
[ -f "$REPO/codebase/tools/rescue_authorization_adapter.py" ] \
  || die "Abhijan authorization adapter missing from $REPO"
[ -x "$PYTHON" ] || die "Python runtime not executable: $PYTHON"
ok "rescue integration files present"

echo "=== 2. Wired topology ==="
ping -c1 -t2 "$JETSON_IP" >/dev/null 2>&1 \
  && ok "Alpha reachable at $JETSON_IP" \
  || die "Alpha unreachable; verify Mac 192.168.50.14 and the Ethernet switch"
nc -G 2 -z "$JETSON_IP" 22 >/dev/null 2>&1 \
  && ok "Alpha SSH port 22" || die "Alpha SSH is not accepting connections"
nc -G 2 -z 192.168.50.12 51001 >/dev/null 2>&1 \
  && ok "Bravo peer 192.168.50.12:51001" || die "Bravo peer is not listening"
nc -G 2 -z 192.168.50.13 51003 >/dev/null 2>&1 \
  && ok "Charlie peer 192.168.50.13:51003" || die "Charlie peer is not listening"

if [ -z "$PRATIK_IP" ]; then
  PRATIK_IP=192.168.50.11
fi
[ "$PRATIK_IP" = "192.168.50.11" ] \
  || die "Pratik must use the frozen private address 192.168.50.11; refusing $PRATIK_IP"
ping -c1 -t2 "$PRATIK_IP" >/dev/null 2>&1 \
  && ok "Pratik simulator reachable at $PRATIK_IP" \
  || die "Pratik is unreachable at $PRATIK_IP"

if [ -z "$MAC_ETHERNET_IP" ]; then
  PRATIK_INTERFACE=$(route -n get "$PRATIK_IP" 2>/dev/null | awk '/interface:/{print $2; exit}')
  [ -n "$PRATIK_INTERFACE" ] \
    || die "cannot determine the Ethernet interface used for $PRATIK_IP"
  [ "$PRATIK_INTERFACE" != "en0" ] \
    || die "route to Pratik uses Wi-Fi en0; configure the wired adapter first"
  MAC_ETHERNET_IP=$(ipconfig getifaddr "$PRATIK_INTERFACE" 2>/dev/null || true)
fi
[ -n "$MAC_ETHERNET_IP" ] || die "Mac has no IPv4 address on Pratik's Ethernet interface"
[ "$MAC_ETHERNET_IP" = "192.168.50.14" ] \
  || die "Mac wired address must be 192.168.50.14 for the frozen team topology; found $MAC_ETHERNET_IP"
ok "direct Pratik -> Mac route $PRATIK_IP -> $MAC_ETHERNET_IP"

echo "=== 3. Secure qualifier tunnel ==="
ssh -S "$CTRL" -O exit "$JETSON_USER@$JETSON_IP" 2>/dev/null || true
rm -f "$CTRL"
if lsof -nP -iTCP:$QUALIFIER_PORT -sTCP:LISTEN >/dev/null 2>&1; then
  die "local port $QUALIFIER_PORT is already in use; stop the earlier dashboard tunnel"
fi
ssh -M -S "$CTRL" -f -N -o ExitOnForwardFailure=yes \
  -L "127.0.0.1:$QUALIFIER_PORT:127.0.0.1:$QUALIFIER_PORT" \
  "$JETSON_USER@$JETSON_IP" \
  || die "SSH tunnel failed"
ssh -S "$CTRL" -O check "$JETSON_USER@$JETSON_IP" >/dev/null 2>&1 \
  && ok "127.0.0.1:$QUALIFIER_PORT -> Alpha qualifier" \
  || die "SSH tunnel did not establish"

echo "=== 4. Qualification session ==="
TOKEN=$(ssh -S "$CTRL" "$JETSON_USER@$JETSON_IP" "cat $TOKEN_PATH" 2>/dev/null | tr -d '\r\n')
[ ${#TOKEN} -ge 32 ] || die "no session token; ask Suyash to run 'vs dash' on Alpha"
curl -fsS -H "X-VeriSwarm-Token: $TOKEN" \
  "http://127.0.0.1:$QUALIFIER_PORT/health" >/dev/null \
  && ok "Jetson qualification API" || die "qualification API did not answer"

echo "=== 5. Rescue collector ==="
if lsof -nP -iTCP:$RESCUE_PORT -sTCP:LISTEN >/dev/null 2>&1; then
  die "local port $RESCUE_PORT is already in use; stop the earlier rescue collector"
fi
mkdir -p "$(dirname "$RESCUE_LOG")"
cd "$REPO/codebase" || die "cannot enter codebase"
"$PYTHON" -m tools.rescue_event_collector serve \
  --bind 127.0.0.1 --port "$RESCUE_PORT" \
  --mission-id "$MISSION_ID" --log "$RESCUE_LOG" &
COLLECTOR_PID=$!
for _ in 1 2 3 4 5; do
  curl -fsS "http://127.0.0.1:$RESCUE_PORT/health" >/dev/null 2>&1 && break
  sleep 1
done
curl -fsS "http://127.0.0.1:$RESCUE_PORT/health" >/dev/null \
  && ok "rescue collector on 127.0.0.1:$RESCUE_PORT" \
  || die "rescue collector failed to start"

echo "=== 6. Pratik direct Ethernet ingress ==="
if lsof -nP -iTCP:$PRATIK_INGRESS_PORT -sTCP:LISTEN >/dev/null 2>&1; then
  die "local port $PRATIK_INGRESS_PORT is already in use; stop the earlier Pratik ingress"
fi
"$PYTHON" -m tools.rescue_ethernet_ingress serve \
  --bind "$MAC_ETHERNET_IP" --port "$PRATIK_INGRESS_PORT" \
  --peer-ip "$PRATIK_IP" \
  --collector-url "http://127.0.0.1:$RESCUE_PORT" &
INGRESS_PID=$!
for _ in 1 2 3 4 5; do
  curl -fsS "http://$MAC_ETHERNET_IP:$PRATIK_INGRESS_PORT/health" >/dev/null 2>&1 && break
  sleep 1
done
curl -fsS "http://$MAC_ETHERNET_IP:$PRATIK_INGRESS_PORT/health" >/dev/null \
  && ok "Pratik ingress on $MAC_ETHERNET_IP:$PRATIK_INGRESS_PORT; source locked to $PRATIK_IP" \
  || die "Pratik Ethernet ingress failed to start"

echo "=== 7. Dashboard ==="
export VERISWARM_QUALIFIER_TOKEN="$TOKEN"
export VERISWARM_QUALIFIER_URL="http://127.0.0.1:$QUALIFIER_PORT"
export VERISWARM_RESCUE_URL="http://127.0.0.1:$RESCUE_PORT"
unset VERISWARM_RESCUE_TOKEN

echo
echo "${GRN}Ready.${OFF} Open ${CYN}http://127.0.0.1:5175${OFF}"
echo "Pratik sends rescue events directly to ${CYN}http://$MAC_ETHERNET_IP:$PRATIK_INGRESS_PORT${OFF}"
echo "No rescue bearer token crosses Ethernet; only source IP $PRATIK_IP is accepted."
echo "Run APPROVED BASELINE first and pause on 2/2 ACK. Then run MODEL-HASH ATTACK."
echo "The second stage will show the create-once .dashboard.json filename."
echo "Publish that result in another Mac terminal with:"
echo "  ./ops/publish_rescue_authorization.sh <filename-shown-by-dashboard>"
echo "Ctrl+C here stops the dashboard, collector and SSH tunnel."
echo

cd "$REPO/codebase/c2_dashboard" || die "dashboard directory missing"
[ -d node_modules ] || npm ci || die "npm dependency installation failed"
npm run dev -- --host 127.0.0.1 --port 5175
