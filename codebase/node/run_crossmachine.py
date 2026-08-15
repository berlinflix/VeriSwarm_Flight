"""
Cross-machine orchestrator: run the live distributed protocol across TWO physical
machines on a LAN, rather than as separate processes on one host.

The originator runs here (the x86-64 host); the k peer AttestationService servers
run on the NVIDIA Jetson Orin Nano and are reached over real TCP/IP. This is the
cross-machine counterpart of run_distributed.py: identical protocol, identical
scenarios, but the receipt broadcast, vote collection, and tally now cross a real
network link instead of loopback. It records the end-to-end consensus latency and
the decisions into results/distributed_crossmachine_latency.csv and
results/distributed_crossmachine_decisions.csv.

Peer servers are started/stopped on the Jetson over SSH; the manifest (with each
node's keypair) is generated here and copied across, so both ends are
cryptographically identical. Run after syncing node/ and protocol/ to the Jetson:

    python -m node.run_crossmachine --jetson user@192.168.1.13 \
        --peer-host 192.168.1.13 --orig-host 172.17.252.142 \
        --remote-dir veriswarm --sizes 3,5,7
"""

from __future__ import annotations

import argparse
import statistics
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from protocol.peer_consensus import ConsensusOutcome
from eval.harness import write_csv
from . import common
from .client import Originator

DRONE_NAMES = [
    "alpha", "bravo", "charlie", "delta", "echo",
    "foxtrot", "golf", "hotel", "india", "juliet", "kilo",
]

_base_port = 52000
_SSH = ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "BatchMode=yes"]


def swarm_ids(n):
    return DRONE_NAMES[:n] if n <= len(DRONE_NAMES) else [f"drone{i}" for i in range(n)]


def covisible_formation(ids, alt: float = 14.0, spacing: float = 3.0):
    """Originator centred, peers symmetric around it, all within one altitude of
    separation so every peer is co-visible (matches run_distributed.py)."""
    offs = [0.0]
    y = spacing
    while len(offs) < len(ids):
        offs.append(y)
        offs.append(-y)
        y += spacing
    return {nid: (0.0, offs[i], alt, 0.0) for i, nid in enumerate(ids)}


def _place(manifest: dict, peers, peer_host: str, orig_host: str) -> None:
    """Assign hosts (peers -> Jetson, originator -> this host) and a fresh port
    block (avoids TIME_WAIT collisions across scenario sets)."""
    global _base_port
    for i, (nid, entry) in enumerate(manifest["nodes"].items()):
        entry["host"] = peer_host if nid in peers else orig_host
        entry["port"] = _base_port + i
    _base_port += 100


@contextmanager
def remote_peer_servers(manifest, peer_ids, jetson, remote_dir, tag):
    """Start the k peer servers on the Jetson over SSH; tear them down after."""
    local = Path(tempfile.gettempdir()) / f"cross_manifest_{tag}.json"
    common.save_manifest(manifest, local)
    remote_manifest = f"cross_manifest_{tag}.json"
    subprocess.run(["scp", "-o", "StrictHostKeyChecking=no", str(local),
                    f"{jetson}:{remote_dir}/{remote_manifest}"], check=True)
    procs = []
    try:
        for pid in peer_ids:
            cmd = (f"cd {remote_dir} && exec python3 -m node.server "
                   f"--manifest {remote_manifest} --id {pid}")
            procs.append(subprocess.Popen(_SSH + [jetson, cmd]))
        yield
    finally:
        subprocess.run(_SSH + [jetson, f"pkill -f 'node.server --manifest {remote_manifest}'"],
                       check=False)
        for p in procs:
            p.terminate()
        for p in procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()


def _dec(n, scenario, observed, expected):
    return {"swarm_size": n, "scenario": scenario, "expected": expected.value,
            "observed": observed.value, "match": observed is expected}


