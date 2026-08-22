from __future__ import annotations

import json
from pathlib import Path

import pytest

from rescue.collector import RescueCollector
from rescue.multiview import (
    CameraModel,
    MissingViewEvidence,
    MultiViewFusionError,
    PersonView,
    fuse_person_views,
    locate_with_metric_range,
    triangulate_positive_views,
)
from rescue.schema import RESCUE_SCHEMA, RescueEventError, validate_rescue_event
from rescue.security_bridge import assess_consensus_security
from protocol.peer_consensus import ConsensusOutcome, ConsensusResult
from tools.rescue_multiview_fusion import main as multiview_main


IDENTITY_ROTATION = (
    (1.0, 0.0, 0.0),
    (0.0, 1.0, 0.0),
    (0.0, 0.0, 1.0),
)


def _camera(camera_id: str, position=(0.0, 0.0, 0.0)) -> CameraModel:
    return CameraModel(
        camera_id=camera_id,
        calibration_id=f"{camera_id}-cal-v1",
        width_px=1000,
        height_px=1000,
        fx_px=1000.0,
        fy_px=1000.0,
        cx_px=500.0,
        cy_px=500.0,
        position_ned=position,
        rotation_camera_to_ned=IDENTITY_ROTATION,
        pose_uncertainty_m=0.1,
        bearing_uncertainty_deg=0.5,
    )


def _view(
    source: str,
    camera: CameraModel,
    *,
    observation_id: str | None = None,
    centre_x: float = 0.5,
    range_m: float | None = None,
    range_uncertainty_m: float | None = None,
    confidence: float = 0.86,
    security: str = "VERIFIED",
) -> PersonView:
    return PersonView(
        observation_id=observation_id or f"{source}-person-1",
        source=source,
        frame_id=f"{source}-frame-1",
        observed_at_ms=1_787_394_600_100,
        confidence=confidence,
        bbox_norm=(centre_x - 0.04, 0.35, centre_x + 0.04, 0.65),
        camera=camera,
        range_m=range_m,
        range_uncertainty_m=range_uncertainty_m,
        evidence_security=security,
    )


def _missing(
    *,
    line_of_sight: bool,
    expected: bool = True,
    camera_id: str = "camera-a",
    source: str = "alpha",
) -> MissingViewEvidence:
    return MissingViewEvidence(
        camera_id=camera_id,
        source=source,
        frame_id=f"{source}-frame-1",
        observed_at_ms=1_787_394_600_100,
        healthy=True,
        inference_succeeded=True,
        shares_scene=True,
        expected_in_frame=expected,
        line_of_sight_proven=line_of_sight,
        reason="building geometry checked",
    )


def _event(source: str, seq: int, payload: dict) -> dict:
    return {
        "schema": RESCUE_SCHEMA,
        "mission_id": "OP-VARUNA-001",
        "event_id": f"{source}-observation-{seq}",
        "source": source,
        "source_seq": seq,
        "observed_at_ms": 1_787_394_600_100 + seq,
        "kind": "observation",
        "payload": payload,
    }


def _payload(view: PersonView, enrichment: dict) -> dict:
    return {
        "node": view.source,
        "observation_id": view.observation_id,
        "class_id": "person_candidate",
        "confidence": view.confidence,
        "frame_id": view.frame_id,
        "modality": "rgb",
        "model_id": "sar-rgb-person-v1",
        "model_sha256": "a" * 64,
        "bbox_norm": list(view.bbox_norm),
        **enrichment,
    }


def test_single_positive_with_metric_range_is_located_and_prioritized():
    positive = _view(
        "bravo",
        _camera("camera-b"),
        range_m=10.0,
        range_uncertainty_m=0.3,
        confidence=0.51,
    )
    estimate = locate_with_metric_range(positive)
    assert estimate.position_ned == pytest.approx((0.0, 0.0, 10.0))

    fused = fuse_person_views(
        "building-7-person-1",
        [positive],
        missing_views=[_missing(line_of_sight=False)],
    )
    assert fused.alert_required
    assert fused.priority == "HIGH"
    assert fused.position_ned == pytest.approx((0.0, 0.0, 10.0))
    assert fused.localization_method == "metric_range"
    assert not fused.corroborated
    assert not fused.security_review_required
    assert fused.missing_view_dispositions == (
        ("camera-a", "ABSTAIN_OCCLUDED_OR_UNPROVEN"),
    )


