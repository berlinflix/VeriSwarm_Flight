#!/usr/bin/env bash
# Abhijan's Mac (192.168.50.14): one command, then click the button.
#
#   ./start_abhijan_mac.sh
#
# Opens a tunnel to Alpha, fetches the session token over that same connection,
# and starts the dashboard. No token to copy, no header to set, no second
# terminal. Ctrl+C tears everything down.
#
# ONE-TIME (optional, removes the password prompt entirely):
#   ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519      # if you have no key
#   ssh-copy-id akaberlinflix@192.168.50.10               # asks once, then never again
set -uo pipefail

REPO="${VERISWARM_REPO:-/Volumes/HyperDrive/Development/VERISWARM_SIH_MODEL_SWAP}"
JETSON_USER=akaberlinflix
JETSON_IP=192.168.50.10
COMMIT=4483d87c2e222421d9b413ee997f4ddae89cf8ab
TOKEN_PATH='~/.veriswarm/dashboard_token'
# Short socket path: macOS unix sockets cap around 104 bytes.
CTRL="$HOME/.ssh/vs-ctl"

RED=$'\e[31m'; GRN=$'\e[32m'; YEL=$'\e[33m'; CYN=$'\e[36m'; OFF=$'\e[0m'
ok()  { printf "  ${GRN}PASS${OFF}  %s\n" "$1"; }
die() { printf "  ${RED}FAIL${OFF}  %s\n" "$1"; exit 1; }

cleanup() {
  ssh -S "$CTRL" -O exit "$JETSON_USER@$JETSON_IP" 2>/dev/null
  rm -f "$CTRL"
  echo; echo "${CYN}Tunnel closed.${OFF}"
}
trap cleanup EXIT INT TERM

echo "=== 1. Repository ==="
[ -d "$REPO" ] || die "repo not found: $REPO   (set VERISWARM_REPO=/your/path)"
cd "$REPO" || die "cannot enter $REPO"
HEAD=$(git rev-parse HEAD 2>/dev/null)
[ "$HEAD" = "$COMMIT" ] && ok "frozen commit ${COMMIT:0:12}" \
  || die "wrong commit ${HEAD:0:12} - tell Suyash, do NOT git reset"
[ -z "$(git status --porcelain)" ] && ok "worktree clean" \
  || die "worktree dirty - tell Suyash, do NOT discard changes"

echo "=== 2. Reach Alpha ==="
ping -c1 -t2 "$JETSON_IP" >/dev/null 2>&1 && ok "ping $JETSON_IP" \
  || die "cannot reach $JETSON_IP - check the cable and that your IP is 192.168.50.14"

echo "=== 3. Connect ==="
# Clear a stale tunnel from a previous run before claiming the port.
ssh -S "$CTRL" -O exit "$JETSON_USER@$JETSON_IP" 2>/dev/null
rm -f "$CTRL"
if lsof -nP -iTCP:8765 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "  ${YEL}port 8765 busy - clearing the old tunnel${OFF}"
  lsof -nP -tiTCP:8765 -sTCP:LISTEN | xargs kill 2>/dev/null
  sleep 1
fi
# One multiplexed connection carries the tunnel AND the token fetch, so a
# password (if keys are not set up) is requested at most once.
echo "  (if prompted, enter the Jetson password - once)"
ssh -M -S "$CTRL" -f -N -o ExitOnForwardFailure=yes \
    -L 127.0.0.1:8765:127.0.0.1:8765 "$JETSON_USER@$JETSON_IP" \
  || die "SSH failed - wrong password, or Alpha unreachable"
ssh -S "$CTRL" -O check "$JETSON_USER@$JETSON_IP" >/dev/null 2>&1 \
  && ok "tunnel up (127.0.0.1:8765 -> Alpha)" || die "tunnel did not establish"

echo "=== 4. Session token ==="
TOKEN=$(ssh -S "$CTRL" "$JETSON_USER@$JETSON_IP" "cat $TOKEN_PATH" 2>/dev/null | tr -d '\r\n')
[ ${#TOKEN} -ge 32 ] && ok "token fetched automatically (${#TOKEN} chars)" \
  || die "no token on Alpha - ask Suyash to run ~/start_dashboard.sh"
export VERISWARM_QUALIFIER_TOKEN="$TOKEN"
export VERISWARM_QUALIFIER_URL=http://127.0.0.1:8765

echo "=== 5. Backend health ==="
curl -fsS -H "X-VeriSwarm-Token: $TOKEN" "$VERISWARM_QUALIFIER_URL/health" >/dev/null 2>&1 \
  && ok "qualification API answering" \
  || die "API did not answer - ask Suyash to confirm ~/start_dashboard.sh is running"

echo
echo "${GRN}Ready.${OFF}  ${CYN}Open http://127.0.0.1:5175${OFF}"
echo "Click ${CYN}Model Hash${OFF} -> ${CYN}CLEAN + MODEL HASH${OFF}. Re-run as often as you like."
echo "Ctrl+C here stops the dashboard and closes the tunnel."
echo

cd "$REPO/codebase/c2_dashboard" || die "dashboard directory missing"
[ -d node_modules ] || { echo "  installing dependencies (first run only)..."; npm ci; }
npm run dev -- --host 127.0.0.1 --port 5175
