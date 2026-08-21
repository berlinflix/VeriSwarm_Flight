#!/usr/bin/env bash
# Alpha (Jetson): the model-hash demo, in every quorum condition.
#
#   ./run_model_hash.sh              FULL      both peers up
#   ./run_model_hash.sh --degraded   DEGRADED  exactly one peer up
#   ./run_model_hash.sh --isolated   ISOLATED  no peers up
#   ./run_model_hash.sh --check      preconditions only, changes nothing
#   ./run_model_hash.sh --matrix     print the outcome table and exit
#
# Each mode asserts the peer condition it names, then runs both cases against
# predictions stored in files. A mode that finds the wrong number of peers stops
# rather than silently demonstrating a different scenario than the one claimed.
set -uo pipefail

REPO=/home/akaberlinflix/VeriSwarm_SIH_FROZEN/codebase
IHQ=/home/akaberlinflix/IHQ-20260821-001
CA=/home/akaberlinflix/veriswarm/optee/host/veriswarm_optee_ca
TA=/lib/optee_armtz/7e9a4c10-3b62-4d8e-a1f5-9c2b6d04e7a3.ta
BRAVO=192.168.50.12; BRAVO_PORT=51001
CHARLIE=192.168.50.13; CHARLIE_PORT=51003
MAX_SKEW_MS=250          # protocol bound; peers abstain past this
WARN_SKEW_MS=150         # headroom for drift during the run

RED=$'\e[31m'; GRN=$'\e[32m'; YEL=$'\e[33m'; CYN=$'\e[36m'; OFF=$'\e[0m'

MODE=full; CHECK_ONLY=0
case "${1:-}" in
  "")           MODE=full ;;
  --check)      CHECK_ONLY=1 ;;
  --degraded)   MODE=degraded ;;
  --isolated)   MODE=isolated ;;
  --matrix)
    cat <<EOF

${CYN}Outcome matrix - measured, not predicted${OFF}

  peers live | CLEAN                        | MODEL-SWAP
  -----------+------------------------------+-----------------------------
  2 (full)   | ACCEPTED  acks=2 semantic=2  | REJECTED   disputes=2
  1 (degraded)| NO_QUORUM acks=1 -> HOLD     | REJECTED   disputes=1
  0 (isolated)| NO_QUORUM acks=0 -> HOLD     | NO_QUORUM  -> HOLD

  ${GRN}One witness is enough to refuse. It takes two to permit.${OFF}
  A degraded swarm becomes more conservative, never more permissive.
  With no peers at all even the attack yields NO_QUORUM: the swarm does
  not claim to have caught what it never verified.

  ./run_model_hash.sh              both peers up
  ./run_model_hash.sh --degraded   stop Bravo first
  ./run_model_hash.sh --isolated   stop both peers first

EOF
    exit 0 ;;
  *) echo "unknown option: $1 (try --matrix)"; exit 2 ;;
esac

fail=0
ok()   { printf "  ${GRN}PASS${OFF}  %s\n" "$1"; }
bad()  { printf "  ${RED}FAIL${OFF}  %s\n" "$1"; fail=1; }
warn() { printf "  ${YEL}WARN${OFF}  %s\n" "$1"; }
peer_up() { timeout 3 bash -c "</dev/tcp/$1/$2" 2>/dev/null; }

[ $CHECK_ONLY -eq 1 ] && echo "${CYN}=== PRECONDITION CHECK ===${OFF}" \
                      || echo "${CYN}=== MODE: ${MODE^^} ===${OFF}"

echo "=== 1. Alpha hardware ==="
[ -e /dev/tee0 ] && ok "/dev/tee0 present" || bad "/dev/tee0 missing - OP-TEE unavailable"
[ -f "$TA" ]     && ok "Trusted Application installed" || bad "TA missing: $TA"
[ -x "$CA" ]     && ok "Client Application executable" || bad "CA missing: $CA"

