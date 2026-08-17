"""Deterministic closed-loop security simulation with executable invariants.

This runner needs no PX4, GPU, or network. It exercises the real receipt,
signature, mission/sequence checks, co-visibility, semantic votes, consensus,
equivocation handling, range gate, and final command supervisor. It is the fast
pre-flight/SITL oracle: PX4/Gazebo runs must produce the same decisions.

Run from ``codebase``::

    python3 -m sim.closed_loop
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass

from perception.depth_check import check_free_space
from perception.safety_supervisor import SafetySupervisor
from protocol.geometry import Pose
from protocol.peer_consensus import (
    ConsensusEngine,
    ConsensusOutcome,
    PeerVerifier,
    Vote,
    VoteVerifier,
)
from protocol.receipts import ReceiptSigner, ReceiptVerifier, build_receipt, sha256_hex


IDS = ("alpha", "bravo", "charlie", "delta", "echo")
APPROVED_MODEL = sha256_hex(b"simulation-model-v2")
MALICIOUS_MODEL = sha256_hex(b"simulation-model-tampered")
APPROVED_RUNTIME = sha256_hex(b"simulation-runtime-v2")
MISSION_ID = "deterministic-sitl"
MISSION_EPOCH = 1


@dataclass(frozen=True)
class ScenarioResult:
    scenario: str
    outcome: str
    acks: int
    disputes: int
    semantic_acks: int
    equivocators: tuple[str, ...]
    command_authorized: bool
    released_action: tuple[float, float, float]
    supervisor_reason: str


class ClosedLoopSimulation:
    def __init__(self):
        self.signers = {node: ReceiptSigner() for node in IDS}
        self.keys = {node: signer.public_key_hex for node, signer in self.signers.items()}
        self.vote_verifier = VoteVerifier(self.keys)
        self.engine = ConsensusEngine(num_peers=len(IDS) - 1)
        self.supervisor = SafetySupervisor(min_semantic_acks=2)
        self.sequence = 0
        radius = 5.5
        self.poses = {
            node: Pose(
                radius * math.cos(2.0 * math.pi * index / len(IDS)),
                radius * math.sin(2.0 * math.pi * index / len(IDS)),
                14.0,
            )
            for index, node in enumerate(IDS)
        }
        self.peers = {}
        for node in IDS[1:]:
            verifier = ReceiptVerifier(
                self.keys,
                {APPROVED_MODEL},
                expected_mission_id=MISSION_ID,
                expected_mission_epoch=MISSION_EPOCH,
                approved_runtimes={APPROVED_RUNTIME},
                enforce_sequence=True,
            )
            self.peers[node] = PeerVerifier(
                node,
                self.signers[node],
                verifier,
                o_min=0.1,
                phi_min=23.0,
            )

    def _receipt(self, action, model_hash=APPROVED_MODEL):
        self.sequence += 1
        pose = self.poses["alpha"]
        receipt = build_receipt(
            "alpha",
            f"sim-frame-{self.sequence}".encode(),
            model_hash,
            action,
            mission_id=MISSION_ID,
            mission_epoch=MISSION_EPOCH,
            sequence=self.sequence,
            runtime_hash=APPROVED_RUNTIME,
            pose_enu=(pose.x, pose.y, pose.z, pose.yaw, pose.pitch, pose.roll),
            pose_uncertainty_m=0.05,
        )
        return self.signers["alpha"].sign(receipt)

    def run_scenario(
        self,
        name: str,
        claimed_action,
        peer_action,
        *,
        model_hash=APPROVED_MODEL,
        range_m=60.0,
        delivered_peers=None,
        equivocate=False,
        authorize_after_expiry=False,
    ) -> ScenarioResult:
        signed = self._receipt(claimed_action, model_hash)
        delivered = set(delivered_peers or IDS[1:])
        votes = []
        for node, peer in self.peers.items():
            if node not in delivered:
                continue
            votes.append(peer.vote_on(
                signed,
                my_observation=peer_action,
                my_pose=self.poses[node],
                originator_pose=self.poses["alpha"],
            ))

        if equivocate:
            bravo = self.peers["bravo"]
            votes.append(bravo._build_signed_vote(
                signed.receipt,
                decision=Vote.DISPUTE,
                reason="semantic_disagreement",
            ))

        consensus = self.engine.tally(
            signed.receipt,
            votes,
            self.vote_verifier,
            expected_voters=set(IDS),
        )
        clearance = check_free_space(claimed_action, range_m)
        authorization = self.supervisor.authorize(
            claimed_action,
            consensus,
            forward_clearance=clearance,
            perception_healthy=True,
            state_estimate_healthy=True,
            geofence_clear=True,
            autopilot_guard_healthy=True,
            command_timestamp_ns=signed.receipt.timestamp_ns,
            command_valid_for_ns=signed.receipt.valid_for_ns,
            now_ns=(
                signed.receipt.timestamp_ns + signed.receipt.valid_for_ns + 1
                if authorize_after_expiry
                else signed.receipt.timestamp_ns
            ),
        )
        return ScenarioResult(
            scenario=name,
            outcome=consensus.outcome.value,
            acks=consensus.ack_count,
            disputes=consensus.dispute_count,
            semantic_acks=consensus.semantic_ack_count,
            equivocators=tuple(sorted(consensus.equivocation_voter_ids)),
            command_authorized=authorization.allowed,
            released_action=authorization.action,
            supervisor_reason=authorization.reason,
        )

    def run_all(self) -> list[ScenarioResult]:
        results = [
            self.run_scenario(
                "honest_clear",
                (0.4, 0.0, 0.0),
                (0.4, 0.0, 0.0),
            ),
            self.run_scenario(
                "model_swap",
                (0.4, 0.0, 0.0),
                (0.4, 0.0, 0.0),
                model_hash=MALICIOUS_MODEL,
            ),
            self.run_scenario(
                "semantic_patch",
                (1.0, 0.0, 0.0),
                (0.0, 0.0, 1.0),
                range_m=5.0,
            ),
            self.run_scenario(
                "packet_loss",
                (0.4, 0.0, 0.0),
                (0.4, 0.0, 0.0),
                delivered_peers={"bravo"},
            ),
            self.run_scenario(
                "clearance_blocked",
                (0.4, 0.0, 0.0),
                (0.4, 0.0, 0.0),
                range_m=2.0,
            ),
            self.run_scenario(
                "crypto_only_accept",
                (0.4, 0.0, 0.0),
                None,
            ),
            self.run_scenario(
                "expired_after_consensus",
                (0.4, 0.0, 0.0),
                (0.4, 0.0, 0.0),
                authorize_after_expiry=True,
            ),
            self.run_scenario(
                "single_peer_equivocation",
                (0.4, 0.0, 0.0),
                (0.4, 0.0, 0.0),
                equivocate=True,
            ),
        ]
        self._assert_invariants(results)
        return results

    @staticmethod
    def _assert_invariants(results: list[ScenarioResult]) -> None:
        by_name = {row.scenario: row for row in results}
        assert by_name["honest_clear"].outcome == "ACCEPTED"
        assert by_name["honest_clear"].command_authorized
        for attack in (
            "model_swap",
            "semantic_patch",
            "packet_loss",
            "clearance_blocked",
            "crypto_only_accept",
            "expired_after_consensus",
        ):
            assert not by_name[attack].command_authorized
            assert by_name[attack].released_action == (0.0, 0.0, 0.0)
        assert by_name["clearance_blocked"].outcome == "ACCEPTED"
        assert by_name["clearance_blocked"].supervisor_reason == (
            "forward_clearance_unproven"
        )
        assert by_name["crypto_only_accept"].outcome == "ACCEPTED"
        assert by_name["crypto_only_accept"].supervisor_reason == (
            "semantic_quorum_missing"
        )
        assert by_name["expired_after_consensus"].outcome == "ACCEPTED"
        assert by_name["expired_after_consensus"].supervisor_reason == "command_expired"
        equivocation = by_name["single_peer_equivocation"]
        assert equivocation.equivocators == ("bravo",)
        assert equivocation.outcome == "ACCEPTED"


def main() -> None:
    rows = ClosedLoopSimulation().run_all()
    print(json.dumps([asdict(row) for row in rows], indent=2))
    print("\nall closed-loop security invariants passed")


if __name__ == "__main__":
    main()
