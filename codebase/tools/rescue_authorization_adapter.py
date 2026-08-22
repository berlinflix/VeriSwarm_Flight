"""Publish model-identity qualification results to the rescue event plane.

The qualification service retains detailed protocol evidence.  This adapter emits only
the responder-facing authorization decision required by ``veriswarm.rescue.event.v1``.
It deliberately does not copy model hashes, receipts, votes, or private identities into
the rescue event log.

Delivery is restart safe.  The exact event is persisted before the HTTP request and is
cleared only after the collector confirms either a new acceptance (202) or an idempotent
duplicate (200).  A different evidence file cannot consume the next sequence number while
an earlier event remains pending.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from rescue.schema import RESCUE_SCHEMA, validate_rescue_event


TOKEN_ENV = "VERISWARM_RESCUE_TOKEN"
STATE_SCHEMA = "veriswarm.rescue.authorization_state.v1"
DEFAULT_SOURCE = "abhijan-security"
DEFAULT_MISSION_ID = "OP-VARUNA-001"
RUN_COMPONENT_RE = re.compile(r"[^A-Za-z0-9._:-]+")
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


class AuthorizationAdapterError(RuntimeError):
    """The qualification proof cannot be safely converted or delivered."""


@dataclass(frozen=True)
class AuthorizationDecision:
    decision: str
    reason: str


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise AuthorizationAdapterError(f"{field}_must_be_an_object")
    return value


def _unwrap_evidence(document: Mapping[str, Any]) -> Mapping[str, Any]:
    """Accept either a dashboard evidence file or its HTTP response wrapper."""
    wrapped = document.get("evidence")
    return _mapping(wrapped, "evidence") if wrapped is not None else document


def _count(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def decision_from_evidence(document: Mapping[str, Any]) -> AuthorizationDecision:
    """Derive a fail-closed model-identity decision from retained proof.

    ``ALLOW`` means only that the selected model identity passed the demonstrated
    qualification.  It does not authorize flight by itself.  Motion remains subject to
    Pratik's mission and safety gates.
    """
    evidence = _unwrap_evidence(document)
    proof = evidence.get("dashboard_proof")
    if not isinstance(proof, Mapping):
        return AuthorizationDecision("HOLD", "model_hash_proof_missing")

    actual = evidence.get("actual")
    if not isinstance(actual, Mapping):
        return AuthorizationDecision("HOLD", "model_hash_evidence_inconsistent")

    outcome = actual.get("outcome")
    if outcome == "NO_QUORUM":
        return AuthorizationDecision("HOLD", "model_hash_verification_no_quorum")

    if evidence.get("pass") is not True or proof.get("proof_valid") is not True:
        return AuthorizationDecision("HOLD", "model_hash_verification_failed")

    receipt = evidence.get("receipt")
    if not isinstance(receipt, Mapping):
        return AuthorizationDecision("HOLD", "model_hash_evidence_inconsistent")

    approved = proof.get("approved_model_sha256")
    observed = proof.get("observed_model_sha256")
    receipt_hash = receipt.get("model_hash")
    manifest_hash = proof.get("manifest_sha256")
    if (
        proof.get("schema") != "veriswarm.qualification_dashboard.v1"
        or proof.get("signer_backend") != "optee"
        or not isinstance(approved, str)
        or not SHA256_RE.fullmatch(approved)
        or not isinstance(observed, str)
        or not SHA256_RE.fullmatch(observed)
        or receipt_hash != observed
        or not isinstance(manifest_hash, str)
        or not SHA256_RE.fullmatch(manifest_hash)
    ):
        return AuthorizationDecision("HOLD", "model_hash_evidence_inconsistent")

    case_name = proof.get("case")
    policy_reason = proof.get("policy_reason")
    observed_is_approved = proof.get("observed_is_approved")
    semantic_acks = _count(actual.get("semantic_acks"))
    disputes = _count(actual.get("disputes"))

    if (
        case_name == "clean"
        and policy_reason == "model_hash_approved"
        and observed_is_approved is True
        and observed == approved
        and outcome == "ACCEPTED"
        and semantic_acks is not None
        and semantic_acks >= 2
    ):
        return AuthorizationDecision("ALLOW", "model_hash_approved")

    authorization = evidence.get("authorization")
    if (
        case_name == "model_swap"
        and policy_reason == "model_hash_not_approved"
        and observed_is_approved is False
        and observed != approved
        and outcome == "REJECTED"
        and disputes is not None
        and disputes >= 1
        and isinstance(authorization, Mapping)
        and authorization.get("allowed") is False
        and authorization.get("released") == [0.0, 0.0, 0.0]
    ):
        return AuthorizationDecision("QUARANTINE", "model_hash_not_approved")

    return AuthorizationDecision("HOLD", "model_hash_evidence_inconsistent")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise AuthorizationAdapterError(f"evidence_not_found:{path}") from error
    except json.JSONDecodeError as error:
        raise AuthorizationAdapterError(f"evidence_invalid_json:{error}") from error
    if not isinstance(document, dict):
        raise AuthorizationAdapterError("evidence_must_be_an_object")
    return document


def _default_state() -> dict[str, Any]:
    return {"schema": STATE_SCHEMA, "next_seq": 1, "pending": None}


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return _default_state()
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise AuthorizationAdapterError(f"state_invalid_json:{error}") from error
    if not isinstance(state, dict) or state.get("schema") != STATE_SCHEMA:
        raise AuthorizationAdapterError("state_schema_invalid")
    next_seq = state.get("next_seq")
    if isinstance(next_seq, bool) or not isinstance(next_seq, int) or next_seq < 1:
        raise AuthorizationAdapterError("state_next_seq_invalid")
    pending = state.get("pending")
    if pending is not None and not isinstance(pending, dict):
        raise AuthorizationAdapterError("state_pending_invalid")
    return {"schema": STATE_SCHEMA, "next_seq": next_seq, "pending": pending}


def _write_state(path: Path, state: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(state, indent=2, sort_keys=True) + "\n").encode("utf-8")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def _event_component(document: Mapping[str, Any]) -> str:
    evidence = _unwrap_evidence(document)
    run_id = evidence.get("run_id")
    if isinstance(run_id, str):
        component = RUN_COMPONENT_RE.sub("-", run_id).strip("-._:")
        if component:
            return component[:72]
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def build_event(
    document: Mapping[str, Any],
    *,
    node: str,
    source_seq: int,
    mission_id: str = DEFAULT_MISSION_ID,
    source: str = DEFAULT_SOURCE,
    observed_at_ms: int | None = None,
) -> dict[str, Any]:
    evidence = _unwrap_evidence(document)
    decision = decision_from_evidence(document)
    if observed_at_ms is None:
        created_ns = evidence.get("created_ns")
        observed_at_ms = (
            created_ns // 1_000_000
            if isinstance(created_ns, int) and not isinstance(created_ns, bool) and created_ns >= 0
            else time.time_ns() // 1_000_000
        )
    component = _event_component(document)
    event_id = f"{source}:{node}:{component}"
    if len(event_id) > 128:
        suffix = hashlib.sha256(event_id.encode("utf-8")).hexdigest()[:20]
        event_id = f"{source}:{node}:{suffix}"
    return validate_rescue_event(
        {
            "schema": RESCUE_SCHEMA,
            "mission_id": mission_id,
            "event_id": event_id,
            "source": source,
            "source_seq": source_seq,
            "observed_at_ms": observed_at_ms,
            "kind": "authorization",
            "payload": {
                "node": node,
                "decision": decision.decision,
                "reason": decision.reason,
            },
        },
        expected_mission_id=mission_id,
    )


def prepare_pending_event(
    document: Mapping[str, Any],
    *,
    node: str,
    state_path: Path,
    mission_id: str = DEFAULT_MISSION_ID,
    source: str = DEFAULT_SOURCE,
) -> dict[str, Any]:
    state = load_state(state_path)
    pending = state["pending"]
    candidate = build_event(
        document,
        node=node,
        source_seq=state["next_seq"],
        mission_id=mission_id,
        source=source,
        observed_at_ms=(pending.get("observed_at_ms") if pending is not None else None),
    )
    if pending is not None:
        if pending != candidate:
            raise AuthorizationAdapterError(
                f"pending_event_must_be_delivered_first:{pending.get('event_id', 'unknown')}"
            )
        return pending
    state["pending"] = candidate
    _write_state(state_path, state)
    return candidate


def _events_url(collector_url: str) -> str:
    parsed = urlparse(collector_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise AuthorizationAdapterError("collector_url_invalid")
    return f"{collector_url.rstrip('/')}/events"


def post_event(
    event: Mapping[str, Any],
    *,
    collector_url: str,
    token: str = "",
    timeout_s: float = 5.0,
) -> dict[str, Any]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(
        _events_url(collector_url),
        data=json.dumps(event, sort_keys=True, allow_nan=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout_s) as response:  # noqa: S310 - configured LAN URL
            status = response.status
            payload = json.loads(response.read())
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise AuthorizationAdapterError(f"collector_http_{error.code}:{detail.strip()}") from error
    except URLError as error:
        raise AuthorizationAdapterError(f"collector_unreachable:{error.reason}") from error
    except TimeoutError as error:
        raise AuthorizationAdapterError("collector_timeout") from error
    except json.JSONDecodeError as error:
        raise AuthorizationAdapterError("collector_response_invalid_json") from error
    if status not in {200, 202} or not isinstance(payload, dict) or payload.get("ok") is not True:
        raise AuthorizationAdapterError(f"collector_rejected_response:{status}")
    return payload


def mark_delivered(state_path: Path, event: Mapping[str, Any]) -> None:
    state = load_state(state_path)
    if state["pending"] != event:
        raise AuthorizationAdapterError("delivered_event_does_not_match_pending")
    state["pending"] = None
    state["next_seq"] = event["source_seq"] + 1
    _write_state(state_path, state)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Publish retained model-hash qualification proof as a rescue authorization"
    )
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--node", required=True, help="rescue node whose model was qualified")
    parser.add_argument("--collector-url", default="http://127.0.0.1:8770")
    parser.add_argument("--mission-id", default=DEFAULT_MISSION_ID)
    parser.add_argument("--source", default=DEFAULT_SOURCE)
    parser.add_argument(
        "--state",
        type=Path,
        default=Path("results/abhijan_authorization_state.json"),
    )
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the derived event without changing state or contacting the collector",
    )
    args = parser.parse_args(argv)

    try:
        document = _read_json(args.evidence)
        if args.dry_run:
            state = load_state(args.state)
            event = build_event(
                document,
                node=args.node,
                source_seq=state["next_seq"],
                mission_id=args.mission_id,
                source=args.source,
            )
            print(json.dumps(event, indent=2, sort_keys=True))
            return 0

        event = prepare_pending_event(
            document,
            node=args.node,
            state_path=args.state,
            mission_id=args.mission_id,
            source=args.source,
        )
        response = post_event(
            event,
            collector_url=args.collector_url,
            token=os.environ.get(TOKEN_ENV, ""),
            timeout_s=args.timeout,
        )
        mark_delivered(args.state, event)
    except (AuthorizationAdapterError, OSError, ValueError) as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 2

    print(
        "PASS "
        f"event_id={event['event_id']} "
        f"decision={event['payload']['decision']} "
        f"duplicate={bool(response.get('duplicate'))}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
