"""
Tests for the perception -> action controller and provenance hashing.

These cover the dependency-free core (no YOLO / NumPy needed): the action
mapping that decides whether two drones "agree", and the weight-file hash that
backs the model-swap provenance check.
"""

from __future__ import annotations

import math
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from perception.yolo_action import (  # noqa: E402
    Detection,
    detections_to_action,
    model_hash,
)


def _l2(a, b):
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def test_clear_path_is_full_forward():
    assert detections_to_action([]) == (1.0, 0.0, 0.0)


def test_action_components_in_range():
    dets = [Detection(0, 0.5, 0.9, 0.9, 0.9, 0.99)]  # huge, close, central
    fwd, lat, vert = detections_to_action(dets)
    for v in (fwd, lat, vert):
        assert -1.0 <= v <= 1.0


def test_central_obstacle_slows_and_climbs():
    # Obstacle dead ahead, low in the frame -> slow down and climb, no big turn.
    dets = [Detection(0, 0.5, 0.75, 0.4, 0.4, 0.9)]
    fwd, lat, vert = detections_to_action(dets)
    assert fwd < 1.0          # slowed from full forward
    assert vert > 0.0         # climbing over a low obstacle
    assert abs(lat) < 0.1     # centred obstacle -> little lateral steer


def test_obstacle_on_right_steers_left():
    right = detections_to_action([Detection(0, 0.85, 0.5, 0.3, 0.3, 0.9)])
    left = detections_to_action([Detection(0, 0.15, 0.5, 0.3, 0.3, 0.9)])
    assert right[1] < 0.0     # obstacle on the right -> steer left (negative)
    assert left[1] > 0.0      # obstacle on the left  -> steer right (positive)


def test_fooled_drone_diverges_from_honest_peer():
    """The honest peer sees a strong central obstacle; the patched drone sees
    nothing. Their actions must differ by more than the semantic threshold."""
    honest = detections_to_action([Detection(0, 0.5, 0.7, 0.5, 0.5, 0.95)])
    fooled = detections_to_action([])  # patch made YOLO miss the obstacle
    assert _l2(honest, fooled) > 0.5


def test_model_hash_is_deterministic_and_content_sensitive(tmp_path):
    good = tmp_path / "yolov8n.pt"
    good.write_bytes(b"genuine-weights-blob" * 100)
    swapped = tmp_path / "yolov8n_backdoored.pt"
    swapped.write_bytes(b"tampered-weights-blob" * 100)

    assert model_hash(str(good)) == model_hash(str(good))     # deterministic
    assert model_hash(str(good)) != model_hash(str(swapped))  # swap is visible
    assert len(model_hash(str(good))) == 64                   # SHA-256 hex
