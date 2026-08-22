from __future__ import annotations

import json
from pathlib import Path

import pytest

from node.events import verify_event_chain
from rescue.collector import RescueCollector, RescueCollectorError
from rescue.schema import RESCUE_SCHEMA, RescueEventError, validate_rescue_event


def _event(kind: str, payload: dict, *, source="alpha", seq=1, event_id=None) -> dict:
    return {
        "schema": RESCUE_SCHEMA,
        "mission_id": "OP-VARUNA-001",
        "event_id": event_id or f"{source}-{kind}-{seq}",
        "source": source,
        "source_seq": seq,
        "observed_at_ms": 1_787_394_600_000 + seq,
        "kind": kind,
        "payload": payload,
    }


def _observation(
    *,
    node="alpha",
    observation_id="track-1",
    confidence=0.7,
    position=(10.0, 10.0, 0.0),
) -> dict:
    payload = {
        "node": node,
        "observation_id": observation_id,
        "class_id": "person_candidate",
        "confidence": confidence,
        "frame_id": f"{node}-frame-1",
        "modality": "rgb",
        "model_id": "sar-alert-rgb-k0",
        "model_sha256": None,
        "bbox_norm": [0.2, 0.1, 0.5, 0.9],
    }
    if position is not None:
        payload["position_ned"] = list(position)
        payload["uncertainty_m"] = 2.0
    return payload


def test_observation_normalizes_and_allows_development_model_without_hash():
    event = validate_rescue_event(_event("observation", _observation()))
    assert event["payload"]["model_sha256"] is None
    assert event["payload"]["bbox_norm"] == [0.2, 0.1, 0.5, 0.9]


@pytest.mark.parametrize(
    "mutation, message",
    [
        (lambda event: event.update(schema="wrong"), "schema must be"),
        (lambda event: event.update(mission_id="../unsafe"), "mission_id"),
        (lambda event: event.update(source_seq=0), "source_seq"),
        (lambda event: event["payload"].update(confidence=float("nan")), "finite"),
        (lambda event: event["payload"].update(bbox_norm=[0.5, 0.2, 0.4, 0.9]), "x2>x1"),
        (lambda event: event["payload"].update(class_id="confirmed_survivor"), "unsupported"),
    ],
)
def test_invalid_observation_fails_closed(mutation, message):
    event = _event("observation", _observation())
    mutation(event)
    with pytest.raises(RescueEventError, match=message):
        validate_rescue_event(event, expected_mission_id="OP-VARUNA-001")


def test_collector_is_idempotent_and_preserves_event_chain(tmp_path):
    path = tmp_path / "rescue.jsonl"
    collector = RescueCollector("OP-VARUNA-001", path)
    event = _event("observation", _observation())

    first = collector.collect(event)
    second = collector.collect(event)

    assert first.accepted and not first.duplicate
    assert second.accepted and second.duplicate
    assert collector.state()["events_applied"] == 1
    assert verify_event_chain(path) == (True, "ok")


def test_event_id_reuse_with_different_content_is_rejected(tmp_path):
    collector = RescueCollector("OP-VARUNA-001", tmp_path / "rescue.jsonl")
    first = _event("observation", _observation(confidence=0.7), event_id="fixed-id")
    second = _event("observation", _observation(confidence=0.9), event_id="fixed-id")
    collector.collect(first)
    with pytest.raises(RescueCollectorError, match="event_id_reused"):
        collector.collect(second)


def test_source_sequence_reversal_is_rejected_but_forward_gap_warns(tmp_path):
    collector = RescueCollector("OP-VARUNA-001", tmp_path / "rescue.jsonl")
    collector.collect(_event("observation", _observation(observation_id="a"), seq=2))
    with pytest.raises(RescueCollectorError, match="not_monotonic"):
        collector.collect(_event("observation", _observation(observation_id="b"), seq=1))
    result = collector.collect(_event("observation", _observation(observation_id="c"), seq=5))
    assert result.warning == "source_sequence_gap:expected=3,got=5"


def test_collector_rejects_another_mission(tmp_path):
    collector = RescueCollector("OP-VARUNA-001", tmp_path / "rescue.jsonl")
    event = _event("observation", _observation())
    event["mission_id"] = "OTHER-MISSION"
    with pytest.raises(RescueEventError, match="mission_id mismatch"):
        collector.collect(event)


def test_telemetry_source_must_match_payload_node(tmp_path):
    collector = RescueCollector("OP-VARUNA-001", tmp_path / "rescue.jsonl")
    event = _event("observation", _observation(node="bravo"), source="alpha")
    with pytest.raises(RescueEventError, match="not an authorized stream"):
        collector.collect(event)


@pytest.mark.parametrize(
    "kind, source, payload",
    [
        (
            "observation",
            "alpha.perception",
            _observation(node="alpha", observation_id="role-source-person"),
        ),
        (
            "coverage",
            "alpha.telemetry",
            {
                "node": "alpha",
                "sector_id": "sector-a",
                "visited_cells": 2,
                "total_cells": 10,
            },
        ),
        (
            "hazard",
            "alpha.fusion",
            {
                "node": "alpha",
                "hazard_id": "fire-1",
                "class_id": "fire",
                "confidence": 0.9,
                "position_ned": [1.0, 2.0, 0.0],
                "uncertainty_m": 1.0,
            },
        ),
    ],
)
def test_role_scoped_node_sources_have_independent_sequences(kind, source, payload):
    event = validate_rescue_event(_event(kind, payload, source=source, seq=1))
    assert event["source"] == source


