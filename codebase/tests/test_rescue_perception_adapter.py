from __future__ import annotations

import pytest

from perception.yolo_action import Detection
from rescue.collector import RescueCollector
from rescue.perception_adapter import (
    PerceptionAdapterError,
    build_observation_event,
    canonical_rescue_class,
    detection_bbox_norm,
)


def _event(
    detection: Detection | None = None,
    *,
    class_map=None,
    **overrides,
):
    values = {
        "class_map": class_map if class_map is not None else {0: "survivor"},
        "mission_id": "OP-VARUNA-001",
        "node": "alpha",
        "source_seq": 7,
        "observed_at_ms": 1_787_394_601_200,
        "event_id": "varuna-alpha-person-007",
        "observation_id": "alpha-person-track-7",
        "frame_id": "alpha-frame-104",
        "modality": "rgb",
        "model_id": "sar-alert-rgb-k0",
        "model_sha256": None,
    }
    values.update(overrides)
    return build_observation_event(
        detection or Detection(cls=0, x=0.395, y=0.515, w=0.17, h=0.59, conf=0.87),
        **values,
    )


@pytest.mark.parametrize("label", ["person", "survivor", "person_candidate"])
def test_person_and_survivor_labels_emit_only_person_candidate(label):
    event = _event(class_map={0: label})
    assert event["kind"] == "observation"
    assert event["source"] == event["payload"]["node"] == "alpha"
    assert event["payload"]["class_id"] == "person_candidate"
    assert event["payload"]["confidence"] == 0.87
    assert event["payload"]["model_id"] == "sar-alert-rgb-k0"
    assert event["payload"]["model_sha256"] is None
    assert event["payload"]["bbox_norm"] == pytest.approx([0.31, 0.22, 0.48, 0.81])
    assert "position_ned" not in event["payload"]
    assert "uncertainty_m" not in event["payload"]


def test_hazard_detection_uses_observation_event_and_final_model_hash():
    model_hash = "A" * 64
    event = _event(
        Detection(cls=4, x=0.5, y=0.5, w=0.4, h=0.2, conf=0.94),
        class_map={4: "fire"},
        event_id="varuna-alpha-fire-008",
        observation_id="alpha-fire-8",
        source_seq=8,
        model_id="sar-fire-smoke-v1",
        model_sha256=model_hash,
    )
    assert event["kind"] == "observation"
    assert event["payload"]["class_id"] == "fire"
    assert event["payload"]["model_sha256"] == model_hash.lower()


def test_ned_position_is_emitted_only_with_fusion_uncertainty():
    event = _event(position_ned=(42.1, 18.7, 0.0), uncertainty_m=3.2)
    assert event["payload"]["position_ned"] == [42.1, 18.7, 0.0]
    assert event["payload"]["uncertainty_m"] == 3.2

    with pytest.raises(PerceptionAdapterError, match="supplied together"):
        _event(position_ned=(42.1, 18.7, 0.0))
    with pytest.raises(PerceptionAdapterError, match="supplied together"):
        _event(uncertainty_m=3.2)


def test_box_is_clipped_to_image_and_zero_area_fails_closed():
    clipped = detection_bbox_norm(
        Detection(cls=0, x=0.05, y=0.95, w=0.2, h=0.2, conf=0.8)
    )
    assert clipped == pytest.approx([0.0, 0.85, 0.15, 1.0])

    with pytest.raises(PerceptionAdapterError, match="positive visible box area"):
        detection_bbox_norm(Detection(cls=0, x=0.5, y=0.5, w=0.0, h=0.2, conf=0.8))


def test_unknown_or_unmapped_model_classes_fail_closed():
    with pytest.raises(PerceptionAdapterError, match="missing from the frozen class map"):
        _event(class_map={1: "fire"})
    with pytest.raises(PerceptionAdapterError, match="unsupported rescue class"):
        _event(class_map={0: "confirmed_survivor"})


def test_adapter_output_ingests_without_schema_translation(tmp_path):
    collector = RescueCollector("OP-VARUNA-001", tmp_path / "rescue.jsonl")
    result = collector.collect(_event())
    assert result.accepted and not result.duplicate
    report = collector.report()
    assert report["summary"]["person_candidates"] == 1
    assert report["people"][0]["position_ned"] is None
    assert report["people"][0]["uncertainty_m"] is None


def test_invalid_final_hash_is_rejected_before_handoff():
    with pytest.raises(PerceptionAdapterError, match="model_sha256"):
        _event(model_sha256="not-a-sha256")


def test_canonical_class_normalization_is_deliberately_narrow():
    assert canonical_rescue_class("Road Blocked") == "road_blocked"
    with pytest.raises(PerceptionAdapterError, match="unsupported rescue class"):
        canonical_rescue_class("person_of_interest")
