"""Model-bound held-out evaluation for the rescue person detector.

Importing this module performs no I/O, download, model initialization, or GPU
work.  The production entry point does not accept caller-supplied prediction
boxes: it loads the exact hashed checkpoint, runs it over a hashed ordered
image corpus, writes create-once prediction evidence, and only then computes
metrics.  Unit tests inject a local model factory without weakening that path.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from .artifact_io import (
    ArtifactIOError,
    create_external_json,
    require_external_workspace,
    require_path_within_workspace,
    sha256_file,
)
from .selection import (
    DATA_KINDS,
    FULL_CANDIDATES,
    SMALL_PERSON_BASIS,
    SMALL_PERSON_MAX_AREA_PX2,
    SMOKE_CANDIDATE,
    VALIDATION_REPORT_SCHEMA,
    ValidationReport,
    _VALIDATION_ISSUANCE_TOKEN,
)


DATASET_MANIFEST_SCHEMA = "veriswarm.rescue.evaluation_dataset.v2"
INFERENCE_REPORT_SCHEMA = "veriswarm.rescue.detector_inference.v1"
IOU_THRESHOLD = 0.5
COORDINATE_FORMATS = frozenset({"xyxy_abs", "xywh_normalized"})
INFERENCE_CONFIDENCE_FLOOR = 0.001
_CANDIDATE_IDENTITIES = {
    "yolov8n-640-smoke": ("yolov8n.pt", [1, 3, 640, 640]),
    "yolov8n-640": ("yolov8n.pt", [1, 3, 640, 640]),
    "yolov8n-960": ("yolov8n.pt", [1, 3, 960, 960]),
    "yolov8s-640": ("yolov8s.pt", [1, 3, 640, 640]),
}

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_MANIFEST_FIELDS = frozenset(
    {
        "schema",
        "dataset_id",
        "dataset_sha256",
        "class_map",
        "split",
        "data_kind",
        "source_evidence",
        "ordered_image_ids",
        "ground_truth_records_path",
        "ground_truth_records_sha256",
    }
)
_GROUND_TRUTH_IMAGE_FIELDS = frozenset(
    {
        "image_id",
        "image_path",
        "image_sha256",
        "evaluation_width",
        "evaluation_height",
        "boxes",
    }
)
_PREDICTION_IMAGE_FIELDS = frozenset(
    {"image_id", "evaluation_width", "evaluation_height", "boxes"}
)
_GROUND_TRUTH_BOX_FIELDS = frozenset({"class_id", "coordinate_format", "bbox"})
_PREDICTION_BOX_FIELDS = frozenset(
    {"class_id", "coordinate_format", "bbox", "confidence"}
)
_INFERENCE_FIELDS = frozenset(
    {
        "schema",
        "report_id",
        "candidate",
        "candidate_stage",
        "architecture",
        "input_shape_nchw",
        "training_plan_sha256",
        "model",
        "dataset_manifest",
        "dataset_id",
        "dataset_sha256",
        "split",
        "data_kind",
        "ordered_image_ids_sha256",
        "ground_truth_records",
        "prediction_records",
        "preprocess",
        "inference",
        "started_at_utc",
        "completed_at_utc",
        "passed",
    }
)


class EvaluationError(ValueError):
    """Evaluation input or evidence violates the frozen contract."""


def _reject_constant(value: str) -> None:
    raise EvaluationError(f"non-finite JSON constant is forbidden: {value}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise EvaluationError(f"duplicate JSON object key is forbidden: {key!r}")
        result[key] = value
    return result


def _load_json_object(path: Path, field: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise EvaluationError(f"{field} must be one existing regular file: {path}")
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=_reject_constant,
            object_pairs_hook=_unique_object,
        )
    except EvaluationError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise EvaluationError(f"cannot read {field} {path}: {error}") from error
    if type(value) is not dict:
        raise EvaluationError(f"{field} must contain one JSON object")
    return value


def _exact_fields(value: Mapping[str, Any], expected: frozenset[str], field: str) -> None:
    actual = frozenset(value)
    if actual != expected:
        raise EvaluationError(
            f"{field} fields mismatch; missing={sorted(expected - actual)}, "
            f"unknown={sorted(actual - expected)}"
        )


def _trimmed_string(value: Any, field: str) -> str:
    if type(value) is not str or not value or value.strip() != value:
        raise EvaluationError(f"{field} must be a non-empty trimmed string")
    return value


def _sha256(value: Any, field: str) -> str:
    result = _trimmed_string(value, field)
    if _SHA256.fullmatch(result) is None:
        raise EvaluationError(f"{field} must be a lowercase SHA-256 hex digest")
    return result


def _finite_number(value: Any, field: str) -> float:
    if type(value) not in {int, float} or isinstance(value, bool):
        raise EvaluationError(f"{field} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise EvaluationError(f"{field} must be a finite number")
    return result


def _positive_integer(value: Any, field: str) -> int:
    if type(value) is not int or value <= 0:
        raise EvaluationError(f"{field} must be a positive integer")
    return value


def _canonical_value_bytes(value: Any) -> bytes:
    try:
        rendered = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as error:
        raise EvaluationError(f"records are not strict JSON: {error}") from error
    return rendered.encode("utf-8")


def canonical_records_sha256(records: Sequence[Mapping[str, Any]]) -> str:
    """Return the deterministic digest used to bind records to a manifest."""

    if isinstance(records, (str, bytes, bytearray)) or not isinstance(records, Sequence):
        raise EvaluationError("records must be an ordered JSON-like sequence")
    return hashlib.sha256(_canonical_value_bytes(list(records))).hexdigest()


def _load_json_records(path: Path, field: str) -> list[dict[str, Any]]:
    if path.is_symlink() or not path.is_file():
        raise EvaluationError(f"{field} must be one existing regular file: {path}")
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=_reject_constant,
            object_pairs_hook=_unique_object,
        )
    except EvaluationError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise EvaluationError(f"cannot read {field} {path}: {error}") from error
    if type(value) is not list or not all(type(item) is dict for item in value):
        raise EvaluationError(f"{field} must contain one JSON array of objects")
    return value


def _exclusive_json_records(path: Path, records: Sequence[Mapping[str, Any]]) -> None:
    if not path.is_absolute():
        raise EvaluationError("prediction record path must be absolute")
    if os.path.lexists(path):
        raise EvaluationError(f"refusing to overwrite prediction records: {path}")
    data = _canonical_value_bytes(list(records)) + b"\n"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as error:
        raise EvaluationError(f"cannot create prediction records {path}: {error}") from error


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _utc(value: Any, field: str) -> str:
    text = _trimmed_string(value, field)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise EvaluationError(f"{field} must be ISO-8601") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise EvaluationError(f"{field} must include a timezone")
    return text


def _tensor_rows(value: Any, field: str) -> list[Any]:
    """Convert an Ultralytics/Torch tensor-like value without importing Torch."""

    current = value
    for method in ("detach", "cpu"):
        callback = getattr(current, method, None)
        if callable(callback):
            current = callback()
    callback = getattr(current, "tolist", None)
    if callable(callback):
        current = callback()
    if type(current) is not list:
        raise EvaluationError(f"{field} must be tensor-like and convertible to a list")
    return current


def _dataset_semantic_sha256(records: Sequence[Mapping[str, Any]]) -> str:
    semantic: list[dict[str, Any]] = []
    for record in records:
        semantic.append(
            {
                "image_id": record.get("image_id"),
                "image_sha256": record.get("image_sha256"),
                "evaluation_width": record.get("evaluation_width"),
                "evaluation_height": record.get("evaluation_height"),
                "boxes": record.get("boxes"),
            }
        )
    return hashlib.sha256(_canonical_value_bytes(semantic)).hexdigest()


def _load_evaluation_dataset(
    *,
    manifest_path: Path,
    expected_manifest_sha256: str,
    workspace: Path,
    split: str,
    data_kind: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], Path, str, tuple[str, ...]]:
    actual_manifest_sha256 = sha256_file(manifest_path)
    if actual_manifest_sha256 != expected_manifest_sha256:
        raise EvaluationError(
            "dataset manifest SHA-256 mismatch; refusing unbound evaluation data"
        )
    manifest = _load_json_object(manifest_path, "dataset manifest")
    _exact_fields(manifest, _MANIFEST_FIELDS, "dataset manifest")
    if manifest["schema"] != DATASET_MANIFEST_SCHEMA:
        raise EvaluationError(f"unsupported dataset manifest schema: {manifest['schema']!r}")
    if manifest["class_map"] != {"0": "person_candidate"}:
        raise EvaluationError(
            "dataset class_map must be exactly {'0': 'person_candidate'}; "
            "safe/disaster state classification is separate"
        )
    if manifest["split"] != split:
        raise EvaluationError("requested split does not match dataset manifest")
    if manifest["data_kind"] != data_kind:
        raise EvaluationError("requested data_kind does not match dataset manifest")
    _trimmed_string(manifest["dataset_id"], "dataset_id")
    source_evidence = manifest["source_evidence"]
    if type(source_evidence) is not dict or set(source_evidence) != {
        "path", "sha256", "schema"
    }:
        raise EvaluationError("source_evidence fields differ")
    try:
        source_evidence_path = require_path_within_workspace(
            source_evidence["path"], workspace
        )
    except (ArtifactIOError, TypeError) as error:
        raise EvaluationError("source_evidence.path is unsafe") from error
    source_evidence_hash = _sha256(
        source_evidence["sha256"], "source_evidence.sha256"
    )
    if sha256_file(source_evidence_path) != source_evidence_hash:
        raise EvaluationError("source evidence SHA-256 mismatch")
    source_payload = _load_json_object(source_evidence_path, "source evidence")
    if source_payload.get("schema") != _trimmed_string(
        source_evidence["schema"], "source_evidence.schema"
    ):
        raise EvaluationError("source evidence schema differs from its manifest binding")
    expected_dataset_hash = _sha256(manifest["dataset_sha256"], "dataset_sha256")
    expected_records_hash = _sha256(
        manifest["ground_truth_records_sha256"], "ground_truth_records_sha256"
    )
    try:
        records_path = require_path_within_workspace(
            manifest["ground_truth_records_path"], workspace
        )
    except (ArtifactIOError, TypeError) as error:
        raise EvaluationError(f"ground-truth record path is unsafe: {error}") from error
    actual_records_hash = sha256_file(records_path)
    if actual_records_hash != expected_records_hash:
        raise EvaluationError("ground-truth record file SHA-256 mismatch")
    records = _load_json_records(records_path, "ground-truth records")
    ordered_raw = manifest["ordered_image_ids"]
    if type(ordered_raw) is not list or not ordered_raw:
        raise EvaluationError("ordered_image_ids must be a non-empty JSON array")
    ordered = tuple(
        _trimmed_string(value, f"ordered_image_ids[{index}]")
        for index, value in enumerate(ordered_raw)
    )
    if len(set(ordered)) != len(ordered):
        raise EvaluationError("ordered_image_ids must be unique")
    if [record.get("image_id") for record in records] != list(ordered):
        raise EvaluationError("ground-truth records do not preserve ordered_image_ids")
    if _dataset_semantic_sha256(records) != expected_dataset_hash:
        raise EvaluationError("dataset semantic SHA-256 mismatch")

    seen_image_hashes: set[str] = set()
    for index, record in enumerate(records):
        _exact_fields(record, _GROUND_TRUTH_IMAGE_FIELDS, f"ground_truth_records[{index}]")
        try:
            image_path = require_path_within_workspace(record["image_path"], workspace)
        except (ArtifactIOError, TypeError) as error:
            raise EvaluationError(f"ground_truth_records[{index}].image_path is unsafe") from error
        image_hash = _sha256(
            record["image_sha256"], f"ground_truth_records[{index}].image_sha256"
        )
        if image_hash in seen_image_hashes:
            raise EvaluationError("evaluation split contains duplicate image bytes")
        seen_image_hashes.add(image_hash)
        if sha256_file(image_path) != image_hash:
            raise EvaluationError(
                f"ground_truth_records[{index}] image SHA-256 mismatch"
            )
    # Parse once now to validate boxes and original-source dimensions.
    _parse_image_records(records, ordered_image_ids=ordered, prediction=False)
    if sha256_file(records_path) != actual_records_hash:
        raise EvaluationError("ground-truth records changed while being validated")
    if sha256_file(manifest_path) != actual_manifest_sha256:
        raise EvaluationError("dataset manifest changed while being validated")
    return manifest, records, records_path, actual_records_hash, ordered


def create_evaluation_dataset_manifest(
    *,
    dataset_id: str,
    split: Literal["val", "test"],
    data_kind: Literal["real_aerial", "synthetic_disaster"],
    ground_truth_records: Sequence[Mapping[str, Any]],
    source_evidence_path: str | os.PathLike[str],
    source_evidence_schema: str,
    records_path: str | os.PathLike[str],
    manifest_path: str | os.PathLike[str],
    workspace_root: str | os.PathLike[str],
    repository_root: str | os.PathLike[str],
) -> Path:
    """Create one hashed original-coordinate evaluation corpus manifest."""

    dataset_id = _trimmed_string(dataset_id, "dataset_id")
    source_evidence_schema = _trimmed_string(
        source_evidence_schema, "source_evidence_schema"
    )
    if split not in {"val", "test"}:
        raise EvaluationError("evaluation dataset split must be val or test")
    if data_kind not in DATA_KINDS:
        raise EvaluationError("evaluation dataset data_kind is unsupported")
    if (
        isinstance(ground_truth_records, (str, bytes, bytearray))
        or not isinstance(ground_truth_records, Sequence)
        or not ground_truth_records
        or not all(type(record) is dict for record in ground_truth_records)
    ):
        raise EvaluationError("ground_truth_records must be a non-empty sequence of objects")
    records = [dict(record) for record in ground_truth_records]
    ordered = tuple(
        _trimmed_string(record.get("image_id"), f"ground_truth_records[{index}].image_id")
        for index, record in enumerate(records)
    )
    if len(set(ordered)) != len(ordered):
        raise EvaluationError("evaluation image IDs must be unique")
    try:
        workspace = require_external_workspace(workspace_root, repository_root)
        output_records = require_path_within_workspace(records_path, workspace)
        output_manifest = require_path_within_workspace(manifest_path, workspace)
        source_path = require_path_within_workspace(source_evidence_path, workspace)
    except ArtifactIOError as error:
        raise EvaluationError(str(error)) from error
    source_hash = sha256_file(source_path)
    source_payload = _load_json_object(source_path, "source evidence")
    if source_payload.get("schema") != source_evidence_schema:
        raise EvaluationError("source evidence does not have the declared schema")
    seen_image_hashes: set[str] = set()
    for index, record in enumerate(records):
        _exact_fields(record, _GROUND_TRUTH_IMAGE_FIELDS, f"ground_truth_records[{index}]")
        try:
            image_path = require_path_within_workspace(record["image_path"], workspace)
        except (ArtifactIOError, TypeError) as error:
            raise EvaluationError(
                f"ground_truth_records[{index}].image_path is unsafe"
            ) from error
        expected_image_hash = _sha256(
            record["image_sha256"], f"ground_truth_records[{index}].image_sha256"
        )
        if expected_image_hash in seen_image_hashes:
            raise EvaluationError("evaluation split contains duplicate image bytes")
        seen_image_hashes.add(expected_image_hash)
        if sha256_file(image_path) != expected_image_hash:
            raise EvaluationError(f"ground_truth_records[{index}] image SHA-256 mismatch")
    _parse_image_records(records, ordered_image_ids=ordered, prediction=False)
    _exclusive_json_records(output_records, records)
    payload = {
        "schema": DATASET_MANIFEST_SCHEMA,
        "dataset_id": dataset_id,
        "dataset_sha256": _dataset_semantic_sha256(records),
        "class_map": {"0": "person_candidate"},
        "split": split,
        "data_kind": data_kind,
        "source_evidence": {
            "path": str(source_path),
            "sha256": source_hash,
            "schema": source_evidence_schema,
        },
        "ordered_image_ids": list(ordered),
        "ground_truth_records_path": str(output_records),
        "ground_truth_records_sha256": sha256_file(output_records),
    }
    try:
        created = create_external_json(
            output_manifest,
            payload,
            workspace_root=workspace,
            repository_root=repository_root,
        )
    except ArtifactIOError as error:
        raise EvaluationError(str(error)) from error
    _load_evaluation_dataset(
        manifest_path=created,
        expected_manifest_sha256=sha256_file(created),
        workspace=workspace,
        split=split,
        data_kind=data_kind,
    )
    return created


@dataclass(frozen=True, slots=True)
class _Box:
    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def area(self) -> float:
        return (self.x2 - self.x1) * (self.y2 - self.y1)


@dataclass(frozen=True, slots=True)
class _Prediction:
    box: _Box
    confidence: float
    image_index: int
    prediction_index: int


def _parse_box(
    raw_value: Any,
    *,
    width: int,
    height: int,
    field: str,
    prediction: bool,
) -> tuple[_Box, float | None]:
    if type(raw_value) is not dict:
        raise EvaluationError(f"{field} must be a JSON object")
    expected = _PREDICTION_BOX_FIELDS if prediction else _GROUND_TRUTH_BOX_FIELDS
    _exact_fields(raw_value, expected, field)
    if raw_value["class_id"] != 0 or type(raw_value["class_id"]) is not int:
        raise EvaluationError(f"{field}.class_id must be exactly integer 0")
    coordinate_format = raw_value["coordinate_format"]
    if coordinate_format not in COORDINATE_FORMATS:
        raise EvaluationError(
            f"{field}.coordinate_format must be 'xyxy_abs' or 'xywh_normalized'"
        )
    coordinates = raw_value["bbox"]
    if type(coordinates) is not list or len(coordinates) != 4:
        raise EvaluationError(f"{field}.bbox must be a four-number JSON array")
    values = tuple(
        _finite_number(value, f"{field}.bbox[{index}]")
        for index, value in enumerate(coordinates)
    )

    if coordinate_format == "xyxy_abs":
        x1, y1, x2, y2 = values
    else:
        center_x, center_y, box_width, box_height = values
        if not (
            0.0 <= center_x <= 1.0
            and 0.0 <= center_y <= 1.0
            and 0.0 < box_width <= 1.0
            and 0.0 < box_height <= 1.0
        ):
            raise EvaluationError(
                f"{field}.bbox normalized center/size values are out of range"
            )
        x1 = (center_x - box_width / 2.0) * width
        y1 = (center_y - box_height / 2.0) * height
        x2 = (center_x + box_width / 2.0) * width
        y2 = (center_y + box_height / 2.0) * height

    epsilon = 1e-9
    if not (
        -epsilon <= x1 < x2 <= width + epsilon
        and -epsilon <= y1 < y2 <= height + epsilon
    ):
        raise EvaluationError(f"{field}.bbox must lie within evaluation resolution")
    box = _Box(
        x1=max(0.0, x1),
        y1=max(0.0, y1),
        x2=min(float(width), x2),
        y2=min(float(height), y2),
    )
    confidence: float | None = None
    if prediction:
        confidence = _finite_number(raw_value["confidence"], f"{field}.confidence")
        if not 0.0 <= confidence <= 1.0:
            raise EvaluationError(f"{field}.confidence must be in [0, 1]")
    return box, confidence


def _parse_image_records(
    records: Sequence[Mapping[str, Any]],
    *,
    ordered_image_ids: tuple[str, ...],
    prediction: bool,
    expected_dimensions: tuple[tuple[int, int], ...] | None = None,
) -> tuple[tuple[tuple[_Box, ...], ...], tuple[tuple[_Prediction, ...], ...], tuple[tuple[int, int], ...]]:
    label = "prediction_records" if prediction else "ground_truth_records"
    if isinstance(records, (str, bytes, bytearray)) or not isinstance(records, Sequence):
        raise EvaluationError(f"{label} must be an ordered sequence")
    if len(records) != len(ordered_image_ids):
        raise EvaluationError(f"{label} must contain exactly one record per manifest image")

    ground_truth: list[tuple[_Box, ...]] = []
    predictions: list[tuple[_Prediction, ...]] = []
    dimensions: list[tuple[int, int]] = []
    for image_index, (raw, expected_id) in enumerate(zip(records, ordered_image_ids)):
        field = f"{label}[{image_index}]"
        if type(raw) is not dict:
            raise EvaluationError(f"{field} must be a JSON object")
        _exact_fields(
            raw,
            _PREDICTION_IMAGE_FIELDS if prediction else _GROUND_TRUTH_IMAGE_FIELDS,
            field,
        )
        image_id = _trimmed_string(raw["image_id"], f"{field}.image_id")
        if image_id != expected_id:
            raise EvaluationError(
                f"{field}.image_id must preserve manifest order; expected {expected_id!r}"
            )
        width = _positive_integer(raw["evaluation_width"], f"{field}.evaluation_width")
        height = _positive_integer(raw["evaluation_height"], f"{field}.evaluation_height")
        if expected_dimensions is not None and (width, height) != expected_dimensions[image_index]:
            raise EvaluationError(
                f"{field} evaluation resolution differs from ground truth"
            )
        boxes = raw["boxes"]
        if type(boxes) is not list:
            raise EvaluationError(f"{field}.boxes must be a JSON array")
        parsed_gt: list[_Box] = []
        parsed_predictions: list[_Prediction] = []
        for box_index, box_value in enumerate(boxes):
            box, confidence = _parse_box(
                box_value,
                width=width,
                height=height,
                field=f"{field}.boxes[{box_index}]",
                prediction=prediction,
            )
            if prediction:
                assert confidence is not None
                parsed_predictions.append(
                    _Prediction(box, confidence, image_index, box_index)
                )
            else:
                parsed_gt.append(box)
        ground_truth.append(tuple(parsed_gt))
        predictions.append(tuple(parsed_predictions))
        dimensions.append((width, height))
    return tuple(ground_truth), tuple(predictions), tuple(dimensions)


def _iou(first: _Box, second: _Box) -> float:
    intersection_width = max(0.0, min(first.x2, second.x2) - max(first.x1, second.x1))
    intersection_height = max(0.0, min(first.y2, second.y2) - max(first.y1, second.y1))
    intersection = intersection_width * intersection_height
    union = first.area + second.area - intersection
    return intersection / union if union > 0.0 else 0.0


def _ordered_predictions(
    predictions: tuple[tuple[_Prediction, ...], ...],
    minimum_confidence: float = 0.0,
) -> list[_Prediction]:
    return sorted(
        (
            prediction
            for image_predictions in predictions
            for prediction in image_predictions
            if prediction.confidence >= minimum_confidence
        ),
        key=lambda item: (-item.confidence, item.image_index, item.prediction_index),
    )


def _match(
    ground_truth: tuple[tuple[_Box, ...], ...],
    ordered_predictions: Sequence[_Prediction],
) -> tuple[list[int], list[int], int]:
    matched: list[set[int]] = [set() for _ in ground_truth]
    true_positives: list[int] = []
    false_positives: list[int] = []
    matched_small = 0
    for prediction in ordered_predictions:
        candidates: list[tuple[float, int]] = []
        for gt_index, truth in enumerate(ground_truth[prediction.image_index]):
            if gt_index in matched[prediction.image_index]:
                continue
            overlap = _iou(prediction.box, truth)
            if overlap >= IOU_THRESHOLD:
                candidates.append((overlap, gt_index))
        if candidates:
            _, gt_index = sorted(candidates, key=lambda item: (-item[0], item[1]))[0]
            matched[prediction.image_index].add(gt_index)
            true_positives.append(1)
            false_positives.append(0)
            if ground_truth[prediction.image_index][gt_index].area <= SMALL_PERSON_MAX_AREA_PX2:
                matched_small += 1
        else:
            true_positives.append(0)
            false_positives.append(1)
    return true_positives, false_positives, matched_small


def _average_precision(
    true_positives: Sequence[int], false_positives: Sequence[int], ground_truth_count: int
) -> float:
    if ground_truth_count == 0 or not true_positives:
        return 0.0
    cumulative_tp: list[int] = []
    cumulative_fp: list[int] = []
    tp_total = fp_total = 0
    for tp, fp in zip(true_positives, false_positives):
        tp_total += tp
        fp_total += fp
        cumulative_tp.append(tp_total)
        cumulative_fp.append(fp_total)
    recall = [value / ground_truth_count for value in cumulative_tp]
    precision = [
        tp / (tp + fp) if tp + fp else 0.0
        for tp, fp in zip(cumulative_tp, cumulative_fp)
    ]
    recall_envelope = [0.0, *recall, 1.0]
    precision_envelope = [0.0, *precision, 0.0]
    for index in range(len(precision_envelope) - 2, -1, -1):
        precision_envelope[index] = max(
            precision_envelope[index], precision_envelope[index + 1]
        )
    area = 0.0
    for index in range(1, len(recall_envelope)):
        delta = recall_envelope[index] - recall_envelope[index - 1]
        if delta > 0.0:
            area += delta * precision_envelope[index]
    return min(1.0, max(0.0, area))


def _parse_thresholds(values: Sequence[float]) -> tuple[float, ...]:
    if isinstance(values, (str, bytes, bytearray)) or not isinstance(values, Sequence):
        raise EvaluationError("confidence_thresholds must be an ordered sequence")
    thresholds = tuple(
        _finite_number(value, f"confidence_thresholds[{index}]")
        for index, value in enumerate(values)
    )
    if not thresholds:
        raise EvaluationError("confidence_thresholds must not be empty")
    if any(not 0.0 <= value <= 1.0 for value in thresholds):
        raise EvaluationError("confidence thresholds must be in [0, 1]")
    if list(thresholds) != sorted(thresholds) or len(set(thresholds)) != len(thresholds):
        raise EvaluationError("confidence thresholds must be unique and ascending")
    return thresholds


def _candidate_identity(
    candidate: str, candidate_stage: str
) -> tuple[str, list[int]]:
    if candidate not in FULL_CANDIDATES | {SMOKE_CANDIDATE}:
        raise EvaluationError(f"unknown detector candidate: {candidate!r}")
    expected_stage = "smoke" if candidate == SMOKE_CANDIDATE else "full"
    if candidate_stage != expected_stage:
        raise EvaluationError(
            f"candidate_stage for {candidate!r} must be {expected_stage!r}"
        )
    return _CANDIDATE_IDENTITIES[candidate]


def run_detector_inference(
    *,
    report_id: str,
    candidate: str,
    candidate_stage: Literal["full", "smoke"],
    split: Literal["val", "test"],
    data_kind: Literal["real_aerial", "synthetic_disaster"],
    model_checkpoint: str | os.PathLike[str],
    expected_model_sha256: str,
    training_plan_sha256: str,
    dataset_manifest: str | os.PathLike[str],
    expected_dataset_manifest_sha256: str,
    prediction_records_path: str | os.PathLike[str],
    inference_report_path: str | os.PathLike[str],
    workspace_root: str | os.PathLike[str],
    repository_root: str | os.PathLike[str],
    model_factory: Callable[[str], Any] | None = None,
    now: Callable[[], str] = _utc_now,
) -> Path:
    """Run the exact checkpoint and emit immutable per-image prediction evidence."""

    architecture, input_shape = _candidate_identity(candidate, candidate_stage)
    if split not in {"val", "test"}:
        raise EvaluationError("split must be explicitly 'val' or 'test'")
    if data_kind not in DATA_KINDS:
        raise EvaluationError(
            "data_kind must be explicitly 'real_aerial' or 'synthetic_disaster'"
        )
    report_id = _trimmed_string(report_id, "report_id")
    model_hash = _sha256(expected_model_sha256, "expected_model_sha256")
    plan_hash = _sha256(training_plan_sha256, "training_plan_sha256")
    manifest_hash = _sha256(
        expected_dataset_manifest_sha256, "expected_dataset_manifest_sha256"
    )
    try:
        workspace = require_external_workspace(workspace_root, repository_root)
        model_path = require_path_within_workspace(model_checkpoint, workspace)
        manifest_path = require_path_within_workspace(dataset_manifest, workspace)
        predictions_path = require_path_within_workspace(
            prediction_records_path, workspace
        )
        report_path = require_path_within_workspace(inference_report_path, workspace)
    except ArtifactIOError as error:
        raise EvaluationError(str(error)) from error

    if (
        model_path.is_symlink()
        or not model_path.is_file()
        or model_path.suffix.lower() != ".pt"
    ):
        raise EvaluationError("model checkpoint must be one regular .pt file")
    if model_path.name != architecture:
        # Trained Ultralytics checkpoints are normally named best.pt.  The
        # architecture is bound by the training report/plan, not that output
        # filename, so only reject a misleading different frozen base name.
        if model_path.name in {"yolov8n.pt", "yolov8s.pt"}:
            raise EvaluationError("model filename conflicts with candidate architecture")
    if sha256_file(model_path) != model_hash:
        raise EvaluationError("model checkpoint SHA-256 mismatch")

    manifest, ground_truth, ground_truth_path, ground_truth_hash, ordered_ids = (
        _load_evaluation_dataset(
            manifest_path=manifest_path,
            expected_manifest_sha256=manifest_hash,
            workspace=workspace,
            split=split,
            data_kind=data_kind,
        )
    )
    if os.path.lexists(predictions_path) or os.path.lexists(report_path):
        raise EvaluationError("refusing to overwrite detector inference evidence")

    staging = predictions_path.parent / f".{predictions_path.stem}.verified-inputs"
    if os.path.lexists(staging):
        raise EvaluationError(f"refusing to reuse inference input staging: {staging}")
    try:
        staging.mkdir(parents=True, exist_ok=False)
        staged_model = staging / model_path.name
        with model_path.open("rb") as incoming, staged_model.open("xb") as outgoing:
            shutil.copyfileobj(incoming, outgoing, length=1024 * 1024)
            outgoing.flush()
            os.fsync(outgoing.fileno())
    except OSError as error:
        raise EvaluationError(f"cannot stage detector checkpoint: {error}") from error
    if sha256_file(staged_model) != model_hash:
        raise EvaluationError("staged model checkpoint SHA-256 mismatch")

    if model_factory is None:
        try:
            from ultralytics import YOLO
        except ImportError as error:
            raise EvaluationError("Ultralytics is unavailable") from error
        model_factory = YOLO
    model = model_factory(str(staged_model))
    if getattr(model, "task", "detect") != "detect":
        raise EvaluationError("checkpoint task must be object detection")
    if getattr(model, "names", None) != {0: "person_candidate"}:
        raise EvaluationError(
            "checkpoint class map must be exactly {0: 'person_candidate'}"
        )

    inference_arguments = {
        "imgsz": input_shape[2],
        "conf": INFERENCE_CONFIDENCE_FLOOR,
        "iou": 0.7,
        "max_det": 300,
        "device": 0,
        "half": False,
        "augment": False,
        "agnostic_nms": False,
        "classes": [0],
        "save": False,
        "stream": False,
        "verbose": False,
    }
    started_at = _utc(now(), "started_at_utc")
    prediction_records: list[dict[str, Any]] = []
    for index, truth in enumerate(ground_truth):
        image_path = Path(truth["image_path"])
        try:
            raw_results = model.predict(source=str(image_path), **inference_arguments)
        except Exception as error:
            raise EvaluationError(
                f"detector inference failed for image {truth['image_id']!r}: {error}"
            ) from error
        results = list(raw_results)
        if len(results) != 1:
            raise EvaluationError("one input image must produce exactly one result")
        result = results[0]
        expected_shape = (
            int(truth["evaluation_height"]),
            int(truth["evaluation_width"]),
        )
        actual_shape = tuple(getattr(result, "orig_shape", ()))
        if actual_shape != expected_shape:
            raise EvaluationError(
                f"inference result dimensions differ for {truth['image_id']!r}"
            )
        boxes_object = getattr(result, "boxes", None)
        boxes: list[dict[str, Any]] = []
        if boxes_object is not None:
            xyxy = _tensor_rows(getattr(boxes_object, "xyxy", None), "boxes.xyxy")
            confidence = _tensor_rows(
                getattr(boxes_object, "conf", None), "boxes.conf"
            )
            classes = _tensor_rows(getattr(boxes_object, "cls", None), "boxes.cls")
            if not (len(xyxy) == len(confidence) == len(classes)):
                raise EvaluationError("inference box tensor lengths differ")
            for box_index, (coordinates, score, class_value) in enumerate(
                zip(xyxy, confidence, classes)
            ):
                class_number = _finite_number(class_value, f"boxes.cls[{box_index}]")
                if class_number != 0.0:
                    raise EvaluationError("inference emitted a non-person class")
                box = {
                    "class_id": 0,
                    "coordinate_format": "xyxy_abs",
                    "bbox": list(coordinates)
                    if isinstance(coordinates, (list, tuple))
                    else coordinates,
                    "confidence": score,
                }
                _parse_box(
                    box,
                    width=expected_shape[1],
                    height=expected_shape[0],
                    field=f"prediction_records[{index}].boxes[{box_index}]",
                    prediction=True,
                )
                boxes.append(box)
        prediction_records.append(
            {
                "image_id": truth["image_id"],
                "evaluation_width": expected_shape[1],
                "evaluation_height": expected_shape[0],
                "boxes": boxes,
            }
        )

    _exclusive_json_records(predictions_path, prediction_records)
    prediction_hash = sha256_file(predictions_path)
    if sha256_file(model_path) != model_hash or sha256_file(staged_model) != model_hash:
        raise EvaluationError("model checkpoint changed during inference")
    if sha256_file(ground_truth_path) != ground_truth_hash:
        raise EvaluationError("ground-truth records changed during inference")
    if sha256_file(manifest_path) != manifest_hash:
        raise EvaluationError("dataset manifest changed during inference")
    for index, truth in enumerate(ground_truth):
        if sha256_file(truth["image_path"]) != truth["image_sha256"]:
            raise EvaluationError(f"source image changed during inference at index {index}")

    payload = {
        "schema": INFERENCE_REPORT_SCHEMA,
        "report_id": report_id,
        "candidate": candidate,
        "candidate_stage": candidate_stage,
        "architecture": architecture,
        "input_shape_nchw": input_shape,
        "training_plan_sha256": plan_hash,
        "model": {
            "source_path": str(model_path),
            "staged_path": str(staged_model),
            "sha256": model_hash,
        },
        "dataset_manifest": {"path": str(manifest_path), "sha256": manifest_hash},
        "dataset_id": manifest["dataset_id"],
        "dataset_sha256": manifest["dataset_sha256"],
        "split": split,
        "data_kind": data_kind,
        "ordered_image_ids_sha256": hashlib.sha256(
            _canonical_value_bytes(list(ordered_ids))
        ).hexdigest(),
        "ground_truth_records": {
            "path": str(ground_truth_path),
            "sha256": ground_truth_hash,
        },
        "prediction_records": {
            "path": str(predictions_path),
            "sha256": prediction_hash,
            "image_count": len(prediction_records),
        },
        "preprocess": {
            "coordinate_space": "original_source_pixels",
            "resize": "ultralytics_letterbox",
            "input_shape_nchw": input_shape,
        },
        "inference": inference_arguments,
        "started_at_utc": started_at,
        "completed_at_utc": _utc(now(), "completed_at_utc"),
        "passed": True,
    }
    try:
        return create_external_json(
            report_path,
            payload,
            workspace_root=workspace,
            repository_root=repository_root,
        )
    except ArtifactIOError as error:
        raise EvaluationError(str(error)) from error


def _load_inference_evidence(
    *,
    inference_report: str | os.PathLike[str],
    workspace: Path,
    candidate: str,
    candidate_stage: str,
    split: str,
    data_kind: str,
    model_path: Path,
    model_sha256: str,
    training_plan_sha256: str,
    manifest_path: Path,
    manifest_sha256: str,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, Any],
]:
    try:
        report_path = require_path_within_workspace(inference_report, workspace)
    except ArtifactIOError as error:
        raise EvaluationError(str(error)) from error
    report_hash = sha256_file(report_path)
    report = _load_json_object(report_path, "detector inference report")
    _exact_fields(report, _INFERENCE_FIELDS, "detector inference report")
    if report["schema"] != INFERENCE_REPORT_SCHEMA or report["passed"] is not True:
        raise EvaluationError("detector inference report did not pass")
    _trimmed_string(report["report_id"], "inference report_id")
    architecture, input_shape = _candidate_identity(candidate, candidate_stage)
    expected_identity = {
        "candidate": candidate,
        "candidate_stage": candidate_stage,
        "architecture": architecture,
        "input_shape_nchw": input_shape,
        "training_plan_sha256": training_plan_sha256,
        "split": split,
        "data_kind": data_kind,
    }
    for field, expected in expected_identity.items():
        if report[field] != expected:
            raise EvaluationError(f"inference report {field} differs from evaluation")
    _utc(report["started_at_utc"], "inference.started_at_utc")
    _utc(report["completed_at_utc"], "inference.completed_at_utc")

    model = report["model"]
    if type(model) is not dict or set(model) != {"source_path", "staged_path", "sha256"}:
        raise EvaluationError("inference model evidence fields differ")
    if Path(model["source_path"]).resolve(strict=False) != model_path:
        raise EvaluationError("inference used a different source checkpoint")
    if _sha256(model["sha256"], "inference.model.sha256") != model_sha256:
        raise EvaluationError("inference model SHA-256 differs")
    for field in ("source_path", "staged_path"):
        try:
            bound_path = require_path_within_workspace(model[field], workspace)
        except ArtifactIOError as error:
            raise EvaluationError(f"inference.model.{field} is unsafe") from error
        if sha256_file(bound_path) != model_sha256:
            raise EvaluationError(f"inference.model.{field} SHA-256 mismatch")

    # Validate the live corpus before trusting any references copied into the
    # inference report.  This produces a direct manifest/class-map failure for
    # a changed corpus instead of a less useful secondary binding error.
    manifest, ground_truth, gt_path, gt_hash, ordered_ids = _load_evaluation_dataset(
        manifest_path=manifest_path,
        expected_manifest_sha256=manifest_sha256,
        workspace=workspace,
        split=split,
        data_kind=data_kind,
    )

    manifest_ref = report["dataset_manifest"]
    if type(manifest_ref) is not dict or set(manifest_ref) != {"path", "sha256"}:
        raise EvaluationError("inference dataset-manifest evidence fields differ")
    if Path(manifest_ref["path"]).resolve(strict=False) != manifest_path:
        raise EvaluationError("inference used a different dataset manifest")
    if _sha256(manifest_ref["sha256"], "inference.dataset_manifest.sha256") != manifest_sha256:
        raise EvaluationError("inference dataset manifest SHA-256 differs")

    if report["dataset_id"] != manifest["dataset_id"]:
        raise EvaluationError("inference dataset ID differs")
    if report["dataset_sha256"] != manifest["dataset_sha256"]:
        raise EvaluationError("inference semantic dataset SHA-256 differs")
    ordered_hash = hashlib.sha256(
        _canonical_value_bytes(list(ordered_ids))
    ).hexdigest()
    if report["ordered_image_ids_sha256"] != ordered_hash:
        raise EvaluationError("inference ordered-image identity differs")

    gt_ref = report["ground_truth_records"]
    if type(gt_ref) is not dict or set(gt_ref) != {"path", "sha256"}:
        raise EvaluationError("inference ground-truth evidence fields differ")
    if Path(gt_ref["path"]).resolve(strict=False) != gt_path:
        raise EvaluationError("inference ground-truth path differs")
    if gt_ref["sha256"] != gt_hash:
        raise EvaluationError("inference ground-truth SHA-256 differs")

    prediction_ref = report["prediction_records"]
    if type(prediction_ref) is not dict or set(prediction_ref) != {
        "path", "sha256", "image_count"
    }:
        raise EvaluationError("inference prediction evidence fields differ")
    try:
        prediction_path = require_path_within_workspace(prediction_ref["path"], workspace)
    except ArtifactIOError as error:
        raise EvaluationError("inference prediction path is unsafe") from error
    prediction_hash = _sha256(
        prediction_ref["sha256"], "inference.prediction_records.sha256"
    )
    if sha256_file(prediction_path) != prediction_hash:
        raise EvaluationError("inference prediction record SHA-256 mismatch")
    predictions = _load_json_records(prediction_path, "prediction records")
    if prediction_ref["image_count"] != len(ordered_ids) or len(predictions) != len(ordered_ids):
        raise EvaluationError("inference prediction image count differs")

    preprocess = report["preprocess"]
    expected_preprocess = {
        "coordinate_space": "original_source_pixels",
        "resize": "ultralytics_letterbox",
        "input_shape_nchw": input_shape,
    }
    if preprocess != expected_preprocess:
        raise EvaluationError("inference preprocess contract differs")
    expected_arguments = {
        "imgsz": input_shape[2],
        "conf": INFERENCE_CONFIDENCE_FLOOR,
        "iou": 0.7,
        "max_det": 300,
        "device": 0,
        "half": False,
        "augment": False,
        "agnostic_nms": False,
        "classes": [0],
        "save": False,
        "stream": False,
        "verbose": False,
    }
    if report["inference"] != expected_arguments:
        raise EvaluationError("inference arguments differ from the frozen contract")
    if sha256_file(report_path) != report_hash:
        raise EvaluationError("inference report changed while being validated")
    evidence = {
        "report_path": str(report_path),
        "report_sha256": report_hash,
        "ground_truth_records_path": str(gt_path),
        "ground_truth_records_sha256": gt_hash,
        "prediction_records_path": str(prediction_path),
        "prediction_records_sha256": prediction_hash,
        "ordered_image_ids_sha256": ordered_hash,
        "preprocess": expected_preprocess,
    }
    return ground_truth, predictions, evidence


def _derive_metric_values(
    *,
    ground_truth_records: Sequence[Mapping[str, Any]],
    prediction_records: Sequence[Mapping[str, Any]],
    ordered_image_ids: tuple[str, ...],
    thresholds: tuple[float, ...],
) -> tuple[float, list[dict[str, float]]]:
    """Derive the frozen IoU=.50 metrics from immutable record files."""

    ground_truth, _, dimensions = _parse_image_records(
        ground_truth_records,
        ordered_image_ids=ordered_image_ids,
        prediction=False,
    )
    _, predictions, _ = _parse_image_records(
        prediction_records,
        ordered_image_ids=ordered_image_ids,
        prediction=True,
        expected_dimensions=dimensions,
    )

    ground_truth_count = sum(len(boxes) for boxes in ground_truth)
    small_ground_truth_count = sum(
        box.area <= SMALL_PERSON_MAX_AREA_PX2
        for boxes in ground_truth
        for box in boxes
    )
    all_predictions = _ordered_predictions(predictions)
    ap_tp, ap_fp, _ = _match(ground_truth, all_predictions)
    map50 = _average_precision(ap_tp, ap_fp, ground_truth_count)

    points: list[dict[str, float]] = []
    for threshold in thresholds:
        selected_predictions = _ordered_predictions(predictions, threshold)
        true_positives, false_positives, matched_small = _match(
            ground_truth, selected_predictions
        )
        tp_count = sum(true_positives)
        fp_count = sum(false_positives)
        points.append(
            {
                "threshold": threshold,
                "precision": tp_count / (tp_count + fp_count)
                if tp_count + fp_count
                else 0.0,
                "recall": tp_count / ground_truth_count
                if ground_truth_count
                else 0.0,
                "small_person_recall": matched_small / small_ground_truth_count
                if small_ground_truth_count
                else 0.0,
                "mAP50": map50,
            }
        )
    return map50, points


def _seal_validation_report(
    report: ValidationReport,
    *,
    report_path: Path,
    report_sha256: str,
    workspace: Path,
    repository: Path,
) -> ValidationReport:
    object.__setattr__(report, "_issuance_token", _VALIDATION_ISSUANCE_TOKEN)
    object.__setattr__(report, "_evidence_report_path", str(report_path))
    object.__setattr__(report, "_evidence_report_sha256", report_sha256)
    object.__setattr__(report, "_workspace_root", str(workspace))
    object.__setattr__(report, "_repository_root", str(repository))
    return report


def _load_and_rederive_validation_report(
    *,
    report_path: Path,
    report_sha256: str,
    workspace: Path,
    repository: Path,
    expected_report: ValidationReport | None = None,
) -> ValidationReport:
    """Recursively verify a report and rebuild its metrics from record files."""

    if report_path in {workspace, repository}:
        raise EvaluationError("validation report path must be a file below the workspace")
    if sha256_file(report_path) != report_sha256:
        raise EvaluationError("validation report SHA-256 mismatch")
    raw = _load_json_object(report_path, "validation report")
    parsed = ValidationReport.from_dict(raw)
    if parsed.sha256 != report_sha256:
        raise EvaluationError(
            "validation report is not the canonical evaluator-issued encoding"
        )
    if expected_report is not None and parsed.to_dict() != expected_report.to_dict():
        raise EvaluationError(
            "in-memory validation report differs from its create-once evidence file"
        )

    report_paths = {
        Path(parsed.inference_evidence.report_path).resolve(strict=False),
        Path(parsed.inference_evidence.ground_truth_records_path).resolve(strict=False),
        Path(parsed.inference_evidence.prediction_records_path).resolve(strict=False),
    }
    if report_path in report_paths:
        raise EvaluationError(
            "validation report must be distinct from inference and record evidence"
        )

    try:
        inference_path = require_path_within_workspace(
            parsed.inference_evidence.report_path, workspace
        )
    except (ArtifactIOError, TypeError) as error:
        raise EvaluationError("validation inference report path is unsafe") from error
    if sha256_file(inference_path) != parsed.inference_evidence.report_sha256:
        raise EvaluationError("validation inference report SHA-256 mismatch")
    inference = _load_json_object(inference_path, "detector inference report")
    _exact_fields(inference, _INFERENCE_FIELDS, "detector inference report")

    model_ref = inference.get("model")
    if type(model_ref) is not dict or set(model_ref) != {
        "source_path",
        "staged_path",
        "sha256",
    }:
        raise EvaluationError("inference model evidence fields differ")
    manifest_ref = inference.get("dataset_manifest")
    if type(manifest_ref) is not dict or set(manifest_ref) != {"path", "sha256"}:
        raise EvaluationError("inference dataset-manifest evidence fields differ")
    try:
        model_path = require_path_within_workspace(
            _trimmed_string(model_ref["source_path"], "inference.model.source_path"),
            workspace,
        )
        manifest_path = require_path_within_workspace(
            _trimmed_string(
                manifest_ref["path"], "inference.dataset_manifest.path"
            ),
            workspace,
        )
    except ArtifactIOError as error:
        raise EvaluationError("inference source artifact path is unsafe") from error
    inference_model_hash = _sha256(
        model_ref["sha256"], "inference.model.sha256"
    )
    if inference_model_hash != parsed.model_sha256:
        raise EvaluationError("validation model SHA-256 differs from inference")
    manifest_hash = _sha256(
        manifest_ref["sha256"], "inference.dataset_manifest.sha256"
    )

    ground_truth_records, prediction_records, inference_evidence = (
        _load_inference_evidence(
            inference_report=inference_path,
            workspace=workspace,
            candidate=parsed.candidate,
            candidate_stage=parsed.candidate_stage,
            split=parsed.split,
            data_kind=parsed.data_kind,
            model_path=model_path,
            model_sha256=parsed.model_sha256,
            training_plan_sha256=parsed.training_plan_sha256,
            manifest_path=manifest_path,
            manifest_sha256=manifest_hash,
        )
    )
    if inference_evidence != parsed.inference_evidence.to_dict():
        raise EvaluationError(
            "validation inference evidence differs from recursively verified files"
        )

    manifest = _load_json_object(manifest_path, "dataset manifest")
    dataset_id = _trimmed_string(manifest["dataset_id"], "dataset_id")
    dataset_sha256 = _sha256(manifest["dataset_sha256"], "dataset_sha256")
    if dataset_id != parsed.dataset_id or dataset_sha256 != parsed.dataset_sha256:
        raise EvaluationError("validation dataset identity differs from its manifest")
    ordered_image_ids = tuple(manifest["ordered_image_ids"])
    thresholds = tuple(point.threshold for point in parsed.threshold_points)
    map50, points = _derive_metric_values(
        ground_truth_records=ground_truth_records,
        prediction_records=prediction_records,
        ordered_image_ids=ordered_image_ids,
        thresholds=thresholds,
    )
    rebuilt = {
        "schema": VALIDATION_REPORT_SCHEMA,
        "candidate": parsed.candidate,
        "candidate_stage": parsed.candidate_stage,
        "architecture": parsed.architecture,
        "input_shape_nchw": list(parsed.input_shape_nchw),
        "training_plan_sha256": parsed.training_plan_sha256,
        "split": parsed.split,
        "class_map": {"0": "person_candidate"},
        "data_kind": parsed.data_kind,
        "dataset_id": dataset_id,
        "dataset_sha256": dataset_sha256,
        "model_sha256": parsed.model_sha256,
        "inference_evidence": inference_evidence,
        "small_person_definition": {
            "basis": SMALL_PERSON_BASIS,
            "max_area_px2": SMALL_PERSON_MAX_AREA_PX2,
            "inclusive": True,
        },
        "aggregate": {"mAP50": map50},
        "threshold_points": points,
    }
    rederived = ValidationReport.from_dict(rebuilt)
    if rederived.to_dict() != parsed.to_dict():
        raise EvaluationError(
            "validation metric values differ from evaluator rederivation"
        )
    if sha256_file(report_path) != report_sha256:
        raise EvaluationError("validation report changed while being verified")
    return _seal_validation_report(
        rederived,
        report_path=report_path,
        report_sha256=report_sha256,
        workspace=workspace,
        repository=repository,
    )


def load_validation_report(
    report_path: str | os.PathLike[str],
    *,
    workspace_root: str | os.PathLike[str],
    repository_root: str | os.PathLike[str],
) -> ValidationReport:
    """Load durable validation evidence and seal it after full rederivation."""

    try:
        workspace = require_external_workspace(workspace_root, repository_root)
        repository = Path(repository_root).expanduser().resolve(strict=False)
        path = require_path_within_workspace(report_path, workspace)
        report_hash = sha256_file(path)
    except ArtifactIOError as error:
        raise EvaluationError(str(error)) from error
    try:
        return _load_and_rederive_validation_report(
            report_path=path,
            report_sha256=report_hash,
            workspace=workspace,
            repository=repository,
        )
    except ArtifactIOError as error:
        raise EvaluationError(str(error)) from error


def revalidate_validation_report(report: ValidationReport) -> ValidationReport:
    """Re-open and rederive an evaluator-issued report immediately before use."""

    if type(report) is not ValidationReport:
        raise EvaluationError("validation evidence must be a ValidationReport")
    if report._issuance_token is not _VALIDATION_ISSUANCE_TOKEN:
        raise EvaluationError(
            "parsed or caller-constructed reports are not evaluator-issued"
        )
    if not all(
        type(value) is str and value
        for value in (
            report._evidence_report_path,
            report._evidence_report_sha256,
            report._workspace_root,
            report._repository_root,
        )
    ):
        raise EvaluationError("validation report seal metadata is incomplete")
    expected_hash = _sha256(
        report._evidence_report_sha256, "validation report evidence SHA-256"
    )
    try:
        workspace = require_external_workspace(
            report._workspace_root, report._repository_root
        )
        repository = Path(report._repository_root).expanduser().resolve(strict=False)
        path = require_path_within_workspace(report._evidence_report_path, workspace)
    except ArtifactIOError as error:
        raise EvaluationError(str(error)) from error
    try:
        return _load_and_rederive_validation_report(
            report_path=path,
            report_sha256=expected_hash,
            workspace=workspace,
            repository=repository,
            expected_report=report,
        )
    except ArtifactIOError as error:
        raise EvaluationError(str(error)) from error


def generate_validation_report(
    *,
    candidate: str,
    candidate_stage: Literal["full", "smoke"],
    split: Literal["val", "test"],
    data_kind: Literal["real_aerial", "synthetic_disaster"],
    model_checkpoint: str | os.PathLike[str],
    expected_model_sha256: str,
    training_plan_sha256: str,
    dataset_manifest: str | os.PathLike[str],
    expected_dataset_manifest_sha256: str,
    inference_report: str | os.PathLike[str],
    confidence_thresholds: Sequence[float],
    output_path: str | os.PathLike[str],
    workspace_root: str | os.PathLike[str],
    repository_root: str | os.PathLike[str],
) -> ValidationReport:
    """Evaluate one provenance domain and create one immutable report.

    ``split`` is deliberately allowed to be ``test`` so a final held-out test
    report can be produced.  Model/threshold selection remains separate and
    :mod:`rescue_training.selection` rejects test evidence for that purpose.
    """

    _candidate_identity(candidate, candidate_stage)
    if split not in {"val", "test"}:
        raise EvaluationError("split must be explicitly 'val' or 'test'")
    if data_kind not in DATA_KINDS:
        raise EvaluationError(
            "data_kind must be explicitly 'real_aerial' or 'synthetic_disaster'"
        )
    expected_model_sha256 = _sha256(expected_model_sha256, "expected_model_sha256")
    training_plan_sha256 = _sha256(training_plan_sha256, "training_plan_sha256")
    expected_dataset_manifest_sha256 = _sha256(
        expected_dataset_manifest_sha256, "expected_dataset_manifest_sha256"
    )
    thresholds = _parse_thresholds(confidence_thresholds)
    if thresholds[0] < INFERENCE_CONFIDENCE_FLOOR:
        raise EvaluationError(
            "confidence thresholds must not be below the frozen inference floor"
        )

    try:
        workspace = require_external_workspace(workspace_root, repository_root)
        model_path = require_path_within_workspace(model_checkpoint, workspace)
        manifest_path = require_path_within_workspace(dataset_manifest, workspace)
        require_path_within_workspace(output_path, workspace)
        actual_model_sha256 = sha256_file(model_path)
    except ArtifactIOError as error:
        raise EvaluationError(str(error)) from error
    if actual_model_sha256 != expected_model_sha256:
        raise EvaluationError(
            "model checkpoint SHA-256 mismatch; refusing to evaluate different weights"
        )
    ground_truth_records, prediction_records, inference_evidence = (
        _load_inference_evidence(
            inference_report=inference_report,
            workspace=workspace,
            candidate=candidate,
            candidate_stage=candidate_stage,
            split=split,
            data_kind=data_kind,
            model_path=model_path,
            model_sha256=actual_model_sha256,
            training_plan_sha256=training_plan_sha256,
            manifest_path=manifest_path,
            manifest_sha256=expected_dataset_manifest_sha256,
        )
    )
    manifest = _load_json_object(manifest_path, "dataset manifest")
    dataset_id = _trimmed_string(manifest["dataset_id"], "dataset_id")
    dataset_sha256 = _sha256(manifest["dataset_sha256"], "dataset_sha256")
    ordered_image_ids = tuple(manifest["ordered_image_ids"])

    map50, points = _derive_metric_values(
        ground_truth_records=ground_truth_records,
        prediction_records=prediction_records,
        ordered_image_ids=ordered_image_ids,
        thresholds=thresholds,
    )

    architecture, input_shape_nchw = _CANDIDATE_IDENTITIES[candidate]
    payload: dict[str, Any] = {
        "schema": VALIDATION_REPORT_SCHEMA,
        "candidate": candidate,
        "candidate_stage": candidate_stage,
        "architecture": architecture,
        "input_shape_nchw": input_shape_nchw,
        "training_plan_sha256": training_plan_sha256,
        "split": split,
        "class_map": {"0": "person_candidate"},
        "data_kind": data_kind,
        "dataset_id": dataset_id,
        "dataset_sha256": dataset_sha256,
        "model_sha256": actual_model_sha256,
        "inference_evidence": inference_evidence,
        "small_person_definition": {
            "basis": SMALL_PERSON_BASIS,
            "max_area_px2": SMALL_PERSON_MAX_AREA_PX2,
            "inclusive": True,
        },
        "aggregate": {"mAP50": map50},
        "threshold_points": points,
    }
    # Validate the exact consumer contract before publishing evidence.
    ValidationReport.from_dict(payload)
    try:
        created = create_external_json(
            output_path,
            payload,
            workspace_root=workspace,
            repository_root=repository_root,
        )
    except ArtifactIOError as error:
        raise EvaluationError(str(error)) from error
    # Re-open every bound file and rederive the metrics after publication.  The
    # returned object is the only form accepted by threshold/final selection.
    return load_validation_report(
        created,
        workspace_root=workspace,
        repository_root=repository_root,
    )


__all__ = [
    "COORDINATE_FORMATS",
    "DATASET_MANIFEST_SCHEMA",
    "EvaluationError",
    "INFERENCE_CONFIDENCE_FLOOR",
    "INFERENCE_REPORT_SCHEMA",
    "IOU_THRESHOLD",
    "canonical_records_sha256",
    "create_evaluation_dataset_manifest",
    "generate_validation_report",
    "load_validation_report",
    "revalidate_validation_report",
    "run_detector_inference",
]