@pytest.mark.parametrize(
    "kind, source",
    [
        ("observation", "alpha.telemetry"),
        ("coverage", "alpha.perception"),
        ("hazard", "bravo.fusion"),
    ],
)
def test_wrong_role_or_node_cannot_publish_node_events(kind, source):
    payloads = {
        "observation": _observation(node="alpha"),
        "coverage": {
            "node": "alpha",
            "sector_id": "sector-a",
            "visited_cells": 2,
            "total_cells": 10,
        },
        "hazard": {
            "node": "alpha",
            "hazard_id": "fire-1",
            "class_id": "fire",
            "confidence": 0.9,
            "position_ned": [1.0, 2.0, 0.0],
        },
    }
    with pytest.raises(RescueEventError, match="not an authorized stream"):
        validate_rescue_event(_event(kind, payloads[kind], source=source, seq=1))


def test_nearby_person_observations_merge_and_retain_sources(tmp_path):
    collector = RescueCollector("OP-VARUNA-001", tmp_path / "rescue.jsonl")
    collector.collect(_event(
        "observation",
        _observation(node="alpha", observation_id="a", confidence=0.7, position=(10, 10, 0)),
        source="alpha",
        seq=1,
    ))
    collector.collect(_event(
        "observation",
        _observation(node="bravo", observation_id="b", confidence=0.75, position=(12, 11, 0)),
        source="bravo",
        seq=1,
    ))

    state = collector.state()
    assert len(state["people"]) == 1
    assert state["people"][0]["sources"] == ["alpha", "bravo"]
    assert state["alerts"][0]["priority"] == "HIGH"


def test_non_geolocated_people_are_not_merged_from_image_boxes(tmp_path):
    collector = RescueCollector("OP-VARUNA-001", tmp_path / "rescue.jsonl")
    collector.collect(_event(
        "observation",
        _observation(observation_id="a", position=None),
        source="alpha",
        seq=1,
    ))
    collector.collect(_event(
        "observation",
        _observation(node="bravo", observation_id="b", position=None),
        source="bravo",
        seq=1,
    ))
    assert len(collector.state()["people"]) == 2


def test_projection_builds_coverage_hazard_authorization_and_report(tmp_path):
    collector = RescueCollector("OP-VARUNA-001", tmp_path / "rescue.jsonl")
    collector.collect(_event(
        "mission_started",
        {"scenario_id": "varuna-v1", "coordinate_frame": "NED"},
        source="c2",
        seq=1,
    ))
    collector.collect(_event(
        "coverage",
        {"node": "alpha", "sector_id": "sector-a", "visited_cells": 20, "total_cells": 40},
        source="alpha",
        seq=1,
    ))
    collector.collect(_event(
        "hazard",
        {
            "node": "delta",
            "hazard_id": "fire-1",
            "class_id": "fire",
            "confidence": 0.91,
            "position_ned": [20, 8, 0],
        },
        source="delta",
        seq=1,
    ))
    collector.collect(_event(
        "authorization",
        {"node": "bravo", "decision": "QUARANTINE", "reason": "model_hash_not_approved"},
        source="consensus",
        seq=1,
    ))

    report = collector.report()
    assert report["coverage"]["percent"] == 50.0
    assert report["summary"]["mapped_hazards"] == 1
    assert report["summary"]["critical_alerts"] == 2
    assert report["vehicles"][0]["state"] == "QUARANTINED"
    assert "responder confirmation" in report["limitations"][0]


def test_collector_restores_projection_from_existing_log(tmp_path):
    path = tmp_path / "rescue.jsonl"
    first = RescueCollector("OP-VARUNA-001", path)
    first.collect(_event("observation", _observation()))

    restored = RescueCollector("OP-VARUNA-001", path)
    assert restored.state()["events_applied"] == 1
    assert len(restored.report()["people"]) == 1


def test_collector_refuses_a_tampered_existing_log(tmp_path):
    path = tmp_path / "rescue.jsonl"
    collector = RescueCollector("OP-VARUNA-001", path)
    collector.collect(_event("observation", _observation()))
    path.write_text(
        path.read_text(encoding="utf-8").replace("person_candidate", "road_blocked"),
        encoding="utf-8",
    )
    with pytest.raises(RescueCollectorError, match="event_log_chain_invalid"):
        RescueCollector("OP-VARUNA-001", path)


def test_sample_file_ingests_and_generates_report(tmp_path):
    source = Path(__file__).parents[1] / "examples" / "rescue_event_sample.jsonl"
    collector = RescueCollector("OP-VARUNA-001", tmp_path / "rescue.jsonl")
    for line in source.read_text(encoding="utf-8").splitlines():
        collector.collect(json.loads(line))
    report = collector.report()
    assert report["summary"]["person_candidates"] == 1
    assert report["summary"]["mapped_hazards"] == 1
    assert report["coverage"]["visited_cells"] == 18