def run(jetson, peer_host, orig_host, remote_dir, sizes=(3, 5, 7), rounds: int = 30) -> None:
    lat_rows, dec_rows = [], []
    for n in sizes:
        ids = swarm_ids(n)
        originator, peers = ids[0], ids[1:]
        poses = covisible_formation(ids)

        # honest + model_swap share one server set (honest observations)
        obs = {nid: [0.1, 0.0, 0.0] for nid in ids}
        man = common.generate_manifest(ids, poses=poses, observations=obs)
        _place(man, peers, peer_host, orig_host)
        with remote_peer_servers(man, peers, jetson, remote_dir, f"n{n}h"):
            orig = Originator(man, originator, peers)
            if not orig.wait_ready(timeout=40):
                raise RuntimeError(f"peers did not come up over LAN at N={n}")
            cs, tot, last = [], [], None
            for _ in range(rounds):
                last = orig.originate(output=(0.1, 0.0, 0.0), model_hash=common.APPROVED_MODEL)
                cs.append(last.consensus_ms)
                tot.append(last.total_ms)
            lat_rows.append({
                "swarm_size": n, "voting_peers": n - 1,
                "consensus_ms_mean": round(statistics.fmean(cs), 3),
                "consensus_ms_std": round(statistics.stdev(cs) if len(cs) > 1 else 0.0, 3),
                "total_ms_mean": round(statistics.fmean(tot), 3),
                "sign_ms": round(last.sign_ms, 3),
                "rounds": rounds, "transport": "gRPC/LAN (x86-64 host <-> Jetson)",
            })
            dec_rows.append(_dec(n, "honest", last.outcome, ConsensusOutcome.ACCEPTED))
            swap = orig.originate(output=(0.1, 0.0, 0.0), model_hash=common.MALICIOUS_MODEL)
            dec_rows.append(_dec(n, "model_swap", swap.outcome, ConsensusOutcome.REJECTED))
            orig.close()

        # adversarial patch: peers observe an avoidance action the originator missed
        obs2 = {originator: [1.0, 0.0, 0.0]}
        obs2.update({p: [0.0, 0.0, 1.0] for p in peers})
        man2 = common.generate_manifest(ids, poses=poses, observations=obs2)
        _place(man2, peers, peer_host, orig_host)
        with remote_peer_servers(man2, peers, jetson, remote_dir, f"n{n}p"):
            orig2 = Originator(man2, originator, peers)
            if not orig2.wait_ready(timeout=40):
                raise RuntimeError(f"peers did not come up over LAN at N={n} (patch)")
            patch = orig2.originate(output=(1.0, 0.0, 0.0), model_hash=common.APPROVED_MODEL)
            dec_rows.append(_dec(n, "adversarial_patch", patch.outcome, ConsensusOutcome.REJECTED))
            orig2.close()

        print(f"N={n}: LAN consensus {lat_rows[-1]['consensus_ms_mean']} ms "
              f"(+/-{lat_rows[-1]['consensus_ms_std']}), honest={dec_rows[-3]['observed']}, "
              f"swap={dec_rows[-2]['observed']}, patch={dec_rows[-1]['observed']}", flush=True)

    write_csv("distributed_crossmachine_latency.csv", lat_rows)
    write_csv("distributed_crossmachine_decisions.csv", dec_rows)
    passed = sum(1 for r in dec_rows if r["match"])
    print(f"\n{passed}/{len(dec_rows)} cross-machine decisions matched expectation")
    print("wrote results/distributed_crossmachine_latency.csv and ..._decisions.csv")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jetson", required=True, help="ssh target, e.g. user@192.168.1.13")
    ap.add_argument("--peer-host", required=True, help="Jetson IP the originator dials")
    ap.add_argument("--orig-host", required=True, help="this host's LAN IP")
    ap.add_argument("--remote-dir", default="veriswarm", help="repo dir on the Jetson")
    ap.add_argument("--sizes", default="3,5,7")
    args = ap.parse_args()
    sizes = tuple(int(s) for s in args.sizes.split(","))
    run(args.jetson, args.peer_host, args.orig_host, args.remote_dir, sizes)


if __name__ == "__main__":
    main()