def test_two_positive_angles_triangulate_without_depth():
    left = _view("alpha", _camera("camera-a", (-1.0, 0.0, 0.0)), centre_x=0.6)
    right = _view("bravo", _camera("camera-b", (1.0, 0.0, 0.0)), centre_x=0.4)

    estimate = triangulate_positive_views(left, right)
    assert estimate.position_ned == pytest.approx((0.0, 0.0, 10.0), abs=1e-6)

    fused = fuse_person_views("person-ray-crossing-1", [left, right])
    assert fused.position_ned == pytest.approx((0.0, 0.0, 10.0), abs=1e-6)
    assert fused.localization_method == "ray_triangulation"
    assert fused.corroborated
    assert fused.corroborated_sources == ("alpha", "bravo")
    assert fused.positive_sources == ("alpha", "bravo")


def test_negative_view_cannot_triangulate_or_cancel_a_positive():
    positive = _view("bravo", _camera("camera-b"))
    occluded = _missing(line_of_sight=False)
    fused = fuse_person_views("broken-building-person", [positive], missing_views=[occluded])
    assert fused.position_ned is None
    assert fused.localization_method == "bearing_only"
    assert fused.alert_required and fused.priority == "HIGH"
    assert not fused.security_review_required

    with pytest.raises(MultiViewFusionError, match="at least one positive"):
        fuse_person_views("negative-only", [], missing_views=[occluded])


def test_expected_visible_miss_flags_security_review_but_preserves_candidate():
    positive = _view("bravo", _camera("camera-b"), security="VERIFIED")
    fused = fuse_person_views(
        "patch-review-person",
        [positive],
        missing_views=[_missing(line_of_sight=True)],
    )
    assert fused.alert_required
    assert fused.priority == "HIGH"
    assert fused.security_review_required
    assert fused.expected_visible_misses == ("camera-a",)
    assert "expected_visible_miss:camera-a" in fused.security_reasons


def test_disputed_positive_is_retained_for_responder_and_security_review():
    positive = _view(
        "bravo",
        _camera("camera-b"),
        security="DISPUTED",
        confidence=0.93,
    )
    fused = fuse_person_views("disputed-person", [positive])
    assert fused.alert_required and fused.priority == "HIGH"
    assert fused.security_review_required
    assert fused.security_reasons == ("positive_view_disputed:camera-b",)


def _consensus_result(
    outcome: ConsensusOutcome,
    *,
    receipt_hash: str,
    semantic_acks: int,
    disputes: int = 0,
) -> ConsensusResult:
    return ConsensusResult(
        outcome=outcome,
        ack_count=2 if outcome is ConsensusOutcome.ACCEPTED else 0,
        dispute_count=disputes,
        missing_count=0,
        ack_threshold=2,
        dispute_threshold=1,
        reason="test",
        ack_voter_ids=frozenset({"bravo", "charlie"})
        if outcome is ConsensusOutcome.ACCEPTED
        else frozenset(),
        dispute_voter_ids=frozenset({"bravo"}) if disputes else frozenset(),
        semantic_ack_count=semantic_acks,
        semantic_voter_ids=frozenset({"bravo"}) if semantic_acks else frozenset(),
        target_receipt_hash=receipt_hash,
    )


def test_consensus_bridge_verifies_only_bound_semantic_accept():
    receipt_hash = "b" * 64
    assessment = assess_consensus_security(
        _consensus_result(
            ConsensusOutcome.ACCEPTED,
            receipt_hash=receipt_hash,
            semantic_acks=1,
        ),
        expected_receipt_hash=receipt_hash,
        receipt_verified=True,
        model_approved=True,
        runtime_approved=True,
    )
    assert assessment.state == "VERIFIED"
    assert assessment.reasons == ()


def test_consensus_bridge_does_not_convert_abstention_into_verification():
    receipt_hash = "c" * 64
    assessment = assess_consensus_security(
        _consensus_result(
            ConsensusOutcome.ACCEPTED,
            receipt_hash=receipt_hash,
            semantic_acks=0,
        ),
        expected_receipt_hash=receipt_hash,
        receipt_verified=True,
        model_approved=True,
        runtime_approved=True,
    )
    assert assessment.state == "UNVERIFIED"
    assert assessment.reasons == ("semantic_cross_check_insufficient",)

    positive = PersonView(
        **{
            **_view("alpha", _camera("camera-a")).__dict__,
            "evidence_security": assessment.state,
            "evidence_security_reasons": assessment.reasons,
        }
    )
    fused = fuse_person_views("unverified-but-actionable-person", [positive])
    assert fused.alert_required and fused.priority == "HIGH"
    assert fused.security_review_required


