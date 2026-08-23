#!/usr/bin/env bash
# Fetch one create-once qualification dashboard proof from Alpha and publish its
# normalized authorization decision to the rescue collector on Abhijan's Mac.
set -uo pipefail

REPO="${VERISWARM_RESCUE_REPO:-/Volumes/HyperDrive/Development/VERISWARM_SIH_RESCUE_SECURITY}"
PYTHON="${VERISWARM_PYTHON:-/Volumes/HyperDrive/Development/VERISWARM_SIH/.venv/bin/python}"
JETSON_USER="${VERISWARM_JETSON_USER:-akaberlinflix}"
JETSON_IP=192.168.50.10
REMOTE_EVIDENCE=/home/akaberlinflix/IHQ-20260821-001/evidence/protocol
COLLECTOR_PORT_FILE="${VERISWARM_RESCUE_PORT_FILE:-$REPO/codebase/results/abhijan_rescue_collector.port}"
COLLECTOR_PORT="${VERISWARM_RESCUE_PORT:-}"
if [ -z "${VERISWARM_RESCUE_URL:-}" ] && [ -z "$COLLECTOR_PORT" ] \
  && [ -f "$COLLECTOR_PORT_FILE" ]; then
  COLLECTOR_PORT=$(tr -d '\r\n' < "$COLLECTOR_PORT_FILE")
fi
case "${COLLECTOR_PORT:-8770}" in
  ''|*[!0-9]*) printf 'FAIL invalid rescue collector port\n' >&2; exit 1 ;;
esac
COLLECTOR_PORT="${COLLECTOR_PORT:-8770}"
COLLECTOR_URL="${VERISWARM_RESCUE_URL:-http://127.0.0.1:$COLLECTOR_PORT}"
STATE="${VERISWARM_AUTHORIZATION_STATE:-$REPO/codebase/results/abhijan_authorization_state.json}"
NODE=alpha

die() { printf 'FAIL %s\n' "$1" >&2; exit 1; }

FILENAME="${1:-}"
[ -n "$FILENAME" ] || die "usage: $0 <create-once.dashboard.json>"
[ "$(basename "$FILENAME")" = "$FILENAME" ] || die "evidence must be a filename, not a path"
case "$FILENAME" in
  *.dashboard.json) ;;
  *) die "expected the .dashboard.json filename shown by the dashboard" ;;
esac
printf '%s' "$FILENAME" | grep -Eq '^[A-Za-z0-9][A-Za-z0-9._-]{0,159}\.dashboard\.json$' \
  || die "unsafe evidence filename"

[ -x "$PYTHON" ] || die "Python runtime not executable: $PYTHON"
[ -f "$REPO/codebase/tools/rescue_authorization_adapter.py" ] \
  || die "authorization adapter missing from $REPO"
curl -fsS "$COLLECTOR_URL/health" >/dev/null \
  || die "rescue collector is not running at $COLLECTOR_URL"

TMP=$(mktemp -d /private/tmp/veriswarm-rescue-proof.XXXXXX) || die "cannot create temporary directory"
LOCAL_EVIDENCE="$TMP/$FILENAME"
cleanup() {
  rm -f "$LOCAL_EVIDENCE"
  rmdir "$TMP" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

scp -q "$JETSON_USER@$JETSON_IP:$REMOTE_EVIDENCE/$FILENAME" "$LOCAL_EVIDENCE" \
  || die "could not fetch $FILENAME from Alpha"
[ -s "$LOCAL_EVIDENCE" ] || die "fetched evidence is empty"

cd "$REPO/codebase" || die "cannot enter codebase"
"$PYTHON" -m tools.rescue_authorization_adapter \
  --evidence "$LOCAL_EVIDENCE" \
  --node "$NODE" \
  --collector-url "$COLLECTOR_URL" \
  --state "$STATE"
