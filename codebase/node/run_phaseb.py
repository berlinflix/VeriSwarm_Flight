"""
Stage 3 / Phase B: hardware-rooted live distributed consensus.

Identical to run_distributed, but the originator (Alpha) signs its receipts with
the OP-TEE Trusted Application on the Jetson instead of a software key — its
private key never leaves the secure element. The software peers verify Alpha's
hardware signatures over real gRPC and reach consensus exactly as before. This
makes the live distributed result (paper Table 4.15) fully hardware-rooted.

Topology: all nodes on the Jetson's loopback (avoids the WSL2 NAT issue) — Alpha
= OP-TEE signer, peers = software signers, real gRPC between separate processes.

Run ON THE JETSON (needs the CA path):
    cd ~/veriswarm
    VERISWARM_OPTEE_CA=$PWD/optee/host/veriswarm_optee_ca python3 -m node.run_phaseb
"""

from __future__ import annotations

import statistics

from protocol.peer_consensus import ConsensusOutcome
from eval.harness import write_csv
from . import common
from .client import Originator
from .run_distributed import swarm_ids, covisible_formation, peer_servers, _alloc_ports


def _hw_manifest(ids, originator, poses, observations, tee_pubkey):
    """Software manifest, then make the originator hardware-rooted (OP-TEE)."""
    man = common.generate_manifest(ids, poses=poses, observations=observations)
    _alloc_ports(man)
    a = man["nodes"][originator]
    a["backend"] = "optee"
    a["pubkey"] = tee_pubkey       # peers verify against the TEE's public key
    a.pop("seed", None)            # no software private key for Alpha
    return man


def main(n: int = 3, rounds: int = 20) -> None:
    ids = swarm_ids(n)
    originator, peers = ids[0], ids[1:]
    poses = covisible_formation(ids)

    from signing.optee_backend import OPTEEReceiptSigner
    tee_pubkey = OPTEEReceiptSigner().public_key_hex
    print(f"Alpha is hardware-rooted; OP-TEE public key {tee_pubkey[:16]}...")

    lat_row = None
    dec = []

    # honest + model_swap (honest observations)
    obs = {nid: [0.1, 0.0, 0.0] for nid in ids}
    man = _hw_manifest(ids, originator, poses, obs, tee_pubkey)
    with peer_servers(man, peers, "phaseb_h"):
        orig = Originator(man, originator, peers)  # signer_for(alpha) -> OPTEEReceiptSigner
        if not orig.wait_ready():
            raise RuntimeError("peers did not come up")
        cs, tot, sgn, last = [], [], [], None
        for _ in range(rounds):
            last = orig.originate(output=(0.1, 0.0, 0.0), model_hash=common.APPROVED_MODEL)
            cs.append(last.consensus_ms)
            tot.append(last.total_ms)
            sgn.append(last.sign_ms)
        lat_row = {
            "swarm_size": n, "voting_peers": n - 1, "alpha_backend": "op-tee",
            "hw_sign_ms_mean": round(statistics.fmean(sgn), 3),
            "hw_sign_ms_std": round(statistics.stdev(sgn) if len(sgn) > 1 else 0.0, 3),
            "consensus_ms_mean": round(statistics.fmean(cs), 3),
            "consensus_ms_std": round(statistics.stdev(cs) if len(cs) > 1 else 0.0, 3),
            "total_ms_mean": round(statistics.fmean(tot), 3),
            "rounds": rounds, "transport": "gRPC/localhost",
        }
        dec.append(("honest", last.outcome, ConsensusOutcome.ACCEPTED))
        swap = orig.originate(output=(0.1, 0.0, 0.0), model_hash=common.MALICIOUS_MODEL)
        dec.append(("model_swap", swap.outcome, ConsensusOutcome.REJECTED))
        orig.close()

    # adversarial patch (peers observe an avoidance action Alpha missed)
    obs2 = {originator: [1.0, 0.0, 0.0]}
    obs2.update({p: [0.0, 0.0, 1.0] for p in peers})
    man2 = _hw_manifest(ids, originator, poses, obs2, tee_pubkey)
    with peer_servers(man2, peers, "phaseb_p"):
        orig2 = Originator(man2, originator, peers)
        if not orig2.wait_ready():
            raise RuntimeError("peers did not come up (patch)")
        patch = orig2.originate(output=(1.0, 0.0, 0.0), model_hash=common.APPROVED_MODEL)
        dec.append(("adversarial_patch", patch.outcome, ConsensusOutcome.REJECTED))
        orig2.close()

    print(f"\nhardware sign (per receipt, via OP-TEE CA): {lat_row['hw_sign_ms_mean']} ms "
          f"(+/-{lat_row['hw_sign_ms_std']})")
    print(f"distributed consensus (network+verify+tally): {lat_row['consensus_ms_mean']} ms "
          f"(+/-{lat_row['consensus_ms_std']})")
    for name, observed, expected in dec:
        ok = observed is expected
        print(f"  {name:18} {observed.value:9} (expected {expected.value})  {'OK' if ok else 'MISMATCH'}")

    write_csv("distributed_hw.csv", [lat_row])
    write_csv("distributed_hw_decisions.csv",
              [{"scenario": s, "observed": o.value, "expected": e.value, "match": o is e}
               for s, o, e in dec])
    passed = sum(1 for _, o, e in dec if o is e)
    print(f"\n{passed}/{len(dec)} hardware-rooted distributed decisions correct")
    print("wrote results/distributed_hw.csv and results/distributed_hw_decisions.csv")


if __name__ == "__main__":
    main()
