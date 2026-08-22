"""Fail-closed intake contract for the companion person-state classifier.

The detector and state classifier are delivered as one application bundle, but
they are intentionally trained and qualified as separate weights.  This module
prevents a dangerous shortcut: the source dataset is provenance, never the
``safe_walking`` or ``disaster_stressed`` label.

All paths and generated crops live outside Git.  A manifest is accepted only
when every retained person crop has an explicit automated weak-supervision
decision, the labeler artifact and policy are hash-bound, the original media
and crop bytes still match their hashes, and source groups do not cross
train/validation/test boundaries.  These labels are explicitly unverified and
are never represented as statements about a person's actual safety.
"""

from __future__ import annotations

import json
import math
import os
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

from .artifact_io import ArtifactIOError, sha256_file


STATE_DATASET_SCHEMA = "veriswarm.rescue.person_state_dataset.v2"
STATE_RECORD_SCHEMA = "veriswarm.rescue.person_state_record.v2"
WEAK_SUPERVISION_SCHEMA = "veriswarm.rescue.person_state_weak_supervision.v1"
WEAK_SUPERVISION_EVIDENCE_STATUS = "weak_supervision_unverified"
AUTOMATED_LABEL_SOURCE = "automated_weak_supervision"
STATE_MODEL_ID = "sar-rgb-person-state-v1"
STATE_CLASSES = {"0": "disaster_stressed", "1": "safe_walking"}
FROZEN_SOURCE_DATASETS = frozenset(
    {"c2a-v2", "adilshamim8-people-detection-v1"}
)
SPLITS = ("train", "val", "test")
SAFE_REASON = "normal_walking_visual_cue"
DISASTER_REASONS = frozenset(
    {
        "fallen",
        "lying",
        "kneeling_distress",
        "trapped",
        "partially_buried",
        "injured",
        "flood_exposure",
        "fire_exposure",
        "debris_exposure",
        "other_distress_visual_cue",
    }
)
POSE_HINTS = frozenset({"bent", "kneeling", "lying", "sitting", "upright"})
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

_MANIFEST_FIELDS = frozenset(
    {
        "schema",
        "model_id",
        "classes",
        "dataset_origin_is_label",
        "source_datasets",
        "records_path",
        "records_sha256",
        "crop_root",
        "crop_policy",
        "split_policy",
        "weak_supervision",
        "counts",
        "created_at_utc",
    }
)
_SOURCE_FIELDS = frozenset(
    {
        "dataset_id",
        "archive_path",
        "archive_sha256",
        "dataset_use_authorization_status",
        "terms_recorded_by",
        "role",
    }
)
_CROP_POLICY = {
    "context_margin_ratio": 0.35,
    "output_size_hw": [224, 224],
    "padding": "constant_black",
    "interpolation": "bilinear",
}
_SPLIT_POLICY = {
    "group_key": "source_group_id",
    "splits": ["train", "val", "test"],
    "test_used_for_selection": False,
}
_WEAK_SUPERVISION_FIELDS = frozenset(
    {
        "schema",
        "evidence_status",
        "label_source",
        "labeler_type",
        "labeler_id",
        "labeler_version",
        "labeler_artifact_path",
        "labeler_artifact_sha256",
        "policy_path",
        "policy_sha256",
        "forbidden_inputs",
    }
)
_FORBIDDEN_LABELER_INPUTS = [
    "source_dataset_id",
    "source_archive_identity",
    "dataset_origin",
]
_RECORD_FIELDS = frozenset(
    {
        "schema",
        "sample_id",
        "split",
        "source_dataset_id",
        "source_group_id",
        "source_media_path",
        "source_media_sha256",
        "frame_index",
        "person_bbox_xyxy_abs",
        "crop_path",
        "crop_sha256",
        "state_label",
        "state_reason",
        "source_pose_hint",
        "label_source",
        "labeler_id",
        "labeler_version",
        "labeler_artifact_sha256",
        "label_policy_sha256",
        "label_confidence",
        "labeled_at_utc",
        "context_notes",
    }
)


