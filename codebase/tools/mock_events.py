"""
Replay a scripted attack as the live event stream, with no swarm running.

    python -m tools.mock_events --scenario patch --rate 2

This exists so the console can be built and finished before the backend it will
eventually attach to is ready. M2 develops entirely against this file; the day
the real swarm emits its first event, nothing in the console changes, because
both sides were written against `docs/EVENT_SCHEMA.md` rather than against each
other.

It is also the demo's insurance policy. If the swarm will not come up on the day,
the console still runs against a recorded scenario and the story still gets told.

Scenarios
---------
honest        every peer co-observes and agrees; ACCEPTED throughout
patch         an adversarial patch fools alpha; peers dispute; isolation
model_swap    alpha runs unapproved weights; provenance catches it at every peer
collusion     two compromised peers defend a bad receipt; the integer quorum holds
unverified    nobody is co-visible; ACCEPTED with semantic_acks = 0
provisioning  a compromised ground station flashed bravo AND charlie

Every scenario emits the same event types in the same shapes as the real nodes.
The numbers are drawn from the measured results in `results/` so the console is
laid out for values it will actually see -- consensus latencies in the 6-19 ms
band, OP-TEE signing at 4.68 ms, reputation decaying at beta = 0.2.
"""

from __future__ import annotations

import argparse
import math
import random
import time
from typing import Dict, List

from node import events as ev
from node.events import EventLog

SWARM = ["alpha", "bravo", "charlie", "delta", "echo"]

#: Ring formation: R = 5.5 m at 14 m altitude. Both bounds bind -- closer and the
#: peers stop being independent observers, wider and their footprints stop
#: overlapping. See tests/test_angular_diversity.py.
RING_RADIUS_M = 5.5
RING_ALT_M = 14.0

#: Measured on the Jetson's secure element (Table 4.2).
OPTEE_SIGN_MS = 4.68
SOFTWARE_SIGN_MS = 0.19

APPROVED_HASH = "ab12cd34" + "0" * 56
TAMPERED_HASH = "ff99ee88" + "0" * 56

CLEAR_PATH = [1.0, 0.0, 0.0]      # no detections -> full forward. The crash.
AVOIDING = [0.0, 0.0, 1.0]        # obstacle seen -> climb over it


def ring_pose(index: int, n: int = len(SWARM)):
    angle = 2.0 * math.pi * index / n
    return [round(RING_RADIUS_M * math.cos(angle), 2),
            round(RING_RADIUS_M * math.sin(angle), 2),
            RING_ALT_M]


class Diag:
    """Duck-typed stand-in for protocol.peer_consensus.CoVisDiagnostic."""

    def __init__(self, covisible, method, iou, parallax_deg,
                 orb_inliers=None, o_min=0.1, phi_min=23.0, detail=""):
        self.covisible = covisible
        self.method = method
        self.iou = iou
        self.parallax_deg = parallax_deg
        self.orb_inliers = orb_inliers
        self.o_min = o_min
        self.phi_min = phi_min
        self._detail = detail

    def describe(self):
        return self._detail


class Check:
    """Duck-typed stand-in for perception.depth_check.FreeSpaceCheck."""

    def __init__(self, contradicted, measured_range_m, required_clear_m,
                 commanded_forward, reason, detail):
        self.contradicted = contradicted
        self.measured_range_m = measured_range_m
        self.required_clear_m = required_clear_m
        self.commanded_forward = commanded_forward
        self.reason = reason
        self._detail = detail

    def describe(self):
        return self._detail


def covisible_diag(iou=0.19, phi=37.1):
    return Diag(True, "geometric", iou, phi,
                detail=f"co-visible (geometric): o={iou:.3f} >= 0.1, phi={phi:.1f} deg")


def _consensus_ms(rng):
    """Sampled from the measured 6.2-19.3 ms band (Table 4.9)."""
    return round(rng.uniform(6.2, 19.3), 2)


