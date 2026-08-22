from __future__ import annotations

import json
from pathlib import Path

import pytest

from rescue_training.artifact_io import canonical_json_bytes, sha256_file
from rescue_training.evaluation import (
    DATASET_MANIFEST_SCHEMA,
    EvaluationError,
    create_evaluation_dataset_manifest,
    generate_validation_report,
    load_validation_report,
    run_detector_inference,
)
from rescue_training.selection import SelectionError, freeze_validation_threshold


def image(image_id: str, boxes: list[dict], *, width: int = 640, height: int = 640) -> dict:
    return {
        "image_id": image_id,
        "evaluation_width": width,
        "evaluation_height": height,
        "boxes": boxes,
    }


def gt_abs(x1: float, y1: float, x2: float, y2: float) -> dict:
    return {
        "class_id": 0,
        "coordinate_format": "xyxy_abs",
        "bbox": [x1, y1, x2, y2],
    }


def gt_norm(cx: float, cy: float, width: float, height: float) -> dict:
    return {
        "class_id": 0,
        "coordinate_format": "xywh_normalized",
        "bbox": [cx, cy, width, height],
    }


def prediction(box: dict, confidence: float) -> dict:
    return {**box, "confidence": confidence}


def make_inputs(
    tmp_path: Path,
    ground_truth: list[dict],
    predictions: list[dict],
    *,
    split: str = "val",
    data_kind: str = "real_aerial",
) -> dict:
    repository = tmp_path / "repo"
    workspace = tmp_path / "external"
    repository.mkdir(parents=True)
    workspace.mkdir(parents=True)
    model = workspace / "models" / "best.pt"
    model.parent.mkdir()
    model.write_bytes(b"local-verified-checkpoint")

    source_evidence = workspace / "manifests" / "source.json"
    source_evidence.parent.mkdir()
    source_evidence.write_text(
        json.dumps({"schema": "veriswarm.test.source.v1", "passed": True}),
        encoding="utf-8",
    )

    bound_ground_truth = []
    for index, record in enumerate(ground_truth):
        source_image = workspace / "images" / f"{record['image_id']}-{index}.jpg"
        source_image.parent.mkdir(parents=True, exist_ok=True)
        source_image.write_bytes(f"fixture-image:{record['image_id']}:{index}".encode())
        bound_ground_truth.append(
            {
                **record,
                "image_path": str(source_image.resolve()),
                "image_sha256": sha256_file(source_image),
            }
        )

    manifest = workspace / "manifests" / "dataset.json"
    create_evaluation_dataset_manifest(
        dataset_id=f"fixture-{data_kind}-{split}",
        split=split,
        data_kind=data_kind,
        ground_truth_records=bound_ground_truth,
        source_evidence_path=source_evidence,
        source_evidence_schema="veriswarm.test.source.v1",
        records_path=workspace / "manifests" / "ground-truth.json",
        manifest_path=manifest,
        workspace_root=workspace,
        repository_root=repository,
    )

    class Tensor:
        def __init__(self, value):
            self.value = value

        def detach(self):
            return self

        def cpu(self):
            return self

        def tolist(self):
            return self.value

    class Boxes:
        def __init__(self, rows, width, height):
            converted = []
            confidences = []
            for raw in rows:
                values = raw["bbox"]
                if raw["coordinate_format"] == "xywh_normalized":
                    cx, cy, bw, bh = values
                    values = [
                        (cx - bw / 2) * width,
                        (cy - bh / 2) * height,
                        (cx + bw / 2) * width,
                        (cy + bh / 2) * height,
                    ]
                converted.append(values)
                confidences.append(raw["confidence"])
            self.xyxy = Tensor(converted)
            self.conf = Tensor(confidences)
            self.cls = Tensor([row["class_id"] for row in rows])

    class FakeModel:
        names = {0: "person_candidate"}
        task = "detect"

        def __init__(self):
            self.index = 0

        def predict(self, *, source, **kwargs):
            prediction_record = predictions[self.index]
            self.index += 1
            height = prediction_record["evaluation_height"]
            width = prediction_record["evaluation_width"]
            return [
                type(
                    "Result",
                    (),
                    {
                        "orig_shape": (height, width),
                        "boxes": Boxes(prediction_record["boxes"], width, height),
                    },
                )()
            ]

    inference_report = workspace / "reports" / "inference.json"
    run_detector_inference(
        report_id=f"inference-{data_kind}-{split}",
        candidate="yolov8n-640",
        candidate_stage="full",
        split=split,
        data_kind=data_kind,
        model_checkpoint=model,
        expected_model_sha256=sha256_file(model),
        training_plan_sha256="a" * 64,
        dataset_manifest=manifest,
        expected_dataset_manifest_sha256=sha256_file(manifest),
        prediction_records_path=workspace / "reports" / "predictions.json",
        inference_report_path=inference_report,
        workspace_root=workspace,
        repository_root=repository,
        model_factory=lambda path: FakeModel(),
        now=iter(
            ("2026-08-22T10:00:00+00:00", "2026-08-22T10:01:00+00:00")
        ).__next__,
    )
    return {
        "candidate": "yolov8n-640",
        "candidate_stage": "full",
        "split": split,
        "data_kind": data_kind,
        "model_checkpoint": model,
        "expected_model_sha256": sha256_file(model),
        "training_plan_sha256": "a" * 64,
        "dataset_manifest": manifest,
        "expected_dataset_manifest_sha256": sha256_file(manifest),
        "inference_report": inference_report,
        "confidence_thresholds": [0.5],
        "output_path": workspace / "reports" / "validation.json",
        "workspace_root": workspace,
        "repository_root": repository,
    }


