from __future__ import annotations

import json

import pytest

from tools.rescue_authorization_adapter import (
    AuthorizationAdapterError,
    build_event,
    decision_from_evidence,
    load_state,
    mark_delivered,
    prepare_pending_event,
)


def _evidence(case: str = "clean", *, proof_valid: bool = True) -> dict:
    is_swap = case == "model_swap"
    return {
        "schema": "veriswarm.internal_qualifier.v1",
        "run_id": f"rescue-{case}-001",
        "created_ns": 1_787_394_603_000_000_000,
        "pass": proof_valid,
        "actual": {
            "outcome": "REJECTED" if is_swap else "ACCEPTED",
            "disputes": 2 if is_swap else 0,
            "semantic_acks": 0 if is_swap else 2,
        },
        "receipt": {"model_hash": "b" * 64 if is_swap else "a" * 64},
        "authorization": {
            "allowed": False,
            "released": [0.0, 0.0, 0.0],
        },
        "dashboard_proof": {
            "schema": "veriswarm.qualification_dashboard.v1",
            "case": case,
            "approved_model_sha256": "a" * 64,
            "observed_model_sha256": "b" * 64 if is_swap else "a" * 64,
            "observed_is_approved": not is_swap,
            "manifest_sha256": "c" * 64,
            "signer_backend": "optee",
            "policy_reason": (
                "model_hash_not_approved" if is_swap else "model_hash_approved"
            ),
            "proof_valid": proof_valid,
        },
    }


def test_clean_proof_becomes_model_identity_allow():
    decision = decision_from_evidence(_evidence())
    assert decision.decision == "ALLOW"
    assert decision.reason == "model_hash_approved"


def test_model_swap_proof_becomes_quarantine():
    decision = decision_from_evidence(_evidence("model_swap"))
    assert decision.decision == "QUARANTINE"
    assert decision.reason == "model_hash_not_approved"


@pytest.mark.parametrize(
    "mutation, reason",
    [
        (lambda row: row.pop("dashboard_proof"), "model_hash_proof_missing"),
        (
            lambda row: row["dashboard_proof"].update(proof_valid=False),
            "model_hash_verification_failed",
        ),
        (
            lambda row: row["dashboard_proof"].update(observed_is_approved=False),
            "model_hash_evidence_inconsistent",
        ),
        (
            lambda row: row["actual"].update(outcome="NO_QUORUM"),
            "model_hash_verification_no_quorum",
        ),
    ],
)
def test_invalid_or_incomplete_proof_fails_closed_to_hold(mutation, reason):
    evidence = _evidence()
    mutation(evidence)
    decision = decision_from_evidence(evidence)
    assert decision.decision == "HOLD"
    assert decision.reason == reason


def test_http_response_wrapper_is_supported():
    decision = decision_from_evidence({"ok": True, "evidence": _evidence("model_swap")})
    assert decision.decision == "QUARANTINE"


def test_event_contains_only_normalized_authorization_not_raw_proof():
    event = build_event(_evidence("model_swap"), node="alpha", source_seq=4)
    assert event == {
        "schema": "veriswarm.rescue.event.v1",
        "mission_id": "OP-VARUNA-001",
        "event_id": "abhijan-security:alpha:rescue-model_swap-001",
        "source": "abhijan-security",
        "source_seq": 4,
        "observed_at_ms": 1_787_394_603_000,
        "kind": "authorization",
        "payload": {
            "node": "alpha",
            "decision": "QUARANTINE",
            "reason": "model_hash_not_approved",
        },
    }
    serialized = json.dumps(event)
    assert "model_sha256" not in serialized
    assert "receipt" not in serialized
    assert "peer_votes" not in serialized
    assert "dashboard_proof" not in serialized


@pytest.mark.parametrize(
    "mutation",
    [
        lambda row: row["dashboard_proof"].update(signer_backend="software"),
        lambda row: row["dashboard_proof"].update(manifest_sha256="bad"),
        lambda row: row["dashboard_proof"].update(observed_model_sha256="c" * 64),
        lambda row: row["actual"].update(semantic_acks="2"),
    ],
)
def test_untrusted_or_malformed_proof_cannot_allow(mutation):
    evidence = _evidence()
    mutation(evidence)
    decision = decision_from_evidence(evidence)
    assert decision.decision == "HOLD"
    assert decision.reason == "model_hash_evidence_inconsistent"


def test_pending_event_is_persisted_and_identical_retry_reuses_it(tmp_path):
    state_path = tmp_path / "state.json"
    first = prepare_pending_event(_evidence(), node="alpha", state_path=state_path)
    second = prepare_pending_event(_evidence(), node="alpha", state_path=state_path)
    assert second == first
    state = load_state(state_path)
    assert state["next_seq"] == 1
    assert state["pending"] == first


def test_pending_retry_without_evidence_timestamp_keeps_original_event(tmp_path):
    evidence = _evidence()
    evidence.pop("created_ns")
    state_path = tmp_path / "state.json"
    first = prepare_pending_event(evidence, node="alpha", state_path=state_path)
    second = prepare_pending_event(evidence, node="alpha", state_path=state_path)
    assert second == first


def test_different_evidence_cannot_replace_pending_event(tmp_path):
    state_path = tmp_path / "state.json"
    prepare_pending_event(_evidence(), node="alpha", state_path=state_path)
    with pytest.raises(AuthorizationAdapterError, match="pending_event_must_be_delivered"):
        prepare_pending_event(
            _evidence("model_swap"), node="alpha", state_path=state_path
        )


def test_successful_delivery_advances_sequence_and_clears_pending(tmp_path):
    state_path = tmp_path / "state.json"
    first = prepare_pending_event(_evidence(), node="alpha", state_path=state_path)
    mark_delivered(state_path, first)

    state = load_state(state_path)
    assert state["next_seq"] == 2
    assert state["pending"] is None

    second = prepare_pending_event(
        _evidence("model_swap"), node="alpha", state_path=state_path
    )
    assert second["source_seq"] == 2


def test_state_file_is_owner_only(tmp_path):
    state_path = tmp_path / "state.json"
    prepare_pending_event(_evidence(), node="alpha", state_path=state_path)
    assert state_path.stat().st_mode & 0o777 == 0o600
