from __future__ import annotations

import json

import pytest

from rescue_training.contracts import (
    FROZEN_GATES,
    FROZEN_RUNTIME,
    MODEL_ID,
    RUNPOD_WORKSPACE_ROOT,
    TRAINING_PLAN_SCHEMA,
    TrainingContractError,
    TrainingPlan,
)


def _candidate(
    name: str,
    architecture: str,
    imgsz: int,
    epochs: int,
    batch,
    required: bool,
    stage: str,
):
    return {
        "name": name,
        "architecture": architecture,
        "imgsz": imgsz,
        "epochs": epochs,
        "batch": batch,
        "required": required,
        "stage": stage,
    }


def _plan(*, include_optional: bool = True):
    candidates = {
        "yolov8n-640-smoke": _candidate(
            "yolov8n-640-smoke", "yolov8n.pt", 640, 5, 0.70, True, "smoke"
        ),
        "yolov8n-640": _candidate(
            "yolov8n-640", "yolov8n.pt", 640, 40, None, True, "full"
        ),
        "yolov8n-960": _candidate(
            "yolov8n-960", "yolov8n.pt", 960, 40, None, True, "full"
        ),
    }
    if include_optional:
        candidates["yolov8s-640"] = _candidate(
            "yolov8s-640", "yolov8s.pt", 640, 40, None, False, "full"
        )
    return {
        "schema": TRAINING_PLAN_SCHEMA,
        "model_id": MODEL_ID,
        "class_map": {"0": "person_candidate"},
        "runtime": dict(FROZEN_RUNTIME),
        "workspace_root": RUNPOD_WORKSPACE_ROOT,
        "candidates": candidates,
        "gates": dict(FROZEN_GATES),
    }


def test_strict_plan_round_trip_and_runner_api_are_immutable():
    plan = TrainingPlan.from_json_text(json.dumps(_plan()))

    assert plan.schema == TRAINING_PLAN_SCHEMA
    assert plan.model_id == "sar-rgb-person-v1"
    assert dict(plan.class_map) == {0: "person_candidate"}
    assert plan.runtime["required_gpu_substring"] == "RTX 5090"
    assert plan.candidate("yolov8n-640-smoke").batch == 0.70
    assert plan.candidate("yolov8n-960").imgsz == 960
    assert TrainingPlan.from_dict(plan.to_dict()).to_dict() == plan.to_dict()

    with pytest.raises(TypeError):
        plan.class_map[1] = "disaster"  # type: ignore[index]
    with pytest.raises(TypeError):
        plan.candidates["new"] = plan.candidate("yolov8n-640")  # type: ignore[index]
    with pytest.raises(TrainingContractError, match="unknown candidate"):
        plan.candidate("not-frozen")


def test_optional_yolov8s_candidate_may_be_omitted():
    plan = TrainingPlan.from_dict(_plan(include_optional=False))
    assert tuple(plan.candidates) == (
        "yolov8n-640",
        "yolov8n-640-smoke",
        "yolov8n-960",
    )


def test_full_batch_can_only_be_frozen_to_a_positive_integer_after_probe():
    payload = _plan()
    payload["candidates"]["yolov8n-640"]["batch"] = 24
    assert TrainingPlan.from_dict(payload).candidate("yolov8n-640").batch == 24

    payload = _plan()
    payload["candidates"]["yolov8n-640"]["batch"] = 0.70
    with pytest.raises(TrainingContractError, match="positive integer"):
        TrainingPlan.from_dict(payload)


def test_missing_or_modified_candidates_fail_closed():
    payload = _plan()
    del payload["candidates"]["yolov8n-960"]
    with pytest.raises(TrainingContractError, match="mandatory yolov8n"):
        TrainingPlan.from_dict(payload)

    payload = _plan()
    payload["candidates"]["yolov8n-640"]["epochs"] = 39
    with pytest.raises(TrainingContractError, match="not frozen"):
        TrainingPlan.from_dict(payload)


def test_state_classifier_and_extra_fields_are_forbidden():
    payload = _plan()
    payload["class_map"] = {"0": "safe_walking", "1": "disaster"}
    with pytest.raises(TrainingContractError, match="state classes are forbidden"):
        TrainingPlan.from_dict(payload)

    payload = _plan()
    payload["state_classifier"] = {"classes": ["safe_walking", "disaster"]}
    with pytest.raises(TrainingContractError, match="unknown=.*state_classifier"):
        TrainingPlan.from_dict(payload)


@pytest.mark.parametrize(
    ("field", "replacement", "message"),
    [
        ("runtime", {**dict(FROZEN_RUNTIME), "torch": "latest"}, "runtime"),
        ("workspace_root", "/tmp/model", "workspace_root"),
        ("gates", {**dict(FROZEN_GATES), "seed": 1}, "gates"),
    ],
)
def test_runtime_workspace_and_gates_are_exact(field, replacement, message):
    payload = _plan()
    payload[field] = replacement
    with pytest.raises(TrainingContractError, match=message):
        TrainingPlan.from_dict(payload)


def test_json_nan_is_rejected_and_load_rejects_symlink(tmp_path):
    with pytest.raises(TrainingContractError, match="non-finite"):
        TrainingPlan.from_json_text('{"loss": NaN}')
    with pytest.raises(TrainingContractError, match="duplicate JSON field"):
        TrainingPlan.from_json_text('{"schema": "first", "schema": "second"}')

    source = tmp_path / "plan.json"
    source.write_text(json.dumps(_plan()), encoding="utf-8")
    link = tmp_path / "plan-link.json"
    try:
        link.symlink_to(source)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(TrainingContractError, match="regular file"):
        TrainingPlan.load(link)