def test_duplicate_prediction_is_false_positive_and_cannot_reuse_ground_truth(tmp_path):
    truth_box = gt_abs(10, 10, 30, 30)
    ground_truth = [image("a", [truth_box])]
    predictions = [
        image(
            "a",
            [prediction(truth_box, 0.9), prediction(truth_box, 0.8)],
        )
    ]
    report = generate_validation_report(**make_inputs(tmp_path, ground_truth, predictions))
    point = report.threshold_points[0]
    assert point.precision == pytest.approx(0.5)
    assert point.recall == pytest.approx(1.0)
    assert report.aggregate_map50 == pytest.approx(1.0)


def test_one_prediction_cannot_match_two_ground_truth_boxes(tmp_path):
    box = gt_abs(10, 10, 30, 30)
    ground_truth = [image("a", [box, box])]
    predictions = [image("a", [prediction(box, 0.9)])]
    report = generate_validation_report(**make_inputs(tmp_path, ground_truth, predictions))
    assert report.threshold_points[0].precision == pytest.approx(1.0)
    assert report.threshold_points[0].recall == pytest.approx(0.5)


def test_confidence_threshold_is_inclusive_and_grid_is_preserved(tmp_path):
    box = gt_abs(10, 10, 30, 30)
    ground_truth = [image("a", [box])]
    predictions = [image("a", [prediction(box, 0.5)])]
    arguments = make_inputs(tmp_path, ground_truth, predictions)
    arguments["confidence_thresholds"] = [0.5, 0.500001]
    report = generate_validation_report(**arguments)
    assert [point.threshold for point in report.threshold_points] == [0.5, 0.500001]
    assert report.threshold_points[0].recall == 1.0
    assert report.threshold_points[1].recall == 0.0


def test_small_person_is_inclusive_1024_pixels_at_actual_resolution(tmp_path):
    small = gt_abs(0, 0, 32, 32)
    large = gt_abs(100, 100, 133, 132)
    ground_truth = [image("a", [small, large], width=960, height=960)]
    predictions = [
        image(
            "a",
            [prediction(small, 0.9)],
            width=960,
            height=960,
        )
    ]
    report = generate_validation_report(**make_inputs(tmp_path, ground_truth, predictions))
    point = report.threshold_points[0]
    assert point.recall == pytest.approx(0.5)
    assert point.small_person_recall == pytest.approx(1.0)


def test_normalized_ground_truth_and_prediction_use_evaluation_resolution(tmp_path):
    normalized = gt_norm(0.5, 0.5, 32 / 640, 32 / 640)
    ground_truth = [image("a", [normalized])]
    predictions = [image("a", [prediction(normalized, 0.75)])]
    report = generate_validation_report(**make_inputs(tmp_path, ground_truth, predictions))
    assert report.threshold_points[0].small_person_recall == pytest.approx(1.0)
    assert report.aggregate_map50 == pytest.approx(1.0)


def test_empty_predictions_have_defined_zero_metrics(tmp_path):
    ground_truth = [image("a", [gt_abs(10, 10, 20, 20)])]
    predictions = [image("a", [])]
    report = generate_validation_report(**make_inputs(tmp_path, ground_truth, predictions))
    point = report.threshold_points[0]
    assert (point.precision, point.recall, point.small_person_recall) == (0.0, 0.0, 0.0)
    assert report.aggregate_map50 == 0.0