def test_consensus_bridge_digest_failure_is_disputed_but_person_survives():
    expected_hash = "d" * 64
    assessment = assess_consensus_security(
        _consensus_result(
            ConsensusOutcome.ACCEPTED,
            receipt_hash="e" * 64,
            semantic_acks=1,
        ),
        expected_receipt_hash=expected_hash,
        receipt_verified=True,
        model_approved=True,
        runtime_approved=True,
    )
    assert assessment.state == "DISPUTED"
    assert assessment.reasons == ("consensus_receipt_digest_mismatch",)
    positive = PersonView(
        **{
            **_view("alpha", _camera("camera-a")).__dict__,
            "evidence_security": assessment.state,
            "evidence_security_reasons": assessment.reasons,
        }
    )
    fused = fuse_person_views("disputed-but-actionable-person", [positive])
    assert fused.alert_required and fused.security_review_required


def test_consensus_bridge_rejection_and_no_quorum_never_veto_person_policy():
    receipt_hash = "f" * 64
    rejected = assess_consensus_security(
        _consensus_result(
            ConsensusOutcome.REJECTED,
            receipt_hash=receipt_hash,
            semantic_acks=0,
            disputes=1,
        ),
        expected_receipt_hash=receipt_hash,
        receipt_verified=True,
        model_approved=True,
        runtime_approved=True,
    )
    assert rejected.state == "DISPUTED"
    assert "consensus_rejected" in rejected.reasons

    no_quorum = ConsensusResult(
        outcome=ConsensusOutcome.NO_QUORUM,
        ack_count=1,
        dispute_count=0,
        missing_count=1,
        ack_threshold=2,
        dispute_threshold=1,
        reason="test",
        ack_voter_ids=frozenset({"bravo"}),
        semantic_ack_count=0,
        target_receipt_hash=receipt_hash,
    )
    unverified = assess_consensus_security(
        no_quorum,
        expected_receipt_hash=receipt_hash,
        receipt_verified=True,
        model_approved=True,
        runtime_approved=True,
    )
    assert unverified.state == "UNVERIFIED"
    assert unverified.reasons == ("consensus_no_quorum",)


def test_multiview_enrichment_validates_and_projects_without_survivor_veto(tmp_path):
    left = _view("alpha", _camera("camera-a", (-1.0, 0.0, 0.0)), centre_x=0.6)
    right = _view("bravo", _camera("camera-b", (1.0, 0.0, 0.0)), centre_x=0.4)
    fused = fuse_person_views(
        "factory-collapse-person-1",
        [left, right],
        missing_views=[
            _missing(line_of_sight=True, camera_id="camera-c", source="charlie")
        ],
    )
    collector = RescueCollector("OP-VARUNA-001", tmp_path / "events.jsonl")
    for seq, view in enumerate((left, right), 1):
        event = _event(view.source, seq, _payload(view, fused.enrichment_for(view)))
        collector.collect(event)

    # A separate PBFT/control HOLD remains visible but does not erase rescue evidence.
    collector.collect({
        "schema": RESCUE_SCHEMA,
        "mission_id": "OP-VARUNA-001",
        "event_id": "consensus-hold-1",
        "source": "consensus",
        "source_seq": 1,
        "observed_at_ms": 1_787_394_600_200,
        "kind": "authorization",
        "payload": {
            "node": "alpha",
            "decision": "HOLD",
            "reason": "camera evidence requires security review",
        },
    })

    state = collector.state()
    assert len(state["people"]) == 1
    person = state["people"][0]
    assert person["sources"] == ["alpha", "bravo"]
    assert person["corroborated"]
    assert person["position_ned"] == pytest.approx([0.0, 0.0, 10.0], abs=1e-6)
    assert person["security_review_required"]
    alert_kinds = {alert["kind"] for alert in state["alerts"]}
    assert "PERSON_CANDIDATE" in alert_kinds
    assert "PERCEPTION_SECURITY_REVIEW" in alert_kinds
    assert "AUTHORIZATION" in alert_kinds


