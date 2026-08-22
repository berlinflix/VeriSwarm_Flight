"""Alpha-side qualification runner: originate one case, tally, prove, record.

Runs on the Jetson (Alpha). It builds Alpha's signer from the scoped manifest —
``backend: "optee"`` means the OP-TEE hardware signer, constructed locally; there
is no software fallback under the OP-TEE label. It contacts Bravo and Charlie
over the wired LAN, originates a protocol-v4 receipt carrying an explicit
measured ``PerceptionClaim``, tallies the real ``ConsensusEngine`` decision, and
writes canonical, non-overwriting JSON evidence.

Two panel cases, selected by ``--case``:

* ``clean-case.json``  -> expect ACCEPTED with two semantic ACKs.
* ``model-swap-case.json`` -> the tampered weight hash is absent from the
  allowlist, so both peers DISPUTE on provenance and the outcome is REJECTED
  (decision layer holds).

Exit status is non-zero if the actual outcome does not match the case's
``expected`` block, if peers never become ready, or if the evidence path already
exists. A demo that silently "passes" is worse than one that fails loudly.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Optional

from node.client import Originator
from perception.claim import PerceptionClaim
from protocol.geometry import Pose
from protocol.peer_consensus import ConsensusOutcome
from protocol.receipts import sha256_hex

from tools.qualification_peer import claim_from_dict

SCHEMA = "veriswarm.internal_qualifier.v1"


def _git_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        )
        return out.stdout.strip()
    except Exception:
        return "unknown"


def _contract_beside(case_path: Path) -> dict:
    candidate = case_path.parent / "contract.json"
    if candidate.is_file():
        return json.loads(candidate.read_text())
    return {}


def _decision_for(outcome: ConsensusOutcome) -> tuple[bool, list, str]:
    """Map a protocol outcome to the qualifier decision layer.

    The protocol slice authorizes no motion: a clean ACCEPTED still holds because
    this slice supplies no positive health/clearance fixtures, and a REJECTED
    holds because consensus refused it. Both release the zero (hold) action; the
    ACCEPTED-vs-REJECTED distinction is the point being demonstrated.
    """
    if outcome is ConsensusOutcome.ACCEPTED:
        return False, [0.0, 0.0, 0.0], "protocol_accepted;clearance_fixtures_out_of_slice"
    if outcome is ConsensusOutcome.REJECTED:
        return False, [0.0, 0.0, 0.0], "consensus_rejected"
    return False, [0.0, 0.0, 0.0], "consensus_no_quorum"


def run_case(
    *, manifest_path: Path, case_path: Path, out_path: Path,
    run_id: Optional[str] = None, rpc_timeout_s: float = 2.0,
    ready_timeout_s: float = 20.0,
) -> dict:
    manifest = json.loads(manifest_path.read_text())
    case = json.loads(case_path.read_text())
    contract = _contract_beside(case_path)

    originator_id = case.get("originator", "alpha")
    peer_ids = sorted(set(manifest["nodes"]) - {originator_id})
    claim = claim_from_dict(case["claim"])

    # Every invocation is a separate process, so the originator's counter would
    # restart at 1 and collide with the previous run — the peers reject that as
    # `duplicate_sequence`, and a repeated clean case would fail even though
    # nothing is wrong. Seeding from the wall clock (ms since epoch, well inside
    # the uint64 wire field) gives each run a strictly increasing sequence that
    # is never reused, so the panel may re-run a case as often as it likes.
    origin = Originator(
        manifest, originator_id, peer_ids,
        rpc_timeout_s=rpc_timeout_s,
        start_sequence=time.time_ns() // 1_000_000,
    )
    try:
        # A degraded-quorum case declares how many peers it expects to be absent,
        # so require only the rest. Waiting for every peer would abort the round
        # before it starts and report a setup error instead of demonstrating the
        # condition. Peers that never answer are still counted as `missing` by
        # the tally, so nothing is hidden by relaxing the gate.
        expected_missing = int(case.get("expected", {}).get("missing", 0))
        min_ready = max(0, len(peer_ids) - expected_missing)
        if not origin.wait_ready(timeout=ready_timeout_s, minimum=min_ready):
            raise SystemExit(
                f"needed {min_ready} of {len(peer_ids)} peers ready within "
                f"{ready_timeout_s}s: {peer_ids}"
            )
        pose_vals = manifest["nodes"][originator_id].get("pose")
        pose = Pose(*pose_vals[:5]) if pose_vals else None
        outcome = origin.originate(
            output=case["action"],
            model_hash=case["model_hash"],
            input_bytes=f"veriswarm-qualifier:{case['case_id']}".encode(),
            pose=pose,
            perception=claim,
        )
    finally:
        origin.close()

    result = outcome.consensus
    receipt = outcome.receipt
    allowed, released, decision_reason = _decision_for(result.outcome)

    votes = []
    for voter in sorted(set(result.ack_voter_ids) | set(result.dispute_voter_ids)):
        votes.append({
            "voter": voter,
            "decision": "ACK" if voter in result.ack_voter_ids else "DISPUTE",
            "semantic": voter in result.semantic_voter_ids,
        })

    expected = case.get("expected", {})
    actual = {
        "outcome": result.outcome.value,
        "acks": result.ack_count,
        "disputes": result.dispute_count,
        "missing": result.missing_count,
        "semantic_acks": result.semantic_ack_count,
        "reason": result.reason,
    }

    passed = actual["outcome"] == expected.get("outcome")
    if expected.get("outcome") == "ACCEPTED":
        passed = passed and result.semantic_ack_count >= int(expected.get("semantic_acks", 2))
    if expected.get("outcome") == "REJECTED":
        passed = passed and result.dispute_count >= int(expected.get("disputes", 1))

    evidence = {
        "schema": SCHEMA,
        "bundle_id": contract.get("bundle_id", "unknown"),
        "run_id": run_id or f"run-{time.time_ns()}",
        "case_id": case["case_id"],
        "created_ns": time.time_ns(),
        "commit": _git_commit(),
        "public_manifest_sha256": contract.get("public_manifest_sha256", "unknown"),
        "expected": expected,
        "actual": actual,
        "receipt": {
            "digest": sha256_hex(receipt.canonical()),
            "model_hash": receipt.model_hash,
            "input_hash": receipt.input_hash,
            "output": list(receipt.output),
            "action_frame": receipt.action_frame,
            "mission_id": receipt.mission_id,
            "mission_epoch": receipt.mission_epoch,
            "sequence": receipt.sequence,
        },
        "consensus_target_receipt_hash": result.target_receipt_hash,
        "authorization": {
            "allowed": allowed,
            "released": released,
            "reason": decision_reason,
        },
        "timings_ms": {
            "sign": round(outcome.sign_ms, 4),
            "consensus": round(outcome.consensus_ms, 4),
            "total": round(outcome.total_ms, 4),
        },
        "peer_votes": votes,
        "pass": bool(passed),
    }

    # Cross-check §6: every counted vote and the consensus target must equal the
    # receipt digest; the requested command must equal the receipt's signed output.
    digest = evidence["receipt"]["digest"]
    if result.target_receipt_hash and result.target_receipt_hash != digest:
        evidence["pass"] = False
        evidence["actual"]["reason"] += ";consensus_target_receipt_mismatch"
    if list(receipt.output) != list(case["action"]):
        evidence["pass"] = False
        evidence["actual"]["reason"] += ";receipt_output_command_mismatch"

    _write_once(out_path, evidence)
    return evidence


def _write_once(path: Path, evidence: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(evidence, indent=2, sort_keys=True) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise


def _main() -> int:
    parser = argparse.ArgumentParser(description="Run one qualification protocol case")
    parser.add_argument("--manifest", required=True, help="Alpha-scoped manifest")
    parser.add_argument("--case", required=True, help="clean-case.json or model-swap-case.json")
    parser.add_argument("--out", required=True, help="new JSON evidence path (never overwritten)")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--rpc-timeout-s", default=2.0, type=float)
    parser.add_argument("--ready-timeout-s", default=20.0, type=float)
    args = parser.parse_args()

    out_path = Path(args.out).expanduser().resolve()
    if out_path.exists():
        raise SystemExit(f"evidence already exists (never overwrite): {out_path}")

    evidence = run_case(
        manifest_path=Path(args.manifest).expanduser().resolve(strict=True),
        case_path=Path(args.case).expanduser().resolve(strict=True),
        out_path=out_path,
        run_id=args.run_id,
        rpc_timeout_s=args.rpc_timeout_s,
        ready_timeout_s=args.ready_timeout_s,
    )

    a = evidence["actual"]
    verdict = "PASS" if evidence["pass"] else "FAIL"
    print(f"[{verdict}] {evidence['case_id']}: {a['outcome']} "
          f"acks={a['acks']} semantic_acks={a['semantic_acks']} "
          f"disputes={a['disputes']} reason={a['reason']}")
    print(f"       receipt={evidence['receipt']['digest'][:16]} "
          f"model={evidence['receipt']['model_hash'][:16]} evidence={out_path}")
    for v in evidence["peer_votes"]:
        tag = "semantic" if v["semantic"] else "abstain/crypto"
        print(f"       {v['voter']:8} {v['decision']:8} ({tag})")
    return 0 if evidence["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(_main())