# ---------------------------------------------------------------------------
# Scenario bodies. Each yields nothing; it emits and sleeps between rounds.
# ---------------------------------------------------------------------------


def _emit_poses(log: EventLog) -> None:
    for i, node in enumerate(SWARM):
        log.pose(node, ring_pose(i))


def _peer_verdicts(log, target, outcome, acks, disputes, semantic_acks, rng):
    """Every node tallies independently -- one verdict per node, not per round."""
    for node in SWARM:
        if node == target:
            continue
        log.verdict(node, target, outcome, acks, disputes, semantic_acks,
                    consensus_ms=_consensus_ms(rng))


def scenario_honest(log: EventLog, rounds: int, rate: float, rng) -> None:
    reputations = {n: 1.0 for n in SWARM}
    for r in range(1, rounds + 1):
        log.round_start(r)
        _emit_poses(log)
        log.receipt("alpha", AVOIDING, APPROVED_HASH, f"{r:064x}",
                    "optee", OPTEE_SIGN_MS, round_index=r)
        log.depth("alpha", Check(False, 34.2, 1.5, 0.0, "consistent",
                                 "consistent: forward=0.00 needs 1.5 m, measured 34.2 m"))
        for peer in SWARM[1:]:
            log.covisibility(peer, "alpha", covisible_diag())
            log.vote(peer, "alpha", "ACK", "ok", delta=round(rng.uniform(0.01, 0.09), 3))
        _peer_verdicts(log, "alpha", "ACCEPTED", 4, 0, 4, rng)
        log.safe_action("alpha", "EXECUTE", "ACCEPTED", 4)
        for node, value in reputations.items():
            log.reputation(node, value)
        log.round_end(r)
        time.sleep(1.0 / rate)


def scenario_patch(log: EventLog, rounds: int, rate: float, rng) -> None:
    """
    The money shot. A patch hides the obstacle from alpha only; its peers, at
    37 degrees of parallax, still see it. Alpha claims a clear path, disputes
    accumulate, and it is isolated once rho passes 0.5.
    """
    reputations = {n: 1.0 for n in SWARM}
    rejected = 0
    for r in range(1, rounds + 1):
        log.round_start(r)
        _emit_poses(log)
        attacking = r >= 3
        if r == 3:
            log.attack("patch", True, ["alpha"])
            log.log("adversarial patch raised into the scene", level="warn")

        action = CLEAR_PATH if attacking else AVOIDING
        log.receipt("alpha", action, APPROVED_HASH, f"{r:064x}",
                    "optee", OPTEE_SIGN_MS, round_index=r)

        # Alpha's own rangefinder contradicts its detector -- no peers needed.
        if attacking:
            log.depth("alpha", Check(
                True, 7.9, 11.5, 1.0, "contradiction",
                "CONTRADICTION: commanded forward=1.00 needs 11.5 m clear, "
                "but the nearest surface is at 7.9 m"))
        else:
            log.depth("alpha", Check(False, 34.2, 1.5, 0.0, "consistent",
                                     "consistent: measured 34.2 m"))

        for peer in SWARM[1:]:
            log.covisibility(peer, "alpha", covisible_diag())
            if attacking:
                log.vote(peer, "alpha", "DISPUTE", "semantic_disagreement",
                         delta=round(rng.uniform(1.35, 1.45), 3))
            else:
                log.vote(peer, "alpha", "ACK", "ok",
                         delta=round(rng.uniform(0.01, 0.09), 3))

        if attacking:
            _peer_verdicts(log, "alpha", "REJECTED", 0, 4, 4, rng)
            log.safe_action("alpha", "SAFE_FALLBACK", "REJECTED", 4)
            rejected += 1
        else:
            _peer_verdicts(log, "alpha", "ACCEPTED", 4, 0, 4, rng)
            log.safe_action("alpha", "EXECUTE", "ACCEPTED", 4)

        for node in SWARM:
            if node == "alpha" and attacking:
                reputations[node] = max(0.1, reputations[node] - 0.2)
            log.reputation(node, reputations[node])

        rho = rejected / 10.0
        if rho > 0.5:
            log.isolation("alpha", r, rho, n_active_after=4)
            log.log("alpha isolated; swarm continues to waypoint B", level="warn")
            log.round_end(r)
            break
        log.round_end(r)
        time.sleep(1.0 / rate)


