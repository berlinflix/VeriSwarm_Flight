from __future__ import annotations

import json
from urllib.request import urlopen

from rescue.collector import RescueCollector
from tools.covis_multicam_bridge import (
    DASHBOARD_SCHEMA,
    MultiCameraDashboardServer,
    dashboard_status_from_event,
)
from tools.covis_multicam_rescue_adapter import MultiCameraObservationAdapter


def _record(*, missing: tuple[str, ...] = (), sequence: int = 1) -> dict:
    cameras = {}
    for index, name in enumerate(("cam1", "cam2", "cam3", "cam4", "cam5")):
        if name in missing:
            cameras[name] = {
                "frame_sequence": None,
                "received_wall_ns": None,
                "detections": [],
                "class_names": [],
                "detector_error": "camera_offline",
            }
            continue
        cameras[name] = {
            "frame_sequence": sequence,
            "received_wall_ns": 1_787_420_000_000_000_000 + index,
            "detections": [
                {"cls": 0, "x": 0.5, "y": 0.5, "w": 0.2, "h": 0.5, "conf": 0.88}
            ] if name == "cam1" else [],
            "class_names": [[0, "person"]],
            "detector_error": None,
        }
    live = [name for name in cameras if name not in missing]
    pairs = []
    for left_index, left in enumerate(live):
        for right in live[left_index + 1:]:
            pairs.append({
                "camera_a": left,
                "camera_b": right,
                "displayed_decision": "AGREE",
                "displayed_reason": "semantic_agreement",
                "overlap_available": True,
                "appearance_assumption": {
                    "identity_proven": False,
                    "geometry_supported": True,
                    "assumed_same_object": True,
                },
            })
    return {
        "schema": "veriswarm.covis_multicam.v3",
        "run_id": "MULTICAM-TEST-01",
        "event_sequence": sequence,
        "recorded_at_utc": "2026-08-23T00:00:00Z",
        "model_id": "sar-yolo-test",
        "model_sha256": "f" * 64,
        "cameras": cameras,
        "pairs": pairs,
    }


def test_five_camera_status_is_live_and_read_only():
    status = dashboard_status_from_event(_record(), ["cam1", "cam2", "cam3", "cam4", "cam5"])
    assert status["schema"] == DASHBOARD_SCHEMA
    assert status["feed_state"] == "LIVE"
    assert status["connected_cameras"] == 5
    assert status["valid_pairs"] == 10
    assert status["person_candidates"] == 1
    assert status["read_only"] is True


def test_missing_cameras_are_reported_degraded_without_invented_pairs():
    status = dashboard_status_from_event(
        _record(missing=("cam4", "cam5")),
        ["cam1", "cam2", "cam3", "cam4", "cam5"],
    )
    assert status["feed_state"] == "DEGRADED"
    assert status["connected_cameras"] == 3
    assert status["reported_pairs"] == 3
    assert status["total_pairs"] == 10
    assert [row["status"] for row in status["cameras"]][-2:] == ["OFFLINE", "OFFLINE"]


def test_read_only_status_and_mjpeg_endpoints_serve_latest_measured_frame():
    server = MultiCameraDashboardServer("127.0.0.1", 0, ["cam1", "cam2", "cam3", "cam4", "cam5"])
    server.start()
    try:
        jpeg = b"\xff\xd8measured-jpeg\xff\xd9"
        server.state.publish_jpeg(jpeg, _record())
        host, port = server.address
        with urlopen(f"http://{host}:{port}/status", timeout=2) as response:  # noqa: S310
            payload = json.load(response)
        assert payload["ok"] is True
        assert payload["status"]["feed_state"] == "LIVE"
        with urlopen(f"http://{host}:{port}/stream.mjpg", timeout=2) as response:  # noqa: S310
            assert response.readline().startswith(b"--veriswarm-multicam-frame")
            assert response.readline().strip() == b"Content-Type: image/jpeg"
            content_length = int(response.readline().decode().split(":", 1)[1])
            assert response.readline() == b"\r\n"
            assert response.read(content_length) == jpeg
    finally:
        server.close()


def test_adapter_emits_valid_image_space_candidate_once_per_track(tmp_path):
    adapter = MultiCameraObservationAdapter(
        mission_id="OP-VARUNA-001",
        outbox_path=tmp_path / "samik-perception.sqlite3",
        camera_nodes={"cam1": "alpha"},
        camera_calibrations={"cam1": "lab-cam1-v1"},
        model_id="sar-yolo-test",
        publish_hz=2,
    )
    first = adapter.enqueue(_record(), now_ms=10_000)
    repeated = adapter.enqueue(_record(sequence=2), now_ms=10_600)
    assert len(first) == 1
    assert repeated == []
    event = first[0]
    assert event["source"] == "alpha.perception"
    assert event["source_seq"] == 1
    assert event["payload"]["class_id"] == "person_candidate"
    assert "position_ned" not in event["payload"]
    assert "capture_group_id" not in event["payload"]
    assert event["payload"]["evidence_security"] == "UNVERIFIED"


def test_adapter_sequence_survives_restart_and_exact_replay_is_idempotent(tmp_path):
    path = tmp_path / "samik-perception.sqlite3"
    settings = dict(
        mission_id="OP-VARUNA-001",
        outbox_path=path,
        camera_nodes={"cam1": "alpha"},
        camera_calibrations={},
        model_id="sar-yolo-test",
        publish_hz=2,
    )
    first_adapter = MultiCameraObservationAdapter(**settings)
    first = first_adapter.enqueue(_record(sequence=1), now_ms=10_000)[0]
    replay_adapter = MultiCameraObservationAdapter(**settings)
    replay = replay_adapter.enqueue(_record(sequence=1), now_ms=11_000)[0]
    assert replay == first
    next_adapter = MultiCameraObservationAdapter(**settings)
    second = next_adapter.enqueue(_record(sequence=2), now_ms=20_000)[0]
    assert second["source_seq"] == 2


def test_perception_and_pratik_telemetry_share_collector_without_collision(tmp_path):
    adapter = MultiCameraObservationAdapter(
        mission_id="OP-VARUNA-001",
        outbox_path=tmp_path / "samik.sqlite3",
        camera_nodes={"cam1": "alpha"},
        camera_calibrations={},
        model_id="sar-yolo-test",
    )
    person_event = adapter.enqueue(_record(), now_ms=10_000)[0]
    collector = RescueCollector("OP-VARUNA-001", tmp_path / "combined.jsonl")
    collector.collect(person_event)
    collector.collect({
        "schema": "veriswarm.rescue.event.v1",
        "mission_id": "OP-VARUNA-001",
        "event_id": "alpha-coverage-1",
        "source": "alpha.telemetry",
        "source_seq": 1,
        "observed_at_ms": 1_787_420_000_100,
        "kind": "coverage",
        "payload": {
            "node": "alpha",
            "sector_id": "sector-alpha",
            "visited_cells": 1,
            "total_cells": 2,
        },
    })
    state = collector.state()
    assert len(state["people"]) == 1
    assert state["coverage"]["visited_cells"] == 1