def test_higher_confidence_false_positive_reduces_interpolated_ap(tmp_path):
    truth = gt_abs(10, 10, 20, 20)
    false_box = gt_abs(100, 100, 110, 110)
    ground_truth = [image("a", [truth])]
    predictions = [
        image(
            "a",
            [prediction(false_box, 0.9), prediction(truth, 0.8)],
        )
    ]
    report = generate_validation_report(**make_inputs(tmp_path, ground_truth, predictions))
    assert report.aggregate_map50 == pytest.approx(0.5)


def test_test_split_can_be_reported_but_not_used_for_selection(tmp_path):
    box = gt_abs(10, 10, 20, 20)
    ground_truth = [image("a", [box])]
    predictions = [image("a", [prediction(box, 0.9)])]
    report = generate_validation_report(
        **make_inputs(tmp_path, ground_truth, predictions, split="test")
    )
    assert report.split == "test"
    with pytest.raises(SelectionError, match="validation only"):
        freeze_validation_threshold(report)


@pytest.mark.parametrize(
    "bad_box",
    [
        {
            "class_id": 0,
            "coordinate_format": "xyxy_abs",
            "bbox": [float("nan"), 0, 10, 10],
            "confidence": 0.8,
        },
        prediction(gt_abs(20, 20, 10, 30), 0.8),
        prediction(gt_abs(-1, 0, 10, 10), 0.8),
    ],
)
def test_nonfinite_or_invalid_prediction_boxes_are_rejected(tmp_path, bad_box):
    ground_truth = [image("a", [gt_abs(10, 10, 20, 20)])]
    predictions = [image("a", [bad_box])]
    with pytest.raises(EvaluationError):
        generate_validation_report(**make_inputs(tmp_path, ground_truth, predictions))


def test_model_hash_mismatch_fails_before_evaluation(tmp_path):
    box = gt_abs(10, 10, 20, 20)
    arguments = make_inputs(
        tmp_path,
        [image("a", [box])],
        [image("a", [prediction(box, 0.9)])],
    )
    arguments["expected_model_sha256"] = "0" * 64
    with pytest.raises(EvaluationError, match="checkpoint SHA-256 mismatch"):
        generate_validation_report(**arguments)
    assert not arguments["output_path"].exists()


def test_manifest_hash_and_ground_truth_binding_are_enforced(tmp_path):
    box = gt_abs(10, 10, 20, 20)
    arguments = make_inputs(
        tmp_path,
        [image("a", [box])],
        [image("a", [prediction(box, 0.9)])],
    )
    arguments["expected_dataset_manifest_sha256"] = "0" * 64
    with pytest.raises(EvaluationError, match="manifest SHA-256 mismatch"):
        generate_validation_report(**arguments)

    arguments = make_inputs(
        tmp_path / "second",
        [image("a", [box])],
        [image("a", [prediction(box, 0.9)])],
    )
    manifest_payload = json.loads(
        arguments["dataset_manifest"].read_text(encoding="utf-8")
    )
    ground_truth_path = Path(manifest_payload["ground_truth_records_path"])
    ground_truth_payload = json.loads(ground_truth_path.read_text(encoding="utf-8"))
    ground_truth_payload[0]["boxes"] = []
    ground_truth_path.write_text(json.dumps(ground_truth_payload), encoding="utf-8")
    with pytest.raises(EvaluationError, match="ground-truth record file SHA-256"):
        generate_validation_report(**arguments)


def test_validation_report_binds_actual_inference_artifacts(tmp_path):
    box = gt_abs(10, 10, 20, 20)
    arguments = make_inputs(
        tmp_path,
        [image("a", [box])],
        [image("a", [prediction(box, 0.9)])],
    )
    report = generate_validation_report(**arguments)
    written = json.loads(arguments["output_path"].read_text(encoding="utf-8"))
    inference = json.loads(arguments["inference_report"].read_text(encoding="utf-8"))
    evidence = written["inference_evidence"]
    assert report.inference_evidence.to_dict() == evidence
    assert evidence["report_sha256"] == sha256_file(arguments["inference_report"])
    assert evidence["prediction_records_sha256"] == inference["prediction_records"]["sha256"]
    assert evidence["preprocess"] == {
        "coordinate_space": "original_source_pixels",
        "resize": "ultralytics_letterbox",
        "input_shape_nchw": [1, 3, 640, 640],
    }