def scenario_model_swap(log: EventLog, rounds: int, rate: float, rng) -> None:
    for r in range(1, rounds + 1):
        log.round_start(r)
        _emit_poses(log)
        swapped = r >= 2
        if r == 2:
            log.attack("ota", True, ["alpha"])
            log.log("ground station served unapproved weights; alpha reloaded",
                    level="warn")
        log.receipt("alpha", AVOIDING,
                    TAMPERED_HASH if swapped else APPROVED_HASH,
                    f"{r:064x}", "optee", OPTEE_SIGN_MS, round_index=r)
        for peer in SWARM[1:]:
            log.covisibility(peer, "alpha", covisible_diag())
            if swapped:
                # Provenance fires at every peer regardless of co-visibility --
                # it needs no shared view, only the allowlist.
                log.vote(peer, "alpha", "DISPUTE",
                         f"model_hash_not_approved:{TAMPERED_HASH[:16]}")
            else:
                log.vote(peer, "alpha", "ACK", "ok", delta=0.04)
        if swapped:
            _peer_verdicts(log, "alpha", "REJECTED", 0, 4, 0, rng)
            log.safe_action("alpha", "SAFE_FALLBACK", "REJECTED", 0)
        else:
            _peer_verdicts(log, "alpha", "ACCEPTED", 4, 0, 4, rng)
            log.safe_action("alpha", "EXECUTE", "ACCEPTED", 4)
        log.round_end(r)
        time.sleep(1.0 / rate)


def scenario_collusion(log: EventLog, rounds: int, rate: float, rng) -> None:
    """
    Two colluders ACK a patched receipt. At N=5 the integer quorum is untouched:
    T_acc = 3 is out of reach for two ACKs, and two honest DISPUTEs clear
    T_rej = 2. Reputation drives the colluders to the floor in about four rounds.
    """
    colluders = {"delta", "echo"}
    reputations = {n: 1.0 for n in SWARM}
    for r in range(1, rounds + 1):
        log.round_start(r)
        _emit_poses(log)
        if r == 1:
            log.attack("collude", True, sorted(colluders))
        log.receipt("alpha", CLEAR_PATH, APPROVED_HASH, f"{r:064x}",
                    "optee", OPTEE_SIGN_MS, round_index=r)
        log.depth("alpha", Check(True, 7.9, 11.5, 1.0, "contradiction",
                                 "CONTRADICTION: nearest surface at 7.9 m"))
        for peer in SWARM[1:]:
            log.covisibility(peer, "alpha", covisible_diag())
            if peer in colluders:
                log.vote(peer, "alpha", "ACK", "ok", delta=0.02)
            else:
                log.vote(peer, "alpha", "DISPUTE", "semantic_disagreement",
                         delta=round(rng.uniform(1.35, 1.45), 3))
        _peer_verdicts(log, "alpha", "REJECTED", 2, 2, 4, rng)
        log.safe_action("alpha", "SAFE_FALLBACK", "REJECTED", 4)
        for node in SWARM:
            if node in colluders:
                reputations[node] = max(0.1, reputations[node] - 0.2)
            log.reputation(node, reputations[node])
        log.round_end(r)
        time.sleep(1.0 / rate)


