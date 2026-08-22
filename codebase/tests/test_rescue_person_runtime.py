from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from rescue.person_runtime import (
    DetectionObservation,
    FramePacket,
    ModelIdentity,
    NewestFrameMailbox,
    PersonRuntimeError,
    StateAssessment,
    StateDisplayPolicy,
    person_display_sidecar,
)


DETECTOR = ModelIdentity("sar-rgb-person-v1", "a" * 64)
STATE_MODEL = ModelIdentity("sar-person-state-v1", "b" * 64)
BBOX = (0.1, 0.2, 0.4, 0.8)


def _detection(**changes):
    values = {
        "candidate_id": "candidate-7",
        "frame_id": "camera-1-frame-42",
        "bbox_norm": BBOX,
        "detector": DETECTOR,
        "confidence": 0.91,
        "observed_at_ms": 1_000,
    }
    values.update(changes)
    return DetectionObservation(**values)


def _assessment(**changes):
    values = {
        "candidate_id": "candidate-7",
        "frame_id": "camera-1-frame-42",
        "bbox_norm": BBOX,
        "state_model": STATE_MODEL,
        "label": "safe_walking",
        "confidence": 0.92,
        "assessed_at_ms": 1_050,
    }
    values.update(changes)
    return StateAssessment(**values)


def _policy(**changes):
    values = {
        "detector": DETECTOR,
        "state_model": STATE_MODEL,
        "minimum_detector_confidence": 0.5,
        "minimum_state_confidence": 0.8,
        "maximum_detection_age_ms": 500,
        "maximum_state_age_ms": 500,
        "maximum_assessment_delay_ms": 100,
    }
    values.update(changes)
    return StateDisplayPolicy(**values)


def test_model_and_runtime_contracts_are_immutable_and_strict():
    with pytest.raises(PersonRuntimeError, match="lowercase"):
        ModelIdentity("sar-rgb-person-v1", "A" * 64)
    with pytest.raises(PersonRuntimeError, match="normalized"):
        _detection(bbox_norm=(0.1, 0.2, 1.1, 0.8))
    with pytest.raises(PersonRuntimeError, match="state label"):
        _assessment(label="safe")
    with pytest.raises(FrozenInstanceError):
        DETECTOR.model_id = "changed"


def test_verified_high_confidence_safe_is_green_sidecar_only():
    detection = _detection()
    result = person_display_sidecar(
        detection, _assessment(), policy=_policy(), now_ms=1_100
    )

    assert result["display"] == {
        "box_color": "green",
        "status": "SAFE_WALKING",
        "verified": True,
        "reasons": [],
    }
    assert result["class_id"] == "person_candidate"
    assert result["bbox_norm"] == list(BBOX)
    assert result["state_assessment"]["candidate_id"] == "candidate-7"
    assert result["state_assessment"]["frame_id"] == "camera-1-frame-42"
    assert result["state_assessment"]["bbox_norm"] == list(BBOX)
    assert "payload" not in result
    assert "kind" not in result


def test_verified_high_confidence_disaster_is_red_not_review():
    result = person_display_sidecar(
        _detection(),
        _assessment(label="disaster_stressed"),
        policy=_policy(),
        now_ms=1_100,
    )
    assert result["display"]["box_color"] == "red"
    assert result["display"]["status"] == "DISASTER_STRESSED"
    assert result["display"]["verified"] is True


@pytest.mark.parametrize(
    "detection,assessment,reason",
    [
        (
            _detection(detector=ModelIdentity("other-detector", "c" * 64)),
            _assessment(),
            "detector_identity_mismatch",
        ),
        (
            _detection(),
            _assessment(state_model=ModelIdentity("other-state", "c" * 64)),
            "state_model_identity_mismatch",
        ),
        (_detection(), _assessment(candidate_id="candidate-8"), "candidate_id_mismatch"),
        (_detection(), _assessment(frame_id="camera-1-frame-41"), "frame_id_mismatch"),
        (
            _detection(),
            _assessment(bbox_norm=(0.1, 0.2, 0.5, 0.8)),
            "bbox_mismatch",
        ),
    ],
)
def test_mismatched_evidence_is_red_unverified(detection, assessment, reason):
    result = person_display_sidecar(
        detection, assessment, policy=_policy(), now_ms=1_100
    )
    assert result["display"]["box_color"] == "red"
    assert result["display"]["status"] == "UNVERIFIED"
    assert result["display"]["verified"] is False
    assert reason in result["display"]["reasons"]


@pytest.mark.parametrize(
    "detection,assessment,now_ms,reason",
    [
        (_detection(confidence=0.49), _assessment(), 1_100, "detector_confidence_below_policy"),
        (_detection(), _assessment(confidence=0.79), 1_100, "state_confidence_below_policy"),
        (_detection(), None, 1_100, "state_assessment_missing"),
        (_detection(), _assessment(), 1_551, "state_assessment_stale"),
        (_detection(), _assessment(assessed_at_ms=1_600), 1_600, "detection_stale"),
        (
            _detection(),
            _assessment(assessed_at_ms=1_101),
            1_101,
            "assessment_delay_exceeds_policy",
        ),
    ],
)
def test_missing_low_confidence_or_stale_evidence_never_becomes_green(
    detection, assessment, now_ms, reason
):
    result = person_display_sidecar(
        detection, assessment, policy=_policy(), now_ms=now_ms
    )
    assert result["display"]["box_color"] == "red"
    assert result["display"]["status"] == "UNVERIFIED"
    assert reason in result["display"]["reasons"]


def test_newest_frame_replaces_unconsumed_frame_and_reports_drop():
    mailbox = NewestFrameMailbox[str]()
    first = mailbox.publish(FramePacket(1, 100, "frame-1"))
    second = mailbox.publish(FramePacket(2, 101, "frame-2"))
    third = mailbox.publish(FramePacket(3, 101, "frame-3"))

    assert first.replaced_pending_frame is False
    assert second.replaced_pending_frame is True
    assert third.dropped_frames_total == 2
    assert mailbox.take_newest() == FramePacket(3, 101, "frame-3")
    assert mailbox.take_newest() is None
    snapshot = mailbox.snapshot()
    assert snapshot.dropped_frames_total == 2
    assert snapshot.consumed_frames_total == 1
    assert snapshot.has_unconsumed_frame is False


def test_mailbox_enforces_sequence_and_timestamp_order_without_state_change():
    mailbox = NewestFrameMailbox[str]()
    mailbox.publish(FramePacket(10, 500, "newest"))

    with pytest.raises(PersonRuntimeError, match="sequence"):
        mailbox.publish(FramePacket(10, 501, "duplicate-sequence"))
    with pytest.raises(PersonRuntimeError, match="timestamp"):
        mailbox.publish(FramePacket(11, 499, "backward-time"))

    assert mailbox.take_newest() == FramePacket(10, 500, "newest")
    assert mailbox.snapshot().dropped_frames_total == 0