def test_tampered_prediction_evidence_is_rejected(tmp_path):
    box = gt_abs(10, 10, 20, 20)
    arguments = make_inputs(
        tmp_path,
        [image("a", [box])],
        [image("a", [prediction(box, 0.9)])],
    )
    inference = json.loads(arguments["inference_report"].read_text(encoding="utf-8"))
    prediction_path = Path(inference["prediction_records"]["path"])
    prediction_path.write_text("[]\n", encoding="utf-8")
    with pytest.raises(EvaluationError, match="prediction record SHA-256 mismatch"):
        generate_validation_report(**arguments)


def test_tampered_source_image_is_rejected(tmp_path):
    box = gt_abs(10, 10, 20, 20)
    arguments = make_inputs(
        tmp_path,
        [image("a", [box])],
        [image("a", [prediction(box, 0.9)])],
    )
    manifest = json.loads(arguments["dataset_manifest"].read_text(encoding="utf-8"))
    records = json.loads(Path(manifest["ground_truth_records_path"]).read_text(encoding="utf-8"))
    Path(records[0]["image_path"]).write_bytes(b"tampered-source-image")
    with pytest.raises(EvaluationError, match="image SHA-256 mismatch"):
        generate_validation_report(**arguments)


def test_report_is_create_once_and_real_synthetic_domains_are_not_blended(tmp_path):
    box = gt_abs(10, 10, 20, 20)
    arguments = make_inputs(
        tmp_path,
        [image("a", [box])],
        [image("a", [prediction(box, 0.9)])],
        data_kind="synthetic_disaster",
    )
    report = generate_validation_report(**arguments)
    assert report.data_kind == "synthetic_disaster"
    written = json.loads(arguments["output_path"].read_text(encoding="utf-8"))
    assert written["data_kind"] == "synthetic_disaster"
    assert "real_aerial" not in written
    with pytest.raises(EvaluationError, match="overwrite"):
        generate_validation_report(**arguments)


def test_manifest_rejects_green_red_detector_classes(tmp_path):
    box = gt_abs(10, 10, 20, 20)
    arguments = make_inputs(
        tmp_path,
        [image("a", [box])],
        [image("a", [prediction(box, 0.9)])],
    )
    manifest = arguments["dataset_manifest"]
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["class_map"] = {"0": "safe", "1": "disaster"}
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    arguments["expected_dataset_manifest_sha256"] = sha256_file(manifest)
    with pytest.raises(EvaluationError, match="class_map"):
        generate_validation_report(**arguments)


def test_loader_rederives_metrics_and_rejects_fabricated_numbers(tmp_path):
    box = gt_abs(10, 10, 20, 20)
    arguments = make_inputs(
        tmp_path,
        [image("a", [box])],
        [image("a", [prediction(box, 0.9)])],
    )
    generate_validation_report(**arguments)
    payload = json.loads(arguments["output_path"].read_text(encoding="utf-8"))
    payload["threshold_points"][0]["recall"] = 0.0
    arguments["output_path"].write_bytes(canonical_json_bytes(payload))

    with pytest.raises(EvaluationError, match="metric values differ"):
        load_validation_report(
            arguments["output_path"],
            workspace_root=arguments["workspace_root"],
            repository_root=arguments["repository_root"],
        )


def test_selection_revalidation_rejects_prediction_file_changed_after_report(tmp_path):
    box = gt_abs(10, 10, 20, 20)
    arguments = make_inputs(
        tmp_path,
        [image("a", [box])],
        [image("a", [prediction(box, 0.9)])],
    )
    report = generate_validation_report(**arguments)
    inference = json.loads(arguments["inference_report"].read_text(encoding="utf-8"))
    Path(inference["prediction_records"]["path"]).write_text("[]\n", encoding="utf-8")

    with pytest.raises(SelectionError, match="prediction record SHA-256 mismatch"):
        freeze_validation_threshold(report)


def test_durable_report_reload_is_rederived_and_selectable(tmp_path):
    box = gt_abs(10, 10, 20, 20)
    arguments = make_inputs(
        tmp_path,
        [image("a", [box])],
        [image("a", [prediction(box, 0.9)])],
    )
    generate_validation_report(**arguments)
    loaded = load_validation_report(
        arguments["output_path"],
        workspace_root=arguments["workspace_root"],
        repository_root=arguments["repository_root"],
    )

    frozen = freeze_validation_threshold(loaded)
    assert frozen.point.precision == 1.0
    assert frozen.point.recall == 1.0
    assert frozen.model_sha256 == arguments["expected_model_sha256"]
