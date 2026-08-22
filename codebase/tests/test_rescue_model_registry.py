from __future__ import annotations

import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from tools.rescue_model_registry import (  # noqa: E402
    ModelRegistryError,
    load_model_registry,
    validate_model_class_map,
    verify_registry_weights,
)


def _record(**overrides):
    record = {
        "schema": "veriswarm.model.v1",
        "model_id": "sar-alert-rgb-k0",
        "task": "detect",
        "modality": "rgb",
        "classes": ["person_candidate", "fire", "smoke"],
        "weights_sha256": "a" * 64,
        "training_manifest_sha256": "b" * 64,
        "thresholds_sha256": "c" * 64,
        "framework": "ultralytics==8.4.56",
        "input": {"width": 960, "height": 960, "color": "RGB"},
        "status": "candidate",
    }
    record.update(overrides)
    return record


def _write(tmp_path, record):
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    return path


def test_loads_frozen_registry_and_normalizes_hashes(tmp_path):
    registry = load_model_registry(
        _write(tmp_path, _record(weights_sha256="A" * 64))
    )

    assert registry.model_id == "sar-alert-rgb-k0"
    assert registry.classes == ("person_candidate", "fire", "smoke")
    assert registry.weights_sha256 == "a" * 64
    assert registry.to_record()["input"]["color"] == "RGB"


def test_missing_registry_is_rejected(tmp_path):
    with pytest.raises(ModelRegistryError, match="not found"):
        load_model_registry(tmp_path / "missing.json")


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"schema": "veriswarm.model.v2"}, "unsupported.*schema"),
        ({"classes": ["chemical_leak"]}, "unsupported rescue class"),
        ({"classes": []}, "non-empty JSON array"),
        ({"weights_sha256": "wrong"}, "64 hex digits"),
        ({"input": {"width": 0, "height": 960, "color": "RGB"}}, "positive"),
        ({"task": "segment"}, "task='detect'"),
    ],
)
def test_rejects_unsupported_or_malformed_registry(tmp_path, overrides, message):
    with pytest.raises(ModelRegistryError, match=message):
        load_model_registry(_write(tmp_path, _record(**overrides)))


def test_loaded_model_class_map_must_exactly_match_registry(tmp_path):
    registry = load_model_registry(_write(tmp_path, _record()))

    validate_model_class_map(
        registry,
        ((0, "person_candidate"), (1, "fire"), (2, "smoke")),
    )
    with pytest.raises(ModelRegistryError, match="does not match"):
        validate_model_class_map(registry, ((0, "person"), (1, "fire")))


def test_wrong_weight_bytes_are_rejected(tmp_path):
    weights = tmp_path / "rescue.pt"
    weights.write_bytes(b"swapped model bytes")
    registry = load_model_registry(_write(tmp_path, _record()))

    with pytest.raises(ModelRegistryError, match="does not match"):
        verify_registry_weights(registry, weights)