echo "=== 2. Bundle ==="
for f in public/contract.json public/clean-case.json public/model-swap-case.json \
         alpha-private/alpha.manifest.json; do
  [ -f "$IHQ/$f" ] && ok "$f" || bad "missing $f"
done
for d in bravo-private charlie-private; do
  [ -e "$IHQ/$d" ] && bad "$d present on Alpha - seed isolation violated" \
                   || ok "no $d on Alpha"
done

# Scenario predictions are derived once from the signed public cases.
if [ "$MODE" != "full" ]; then
  if [ ! -f "$IHQ/scenarios/clean-$MODE.json" ]; then
    echo "  generating scenario cases (one time)..."
    (cd "$REPO" && PYTHONPATH=$(python3 -m site --user-site) python3 -m tools.make_scenarios \
       --public-dir "$IHQ/public" --out "$IHQ/scenarios") || bad "could not derive scenarios"
  fi
  [ -f "$IHQ/scenarios/clean-$MODE.json" ] && ok "scenario cases for $MODE" \
    || bad "scenario cases missing for $MODE"
fi

echo "=== 3. Clock ==="
if timedatectl show -p NTPSynchronized --value | grep -q yes; then
  ok "clock synchronized ($(date -u '+%H:%M:%S') UTC)"
else
  bad "clock NOT synchronized - Jetson has no RTC battery"
  warn "ensure Charlie ($CHARLIE) is on the switch, then:"
  warn "  sudo systemctl restart systemd-timesyncd && sleep 20"
fi

echo "=== 4. Peer condition for ${MODE^^} ==="
live=0; live_names=""
peer_up "$BRAVO" "$BRAVO_PORT"     && { live=$((live+1)); live_names="bravo"; }
peer_up "$CHARLIE" "$CHARLIE_PORT" && { live=$((live+1)); live_names="${live_names:+$live_names,}charlie"; }
echo "  peers reachable: $live  [${live_names:-none}]"

if [ $CHECK_ONLY -eq 1 ]; then
  [ $live -eq 2 ] && ok "full quorum available" \
    || warn "$live peer(s) up - full mode needs 2; --degraded needs 1; --isolated needs 0"
else
  case "$MODE" in
    full)     [ $live -eq 2 ] && ok "both peers up as FULL requires" \
                || bad "FULL needs 2 peers, found $live - start them, or use --degraded / --isolated" ;;
    degraded) [ $live -eq 1 ] && ok "exactly one peer up as DEGRADED requires" \
                || bad "DEGRADED needs exactly 1 peer, found $live - stop one (Ctrl+C) or start one" ;;
    isolated) [ $live -eq 0 ] && ok "no peers up as ISOLATED requires" \
                || bad "ISOLATED needs 0 peers, found $live - stop them with Ctrl+C" ;;
  esac
fi