def scenario_unverified(log: EventLog, rounds: int, rate: float, rng) -> None:
    """
    The case the console is most likely to render wrongly.

    Alpha is patched and claims a clear path, but no peer shares its view, so
    every peer abstains on the semantic clause and ACKs on cryptographic evidence
    alone. The receipt reaches ACCEPTED with semantic_acks = 0: sound, and
    entirely unchecked. If this looks the same on screen as a verified accept,
    the console is lying to the judge.
    """
    for r in range(1, rounds + 1):
        log.round_start(r)
        for i, node in enumerate(SWARM):
            log.pose(node, [i * 90.0, 0.0, RING_ALT_M])  # strung out, no overlap
        log.receipt("alpha", CLEAR_PATH, APPROVED_HASH, f"{r:064x}",
                    "optee", OPTEE_SIGN_MS, round_index=r)
        log.depth("alpha", Check(True, 8.1, 11.5, 1.0, "contradiction",
                                 "CONTRADICTION: nearest surface at 8.1 m"))
        for peer in SWARM[1:]:
            log.covisibility(peer, "alpha", Diag(
                False, "none", 0.0, 0.0,
                detail="NOT co-visible: o=0.000 < 0.1, no frames for the image fallback"))
            log.vote(peer, "alpha", "ACK", "ok_no_covisibility")
        _peer_verdicts(log, "alpha", "ACCEPTED", 4, 0, 0, rng)
        log.safe_action("alpha", "EXECUTE_DEGRADED", "ACCEPTED", 0)
        log.log("ACCEPTED with no semantic verification -- flying degraded",
                level="warn")
        log.round_end(r)
        time.sleep(1.0 / rate)


def scenario_provisioning(log: EventLog, rounds: int, rate: float, rng) -> None:
    """
    The supply-chain attack: the workstation that flashes SD cards is owned, so
    every aircraft it provisioned carries trojaned weights. Bravo and charlie
    both come up bad -- and provenance catches both, because the allowlist came
    from a mission authority the provisioner never had write access to.
    """
    compromised = ["bravo", "charlie"]
    for r in range(1, rounds + 1):
        log.round_start(r)
        _emit_poses(log)
        if r == 1:
            log.attack("provisioning", True, compromised)
            log.log("ground-crew workstation compromised; bravo and charlie "
                    "provisioned with trojaned weights", level="error")
        for bad in compromised:
            log.receipt(bad, AVOIDING, TAMPERED_HASH, f"{r:064x}",
                        "software", SOFTWARE_SIGN_MS, round_index=r)
            for peer in SWARM:
                if peer == bad:
                    continue
                log.vote(peer, bad, "DISPUTE",
                         f"model_hash_not_approved:{TAMPERED_HASH[:16]}")
            _peer_verdicts(log, bad, "REJECTED", 0, 4, 0, rng)
            log.safe_action(bad, "SAFE_FALLBACK", "REJECTED", 0)
        log.round_end(r)
        time.sleep(1.0 / rate)


SCENARIOS = {
    "honest": scenario_honest,
    "patch": scenario_patch,
    "model_swap": scenario_model_swap,
    "collusion": scenario_collusion,
    "unverified": scenario_unverified,
    "provisioning": scenario_provisioning,
}


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Replay a scripted attack as the live event stream."
    )
    ap.add_argument("--scenario", default="patch", choices=sorted(SCENARIOS))
    ap.add_argument("--rate", type=float, default=2.0, help="rounds per second")
    ap.add_argument("--rounds", type=int, default=12)
    ap.add_argument("--out", default=str(ev.DEFAULT_EVENT_LOG))
    ap.add_argument("--append", action="store_true",
                    help="keep any existing log instead of truncating")
    ap.add_argument("--loop", action="store_true", help="repeat forever")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    log = EventLog(args.out, truncate=not args.append)
    print(f"writing {args.scenario} to {args.out} at {args.rate} rounds/s")
    print("consume it with:  python -c \"from node.events import follow; "
          "[print(e) for e in follow()]\"")

    try:
        while True:
            SCENARIOS[args.scenario](log, args.rounds, args.rate, rng)
            if not args.loop:
                break
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
