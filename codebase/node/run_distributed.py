"""
Stage 3 orchestrator: run the live distributed protocol at 3/5/7 nodes.

For each swarm size it launches the k = N-1 peer drones as *separate processes*
running real AttestationService gRPC servers, then drives the originator through
three scenarios over the network:

    honest             -> every co-visible peer agrees      -> ACCEPTED
    model_swap         -> originator runs an unapproved model -> REJECTED (provenance)
    adversarial_patch  -> originator's action diverges from the co-visible peers'
                          observation                        -> REJECTED (semantic)

It records the real end-to-end consensus latency (signing + gRPC broadcast +
peer verification + tally) and the decisions, into
results/distributed_latency.csv and results/distributed_decisions.csv.

The peers are placed in the co-visible formation measured in the real flight
(~14 m altitude, 3 m spacing), so the C2 gate genuinely engages on the patch.

Run (after `pip install grpcio` if needed):
    python -m node.run_distributed
"""

from __future__ import annotations

import statistics
import subprocess
import sys
import tempfile
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

_base_port = 51000


def swarm_ids(n: int):
    return DRONE_NAMES[:n] if n <= len(DRONE_NAMES) else [f"drone{i}" for i in range(n)]


def covisible_formation(ids, alt: float = 14.0, spacing: float = 3.0):
    """Place the originator at the centre and peers symmetrically around it, all
    within one altitude's worth of separation so every peer is co-visible."""
    offs = [0.0]
    y = spacing
    while len(offs) < len(ids):
        offs.append(y)
        offs.append(-y)
        y += spacing
    return {nid: (0.0, offs[i], alt, 0.0) for i, nid in enumerate(ids)}


def _alloc_ports(manifest: dict) -> None:
    """Assign a fresh port block to this session (avoids TIME_WAIT collisions)."""
    global _base_port
    for i, entry in enumerate(manifest["nodes"].values()):
        entry["host"] = "127.0.0.1"
        entry["port"] = _base_port + i
    _base_port += 100


@contextmanager
def peer_servers(manifest: dict, peer_ids, tag: str):
    mpath = Path(tempfile.gettempdir()) / f"veriswarm_manifest_{tag}.json"
    common.save_manifest(manifest, mpath)
    procs, logs = [], []
    try:
        for pid in peer_ids:
            log = open(Path(tempfile.gettempdir()) / f"veriswarm_{tag}_{pid}.log", "w")
            logs.append(log)
            procs.append(subprocess.Popen(
                [sys.executable, "-m", "node.server", "--manifest", str(mpath), "--id", pid],
                stdout=log, stderr=subprocess.STDOUT,
            ))
        yield
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
        for log in logs:
            log.close()


def _dec(n, scenario, observed, expected):
    return {
        "swarm_size": n, "scenario": scenario,
        "expected": expected.value, "observed": observed.value,
        "match": observed is expected,
    }


def run(sizes=(3, 5, 7), rounds: int = 30) -> None:
    lat_rows, dec_rows = [], []
    for n in sizes:
        ids = swarm_ids(n)
        originator, peers = ids[0], ids[1:]
        poses = covisible_formation(ids)

        # --- honest + model_swap share one server set (honest observations) ---
        obs = {nid: [0.1, 0.0, 0.0] for nid in ids}
        man = common.generate_manifest(ids, poses=poses, observations=obs)
        _alloc_ports(man)
        with peer_servers(man, peers, f"n{n}_h"):
            orig = Originator(man, originator, peers)
            if not orig.wait_ready():
                raise RuntimeError(f"peers did not come up at N={n}")
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
                "rounds": rounds, "transport": "gRPC/localhost",
            })
            dec_rows.append(_dec(n, "honest", last.outcome, ConsensusOutcome.ACCEPTED))
            swap = orig.originate(output=(0.1, 0.0, 0.0), model_hash=common.MALICIOUS_MODEL)
            dec_rows.append(_dec(n, "model_swap", swap.outcome, ConsensusOutcome.REJECTED))
            orig.close()

        # --- adversarial patch: peers observe an avoidance action alpha missed ---
        obs2 = {originator: [1.0, 0.0, 0.0]}
        obs2.update({p: [0.0, 0.0, 1.0] for p in peers})
        man2 = common.generate_manifest(ids, poses=poses, observations=obs2)
        _alloc_ports(man2)
        with peer_servers(man2, peers, f"n{n}_p"):
            orig2 = Originator(man2, originator, peers)
            if not orig2.wait_ready():
                raise RuntimeError(f"peers did not come up at N={n} (patch)")
            patch = orig2.originate(output=(1.0, 0.0, 0.0), model_hash=common.APPROVED_MODEL)
            dec_rows.append(_dec(n, "adversarial_patch", patch.outcome, ConsensusOutcome.REJECTED))
            orig2.close()

        print(f"N={n}: consensus {lat_rows[-1]['consensus_ms_mean']} ms "
              f"(+/-{lat_rows[-1]['consensus_ms_std']}), "
              f"honest={dec_rows[-3]['observed']}, swap={dec_rows[-2]['observed']}, "
              f"patch={dec_rows[-1]['observed']}", flush=True)

    write_csv("distributed_latency.csv", lat_rows)
    write_csv("distributed_decisions.csv", dec_rows)
    passed = sum(1 for r in dec_rows if r["match"])
    print(f"\n{passed}/{len(dec_rows)} distributed decisions matched expectation")
    print("wrote results/distributed_latency.csv and results/distributed_decisions.csv")


if __name__ == "__main__":
    run()
