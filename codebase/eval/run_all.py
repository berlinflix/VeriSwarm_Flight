"""
VeriSwarm evaluation runner.

Executes the experiment groups defined in the paper build plan (Section E +
ADDENDUM), writing one CSV per metric under results/ plus an append-only round
log and a manifest. Every figure in the CSVs is a real measurement taken by
exercising the protocol code; nothing is hard-coded.

Usage
-----
    python -m eval.run_all                 # all software runs
    python -m eval.run_all --only R1,R5    # selected groups
    python -m eval.run_all --hardware      # also attempt R2/R-EN (needs Jetson)
    python -m eval.run_all --repeats 1000  # override the timing sample count

Hardware-only groups (R2 OP-TEE signing latency, R-EN energy) are skipped and
written as PENDING unless an OP-TEE backend is present (see harness.optee_available).
Run those on the Alpha node once the Trusted Application is built.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import math
import os
import random
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

import nacl.signing

from protocol.receipts import (
    ReceiptSigner,
    SignedReceipt,
    build_receipt,
    sha256_hex,
)
from protocol.peer_consensus import (
    ConsensusEngine,
    ConsensusOutcome,
    IsolationPolicy,
    IsolationTracker,
    PeerVerifier,
    ReceiptVerifier,
    ReputationStore,
    VoteVerifier,
    Vote,
    covisibility,
    outputs_agree,
    quorum_thresholds,
)
from protocol.geometry import Pose

from eval import harness as H
from eval.baseline import MajorityBaseline, PlainVote


# ---------------------------------------------------------------------------
# Artifact bookkeeping
# ---------------------------------------------------------------------------


@dataclass
class Artifact:
    filename: str
    feeds: str
    rows: List[Dict[str, object]] = field(default_factory=list)
    status: str = "filled"  # "filled" | "pending"


# ---------------------------------------------------------------------------
# R1 / R2 — receipt signing + hashing latency
# ---------------------------------------------------------------------------


def run_r1_sign_latency(log: H.RunLogger, rng: random.Random, repeats: int) -> List[Artifact]:
    """R1: software (PyNaCl) sign + SHA-256 hash latency over many cycles."""
    cycles = max(repeats, 1000)
    signer = ReceiptSigner()
    hash_ms: List[float] = []
    sign_ms: List[float] = []
    total_ms: List[float] = []
    for i in range(cycles):
        receipt = build_receipt(
            drone_id="alpha", input_bytes=bytes(rng.randrange(256) for _ in range(16)),
            model_hash=H.APPROVED_MODEL, output=(rng.random(), rng.random(), rng.random()),
        )
        canon = receipt.canonical()
        t0 = time.perf_counter_ns()
        hashlib.sha256(canon).digest()
        t1 = time.perf_counter_ns()
        signer._key.sign(canon)
        t2 = time.perf_counter_ns()
        hash_ms.append((t1 - t0) / 1e6)
        sign_ms.append((t2 - t1) / 1e6)
        total_ms.append((t2 - t0) / 1e6)
    h, s, t = H.Stat.of(hash_ms), H.Stat.of(sign_ms), H.Stat.of(total_ms)
    log.round(run="R1", backend="software", cycles=cycles,
              hash_ms=round(h.mean, 4), sign_ms=round(s.mean, 4), total_ms=round(t.mean, 4))
    row = {
        "backend": "software", "hash_ms_mean": round(h.mean, 4), "hash_ms_std": round(h.std, 4),
        "sign_ms_mean": round(s.mean, 4), "sign_ms_std": round(s.std, 4),
        "total_ms_mean": round(t.mean, 4), "total_ms_std": round(t.std, 4), "n_cycles": cycles,
    }
    return [Artifact("sign_latency.csv", "Table 4.2 / Fig 12", [row])]


def run_r2_optee_latency(log: H.RunLogger, rng: random.Random, repeats: int) -> List[Artifact]:
    """R2: OP-TEE signing latency, measured by the host CA's `bench` mode.

    One TEEC session signs a fixed message N times, timing each
    TEEC_InvokeCommand (normal->secure world switch + Ed25519 sign inside the
    TEE). Process spawn and session setup are excluded, so the number is
    directly comparable to R1's in-process software sign. Hardware-only.
    """
    scope = "single_session_invoke_excludes_process_and_session_setup"
    pending = {"backend": "op-tee", "sign_us_mean": "PENDING", "sign_us_std": "PENDING",
               "sign_ms_mean": "PENDING", "sign_ms_std": "PENDING", "n_cycles": 0,
               "scope": scope}
    if not H.optee_available():
        log.round(run="R2", status="SKIPPED", reason="op-tee backend not available (run on Alpha/Jetson)")
        return [Artifact("sign_latency_optee.csv", "Table 4.2 / Fig 12", [pending], status="pending")]
    ca = os.environ.get("VERISWARM_OPTEE_CA") or shutil.which("veriswarm_optee_ca")
    if not ca:
        log.round(run="R2", status="SKIPPED", reason="CA not found; set VERISWARM_OPTEE_CA")
        return [Artifact("sign_latency_optee.csv", "Table 4.2 / Fig 12", [pending], status="pending")]
    cycles = max(repeats, 1000)
    proc = subprocess.run([ca, "bench", str(cycles)], capture_output=True, text=True, timeout=600)
    samples_us = [float(x) for x in proc.stdout.split() if x.strip()]
    if not samples_us:
        log.round(run="R2", status="FAILED", reason=f"no samples; stderr={proc.stderr.strip()[:200]}")
        return [Artifact("sign_latency_optee.csv", "Table 4.2 / Fig 12", [pending], status="pending")]
    s = H.Stat.of(samples_us)
    log.round(run="R2", backend="op-tee", cycles=len(samples_us),
              sign_us=round(s.mean, 2), sign_ms=round(s.mean / 1000.0, 4))
    # Raw per-sign samples for the Fig 12 distribution.
    H.write_csv("sign_latency_optee_samples.csv",
                [{"i": i, "sign_us": round(v, 3)} for i, v in enumerate(samples_us)])
    row = {"backend": "op-tee", "sign_us_mean": round(s.mean, 2), "sign_us_std": round(s.std, 2),
           "sign_ms_mean": round(s.mean / 1000.0, 4), "sign_ms_std": round(s.std / 1000.0, 4),
           "n_cycles": len(samples_us), "scope": scope}
    return [Artifact("sign_latency_optee.csv", "Table 4.2 / Fig 12", [row])]


# ---------------------------------------------------------------------------
# R3 — receipt size + serialize time
# ---------------------------------------------------------------------------


def run_r3_receipt_size(log: H.RunLogger, rng: random.Random, repeats: int) -> List[Artifact]:
    signer = ReceiptSigner()
    receipt = build_receipt(drone_id="alpha", input_bytes=b"\x00" * 128,
                            model_hash=H.APPROVED_MODEL, output=(0.1, -0.3, 0.5))
    signed = signer.sign(receipt)
    canon_bytes = len(receipt.canonical())
    sig_bytes = len(bytes.fromhex(signed.signature))
    wire_bytes = len(signed.serialize())
    cycles = max(repeats, 1000)
    ser_us: List[float] = []
    for _ in range(cycles):
        t0 = time.perf_counter_ns()
        signed.serialize()
        ser_us.append((time.perf_counter_ns() - t0) / 1e3)
    s = H.Stat.of(ser_us)
    log.round(run="R3", canonical_bytes=canon_bytes, sig_bytes=sig_bytes, wire_bytes=wire_bytes,
              serialize_us=round(s.mean, 3))
    row = {"canonical_bytes": canon_bytes, "sig_bytes": sig_bytes, "wire_bytes": wire_bytes,
           "serialize_us_mean": round(s.mean, 3), "serialize_us_std": round(s.std, 3), "n": cycles}
    return [Artifact("receipt_size.csv", "Table 4.3", [row])]


# ---------------------------------------------------------------------------
# R4 — per-check latency (signature / provenance / semantic / co-visibility)
# ---------------------------------------------------------------------------


def run_r4_verify_latency(log: H.RunLogger, rng: random.Random, repeats: int) -> List[Artifact]:
    cycles = max(repeats, 2000)
    signer = ReceiptSigner()
    vk = nacl.signing.VerifyKey(signer.public_key_bytes)
    receipt = build_receipt(drone_id="alpha", input_bytes=b"\x00" * 128,
                            model_hash=H.APPROVED_MODEL, output=(0.1, -0.3, 0.5))
    canon = receipt.canonical()
    sig = signer._key.sign(canon).signature
    approved = frozenset({H.APPROVED_MODEL})
    claimed = (0.1, -0.3, 0.5)
    observed = (0.12, -0.28, 0.49)
    pose_a, pose_b = Pose(0, 0, 10), Pose(2, 0, 10)

    def time_loop(fn) -> H.Stat:
        samples = []
        for _ in range(cycles):
            t0 = time.perf_counter_ns()
            fn()
            samples.append((time.perf_counter_ns() - t0) / 1e6)
        return H.Stat.of(samples)

    sig_stat = time_loop(lambda: vk.verify(canon, sig))
    prov_stat = time_loop(lambda: receipt.model_hash in approved)
    sem_stat = time_loop(lambda: outputs_agree(claimed, observed, 0.5))
    cov_stat = time_loop(lambda: covisibility(pose_a, pose_b))

    rows = []
    for name, st in [("signature", sig_stat), ("provenance", prov_stat),
                     ("semantic_l2", sem_stat), ("covisibility", cov_stat)]:
        log.round(run="R4", check=name, latency_ms=round(st.mean, 6))
        rows.append({"check": name, "latency_ms_mean": round(st.mean, 6),
                     "latency_ms_std": round(st.std, 6), "n": cycles})
    return [Artifact("verify_latency.csv", "Table 4.5", rows)]


# ---------------------------------------------------------------------------
# R5 — the four base scenarios (confirms Table 4.4 decision outcomes)
# ---------------------------------------------------------------------------


def run_r5_base_scenarios(log: H.RunLogger, rng: random.Random, repeats: int) -> List[Artifact]:
    rows = []
    # honest, model-swap, adversarial-patch at N=3; drop at N=5.
    specs = [
        ("honest", 3, ConsensusOutcome.ACCEPTED),
        ("model_swap", 3, ConsensusOutcome.REJECTED),
        ("adversarial_patch", 3, ConsensusOutcome.REJECTED),
        ("message_drop", 5, ConsensusOutcome.NO_QUORUM),
    ]
    for name, n, expected in specs:
        if name == "honest":
            sw = H.build_swarm(n)
            signed = H.alpha_receipt(sw, output=(0.1, 0.0, 0.0))
            votes = H.honest_votes(sw, signed, observation=(0.1, 0.0, 0.0))
        elif name == "model_swap":
            sw = H.build_swarm(n)  # approves only the good model
            signed = H.alpha_receipt(sw, model_hash=H.MALICIOUS_MODEL)
            votes = H.provenance_votes(sw, signed)
        elif name == "adversarial_patch":
            sw = H.build_swarm(n)
            signed = H.alpha_receipt(sw, output=(0.0, 0.0, -1.0))
            votes = H.patch_votes(sw, signed, observation=(0.0, 0.0, 1.0))
        else:  # message_drop: only one of four peers responds
            sw = H.build_swarm(n)
            signed = H.alpha_receipt(sw, output=(0.1, 0.0, 0.0))
            all_votes = H.honest_votes(sw, signed, observation=(0.1, 0.0, 0.0))
            votes = all_votes[:1]
        rr = H.run_round(sw, signed, votes, repeats=max(repeats, 50))
        ok = rr.outcome is expected
        log.round(run="R5", scenario=name, N=n, outcome=rr.outcome.value,
                  decision_ms=round(rr.decision_ms, 4), matches_table_4_4=ok)
        rows.append({"scenario": name, "N": n, "outcome": rr.outcome.value,
                     "expected": expected.value, "matches_table_4_4": ok,
                     "detect_ms_mean": round(rr.decision_ms, 4),
                     "ack": rr.ack_count, "dispute": rr.dispute_count, "missing": rr.missing_count})
    return [Artifact("detection_latency.csv", "Table 4.6 / Table 4.4", rows)]


# ---------------------------------------------------------------------------
# R6+ — colluding voters + reputation trace
# ---------------------------------------------------------------------------


def _malicious_swarm(n: int):
    """Swarm whose honest peers reject the malicious model; colluders accept it."""
    sw = H.build_swarm(n)  # honest verifier approves only the good model
    colluder_rv = ReceiptVerifier(peer_keys=sw.peer_keys,
                                  approved_models={H.APPROVED_MODEL, H.MALICIOUS_MODEL})
    return sw, colluder_rv


def run_r6_colluding(log: H.RunLogger, rng: random.Random, repeats: int) -> List[Artifact]:
    byz_rows = []
    for n in (3, 5, 7):
        sw, colluder_rv = _malicious_swarm(n)
        k = sw.k
        f_theoretical = max(0, (k - 1) // 3)
        eng = ConsensusEngine(num_peers=k)
        signed = H.alpha_receipt(sw, model_hash=H.MALICIOUS_MODEL)
        peers = sw.ids[1:]
        for n_coll in range(0, k + 1):
            colluders = set(peers[:n_coll])
            votes = []
            for d in peers:
                rv = colluder_rv if d in colluders else sw.receipt_verifier
                votes.append(PeerVerifier(d, sw.signers[d], rv).vote_on(signed))
            # Integer-only rule.
            res_int = eng.tally(signed.receipt, votes, sw.vote_verifier,
                                expected_voters=set(sw.ids))
            # Reputation rule with colluders pre-decayed (caught in earlier rounds).
            rep = ReputationStore()
            for c in colluders:
                rep._weights[c] = rep.r_min
            res_rep = eng.tally(signed.receipt, votes, sw.vote_verifier,
                                expected_voters=set(sw.ids), reputation=rep)
            forced_int = res_int.outcome is ConsensusOutcome.ACCEPTED
            forced_rep = res_rep.outcome is ConsensusOutcome.ACCEPTED
            log.round(run="R6+", N=n, k=k, f=f_theoretical, n_colluders=n_coll,
                      outcome_int=res_int.outcome.value, outcome_rep=res_rep.outcome.value)
            byz_rows.append({"N": n, "k": k, "f_theoretical": f_theoretical,
                             "n_colluders": n_coll,
                             "forced_accept_integer": forced_int,
                             "forced_accept_reputation": forced_rep})

    # Sustained-attack reputation trace at N=5 with 2 colluders.
    trace_rows = _reputation_trace(log, n=5, n_colluders=2, rounds=12)
    return [
        Artifact("byz_resilience.csv", "Table 4.7", byz_rows),
        Artifact("reputation_trace.csv", "Table 4.7 / Fig (reputation)", trace_rows),
    ]


def _reputation_trace(log: H.RunLogger, n: int, n_colluders: int, rounds: int):
    sw, colluder_rv = _malicious_swarm(n)
    k = sw.k
    eng = ConsensusEngine(num_peers=k)
    peers = sw.ids[1:]
    colluders = set(peers[:n_colluders])
    rep = ReputationStore()
    rows = []
    for r in range(1, rounds + 1):
        signed = H.alpha_receipt(sw, model_hash=H.MALICIOUS_MODEL)
        votes = []
        for d in peers:
            rv = colluder_rv if d in colluders else sw.receipt_verifier
            votes.append(PeerVerifier(d, sw.signers[d], rv).vote_on(signed))
        res = eng.tally(signed.receipt, votes, sw.vote_verifier,
                        expected_voters=set(sw.ids), reputation=rep)
        rep.update(res.ack_voter_ids, res.dispute_voter_ids, res.outcome)
        for d in peers:
            rows.append({"round": r, "drone_id": d,
                         "is_colluder": d in colluders, "r_i": round(rep.weight(d), 4)})
        log.round(run="R6+trace", round=r, outcome=res.outcome.value,
                  colluder_r=round(H.Stat.of([rep.weight(c) for c in colluders]).mean, 3))
    return rows


# ---------------------------------------------------------------------------
# R7 — isolation sweep (reputation-threshold isolation under sustained attack)
# ---------------------------------------------------------------------------


def run_r7_isolation(log: H.RunLogger, rng: random.Random, repeats: int) -> List[Artifact]:
    """A drone that persistently dissents from correct outcomes is isolated when
    its reputation falls to the cutoff; honest drones are never isolated."""
    W = 10
    r_isolate = 0.3
    rows = []
    n = 5
    sw, colluder_rv = _malicious_swarm(n)
    k = sw.k
    eng = ConsensusEngine(num_peers=k)
    peers = sw.ids[1:]
    attacker = peers[0]  # persistently ACKs malicious receipts (wrong side)
    rep = ReputationStore()
    rounds_to_isolation = None
    false_isolations = 0
    for r in range(1, W + 1):
        signed = H.alpha_receipt(sw, model_hash=H.MALICIOUS_MODEL)
        votes = []
        for d in peers:
            rv = colluder_rv if d == attacker else sw.receipt_verifier
            votes.append(PeerVerifier(d, sw.signers[d], rv).vote_on(signed))
        res = eng.tally(signed.receipt, votes, sw.vote_verifier,
                        expected_voters=set(sw.ids), reputation=rep)
        rep.update(res.ack_voter_ids, res.dispute_voter_ids, res.outcome)
        # Isolation check.
        if rounds_to_isolation is None and rep.weight(attacker) <= r_isolate:
            rounds_to_isolation = r
        for d in peers:
            if d != attacker and rep.weight(d) <= r_isolate:
                false_isolations += 1
        log.round(run="R7", round=r, attacker_r=round(rep.weight(attacker), 3),
                  outcome=res.outcome.value)
    rows.append({"W": W, "alpha": 0.5, "r_isolate": r_isolate,
                 "rounds_to_isolation": rounds_to_isolation if rounds_to_isolation else "not_isolated",
                 "false_isolations": false_isolations})

    event_rows = _run_cti_sf_isolation(log, n=5, W=W)
    return [
        Artifact("isolation.csv", "Table 4.8", rows),
        Artifact("isolation_event.csv", "Section 3.6 (CTI-SF mechanism)", event_rows),
    ]


def _run_cti_sf_isolation(log: H.RunLogger, n: int, W: int) -> List[Dict[str, object]]:
    """CTI-SF (Algorithm 4) end to end: a compromised originator whose receipts
    the swarm keeps rejecting is isolated by the sliding-window rule; the event
    is logged and announced, and the active peer set and quorum shrink to match.
    Honest originators, whose receipts are accepted, are never isolated.

    Real consensus outcomes drive the tracker — every REJECTED/ACCEPTED comes
    from an actual ConsensusEngine.tally over signed votes, nothing is asserted.
    """
    sw = H.build_swarm(n)  # honest verifier approves only the good model
    logged: List = []
    announced: List = []
    tracker = IsolationTracker(
        roster=set(sw.ids), policy=IsolationPolicy(window=W, alpha=0.5),
        on_log=logged.append, on_announce=announced.append,
    )
    bad = sw.ids[0]                 # compromised: runs an unapproved model each round
    honest_ref = sw.ids[1]         # a persistently honest originator (for quorum readout)

    k_before = len(tracker.active_voters(honest_ref))
    ack_before, rej_before = tracker.quorum_for(honest_ref)
    round_isolated = None

    for r in range(1, W + 1):
        # (a) Compromised originator: unapproved model -> honest peers DISPUTE on
        #     provenance -> REJECTED. Recorded against the bad drone's window.
        if not tracker.is_isolated(bad):
            recpt = build_receipt(drone_id=bad, input_bytes=b"\x00" * 128,
                                  model_hash=H.MALICIOUS_MODEL, output=(0.0, 0.0, 0.0))
            signed_bad = sw.signers[bad].sign(recpt)
            voters = sorted(tracker.active_voters(bad))
            votes = [PeerVerifier(d, sw.signers[d], sw.receipt_verifier).vote_on(signed_bad)
                     for d in voters]
            eng = ConsensusEngine(num_peers=len(voters))
            res = eng.tally(recpt, votes, sw.vote_verifier,
                            expected_voters=set(voters) | {bad})
            ev = tracker.record(bad, res.outcome, round_index=r)
            if ev is not None and round_isolated is None:
                round_isolated = r

        # (b) An honest originator: approved model, co-observing peers agree ->
        #     ACCEPTED. Its window never accrues rejections, so it never isolates.
        if not tracker.is_isolated(honest_ref):
            grecpt = build_receipt(drone_id=honest_ref, input_bytes=b"\x00" * 128,
                                   model_hash=H.APPROVED_MODEL, output=(0.1, 0.0, 0.0))
            signed_good = sw.signers[honest_ref].sign(grecpt)
            hvoters = sorted(tracker.active_voters(honest_ref))
            gvotes = [PeerVerifier(d, sw.signers[d], sw.receipt_verifier)
                      .vote_on(signed_good, my_observation=(0.1, 0.0, 0.0)) for d in hvoters]
            geng = ConsensusEngine(num_peers=len(hvoters))
            gres = geng.tally(grecpt, gvotes, sw.vote_verifier,
                              expected_voters=set(hvoters) | {honest_ref})
            tracker.record(honest_ref, gres.outcome, round_index=r)

        log.round(run="R7-CTI", round=r, bad_rho=round(tracker.rejection_rate(bad), 3),
                  active=len(tracker.active_roster), isolated=len(tracker.isolated))

    false_isolations = sum(1 for d in sw.ids[1:] if tracker.is_isolated(d))
    k_after = len(tracker.active_voters(honest_ref))
    ack_after, rej_after = tracker.quorum_for(honest_ref)
    ev0 = tracker.events[0] if tracker.events else None

    return [{
        "W": W, "alpha": 0.5,
        "isolated_drone": bad,
        "round_isolated": round_isolated if round_isolated else "not_isolated",
        "rho_at_isolation": round(ev0.rho, 3) if ev0 else 0.0,
        "n_active_before": n, "n_active_after": len(tracker.active_roster),
        "k_before": k_before, "k_after": k_after,
        "ack_thresh_before": ack_before, "ack_thresh_after": ack_after,
        "rej_thresh_before": rej_before, "rej_thresh_after": rej_after,
        "event_logged": len(logged), "event_announced": len(announced),
        "false_isolations": false_isolations,
    }]


# ---------------------------------------------------------------------------
# R8 — false-positive rate, with vs without the co-visibility gate
# ---------------------------------------------------------------------------


def run_r8_false_positive(log: H.RunLogger, rng: random.Random, repeats: int) -> List[Artifact]:
    """Honest receipts seen by NON-co-visible peers. Without the gate the
    differing viewpoints look like disagreement (false rejects); with the gate
    the peer abstains and ACKs on the crypto evidence."""
    trials = max(repeats, 200)
    rows = []
    sw = H.build_swarm(2)
    alpha_pose = Pose(0.0, 0.0, 10.0)
    for condition, use_gate in [("no_covisibility_gate", False), ("with_covisibility_gate", True)]:
        false_rej = 0
        for _ in range(trials):
            # Honest originator; the peer is far away (disjoint footprint) and so
            # legitimately observes a different scene.
            claimed = (rng.uniform(-1, 1), rng.uniform(-1, 1), rng.uniform(-1, 1))
            signed = H.alpha_receipt(sw, output=claimed)
            far = Pose(rng.uniform(80, 120), rng.uniform(-10, 10), 10.0)
            peer_obs = (rng.uniform(-1, 1), rng.uniform(-1, 1), rng.uniform(-1, 1))
            pv = PeerVerifier("bravo", sw.signers["bravo"], sw.receipt_verifier, o_min=0.1)
            if use_gate:
                vote = pv.vote_on(signed, my_observation=peer_obs,
                                  my_pose=far, originator_pose=alpha_pose)
            else:
                vote = pv.vote_on(signed, my_observation=peer_obs)
            if vote.vote.decision is Vote.DISPUTE:
                false_rej += 1
        fp_rate = false_rej / trials
        log.round(run="R8", condition=condition, honest_receipts=trials,
                  false_rej=false_rej, fp_rate=round(fp_rate, 4))
        rows.append({"condition": condition, "honest_receipts": trials,
                     "false_rej": false_rej, "fp_rate": round(fp_rate, 4)})
    return [Artifact("false_positive.csv", "Table 4.9", rows)]


# ---------------------------------------------------------------------------
# R9 — protocol overhead + local tally latency at N=3/5/7/9/11
# ---------------------------------------------------------------------------


def run_r9_overhead(log: H.RunLogger, rng: random.Random, repeats: int) -> List[Artifact]:
    overhead_rows = []
    latency_rows = []
    import json as _json
    for n in (3, 5, 7, 9, 11):
        sw = H.build_swarm(n)
        k = sw.k
        signed = H.alpha_receipt(sw, output=(0.1, 0.0, 0.0))
        votes = H.honest_votes(sw, signed, observation=(0.1, 0.0, 0.0))
        receipt_bytes = len(signed.serialize())
        vote_bytes = H.Stat.of([len(v.serialize()) for v in votes]).mean
        # Logical signed-payload messages per cycle:
        #   k SubmitReceipt requests + k direct vote replies
        #   + k*(k-1) peer-to-peer vote broadcasts.
        # Transport headers, TLS records and PushVoteAck replies are deliberately
        # reported as excluded rather than silently pretending this is wire size.
        msgs = k + k + k * (k - 1)
        attest_bytes = receipt_bytes * k + vote_bytes * (k + k * (k - 1))
        # Baseline traffic = just broadcasting the raw action vector to k peers.
        raw_output_bytes = len(_json.dumps(list(signed.receipt.output)).encode()) * k
        overhead_pct = (attest_bytes - raw_output_bytes) / raw_output_bytes * 100.0
        overhead_rows.append({
            "N": n,
            "logical_messages_per_cycle": msgs,
            "signed_payload_bytes_per_cycle": int(attest_bytes),
            "excludes": "grpc,tcp,ip,tls,PushVoteAck",
            "overhead_pct_vs_raw": round(overhead_pct, 1),
        })
        rr = H.run_round(sw, signed, votes, repeats=max(repeats, 200))
        latency_rows.append({
            "N": n,
            "local_verify_and_tally_ms_mean": round(rr.decision_ms, 4),
            "decision_ms_std": round(rr.decision_ms_std, 4),
            "scope": "in_process_not_peer_finality",
        })
        log.round(run="R9", N=n, msgs=msgs, bytes=int(attest_bytes),
                  decision_ms=round(rr.decision_ms, 4))
    return [
        Artifact("overhead.csv", "Table 4.10 / Fig 11", overhead_rows),
        Artifact("consensus_latency.csv", "Fig 9", latency_rows),
    ]


# ---------------------------------------------------------------------------
# R10 — message-drop sweep
# ---------------------------------------------------------------------------


def run_r10_drop_sweep(log: H.RunLogger, rng: random.Random, repeats: int) -> List[Artifact]:
    rows = []
    n = 7
    trials = max(repeats, 100)
    sw = H.build_swarm(n)
    signed = H.alpha_receipt(sw, output=(0.0, 0.0, -1.0))
    # Adversarial-patch scenario so a decision (REJECT) is the target.
    all_votes = H.patch_votes(sw, signed, observation=(0.0, 0.0, 1.0))
    for drop_pct in (10, 20, 30, 40, 50):
        detect_ms = []
        decided = 0
        for _ in range(trials):
            kept = [v for v in all_votes if rng.random() >= drop_pct / 100.0]
            rr = H.run_round(sw, signed, kept, repeats=5)
            if rr.outcome is not ConsensusOutcome.NO_QUORUM:
                decided += 1
                detect_ms.append(rr.decision_ms)
        st = H.Stat.of(detect_ms)
        rows.append({"drop_pct": drop_pct, "trials": trials, "decided": decided,
                     "decision_rate": round(decided / trials, 3),
                     "detect_ms_mean": round(st.mean, 4), "detect_ms_std": round(st.std, 4)})
        log.round(run="R10", drop_pct=drop_pct, decision_rate=round(decided / trials, 3),
                  detect_ms=round(st.mean, 4))
    return [Artifact("drop_sweep.csv", "Fig 10", rows)]


# ---------------------------------------------------------------------------
# R-AB — ablation: contribution of each pipeline layer
# ---------------------------------------------------------------------------


def run_rab_ablation(log: H.RunLogger, rng: random.Random, repeats: int) -> List[Artifact]:
    rows = []
    configs = ["crypto_only", "provenance", "semantic", "full"]
    scenarios = ["honest", "model_swap", "adversarial_patch", "colluding_voter"]
    reps = max(repeats, 20)
    for scenario in scenarios:
        for config in configs:
            caught = sum(_ablation_trial(scenario, config) for _ in range(reps))
            rate = caught / reps
            log.round(run="R-AB", scenario=scenario, config=config, detection_rate=rate)
            rows.append({"config": config, "scenario": scenario, "detection_rate": round(rate, 3)})
    return [Artifact("ablation.csv", "Table 4.12", rows)]


def _ablation_trial(scenario: str, config: str) -> bool:
    """Run one scenario under a pipeline cut at `config`; True if the receipt is
    caught (outcome REJECTED). For the honest scenario nothing should be caught,
    so every configuration is expected to return False -- that column is the
    ablation's own false-positive check. Deterministic, but repeated for
    stability."""
    n = 7
    sw = H.build_swarm(n)
    k = sw.k
    eng = ConsensusEngine(num_peers=k)
    peers = sw.ids[1:]

    crypto_only = config == "crypto_only"
    provenance_on = config in ("provenance", "semantic", "full")
    semantic_on = config in ("semantic", "full")
    reputation_on = config == "full"

    # A verifier that approves all models switches the provenance layer OFF.
    permissive_rv = ReceiptVerifier(peer_keys=sw.peer_keys,
                                    approved_models={H.APPROVED_MODEL, H.MALICIOUS_MODEL})
    honest_rv = sw.receipt_verifier if provenance_on else permissive_rv

    if scenario == "honest":
        # Nothing is wrong: approved model, and every peer sees essentially the
        # action the originator claims. The small offset keeps the semantic
        # check a real comparison rather than a degenerate identity test, while
        # staying far inside theta.
        signed = H.alpha_receipt(sw)
        obs = (0.12, 0.0, 0.0) if semantic_on else None
        votes = [PeerVerifier(d, sw.signers[d], honest_rv).vote_on(signed, my_observation=obs)
                 for d in peers]
        rep = None
    elif scenario == "model_swap":
        signed = H.alpha_receipt(sw, model_hash=H.MALICIOUS_MODEL)
        votes = [PeerVerifier(d, sw.signers[d], honest_rv).vote_on(signed) for d in peers]
        rep = None
    elif scenario == "adversarial_patch":
        signed = H.alpha_receipt(sw, output=(0.0, 0.0, -1.0))
        obs = (0.0, 0.0, 1.0) if semantic_on else None
        votes = [PeerVerifier(d, sw.signers[d], honest_rv).vote_on(signed, my_observation=obs)
                 for d in peers]
        rep = None
    else:  # colluding_voter: patched originator defended by 4 colluders
        signed = H.alpha_receipt(sw, output=(0.0, 0.0, -1.0))
        colluders = set(peers[:4])
        obs_honest = (0.0, 0.0, 1.0) if semantic_on else None
        votes = []
        for d in peers:
            if d in colluders:
                votes.append(PeerVerifier(d, sw.signers[d], honest_rv).vote_on(signed))
            else:
                votes.append(PeerVerifier(d, sw.signers[d], honest_rv).vote_on(
                    signed, my_observation=obs_honest))
        rep = ReputationStore()
        if reputation_on:
            for c in colluders:
                rep._weights[c] = rep.r_min
        else:
            rep = None

    res = eng.tally(signed.receipt, votes, sw.vote_verifier,
                    expected_voters=set(sw.ids), reputation=rep)
    return res.outcome is ConsensusOutcome.REJECTED


# ---------------------------------------------------------------------------
# R-ROC — semantic threshold (theta) ROC sweep
# ---------------------------------------------------------------------------


def run_rroc_theta(log: H.RunLogger, rng: random.Random, repeats: int) -> List[Artifact]:
    population = max(repeats, 2000)
    sigma_honest = 0.08
    # Attack strengths are drawn from a range that includes stealthy patches
    # (low L2, overlapping the honest tail) up to overt ones, so the ROC shows a
    # genuine detection / false-alarm trade-off rather than a separable step.
    attack_l2_lo, attack_l2_hi = 0.2, 1.4
    honest_d = [H.l2(*H.honest_pair(rng, sigma_honest)) for _ in range(population)]
    attack_d = [H.l2(*H.attack_pair(rng, rng.uniform(attack_l2_lo, attack_l2_hi)))
                for _ in range(population)]
    rows = []
    theta = 0.0
    while theta <= 2.0 + 1e-9:
        tpr = sum(1 for d in attack_d if d >= theta) / population
        fpr = sum(1 for d in honest_d if d >= theta) / population
        rows.append({"theta": round(theta, 3), "tpr": round(tpr, 4), "fpr": round(fpr, 4),
                     "sigma_honest": sigma_honest,
                     "attack_l2_lo": attack_l2_lo, "attack_l2_hi": attack_l2_hi})
        theta += 0.05
    log.round(run="R-ROC", population=population, sigma_honest=sigma_honest,
              attack_l2_range=f"[{attack_l2_lo},{attack_l2_hi}]", note="ROC swept theta in [0,2]")
    return [Artifact("theta_roc.csv", "Fig 13", rows)]


# ---------------------------------------------------------------------------
# R-ST — stealth attack sweep (detection vs adversarial magnitude)
# ---------------------------------------------------------------------------


def run_rst_stealth(log: H.RunLogger, rng: random.Random, repeats: int) -> List[Artifact]:
    trials = max(repeats, 500)
    theta_ref = 0.5
    rows = []
    mag = 1.5
    while mag >= 0.1 - 1e-9:
        detected = 0
        for _ in range(trials):
            claimed, observed = H.attack_pair(rng, mag)
            if not outputs_agree(claimed, observed, threshold=theta_ref):
                detected += 1
        rate = detected / trials
        rows.append({"l2_magnitude": round(mag, 3), "theta_ref": theta_ref,
                     "detection_rate": round(rate, 4)})
        log.round(run="R-ST", l2_magnitude=round(mag, 3), detection_rate=round(rate, 4))
        mag -= 0.1
    return [Artifact("stealth_sweep.csv", "Fig 14", rows)]


# ---------------------------------------------------------------------------
# R-BL — baseline head-to-head (unattested majority vote vs VeriSwarm)
# ---------------------------------------------------------------------------


def run_rbl_baseline(log: H.RunLogger, rng: random.Random, repeats: int) -> List[Artifact]:
    rows = []
    n = 5
    base = MajorityBaseline()

    # 1) Model swap: baseline has no provenance check.
    sw = H.build_swarm(n)
    signed = H.alpha_receipt(sw, model_hash=H.MALICIOUS_MODEL)
    base_votes = [base.naive_vote(d, signed.receipt.output, (0.1, 0.0, 0.0)) for d in sw.ids[1:]]
    base_outcome = base.decide(base_votes)
    vs_votes = H.provenance_votes(sw, signed)
    vs_outcome = H.run_round(sw, signed, vs_votes).outcome
    rows.append({"metric": "model_swap_detection",
                 "majority_vote": 1.0 if base_outcome == "REJECTED" else 0.0,
                 "veriswarm": 1.0 if vs_outcome is ConsensusOutcome.REJECTED else 0.0})

    # 2) Adversarial patch: both have a semantic check -> expect parity.
    sw = H.build_swarm(n)
    signed = H.alpha_receipt(sw, output=(0.0, 0.0, -1.0))
    base_votes = [base.naive_vote(d, signed.receipt.output, (0.0, 0.0, 1.0)) for d in sw.ids[1:]]
    base_outcome = base.decide(base_votes)
    vs_outcome = H.run_round(sw, signed, H.patch_votes(sw, signed, observation=(0.0, 0.0, 1.0))).outcome
    rows.append({"metric": "adversarial_patch_detection",
                 "majority_vote": 1.0 if base_outcome == "REJECTED" else 0.0,
                 "veriswarm": 1.0 if vs_outcome is ConsensusOutcome.REJECTED else 0.0})

    # 3) Vote forgery: attacker injects unauthenticated DISPUTE votes on an honest
    #    receipt. Baseline counts them; VeriSwarm drops them (signature check).
    sw = H.build_swarm(n)
    signed = H.alpha_receipt(sw, output=(0.1, 0.0, 0.0))
    honest_base = [base.naive_vote(d, signed.receipt.output, (0.1, 0.0, 0.0)) for d in sw.ids[1:]]
    # Unauthenticated votes admit a Sybil flood: the attacker fabricates more
    # ghost identities than there are honest peers, flipping the plain majority.
    n_ghosts = 2 * len(honest_base) + 1
    forged_base = honest_base + [PlainVote(f"ghost{i}", "DISPUTE") for i in range(n_ghosts)]
    base_outcome = base.decide(forged_base)  # flipped to REJECTED by the ghosts
    # VeriSwarm: forged votes from unknown keys are dropped by the vote verifier.
    real_votes = H.honest_votes(sw, signed, observation=(0.1, 0.0, 0.0))
    rogue = ReceiptSigner()
    forged_pv = PeerVerifier("ghost1", rogue, sw.receipt_verifier)
    forged_vote = forged_pv.vote_on(signed, my_observation=(0.1, 0.0, 0.0))
    vs_outcome = H.run_round(sw, signed, real_votes + [forged_vote]).outcome
    rows.append({"metric": "vote_forgery_resistance",
                 "majority_vote": 1.0 if base_outcome == "ACCEPTED" else 0.0,
                 "veriswarm": 1.0 if vs_outcome is ConsensusOutcome.ACCEPTED else 0.0})

    # 4) Colluding voters defending a patched originator.
    sw = H.build_swarm(7)
    signed = H.alpha_receipt(sw, output=(0.0, 0.0, -1.0))
    peers = sw.ids[1:]
    colluders = set(peers[:4])
    base_votes = []
    for d in peers:
        obs = (0.0, 0.0, -1.0) if d in colluders else (0.0, 0.0, 1.0)  # colluders fake agreement
        base_votes.append(base.naive_vote(d, signed.receipt.output, obs))
    base_outcome = base.decide(base_votes)
    vs_votes = []
    for d in peers:
        if d in colluders:
            vs_votes.append(PeerVerifier(d, sw.signers[d], sw.receipt_verifier).vote_on(signed))
        else:
            vs_votes.append(PeerVerifier(d, sw.signers[d], sw.receipt_verifier).vote_on(
                signed, my_observation=(0.0, 0.0, 1.0)))
    rep = ReputationStore()
    for c in colluders:
        rep._weights[c] = rep.r_min
    vs_outcome = H.run_round(sw, signed, vs_votes, reputation=rep).outcome
    rows.append({"metric": "colluding_voter_resistance",
                 "majority_vote": 1.0 if base_outcome == "REJECTED" else 0.0,
                 "veriswarm": 1.0 if vs_outcome is ConsensusOutcome.REJECTED else 0.0})

    for r in rows:
        log.round(run="R-BL", metric=r["metric"], majority_vote=r["majority_vote"],
                  veriswarm=r["veriswarm"])
    return [Artifact("baseline.csv", "Table 4.14", rows)]


# ---------------------------------------------------------------------------
# R-EN — energy (Jetson, hardware-only)
# ---------------------------------------------------------------------------


def _tegrastats_bin() -> Optional[str]:
    return shutil.which("tegrastats") or (
        "/usr/bin/tegrastats" if os.path.exists("/usr/bin/tegrastats") else None)


def _sample_power_w(teg: str, seconds: float, interval_ms: int = 200) -> H.Stat:
    """Mean board input power (VDD_IN) over a window, in watts. Needs root."""
    pat = re.compile(r"VDD_IN (\d+)mW")
    proc = subprocess.Popen([teg, "--interval", str(interval_ms)],
                            stdout=subprocess.PIPE, text=True)
    samples: List[float] = []
    t_end = time.time() + seconds
    try:
        while time.time() < t_end:
            line = proc.stdout.readline()
            if not line:
                break
            m = pat.search(line)
            if m:
                samples.append(int(m.group(1)) / 1000.0)  # mW -> W
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except Exception:
            proc.kill()
    return H.Stat.of(samples)


def _bench_cycle_ms(ca: str, iters: int) -> float:
    proc = subprocess.run([ca, "bench", str(iters)], capture_output=True,
                          text=True, timeout=600)
    samples = [float(x) for x in proc.stdout.split() if x.strip()]
    return (H.Stat.of(samples).mean / 1000.0) if samples else 0.0  # us -> ms


def run_ren_energy(log: H.RunLogger, rng: random.Random, repeats: int) -> List[Artifact]:
    """R-EN: power + per-cycle energy on the Orin Nano via tegrastats (VDD_IN),
    for the OP-TEE and software signers against an idle baseline. Per-cycle
    energy = mean_power_w * cycle_ms (W*ms = mJ). Hardware-only."""
    pending = [{"backend": b, "idle_power_w": "PENDING", "mean_power_w": "PENDING",
                "delta_power_w": "PENDING", "cycle_ms": "PENDING", "energy_mj": "PENDING"}
               for b in ("software", "op-tee")]
    if not H.optee_available():
        log.round(run="R-EN", status="SKIPPED", reason="needs Jetson/OP-TEE")
        return [Artifact("energy.csv", "Table 4.13", pending, status="pending")]
    teg = _tegrastats_bin()
    if not teg:
        log.round(run="R-EN", status="SKIPPED",
                  reason="tegrastats not found (no on-board power telemetry)")
        return [Artifact("energy.csv", "Table 4.13", pending, status="pending")]

    idle = _sample_power_w(teg, 5.0)
    log.round(run="R-EN", phase="idle", power_w=round(idle.mean, 3), n=idle.n)
    if idle.n == 0:
        log.round(run="R-EN", status="FAILED", reason="no VDD_IN samples from tegrastats")
        return [Artifact("energy.csv", "Table 4.13", pending, status="pending")]

    rows: List[Dict[str, object]] = []

    # OP-TEE under a sustained, silent signing load.
    ca = os.environ.get("VERISWARM_OPTEE_CA") or shutil.which("veriswarm_optee_ca")
    if ca:
        cyc = _bench_cycle_ms(ca, max(repeats, 2000))
        load_proc = subprocess.Popen([ca, "loadsign", "100000000"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1.0)  # let power ramp up
        load = _sample_power_w(teg, 8.0)
        load_proc.terminate()
        try:
            load_proc.wait(timeout=3)
        except Exception:
            load_proc.kill()
        rows.append({"backend": "op-tee", "idle_power_w": round(idle.mean, 3),
                     "mean_power_w": round(load.mean, 3),
                     "delta_power_w": round(load.mean - idle.mean, 3),
                     "cycle_ms": round(cyc, 4), "energy_mj": round(load.mean * cyc, 4)})
        log.round(run="R-EN", backend="op-tee", power_w=round(load.mean, 3),
                  cycle_ms=round(cyc, 4), energy_mj=round(load.mean * cyc, 4))

    # Software (PyNaCl) under a sustained signing load on the same board.
    import threading
    stop = threading.Event()
    counter = {"n": 0, "ns": 1}

    def sw_load():
        signer = ReceiptSigner()
        msg = b"\xab" * 256
        n = 0
        t0 = time.perf_counter_ns()
        while not stop.is_set():
            signer._key.sign(msg)
            n += 1
        counter["n"] = n
        counter["ns"] = max(1, time.perf_counter_ns() - t0)

    th = threading.Thread(target=sw_load)
    th.start()
    time.sleep(1.0)
    sw = _sample_power_w(teg, 8.0)
    stop.set()
    th.join()
    sw_cyc = (counter["ns"] / counter["n"] / 1e6) if counter["n"] else 0.0
    rows.append({"backend": "software", "idle_power_w": round(idle.mean, 3),
                 "mean_power_w": round(sw.mean, 3),
                 "delta_power_w": round(sw.mean - idle.mean, 3),
                 "cycle_ms": round(sw_cyc, 6), "energy_mj": round(sw.mean * sw_cyc, 6)})
    log.round(run="R-EN", backend="software", power_w=round(sw.mean, 3), cycle_ms=round(sw_cyc, 6))

    return [Artifact("energy.csv", "Table 4.13", rows)]


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

RUNS: Dict[str, Callable] = {
    "R1": run_r1_sign_latency,
    "R2": run_r2_optee_latency,
    "R3": run_r3_receipt_size,
    "R4": run_r4_verify_latency,
    "R5": run_r5_base_scenarios,
    "R6": run_r6_colluding,
    "R7": run_r7_isolation,
    "R8": run_r8_false_positive,
    "R9": run_r9_overhead,
    "R10": run_r10_drop_sweep,
    "R-AB": run_rab_ablation,
    "R-ROC": run_rroc_theta,
    "R-ST": run_rst_stealth,
    "R-BL": run_rbl_baseline,
    "R-EN": run_ren_energy,
}

HARDWARE_RUNS = {"R2", "R-EN"}


def _merged_manifest(
    new_rows: List[Dict[str, object]], selected: List[str], partial_run: bool
) -> List[Dict[str, object]]:
    """Preserve unrelated evidence when ``--only`` refreshes selected runs."""
    if not partial_run:
        return new_rows
    path = H.RESULTS_DIR / "manifest.csv"
    if not path.exists() or path.stat().st_size == 0:
        return new_rows
    with path.open(newline="") as fh:
        old_rows = list(csv.DictReader(fh))
    selected_set = set(selected)
    preserved = [row for row in old_rows if row.get("run") not in selected_set]
    return preserved + new_rows


def main() -> None:
    parser = argparse.ArgumentParser(description="VeriSwarm evaluation runner")
    parser.add_argument("--only", type=str, default="",
                        help="comma-separated run ids (e.g. R1,R5,R-AB); default all")
    parser.add_argument("--hardware", action="store_true",
                        help="also attempt hardware runs (R2, R-EN) on the Jetson")
    parser.add_argument("--repeats", type=int, default=0,
                        help="override timing/trial sample count (0 = per-run default)")
    parser.add_argument("--seed", type=int, default=H.DEFAULT_SEED)
    args = parser.parse_args()

    only_list = [r.strip() for r in args.only.split(",") if r.strip()]
    selected = only_list or list(RUNS)
    if not args.hardware:
        # Drop hardware-only runs unless the user named them explicitly.
        selected = [r for r in selected if r not in HARDWARE_RUNS or r in only_list]

    rng = random.Random(args.seed)
    log = H.RunLogger(tag="all")
    log.line(f"# seed={args.seed} selected={','.join(selected)}")
    print(f"VeriSwarm eval — seed={args.seed}, runs={','.join(selected)}")
    print(f"OP-TEE backend available: {H.optee_available()}")

    manifest: List[Dict[str, object]] = []
    for rid in selected:
        fn = RUNS.get(rid)
        if fn is None:
            print(f"  ! unknown run id {rid}, skipping")
            continue
        t0 = time.perf_counter()
        artifacts = fn(log, rng, args.repeats)
        dt = time.perf_counter() - t0
        for art in artifacts:
            path = H.write_csv(art.filename, art.rows)
            manifest.append({"run": rid, "feeds": art.feeds, "csv": art.filename,
                             "status": art.status, "n_rows": len(art.rows)})
            print(f"  {rid:5s} -> {art.filename:24s} [{art.status:7s}] "
                  f"{len(art.rows)} rows -> {art.feeds}  ({dt:.2f}s)")

    refreshed_count = len(manifest)
    manifest = _merged_manifest(manifest, selected, partial_run=bool(only_list))
    H.write_csv("manifest.csv", manifest)
    log.line("# done")
    log.close()
    pending = [m for m in manifest if m["status"] == "pending"]
    print(
        f"\nRefreshed {refreshed_count} result CSVs; manifest contains "
        f"{len(manifest)} artifacts. Log: {log.path.name}"
    )
    if pending:
        print(f"PENDING (need Jetson/OP-TEE): {', '.join(m['csv'] for m in pending)}")


if __name__ == "__main__":
    main()