class StateDatasetError(ValueError):
    """State-classifier data or weak-label provenance is ambiguous or mutable."""


def _reject_constant(value: str) -> None:
    raise StateDatasetError(f"non-finite JSON constant is forbidden: {value}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise StateDatasetError(f"duplicate JSON key is forbidden: {key!r}")
        result[key] = value
    return result


def _loads_object(text: str, field: str) -> dict[str, Any]:
    try:
        value = json.loads(
            text,
            parse_constant=_reject_constant,
            object_pairs_hook=_unique_object,
        )
    except StateDatasetError:
        raise
    except (json.JSONDecodeError, TypeError) as error:
        raise StateDatasetError(f"invalid {field} JSON: {error}") from error
    if type(value) is not dict:
        raise StateDatasetError(f"{field} must contain one JSON object")
    return value


def _load_object(path: Path, field: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise StateDatasetError(f"{field} must be one regular file: {path}")
    try:
        return _loads_object(path.read_text(encoding="utf-8"), field)
    except StateDatasetError:
        raise
    except (OSError, UnicodeError) as error:
        raise StateDatasetError(f"cannot read {field}: {error}") from error


def _exact_fields(value: Mapping[str, Any], expected: frozenset[str], field: str) -> None:
    actual = frozenset(value)
    if actual != expected:
        raise StateDatasetError(
            f"{field} fields differ; missing={sorted(expected - actual)}, "
            f"unknown={sorted(actual - expected)}"
        )


def _text(value: Any, field: str) -> str:
    if type(value) is not str or not value or value.strip() != value:
        raise StateDatasetError(f"{field} must be non-empty trimmed text")
    return value


def _sha256(value: Any, field: str) -> str:
    if type(value) is not str or not SHA256_RE.fullmatch(value):
        raise StateDatasetError(f"{field} must be a lowercase SHA-256")
    return value


def _timestamp(value: Any, field: str) -> str:
    text = _text(value, field)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise StateDatasetError(f"{field} must be ISO-8601") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise StateDatasetError(f"{field} must include a timezone")
    return text


def _regular_hashed_file(path_value: Any, digest_value: Any, field: str) -> Path:
    path_text = _text(path_value, f"{field}.path")
    path = Path(path_text)
    if not path.is_absolute():
        raise StateDatasetError(f"{field}.path must be absolute")
    if path.is_symlink():
        raise StateDatasetError(f"{field} must not be a symbolic link: {path}")
    resolved = path.resolve(strict=False)
    if not resolved.is_file():
        raise StateDatasetError(f"{field} must be one regular file: {resolved}")
    expected = _sha256(digest_value, f"{field}.sha256")
    try:
        actual = sha256_file(resolved)
    except ArtifactIOError as error:
        raise StateDatasetError(str(error)) from error
    if actual != expected:
        raise StateDatasetError(f"{field} SHA-256 mismatch")
    return resolved


def _confidence(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StateDatasetError(f"{field} must be numeric")
    normalized = float(value)
    if not math.isfinite(normalized) or not 0.0 < normalized <= 1.0:
        raise StateDatasetError(f"{field} must lie in (0, 1]")
    return normalized


def _validate_weak_supervision(raw: Any) -> dict[str, Any]:
    field = "weak_supervision"
    if type(raw) is not dict:
        raise StateDatasetError(f"{field} must be an object")
    _exact_fields(raw, _WEAK_SUPERVISION_FIELDS, field)
    if raw["schema"] != WEAK_SUPERVISION_SCHEMA:
        raise StateDatasetError("unsupported weak-supervision schema")
    if raw["evidence_status"] != WEAK_SUPERVISION_EVIDENCE_STATUS:
        raise StateDatasetError(
            "state evidence must be marked weak_supervision_unverified"
        )
    if raw["label_source"] != AUTOMATED_LABEL_SOURCE:
        raise StateDatasetError("label_source must be automated_weak_supervision")
    if raw["labeler_type"] != "automated":
        raise StateDatasetError("weak-supervision labeler_type must be automated")
    labeler_id = _text(raw["labeler_id"], f"{field}.labeler_id")
    labeler_version = _text(raw["labeler_version"], f"{field}.labeler_version")
    artifact = _regular_hashed_file(
        raw["labeler_artifact_path"],
        raw["labeler_artifact_sha256"],
        f"{field}.labeler_artifact",
    )
    policy = _regular_hashed_file(
        raw["policy_path"], raw["policy_sha256"], f"{field}.policy"
    )
    if artifact == policy:
        raise StateDatasetError("labeler artifact and policy must be distinct files")
    if raw["forbidden_inputs"] != _FORBIDDEN_LABELER_INPUTS:
        raise StateDatasetError(
            "weak-supervision policy must forbid dataset-origin inputs"
        )
    return {
        "schema": WEAK_SUPERVISION_SCHEMA,
        "evidence_status": WEAK_SUPERVISION_EVIDENCE_STATUS,
        "label_source": AUTOMATED_LABEL_SOURCE,
        "labeler_type": "automated",
        "labeler_id": labeler_id,
        "labeler_version": labeler_version,
        "labeler_artifact_path": str(artifact),
        "labeler_artifact_sha256": raw["labeler_artifact_sha256"],
        "policy_path": str(policy),
        "policy_sha256": raw["policy_sha256"],
        "forbidden_inputs": list(_FORBIDDEN_LABELER_INPUTS),
    }


def _bbox(value: Any, field: str) -> tuple[float, float, float, float]:
    if type(value) is not list or len(value) != 4:
        raise StateDatasetError(f"{field} must contain four numbers")
    numbers: list[float] = []
    for index, item in enumerate(value):
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise StateDatasetError(f"{field}[{index}] must be numeric")
        number = float(item)
        if not math.isfinite(number) or number < 0:
            raise StateDatasetError(f"{field}[{index}] must be finite and nonnegative")
        numbers.append(number)
    if numbers[2] <= numbers[0] or numbers[3] <= numbers[1]:
        raise StateDatasetError(f"{field} must satisfy x2>x1 and y2>y1")
    return tuple(numbers)  # type: ignore[return-value]


def _records(path: Path) -> Iterable[tuple[int, dict[str, Any]]]:
    try:
        with path.open("r", encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, start=1):
                if not line.strip():
                    raise StateDatasetError(
                        f"records JSONL contains a blank line at {line_number}"
                    )
                yield line_number, _loads_object(line, f"records line {line_number}")
    except StateDatasetError:
        raise
    except (OSError, UnicodeError) as error:
        raise StateDatasetError(f"cannot read records JSONL: {error}") from error


def _validate_record(
    raw: Mapping[str, Any],
    *,
    line_number: int,
    crop_root: Path,
    source_ids: frozenset[str],
    weak_supervision: Mapping[str, Any],
) -> dict[str, Any]:
    field = f"records line {line_number}"
    _exact_fields(raw, _RECORD_FIELDS, field)
    if raw["schema"] != STATE_RECORD_SCHEMA:
        raise StateDatasetError(f"{field} has an unsupported schema")
    sample_id = _text(raw["sample_id"], f"{field}.sample_id")
    split = raw["split"]
    if split not in SPLITS:
        raise StateDatasetError(f"{field}.split must be train, val, or test")
    dataset_id = _text(raw["source_dataset_id"], f"{field}.source_dataset_id")
    if dataset_id not in source_ids:
        raise StateDatasetError(f"{field} names an undeclared source dataset")
    group_id = _text(raw["source_group_id"], f"{field}.source_group_id")
    media = _regular_hashed_file(
        raw["source_media_path"], raw["source_media_sha256"], f"{field}.source_media"
    )
    frame_index = raw["frame_index"]
    if frame_index is not None and (
        type(frame_index) is not int or isinstance(frame_index, bool) or frame_index < 0
    ):
        raise StateDatasetError(f"{field}.frame_index must be null or an integer >= 0")
    bbox = _bbox(raw["person_bbox_xyxy_abs"], f"{field}.person_bbox_xyxy_abs")
    crop = _regular_hashed_file(raw["crop_path"], raw["crop_sha256"], f"{field}.crop")
    try:
        crop.relative_to(crop_root)
    except ValueError as error:
        raise StateDatasetError(f"{field}.crop lies outside crop_root") from error
    expected_crop_parent = crop_root / str(split) / str(raw["state_label"])
    if crop.parent.resolve(strict=False) != expected_crop_parent.resolve(strict=False):
        raise StateDatasetError(
            f"{field}.crop must be under crop_root/<split>/<state_label>"
        )

    label = raw["state_label"]
    if label not in STATE_CLASSES.values():
        raise StateDatasetError(f"{field}.state_label is unsupported")
    reason = raw["state_reason"]
    if label == "safe_walking" and reason != SAFE_REASON:
        raise StateDatasetError(
            f"{field}: safe_walking requires reason {SAFE_REASON!r}"
        )
    if label == "disaster_stressed" and reason not in DISASTER_REASONS:
        raise StateDatasetError(f"{field}: disaster reason is not frozen")
    pose_hint = raw["source_pose_hint"]
    if pose_hint is not None and pose_hint not in POSE_HINTS:
        raise StateDatasetError(f"{field}.source_pose_hint is unsupported")
    if raw["label_source"] != AUTOMATED_LABEL_SOURCE:
        raise StateDatasetError(
            f"{field}: label_source must be automated weak supervision; "
            "dataset origin cannot be the state label"
        )
    labeler_id = _text(raw["labeler_id"], f"{field}.labeler_id")
    labeler_version = _text(raw["labeler_version"], f"{field}.labeler_version")
    artifact_sha256 = _sha256(
        raw["labeler_artifact_sha256"], f"{field}.labeler_artifact_sha256"
    )
    policy_sha256 = _sha256(
        raw["label_policy_sha256"], f"{field}.label_policy_sha256"
    )
    expected_provenance = {
        "labeler_id": weak_supervision["labeler_id"],
        "labeler_version": weak_supervision["labeler_version"],
        "labeler_artifact_sha256": weak_supervision["labeler_artifact_sha256"],
        "label_policy_sha256": weak_supervision["policy_sha256"],
    }
    actual_provenance = {
        "labeler_id": labeler_id,
        "labeler_version": labeler_version,
        "labeler_artifact_sha256": artifact_sha256,
        "label_policy_sha256": policy_sha256,
    }
    if actual_provenance != expected_provenance:
        raise StateDatasetError(
            f"{field}: weak-supervision labeler provenance differs from manifest"
        )
    confidence = _confidence(raw["label_confidence"], f"{field}.label_confidence")
    labeled_at = _timestamp(raw["labeled_at_utc"], f"{field}.labeled_at_utc")
    if type(raw["context_notes"]) is not str:
        raise StateDatasetError(f"{field}.context_notes must be text")

    return {
        "sample_id": sample_id,
        "split": split,
        "source_dataset_id": dataset_id,
        "source_group_id": group_id,
        "source_media_path": str(media),
        "source_media_sha256": raw["source_media_sha256"],
        "frame_index": frame_index,
        "person_bbox_xyxy_abs": list(bbox),
        "crop_path": str(crop),
        "crop_sha256": raw["crop_sha256"],
        "state_label": label,
        "state_reason": reason,
        "source_pose_hint": pose_hint,
        "labeler_id": labeler_id,
        "labeler_version": labeler_version,
        "labeler_artifact_sha256": artifact_sha256,
        "label_policy_sha256": policy_sha256,
        "label_confidence": confidence,
        "labeled_at_utc": labeled_at,
    }


def validate_state_dataset_manifest(
    manifest_path: str | os.PathLike[str],
) -> dict[str, Any]:
    """Validate complete, weakly supervised, group-safe classifier intake."""

    input_path = Path(manifest_path)
    if input_path.is_symlink():
        raise StateDatasetError("state dataset manifest must not be a symbolic link")
    path = input_path.resolve(strict=False)
    manifest = _load_object(path, "state dataset manifest")
    _exact_fields(manifest, _MANIFEST_FIELDS, "state dataset manifest")
    if manifest["schema"] != STATE_DATASET_SCHEMA:
        raise StateDatasetError("unsupported state dataset schema")
    if manifest["model_id"] != STATE_MODEL_ID:
        raise StateDatasetError("state dataset model_id is not frozen")
    if manifest["classes"] != STATE_CLASSES:
        raise StateDatasetError("state classes are not frozen")
    if manifest["dataset_origin_is_label"] is not False:
        raise StateDatasetError("dataset origin must never be accepted as a state label")
    if manifest["crop_policy"] != _CROP_POLICY:
        raise StateDatasetError("crop policy is not frozen")
    if manifest["split_policy"] != _SPLIT_POLICY:
        raise StateDatasetError("split policy is not frozen")
    weak_supervision = _validate_weak_supervision(manifest["weak_supervision"])
    _timestamp(manifest["created_at_utc"], "created_at_utc")

    sources_value = manifest["source_datasets"]
    if type(sources_value) is not list or len(sources_value) != 2:
        raise StateDatasetError("exactly two frozen source datasets are required")
    source_ids: set[str] = set()
    source_evidence: list[dict[str, Any]] = []
    for index, raw in enumerate(sources_value):
        if type(raw) is not dict:
            raise StateDatasetError(f"source_datasets[{index}] must be an object")
        _exact_fields(raw, _SOURCE_FIELDS, f"source_datasets[{index}]")
        dataset_id = _text(raw["dataset_id"], f"source_datasets[{index}].dataset_id")
        if dataset_id in source_ids:
            raise StateDatasetError("duplicate source dataset ID")
        source_ids.add(dataset_id)
        archive = _regular_hashed_file(
            raw["archive_path"],
            raw["archive_sha256"],
            f"source_datasets[{index}].archive",
        )
        if raw["dataset_use_authorization_status"] != "authorization_recorded":
            raise StateDatasetError(
                "each dataset requires recorded use authorization provenance"
            )
        intake_actor = _text(
            raw["terms_recorded_by"], f"source_datasets[{index}].terms_recorded_by"
        )
        if raw["role"] != "candidate_media_only":
            raise StateDatasetError("source datasets are candidate media only")
        source_evidence.append(
            {
                "dataset_id": dataset_id,
                "archive_path": str(archive),
                "archive_sha256": raw["archive_sha256"],
                "dataset_use_authorization_status": "authorization_recorded",
                "terms_recorded_by": intake_actor,
            }
        )
    if frozenset(source_ids) != FROZEN_SOURCE_DATASETS:
        raise StateDatasetError("source dataset IDs do not match the frozen C2A/safe pair")

    crop_root_input = Path(_text(manifest["crop_root"], "crop_root"))
    if not crop_root_input.is_absolute() or crop_root_input.is_symlink():
        raise StateDatasetError("crop_root must be an existing regular directory")
    crop_root = crop_root_input.resolve(strict=False)
    if not crop_root.is_dir():
        raise StateDatasetError("crop_root must be an existing regular directory")
    records_path = _regular_hashed_file(
        manifest["records_path"], manifest["records_sha256"], "records"
    )

    seen_samples: set[str] = set()
    group_splits: dict[str, set[str]] = defaultdict(set)
    media_splits: dict[str, set[str]] = defaultdict(set)
    crop_splits: dict[str, set[str]] = defaultdict(set)
    actual_counts: dict[str, Counter[str]] = {
        split: Counter() for split in SPLITS
    }
    source_split_counts: dict[str, dict[str, Counter[str]]] = {
        source_id: {split: Counter() for split in SPLITS}
        for source_id in source_ids
    }
    record_count = 0
    label_confidences: list[float] = []
    for line_number, raw in _records(records_path):
        record = _validate_record(
            raw,
            line_number=line_number,
            crop_root=crop_root,
            source_ids=frozenset(source_ids),
            weak_supervision=weak_supervision,
        )
        if record["sample_id"] in seen_samples:
            raise StateDatasetError(f"duplicate sample_id: {record['sample_id']!r}")
        seen_samples.add(record["sample_id"])
        split = record["split"]
        group_splits[record["source_group_id"]].add(split)
        media_splits[record["source_media_sha256"]].add(split)
        crop_splits[record["crop_sha256"]].add(split)
        actual_counts[split][record["state_label"]] += 1
        source_split_counts[record["source_dataset_id"]][split][
            record["state_label"]
        ] += 1
        label_confidences.append(record["label_confidence"])
        record_count += 1
    if record_count == 0:
        raise StateDatasetError("records JSONL is empty")
    for field, mapping in (
        ("source_group_id", group_splits),
        ("source_media_sha256", media_splits),
        ("crop_sha256", crop_splits),
    ):
        leaking = sorted(key for key, splits in mapping.items() if len(splits) != 1)
        if leaking:
            raise StateDatasetError(
                f"cross-split leakage by {field}: {leaking[:5]}"
            )

    # Weak-supervision metadata alone cannot prevent the classifier from
    # learning archive appearance when one source supplies only distress and
    # the other supplies only safe examples.  Require both frozen sources to
    # contribute both states to every split so origin is not a perfect label.
    for source_id in sorted(source_ids):
        for split in SPLITS:
            missing_labels = sorted(
                label
                for label in STATE_CLASSES.values()
                if source_split_counts[source_id][split][label] < 1
            )
            if missing_labels:
                raise StateDatasetError(
                    "dataset-source/class confounding is forbidden: "
                    f"{source_id!r} split {split!r} lacks {missing_labels}"
                )

    expected_counts = manifest["counts"]
    if type(expected_counts) is not dict or set(expected_counts) != set(SPLITS):
        raise StateDatasetError("counts must contain exactly train, val, and test")
    normalized_counts: dict[str, dict[str, int]] = {}
    for split in SPLITS:
        split_counts = expected_counts[split]
        if type(split_counts) is not dict or set(split_counts) != set(STATE_CLASSES.values()):
            raise StateDatasetError(f"counts.{split} must contain both state classes")
        normalized_counts[split] = {}
        for label in STATE_CLASSES.values():
            expected = split_counts[label]
            if type(expected) is not int or isinstance(expected, bool) or expected < 1:
                raise StateDatasetError(
                    f"counts.{split}.{label} must be an integer >= 1"
                )
            actual = actual_counts[split][label]
            if actual != expected:
                raise StateDatasetError(
                    f"counts.{split}.{label} mismatch: expected {expected}, got {actual}"
                )
            normalized_counts[split][label] = expected

    return {
        "manifest_path": str(path),
        "manifest_sha256": sha256_file(path),
        "model_id": STATE_MODEL_ID,
        "classes": dict(STATE_CLASSES),
        "source_datasets": sorted(source_evidence, key=lambda item: item["dataset_id"]),
        "records_path": str(records_path),
        "records_sha256": manifest["records_sha256"],
        "crop_root": str(crop_root),
        "crop_policy": dict(_CROP_POLICY),
        "split_policy": dict(_SPLIT_POLICY),
        "weak_supervision": weak_supervision,
        "state_evidence_status": WEAK_SUPERVISION_EVIDENCE_STATUS,
        "counts": normalized_counts,
        "source_class_counts": {
            source_id: {
                split: {
                    label: source_split_counts[source_id][split][label]
                    for label in STATE_CLASSES.values()
                }
                for split in SPLITS
            }
            for source_id in sorted(source_ids)
        },
        "record_count": record_count,
        "weak_supervision_records": record_count,
        "label_confidence_range": {
            "minimum": min(label_confidences),
            "maximum": max(label_confidences),
        },
        "dataset_origin_is_label": False,
    }


__all__ = [
    "DISASTER_REASONS",
    "AUTOMATED_LABEL_SOURCE",
    "FROZEN_SOURCE_DATASETS",
    "POSE_HINTS",
    "SAFE_REASON",
    "STATE_CLASSES",
    "STATE_DATASET_SCHEMA",
    "STATE_MODEL_ID",
    "STATE_RECORD_SCHEMA",
    "StateDatasetError",
    "WEAK_SUPERVISION_EVIDENCE_STATUS",
    "WEAK_SUPERVISION_SCHEMA",
    "validate_state_dataset_manifest",
]
