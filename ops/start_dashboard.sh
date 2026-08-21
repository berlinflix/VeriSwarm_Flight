#!/usr/bin/env bash
# Alpha (Jetson): serve the qualification dashboard API for Abhijan's Mac.
#
# Binds 127.0.0.1 ONLY. The Mac reaches it through an SSH tunnel, so the API is
# never exposed on the LAN and the one-session token never crosses the wire in
# the clear.
#
# Run AFTER ./run_model_hash.sh has passed. That ordering is deliberate: it
# separates protocol/OP-TEE/network faults from Vite, tunnel and browser faults.
set -uo pipefail

REPO=/home/akaberlinflix/VeriSwarm_SIH_FROZEN/codebase
IHQ=/home/akaberlinflix/IHQ-20260821-001
CA=/home/akaberlinflix/veriswarm/optee/host/veriswarm_optee_ca

RED=$'\e[31m'; GRN=$'\e[32m'; CYN=$'\e[36m'; OFF=$'\e[0m'

# The service builds Alpha's OP-TEE signer only when a case is requested, so a
# missing CA surfaces as a mid-demo failure rather than a startup error. Check now.
for p in /dev/tee0 "$CA" "$IHQ/alpha-private/alpha.manifest.json" "$IHQ/public/contract.json"; do
  [ -e "$p" ] || { echo "${RED}FAIL${OFF} missing: $p"; exit 1; }
done

# Peers must already be up: the dashboard runs the same two cases as the CLI.
for hp in 192.168.50.12:51001 192.168.50.13:51003; do
  h=${hp%:*}; p=${hp#*:}
  timeout 3 bash -c "</dev/tcp/$h/$p" 2>/dev/null \
    || { echo "${RED}FAIL${OFF} peer $hp not listening - start Bravo/Charlie first"; exit 1; }
done

if ! timedatectl show -p NTPSynchronized --value | grep -q yes; then
  echo "${RED}FAIL${OFF} clock not synchronized; peers will abstain (ok_no_observation)"
  echo "      fix: sudo systemctl restart systemd-timesyncd && sleep 20"
  exit 1
fi

umask 077
export VERISWARM_OPTEE_CA="$CA"
export VERISWARM_QUALIFIER_TOKEN="$(openssl rand -hex 32)"
USER_SITE=$(python3 -m site --user-site)

# Publish the token to a private file instead of making a human carry it.
# Abhijan's launcher reads it back over the SSH connection it already opens, so
# the secret never crosses chat, a screenshot, or a browser-side variable, and a
# fresh one is minted every session. Readable only by this user.
TOKEN_DIR=/home/akaberlinflix/.veriswarm
TOKEN_FILE="$TOKEN_DIR/dashboard_token"
mkdir -p "$TOKEN_DIR"; chmod 700 "$TOKEN_DIR"
printf '%s\n' "$VERISWARM_QUALIFIER_TOKEN" > "$TOKEN_FILE"
chmod 600 "$TOKEN_FILE"
# The token is only valid while this service runs; never leave it on disk after.
trap 'rm -f "$TOKEN_FILE"; echo; echo "${CYN}Token revoked.${OFF}"' EXIT INT TERM

echo
echo "${CYN}=======================================================${OFF}"
echo "${GRN} Dashboard ready. Abhijan needs NO token from you.${OFF}"
echo "${CYN} His launcher fetches it over SSH automatically.${OFF}"
echo
echo "${CYN} Tell him: ./start_abhijan_mac.sh${OFF}"
echo "${CYN}=======================================================${OFF}"
echo
echo "${GRN}Starting dashboard API on 127.0.0.1:8765  (Ctrl+C to stop)${OFF}"
echo

cd "$REPO" || exit 1
sudo --preserve-env=VERISWARM_OPTEE_CA,VERISWARM_QUALIFIER_TOKEN \
  env "PYTHONPATH=$USER_SITE" python3 -m tools.qualification_dashboard_service \
  --bind 127.0.0.1 --port 8765 \
  --manifest "$IHQ/alpha-private/alpha.manifest.json" \
  --public-dir "$IHQ/public" \
  --evidence-dir "$IHQ/evidence/protocol"