# Skew only matters where a peer can actually vote.
if [ $live -gt 0 ] && [ $fail -eq 0 ]; then
  echo "=== 5. Peer clock offsets (bound ${MAX_SKEW_MS}ms) ==="
  OFFSETS=$(cd "$REPO" && PYTHONPATH=$(python3 -m site --user-site) python3 -c "
import sys,time,json
sys.path.insert(0,'.')
from node import common
from protocol import attestation_pb2 as pb, attestation_pb2_grpc as pb_grpc
m=json.load(open('$IHQ/alpha-private/alpha.manifest.json'))
for pid in ('bravo','charlie'):
    try:
        ch=common.channel_for(m,'alpha',pid); s=pb_grpc.AttestationServiceStub(ch)
        t0=time.time_ns(); r=s.Ping(pb.PingRequest(from_drone_id='alpha'),timeout=3); t1=time.time_ns()
        print('%s %.1f'%(pid,(r.timestamp_ns-(t0+t1)//2)/1e6)); ch.close()
    except Exception:
        print('%s OFFLINE'%pid)
" 2>/dev/null)
  while read -r peer off; do
    [ -z "$peer" ] && continue
    [ "$off" = "OFFLINE" ] && { echo "  ${CYN}----${OFF}  $peer offline (expected in $MODE)"; continue; }
    abs=${off#-}
    if   awk "BEGIN{exit !($abs > $MAX_SKEW_MS)}"; then bad "$peer offset ${off}ms > ${MAX_SKEW_MS}ms - it WILL abstain"
    elif awk "BEGIN{exit !($abs > $WARN_SKEW_MS)}"; then warn "$peer offset ${off}ms - little headroom"
    else ok "$peer offset ${off}ms"; fi
  done <<< "$OFFSETS"
fi

echo
if [ $fail -ne 0 ]; then
  echo "${RED}PRECONDITIONS FAILED - not running the protocol.${OFF}"; exit 1
fi
echo "${GRN}All preconditions passed.${OFF}"
[ $CHECK_ONLY -eq 1 ] && { echo "(--check: stopping here)"; exit 0; }

if [ "$MODE" = "full" ]; then
  CLEAN_CASE="$IHQ/public/clean-case.json"; SWAP_CASE="$IHQ/public/model-swap-case.json"
else
  CLEAN_CASE="$IHQ/scenarios/clean-$MODE.json"; SWAP_CASE="$IHQ/scenarios/model-swap-$MODE.json"
fi

export VERISWARM_OPTEE_CA="$CA"
USER_SITE=$(python3 -m site --user-site)
TAG="${MODE^^}-$(date -u +%Y%m%dT%H%M%S)"
cd "$REPO" || exit 1

# The hardware preflight needs no peers, so it runs in every mode.
echo; echo "=== 6. Fresh OP-TEE preflight ==="
PUB=$(python3 -c "import json;print(json.load(open('$IHQ/public/contract.json'))['alpha_pubkey'])")
sudo env "PYTHONPATH=$USER_SITE" "VERISWARM_OPTEE_CA=$CA" python3 -m tools.optee_preflight \
  --ca "$CA" --ta "$TA" --expected-pubkey "$PUB" \
  --out "$IHQ/evidence/optee/preflight-$TAG.json" \
  --mission-id internal-qualifier-2026-08-19 --mission-epoch 1 \
  --node-id alpha --sequence 0 || { echo "${RED}OP-TEE preflight FAILED${OFF}"; exit 1; }

echo; echo "=== 7. Q-CLEAN ($MODE) ==="
sudo env "PYTHONPATH=$USER_SITE" "VERISWARM_OPTEE_CA=$CA" python3 -m tools.qualification_protocol_demo \
  --manifest "$IHQ/alpha-private/alpha.manifest.json" --case "$CLEAN_CASE" \
  --out "$IHQ/evidence/protocol/clean-$TAG.json" --run-id "$TAG-CLEAN"
clean_rc=$?

echo; echo "=== 8. Q-MODEL-SWAP ($MODE) ==="
sudo env "PYTHONPATH=$USER_SITE" "VERISWARM_OPTEE_CA=$CA" python3 -m tools.qualification_protocol_demo \
  --manifest "$IHQ/alpha-private/alpha.manifest.json" --case "$SWAP_CASE" \
  --out "$IHQ/evidence/protocol/model-swap-$TAG.json" --run-id "$TAG-MODEL-SWAP"
swap_rc=$?

echo
if [ $clean_rc -eq 0 ] && [ $swap_rc -eq 0 ]; then
  echo "${GRN}=== ${MODE^^} PASSED ===${OFF}  evidence tag: $TAG"
  case "$MODE" in
    full)     echo "  Both cases behaved as the signed panel cases predict." ;;
    degraded) echo "  ${CYN}One witness refused the attack; one ACK could not authorise the clean command.${OFF}" ;;
    isolated) echo "  ${CYN}With no witnesses, nothing is authorised and nothing is claimed caught.${OFF}" ;;
  esac
  exit 0
fi
echo "${RED}=== ${MODE^^} FAILED ===${OFF} clean_rc=$clean_rc swap_rc=$swap_rc (evidence retained: $TAG)"
exit 1