def test_capture_group_alone_cannot_merge_unlocalized_people(tmp_path):
    first = _view("alpha", _camera("camera-a", (0.0, 0.0, 0.0)))
    second = _view("bravo", _camera("camera-b", (0.01, 0.0, 0.0)))
    fused = fuse_person_views("association-hint-only", [first, second])
    assert fused.position_ned is None
    assert fused.localization_method == "bearing_only"

    collector = RescueCollector("OP-VARUNA-001", tmp_path / "events.jsonl")
    for seq, view in enumerate((first, second), 1):
        collector.collect(
            _event(view.source, seq, _payload(view, fused.enrichment_for(view)))
        )
    # The same producer-supplied group ID is traceability only. Without measured
    # location, preserving two candidates is safer than suppressing a person.
    assert len(collector.state()["people"]) == 2


def test_nearby_localized_people_with_different_groups_do_not_merge(tmp_path):
    first = _view(
        "alpha",
        _camera("camera-a", (0.0, 0.0, 0.0)),
        range_m=10.0,
        range_uncertainty_m=0.2,
    )
    second = _view(
        "bravo",
        _camera("camera-b", (1.0, 0.0, 0.0)),
        range_m=10.0,
        range_uncertainty_m=0.2,
    )
    first_fused = fuse_person_views("person-nearby-1", [first])
    second_fused = fuse_person_views("person-nearby-2", [second])
    collector = RescueCollector("OP-VARUNA-001", tmp_path / "events.jsonl")
    collector.collect(
        _event("alpha", 1, _payload(first, first_fused.enrichment_for(first)))
    )
    collector.collect(
        _event("bravo", 1, _payload(second, second_fused.enrichment_for(second)))
    )
    assert len(collector.state()["people"]) == 2


def test_stale_positive_views_do_not_claim_corroboration():
    first = _view("alpha", _camera("camera-a"))
    second_values = _view("bravo", _camera("camera-b")).__dict__.copy()
    second_values["observed_at_ms"] = first.observed_at_ms + 5_000
    second = PersonView(**second_values)
    fused = fuse_person_views("stale-cross-view", [first, second])
    assert fused.alert_required
    assert not fused.corroborated
    assert fused.corroborated_sources == ()
    assert fused.enrichment_for(first)["corroborated_sources"] == []
    assert "positive_views_span_time_limit" in fused.localization_warnings


@pytest.mark.parametrize(
    "mutation, message",
    [
        (lambda payload: payload.pop("camera_calibration_id"), "supplied together"),
        (lambda payload: payload.update(bearing_ned=[0.0, 0.0, 2.0]), "unit vector"),
        (lambda payload: payload.update(evidence_security="TRUST_ME"), "evidence_security"),
        (
            lambda payload: (payload.pop("position_ned"), payload.pop("uncertainty_m")),
            "requires position_ned",
        ),
    ],
)
def test_multiview_observation_metadata_fails_closed(mutation, message):
    view = _view(
        "alpha",
        _camera("camera-a"),
        range_m=10.0,
        range_uncertainty_m=0.2,
    )
    fused = fuse_person_views("candidate-1", [view])
    payload = _payload(view, fused.enrichment_for(view))
    mutation(payload)
    with pytest.raises(RescueEventError, match=message):
        validate_rescue_event(_event("alpha", 1, payload))


def test_invalid_rotation_and_low_parallax_fail_closed():
    with pytest.raises(MultiViewFusionError, match="right-handed"):
        CameraModel(
            camera_id="bad-camera",
            calibration_id="bad-cal",
            width_px=1000,
            height_px=1000,
            fx_px=1000,
            fy_px=1000,
            cx_px=500,
            cy_px=500,
            position_ned=(0, 0, 0),
            rotation_camera_to_ned=((1, 0, 0), (0, 1, 0), (0, 0, -1)),
        )
    first = _view("alpha", _camera("camera-a", (0.0, 0.0, 0.0)))
    second = _view("bravo", _camera("camera-b", (0.01, 0.0, 0.0)))
    with pytest.raises(MultiViewFusionError, match="insufficient parallax"):
        triangulate_positive_views(first, second)


def test_broken_building_sample_cli_preserves_and_locates_single_positive(capsys):
    sample = Path(__file__).parents[1] / "examples" / "rescue_multiview_person_sample.json"
    assert multiview_main(["--input", str(sample)]) == 0
    output = json.loads(capsys.readouterr().out)
    candidate = output["candidate"]
    assert candidate["alert_required"]
    assert candidate["priority"] == "HIGH"
    assert candidate["position_ned"] == pytest.approx([0.0, 0.0, 10.0])
    assert candidate["missing_view_dispositions"] == [
        ["camera-a", "ABSTAIN_OCCLUDED_OR_UNPROVEN"]
    ]
    assert not candidate["security_review_required"]
