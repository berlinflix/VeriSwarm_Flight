"""Fail-closed validation-threshold and provisional model selection.

The selector deliberately has no dependency on Ultralytics, Torch, or CUDA. It
consumes create-once JSON evidence produced by those layers and applies the
frozen rescue-person gates. Real aerial and synthetic-disaster evidence remain
separate throughout; no blended or averaged score is calculated. Untouched-test
evidence is deliberately absent: deployment may release or block this frozen
choice after its one test evaluation, but may not use test results to promote a
different candidate.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import TYPE_CHECKING, Any, Iterable, Literal, Mapping

from .artifact_io import ArtifactIOError, canonical_json_bytes

if TYPE_CHECKING:
    from .deployment import CandidateQualification


VALIDATION_REPORT_SCHEMA = "veriswarm.rescue.validation_report.v2"
SELECTION_REPORT_SCHEMA = "veriswarm.rescue.model_selection.v2"

MANDATORY_FULL_CANDIDATES = frozenset({"yolov8n-640", "yolov8n-960"})
OPTIONAL_FULL_CANDIDATES = frozenset({"yolov8s-640"})
FULL_CANDIDATES = MANDATORY_FULL_CANDIDATES | OPTIONAL_FULL_CANDIDATES
SMOKE_CANDIDATE = "yolov8n-640-smoke"
DATA_KINDS = frozenset({"real_aerial", "synthetic_disaster"})

MIN_RECALL = 0.75
MIN_PRECISION = 0.60
MIN_MAP50 = 0.70
MIN_SMALL_PERSON_RECALL = 0.60

SMALL_PERSON_BASIS = "bbox_area_original_source_pixels"
SMALL_PERSON_MAX_AREA_PX2 = 32 * 32

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_VALIDATION_FIELDS = frozenset(
    {
        "schema",
        "candidate",
        "candidate_stage",
        "architecture",
        "input_shape_nchw",
        "training_plan_sha256",
        "split",
        "class_map",
        "data_kind",
        "dataset_id",
        "dataset_sha256",
        "model_sha256",
        "inference_evidence",
        "small_person_definition",
        "aggregate",
        "threshold_points",
    }
)
_INFERENCE_EVIDENCE_FIELDS = frozenset(
    {
        "report_path",
        "report_sha256",
        "ground_truth_records_path",
        "ground_truth_records_sha256",
        "prediction_records_path",
        "prediction_records_sha256",
        "ordered_image_ids_sha256",
        "preprocess",
    }
)
_PREPROCESS_FIELDS = frozenset(
    {"coordinate_space", "resize", "input_shape_nchw"}
)
_SMALL_PERSON_FIELDS = frozenset({"basis", "max_area_px2", "inclusive"})
_AGGREGATE_FIELDS = frozenset({"mAP50"})
_POINT_FIELDS = frozenset(
    {"threshold", "precision", "recall", "small_person_recall", "mAP50"}
)

_CANDIDATE_IDENTITIES: Mapping[str, tuple[str, tuple[int, int, int, int]]] = {
    "yolov8n-640": ("yolov8n.pt", (1, 3, 640, 640)),
    "yolov8n-960": ("yolov8n.pt", (1, 3, 960, 960)),
    "yolov8s-640": ("yolov8s.pt", (1, 3, 640, 640)),
    "yolov8n-640-smoke": ("yolov8n.pt", (1, 3, 640, 640)),
}

# A parsed JSON object is not qualification evidence.  The evaluator sets this
# process-local token only after it has re-opened the create-once validation
# report, recursively verified the checkpoint/dataset/inference files, and
# rederived every metric from the bound ground-truth and prediction records.
# The durable trust boundary is still the hash-bound on-disk evidence; this
# token prevents callers from passing an ordinary ``ValidationReport.from_dict``
# object directly into selection and bypassing that verification step.
_VALIDATION_ISSUANCE_TOKEN = object()


class SelectionError(ValueError):
    """Evidence is malformed, incomplete, or cannot satisfy frozen gates."""


def _reject_constant(value: str) -> None:
    raise SelectionError(f"non-finite JSON constant is forbidden: {value}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SelectionError(f"duplicate JSON object key is forbidden: {key!r}")
        result[key] = value
    return result


def _loads(text: str) -> Any:
    if type(text) is not str:
        raise SelectionError("report JSON must be text")
    try:
        return json.loads(
            text,
            parse_constant=_reject_constant,
            object_pairs_hook=_unique_object,
        )
    except SelectionError:
        raise
    except (json.JSONDecodeError, TypeError) as error:
        raise SelectionError(f"invalid report JSON: {error}") from error


def _object(value: Any, field: str) -> dict[str, Any]:
    if type(value) is not dict or not all(type(key) is str for key in value):
        raise SelectionError(f"{field} must be a JSON object with string keys")
    return value


def _exact_fields(value: Mapping[str, Any], expected: frozenset[str], field: str) -> None:
    actual = frozenset(value)
    if actual != expected:
        raise SelectionError(
            f"{field} fields mismatch; missing={sorted(expected - actual)}, "
            f"unknown={sorted(actual - expected)}"
        )


def _string(value: Any, field: str) -> str:
    if type(value) is not str or not value or value.strip() != value:
        raise SelectionError(f"{field} must be a non-empty trimmed string")
    return value


def _hash(value: Any, field: str) -> str:
    result = _string(value, field)
    if _SHA256.fullmatch(result) is None:
        raise SelectionError(f"{field} must be a lowercase SHA-256 hex digest")
    return result


def _absolute_path(value: Any, field: str) -> str:
    """Accept an absolute POSIX or Windows path without host-OS coupling."""

    result = _string(value, field)
    posix = PurePosixPath(result)
    windows = PureWindowsPath(result)
    if not posix.is_absolute() and not windows.is_absolute():
        raise SelectionError(f"{field} must be an absolute path")
    parts = posix.parts if posix.is_absolute() else windows.parts
    if any(part in {".", ".."} for part in parts):
        raise SelectionError(f"{field} must be a normalized absolute path")
    return result


def _canonical_sha256(value: Mapping[str, Any]) -> str:
    try:
        payload = canonical_json_bytes(value)
    except ArtifactIOError as error:
        raise SelectionError(f"evidence is not canonical JSON: {error}") from error
    return hashlib.sha256(payload).hexdigest()


def _ratio(value: Any, field: str) -> float:
    if type(value) not in {int, float} or isinstance(value, bool):
        raise SelectionError(f"{field} must be a finite number in [0, 1]")
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise SelectionError(f"{field} must be a finite number in [0, 1]")
    return result


def _nonnegative_integer(value: Any, field: str) -> int:
    if type(value) is not int or value < 0:
        raise SelectionError(f"{field} must be a non-negative integer")
    return value


def _input_shape(value: Any, field: str) -> tuple[int, int, int, int]:
    if type(value) is not list or len(value) != 4:
        raise SelectionError(f"{field} must be a four-item JSON array")
    shape = tuple(
        _nonnegative_integer(item, f"{field}[{index}]")
        for index, item in enumerate(value)
    )
    if any(dimension == 0 for dimension in shape):
        raise SelectionError(f"{field} dimensions must be positive")
    return shape


def _load_regular_file(path: str | os.PathLike[str], parser: Any) -> Any:
    source = Path(path)
    if source.is_symlink() or not source.is_file():
        raise SelectionError(f"report must be one regular file: {source}")
    try:
        return parser(source.read_text(encoding="utf-8"))
    except SelectionError:
        raise
    except (OSError, UnicodeError) as error:
        raise SelectionError(f"cannot read report {source}: {error}") from error


@dataclass(frozen=True, slots=True)
class InferenceEvidence:
    """Immutable binding to the evaluator-issued detector inference corpus."""

    report_path: str
    report_sha256: str
    ground_truth_records_path: str
    ground_truth_records_sha256: str
    prediction_records_path: str
    prediction_records_sha256: str
    ordered_image_ids_sha256: str
    input_shape_nchw: tuple[int, int, int, int]

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, Any],
        *,
        expected_input_shape: tuple[int, int, int, int],
    ) -> "InferenceEvidence":
        raw = _object(value, "inference_evidence")
        _exact_fields(raw, _INFERENCE_EVIDENCE_FIELDS, "inference_evidence")
        preprocess = _object(raw["preprocess"], "inference_evidence.preprocess")
        _exact_fields(
            preprocess,
            _PREPROCESS_FIELDS,
            "inference_evidence.preprocess",
        )
        input_shape = _input_shape(
            preprocess["input_shape_nchw"],
            "inference_evidence.preprocess.input_shape_nchw",
        )
        if preprocess["coordinate_space"] != "original_source_pixels":
            raise SelectionError(
                "inference_evidence.preprocess.coordinate_space must be "
                "'original_source_pixels'"
            )
        if preprocess["resize"] != "ultralytics_letterbox":
            raise SelectionError(
                "inference_evidence.preprocess.resize must be "
                "'ultralytics_letterbox'"
            )
        if input_shape != expected_input_shape:
            raise SelectionError(
                "inference_evidence preprocess shape must match the candidate input shape"
            )

        report_path = _absolute_path(
            raw["report_path"], "inference_evidence.report_path"
        )
        ground_truth_path = _absolute_path(
            raw["ground_truth_records_path"],
            "inference_evidence.ground_truth_records_path",
        )
        prediction_path = _absolute_path(
            raw["prediction_records_path"],
            "inference_evidence.prediction_records_path",
        )
        if len({report_path, ground_truth_path, prediction_path}) != 3:
            raise SelectionError(
                "inference report, ground truth, and prediction paths must be distinct"
            )
        return cls(
            report_path=report_path,
            report_sha256=_hash(
                raw["report_sha256"], "inference_evidence.report_sha256"
            ),
            ground_truth_records_path=ground_truth_path,
            ground_truth_records_sha256=_hash(
                raw["ground_truth_records_sha256"],
                "inference_evidence.ground_truth_records_sha256",
            ),
            prediction_records_path=prediction_path,
            prediction_records_sha256=_hash(
                raw["prediction_records_sha256"],
                "inference_evidence.prediction_records_sha256",
            ),
            ordered_image_ids_sha256=_hash(
                raw["ordered_image_ids_sha256"],
                "inference_evidence.ordered_image_ids_sha256",
            ),
            input_shape_nchw=input_shape,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_path": self.report_path,
            "report_sha256": self.report_sha256,
            "ground_truth_records_path": self.ground_truth_records_path,
            "ground_truth_records_sha256": self.ground_truth_records_sha256,
            "prediction_records_path": self.prediction_records_path,
            "prediction_records_sha256": self.prediction_records_sha256,
            "ordered_image_ids_sha256": self.ordered_image_ids_sha256,
            "preprocess": {
                "coordinate_space": "original_source_pixels",
                "resize": "ultralytics_letterbox",
                "input_shape_nchw": list(self.input_shape_nchw),
            },
        }

    @property
    def sha256(self) -> str:
        return _canonical_sha256(self.to_dict())


@dataclass(frozen=True, slots=True)
class ThresholdPoint:
    threshold: float
    precision: float
    recall: float
    small_person_recall: float
    map50: float

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], index: int) -> "ThresholdPoint":
        raw = _object(value, f"threshold_points[{index}]")
        _exact_fields(raw, _POINT_FIELDS, f"threshold_points[{index}]")
        return cls(
            threshold=_ratio(raw["threshold"], f"threshold_points[{index}].threshold"),
            precision=_ratio(raw["precision"], f"threshold_points[{index}].precision"),
            recall=_ratio(raw["recall"], f"threshold_points[{index}].recall"),
            small_person_recall=_ratio(
                raw["small_person_recall"],
                f"threshold_points[{index}].small_person_recall",
            ),
            map50=_ratio(raw["mAP50"], f"threshold_points[{index}].mAP50"),
        )

    @property
    def passes_accuracy_gates(self) -> bool:
        return (
            self.recall >= MIN_RECALL
            and self.precision >= MIN_PRECISION
            and self.map50 >= MIN_MAP50
            and self.small_person_recall >= MIN_SMALL_PERSON_RECALL
        )

    def to_dict(self) -> dict[str, float]:
        return {
            "threshold": self.threshold,
            "precision": self.precision,
            "recall": self.recall,
            "small_person_recall": self.small_person_recall,
            "mAP50": self.map50,
        }


@dataclass(frozen=True, slots=True)
class ValidationReport:
    candidate: str
    candidate_stage: Literal["full", "smoke"]
    architecture: str
    input_shape_nchw: tuple[int, int, int, int]
    training_plan_sha256: str
    split: Literal["val", "test"]
    data_kind: Literal["real_aerial", "synthetic_disaster"]
    dataset_id: str
    dataset_sha256: str
    model_sha256: str
    inference_evidence: InferenceEvidence
    aggregate_map50: float
    threshold_points: tuple[ThresholdPoint, ...]
    _issuance_token: object | None = field(
        default=None, repr=False, compare=False
    )
    _evidence_report_path: str | None = field(
        default=None, repr=False, compare=False
    )
    _evidence_report_sha256: str | None = field(
        default=None, repr=False, compare=False
    )
    _workspace_root: str | None = field(default=None, repr=False, compare=False)
    _repository_root: str | None = field(default=None, repr=False, compare=False)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ValidationReport":
        raw = _object(value, "validation report")
        _exact_fields(raw, _VALIDATION_FIELDS, "validation report")
        if raw["schema"] != VALIDATION_REPORT_SCHEMA:
            raise SelectionError(f"unsupported validation schema: {raw['schema']!r}")

        candidate = _string(raw["candidate"], "candidate")
        if candidate not in FULL_CANDIDATES | {SMOKE_CANDIDATE}:
            raise SelectionError(f"unknown detector candidate: {candidate!r}")
        stage = raw["candidate_stage"]
        expected_stage = "smoke" if candidate == SMOKE_CANDIDATE else "full"
        if stage != expected_stage:
            raise SelectionError(
                f"candidate_stage for {candidate!r} must be {expected_stage!r}"
            )
        architecture = _string(raw["architecture"], "architecture")
        input_shape = _input_shape(raw["input_shape_nchw"], "input_shape_nchw")
        expected_architecture, expected_shape = _CANDIDATE_IDENTITIES[candidate]
        if architecture != expected_architecture or input_shape != expected_shape:
            raise SelectionError(
                f"candidate {candidate!r} must bind architecture "
                f"{expected_architecture!r} and input shape {list(expected_shape)!r}"
            )
        training_plan_sha256 = _hash(
            raw["training_plan_sha256"], "training_plan_sha256"
        )
        split = raw["split"]
        if split not in {"val", "test"}:
            raise SelectionError("split must be exactly 'val' or 'test'")
        if raw["class_map"] != {"0": "person_candidate"}:
            raise SelectionError(
                "detector class_map must be exactly {'0': 'person_candidate'}; "
                "safe/disaster states belong to a separate classifier"
            )
        data_kind = raw["data_kind"]
        if data_kind not in DATA_KINDS:
            raise SelectionError(
                "data_kind must be 'real_aerial' or 'synthetic_disaster'"
            )
        inference_evidence = InferenceEvidence.from_dict(
            raw["inference_evidence"], expected_input_shape=input_shape
        )

        definition = _object(raw["small_person_definition"], "small_person_definition")
        _exact_fields(definition, _SMALL_PERSON_FIELDS, "small_person_definition")
        if definition != {
            "basis": SMALL_PERSON_BASIS,
            "max_area_px2": SMALL_PERSON_MAX_AREA_PX2,
            "inclusive": True,
        }:
            raise SelectionError(
                "small_person_definition must be bbox area <= 1024 px2 in "
                "original source pixels"
            )

        aggregate = _object(raw["aggregate"], "aggregate")
        _exact_fields(aggregate, _AGGREGATE_FIELDS, "aggregate")
        aggregate_map50 = _ratio(aggregate["mAP50"], "aggregate.mAP50")

        point_values = raw["threshold_points"]
        if type(point_values) is not list or not point_values:
            raise SelectionError("threshold_points must be a non-empty JSON array")
        points = tuple(
            ThresholdPoint.from_dict(point, index)
            for index, point in enumerate(point_values)
        )
        thresholds = [point.threshold for point in points]
        if thresholds != sorted(thresholds) or len(set(thresholds)) != len(thresholds):
            raise SelectionError("threshold_points must have unique ascending thresholds")
        if any(not math.isclose(point.map50, aggregate_map50, abs_tol=1e-12) for point in points):
            raise SelectionError("each threshold-point mAP50 must equal aggregate.mAP50")

        return cls(
            candidate=candidate,
            candidate_stage=stage,
            architecture=architecture,
            input_shape_nchw=input_shape,
            training_plan_sha256=training_plan_sha256,
            split=split,
            data_kind=data_kind,
            dataset_id=_string(raw["dataset_id"], "dataset_id"),
            dataset_sha256=_hash(raw["dataset_sha256"], "dataset_sha256"),
            model_sha256=_hash(raw["model_sha256"], "model_sha256"),
            inference_evidence=inference_evidence,
            aggregate_map50=aggregate_map50,
            threshold_points=points,
        )

    @classmethod
    def from_json_text(cls, text: str) -> "ValidationReport":
        return cls.from_dict(_loads(text))

    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> "ValidationReport":
        return _load_regular_file(path, cls.from_json_text)


    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": VALIDATION_REPORT_SCHEMA,
            "candidate": self.candidate,
            "candidate_stage": self.candidate_stage,
            "architecture": self.architecture,
            "input_shape_nchw": list(self.input_shape_nchw),
            "training_plan_sha256": self.training_plan_sha256,
            "split": self.split,
            "class_map": {"0": "person_candidate"},
            "data_kind": self.data_kind,
            "dataset_id": self.dataset_id,
            "dataset_sha256": self.dataset_sha256,
            "model_sha256": self.model_sha256,
            "inference_evidence": self.inference_evidence.to_dict(),
            "small_person_definition": {
                "basis": SMALL_PERSON_BASIS,
                "max_area_px2": SMALL_PERSON_MAX_AREA_PX2,
                "inclusive": True,
            },
            "aggregate": {"mAP50": self.aggregate_map50},
            "threshold_points": [point.to_dict() for point in self.threshold_points],
        }

    @property
    def sha256(self) -> str:
        return _canonical_sha256(self.to_dict())


@dataclass(frozen=True, slots=True)
class FrozenThreshold:
    candidate: str
    architecture: str
    input_shape_nchw: tuple[int, int, int, int]
    training_plan_sha256: str
    data_kind: str
    model_sha256: str
    point: ThresholdPoint


@dataclass(frozen=True, slots=True)
class ValidationEvidence:
    data_kind: str
    dataset_id: str
    dataset_sha256: str
    validation_report_sha256: str
    inference_evidence: InferenceEvidence
    metrics: ThresholdPoint

    def to_dict(self) -> dict[str, Any]:
        return {
            "data_kind": self.data_kind,
            "dataset_id": self.dataset_id,
            "dataset_sha256": self.dataset_sha256,
            "validation_report_sha256": self.validation_report_sha256,
            "inference_evidence": self.inference_evidence.to_dict(),
            "inference_evidence_sha256": self.inference_evidence.sha256,
            "metrics": self.metrics.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class QualificationEvidence:
    candidate: str
    qualification_sha256: str
    qualified: bool
    validation_passed: bool
    confidence_threshold: float
    source_pt_sha256: str
    onnx_sha256: str
    executed_engine_sha256: str
    report_hashes: tuple[tuple[str, str], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate": self.candidate,
            "qualification_sha256": self.qualification_sha256,
            "qualified": self.qualified,
            "validation_passed": self.validation_passed,
            "confidence_threshold": self.confidence_threshold,
            "source_pt_sha256": self.source_pt_sha256,
            "onnx_sha256": self.onnx_sha256,
            "executed_engine_sha256": self.executed_engine_sha256,
            "report_hashes": dict(self.report_hashes),
        }


@dataclass(frozen=True, slots=True)
class SelectedCandidate:
    candidate: str
    architecture: str
    input_shape_nchw: tuple[int, int, int, int]
    training_plan_sha256: str
    confidence_threshold: float
    source_pt_sha256: str
    onnx_sha256: str
    executed_engine_path: str
    executed_engine_sha256: str
    runtime: tuple[tuple[str, str], ...]
    real_aerial: ValidationEvidence
    synthetic_disaster: ValidationEvidence
    considered_candidates: tuple[QualificationEvidence, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SELECTION_REPORT_SCHEMA,
            "candidate": self.candidate,
            "architecture": self.architecture,
            "input_shape_nchw": list(self.input_shape_nchw),
            "training_plan_sha256": self.training_plan_sha256,
            "confidence_threshold": self.confidence_threshold,
            "artifacts": {
                "source_pt_sha256": self.source_pt_sha256,
                "onnx_sha256": self.onnx_sha256,
                "executed_engine_path": self.executed_engine_path,
                "executed_engine_sha256": self.executed_engine_sha256,
            },
            "runtime": dict(self.runtime),
            "validation": {
                "real_aerial": self.real_aerial.to_dict(),
                "synthetic_disaster": self.synthetic_disaster.to_dict(),
            },
            "considered_candidates": [
                evidence.to_dict() for evidence in self.considered_candidates
            ],
        }

    @property
    def sha256(self) -> str:
        return _canonical_sha256(self.to_dict())


def _metric_sort_key(point: ThresholdPoint) -> tuple[float, ...]:
    return (
        -point.recall,
        -point.small_person_recall,
        -point.precision,
        -point.map50,
    )


def _point_sort_key(point: ThresholdPoint) -> tuple[float, ...]:
    # Life-safety recall comes first. The lower threshold is the stable final
    # tie-break because equal metrics must not silently reduce sensitivity.
    return (*_metric_sort_key(point), point.threshold)


def _require_rederived_report(report: ValidationReport) -> ValidationReport:
    """Return a fresh evaluator-verified report or fail closed.

    Structural parsing alone cannot establish that metric numbers came from
    detector inference.  Keep the dependency local to avoid the module cycle:
    :mod:`evaluation` owns the recursive file/hash checks and metric
    rederivation, while this module owns policy and ranking.
    """

    if not isinstance(report, ValidationReport):
        raise SelectionError("selection requires evaluator-issued ValidationReport objects")
    try:
        from .evaluation import EvaluationError, revalidate_validation_report

        return revalidate_validation_report(report)
    except (EvaluationError, ArtifactIOError) as error:
        raise SelectionError(
            f"validation report is not sealed evaluator evidence: {error}"
        ) from error


def freeze_validation_threshold(report: ValidationReport) -> FrozenThreshold:
    """Freeze the best passing confidence threshold from one validation report."""

    report = _require_rederived_report(report)
    if report.split != "val":
        raise SelectionError("confidence thresholds may be selected from validation only")
    if report.candidate_stage != "full":
        raise SelectionError("the smoke candidate is never selectable")
    passing = [point for point in report.threshold_points if point.passes_accuracy_gates]
    if not passing:
        raise SelectionError(
            f"candidate {report.candidate!r} has no threshold passing all accuracy gates"
        )
    return FrozenThreshold(
        candidate=report.candidate,
        architecture=report.architecture,
        input_shape_nchw=report.input_shape_nchw,
        training_plan_sha256=report.training_plan_sha256,
        data_kind=report.data_kind,
        model_sha256=report.model_sha256,
        point=sorted(passing, key=_point_sort_key)[0],
    )


def select_final_candidate(
    validation_reports: Iterable[ValidationReport],
    candidate_qualifications: Iterable["CandidateQualification"],
) -> SelectedCandidate:
    """Provisionally select accuracy-first among validation-qualified candidates.

    Each candidate is evaluated at one common validation-selected threshold in
    both provenance domains. The ranking is lexicographic and keeps real and
    synthetic metrics separate; it never computes a cross-domain mean. Raw or
    self-attested benchmark mappings are not accepted: every qualification must
    be the concrete immutable object produced by :mod:`deployment` after it
    recomputes the PT-to-engine, validation-equivalence, and Nano evidence
    chain. Untouched-test evidence is not part of this API.
    """

    # Local import prevents an import cycle if deployment utilities import the
    # report constants elsewhere during CLI composition.
    from .deployment import (
        CANDIDATE_QUALIFICATION_SCHEMA,
        CandidateQualification,
        is_validated_candidate_qualification,
    )

    reports: dict[tuple[str, str], ValidationReport] = {}
    seen_candidates: set[str] = set()
    for supplied_report in validation_reports:
        report = _require_rederived_report(supplied_report)
        if report.split != "val":
            raise SelectionError("test reports are forbidden during shortlist/selection")
        if report.candidate_stage != "full":
            raise SelectionError("smoke reports are forbidden during final selection")
        key = (report.candidate, report.data_kind)
        if key in reports:
            raise SelectionError(f"duplicate validation report for {key!r}")
        reports[key] = report
        seen_candidates.add(report.candidate)

    missing_candidates = MANDATORY_FULL_CANDIDATES - seen_candidates
    if missing_candidates:
        raise SelectionError(
            f"mandatory full candidates are missing: {sorted(missing_candidates)}"
        )
    for candidate in seen_candidates:
        missing_kinds = DATA_KINDS - {
            kind for name, kind in reports if name == candidate
        }
        if missing_kinds:
            raise SelectionError(
                f"candidate {candidate!r} lacks separate provenance reports: "
                f"{sorted(missing_kinds)}"
            )

    for data_kind in sorted(DATA_KINDS):
        provenance = {
            (
                reports[(candidate, data_kind)].dataset_id,
                reports[(candidate, data_kind)].dataset_sha256,
                reports[
                    (candidate, data_kind)
                ].inference_evidence.ground_truth_records_path,
                reports[
                    (candidate, data_kind)
                ].inference_evidence.ground_truth_records_sha256,
                reports[
                    (candidate, data_kind)
                ].inference_evidence.ordered_image_ids_sha256,
            )
            for candidate in seen_candidates
        }
        if len(provenance) != 1:
            raise SelectionError(
                f"all compared candidates must use one identical {data_kind} "
                "dataset ID/hash and ground-truth/ordered-image evidence"
            )
    real_hash = reports[(sorted(seen_candidates)[0], "real_aerial")].dataset_sha256
    synthetic_hash = reports[
        (sorted(seen_candidates)[0], "synthetic_disaster")
    ].dataset_sha256
    if real_hash == synthetic_hash:
        raise SelectionError(
            "real_aerial and synthetic_disaster dataset hashes must differ"
        )
    inference_report_hashes = {
        report.inference_evidence.report_sha256 for report in reports.values()
    }
    if len(inference_report_hashes) != len(reports):
        raise SelectionError(
            "each candidate/provenance report must bind distinct inference evidence"
        )

    plan_hashes = {report.training_plan_sha256 for report in reports.values()}
    if len(plan_hashes) != 1:
        raise SelectionError(
            "all compared validation reports must bind one training plan SHA-256"
        )

    qualifications: dict[str, CandidateQualification] = {}
    for qualification in candidate_qualifications:
        if (
            type(qualification) is not CandidateQualification
            or not is_validated_candidate_qualification(qualification)
        ):
            raise SelectionError(
                "candidate_qualifications must contain sealed deployment-validated "
                "CandidateQualification objects"
            )
        if qualification.schema != CANDIDATE_QUALIFICATION_SCHEMA:
            raise SelectionError("candidate qualification schema is not frozen")
        if qualification.candidate in qualifications:
            raise SelectionError(
                f"duplicate candidate qualification for {qualification.candidate!r}"
            )
        qualifications[qualification.candidate] = qualification
    missing_qualifications = seen_candidates - qualifications.keys()
    extra_qualifications = qualifications.keys() - seen_candidates
    if missing_qualifications or extra_qualifications:
        raise SelectionError(
            "qualification pool must exactly match compared candidates; "
            f"missing={sorted(missing_qualifications)}, "
            f"extra={sorted(extra_qualifications)}"
        )

    eligible: list[
        tuple[
            str,
            CandidateQualification,
            ThresholdPoint,
            ThresholdPoint,
            ValidationReport,
            ValidationReport,
        ]
    ] = []
    considerations: list[QualificationEvidence] = []
    for candidate in sorted(seen_candidates):
        real = reports[(candidate, "real_aerial")]
        synthetic = reports[(candidate, "synthetic_disaster")]
        if real.model_sha256 != synthetic.model_sha256:
            raise SelectionError(
                f"candidate {candidate!r} provenance reports name different model hashes"
            )
        qualification = qualifications[candidate]
        expected_architecture, expected_shape = _CANDIDATE_IDENTITIES[candidate]
        if (
            qualification.architecture != expected_architecture
            or tuple(qualification.input_shape_nchw) != expected_shape
            or qualification.architecture != real.architecture
            or tuple(qualification.input_shape_nchw) != real.input_shape_nchw
        ):
            raise SelectionError(
                f"candidate {candidate!r} qualification architecture/shape does not "
                "match validation identity"
            )
        if qualification.training_plan_sha256 != real.training_plan_sha256:
            raise SelectionError(
                f"candidate {candidate!r} qualification training-plan hash does not "
                "match validation"
            )
        if qualification.source_pt_sha256 != real.model_sha256:
            raise SelectionError(
                f"candidate {candidate!r} qualified PT hash does not match validation"
            )

        synthetic_by_threshold = {
            point.threshold: point for point in synthetic.threshold_points
        }
        common_passing: list[tuple[ThresholdPoint, ThresholdPoint]] = []
        for real_point in real.threshold_points:
            synthetic_point = synthetic_by_threshold.get(real_point.threshold)
            if (
                synthetic_point is not None
                and real_point.passes_accuracy_gates
                and synthetic_point.passes_accuracy_gates
            ):
                common_passing.append((real_point, synthetic_point))
        selected_pair = (
            sorted(
                common_passing,
                key=lambda pair: (
                    *_metric_sort_key(pair[0]),
                    *_metric_sort_key(pair[1]),
                    pair[0].threshold,
                ),
            )[0]
            if common_passing
            else None
        )

        threshold = _ratio(
            qualification.confidence_threshold,
            f"qualification[{candidate}].confidence_threshold",
        )
        if selected_pair is not None and threshold != selected_pair[0].threshold:
            raise SelectionError(
                f"candidate {candidate!r} qualification threshold is not the "
                "deterministic validation-selected threshold"
            )
        if type(qualification.qualified) is not bool:
            raise SelectionError(
                f"qualification[{candidate}].qualified must be boolean"
            )

        report_hashes = tuple(
            sorted(
                (
                    ("training_completion", _hash(
                        qualification.training_completion_report_sha256,
                        f"qualification[{candidate}].training_completion_report_sha256",
                    )),
                    ("threshold_selection", _hash(
                        qualification.threshold_selection_report_sha256,
                        f"qualification[{candidate}].threshold_selection_report_sha256",
                    )),
                    ("onnx_export", _hash(
                        qualification.onnx_report_sha256,
                        f"qualification[{candidate}].onnx_report_sha256",
                    )),
                    ("engine_identity", _hash(
                        qualification.engine_identity_report_sha256,
                        f"qualification[{candidate}].engine_identity_report_sha256",
                    )),
                    ("validation_accuracy_equivalence", _hash(
                        qualification.validation_accuracy_equivalence_report_sha256,
                        f"qualification[{candidate}].validation_accuracy_equivalence_report_sha256",
                    )),
                    ("nano_benchmark", _hash(
                        qualification.nano_benchmark_report_sha256,
                        f"qualification[{candidate}].nano_benchmark_report_sha256",
                    )),
                )
            )
        )
        qualification_dict = qualification.to_dict()
        if type(qualification_dict) is not dict:
            raise SelectionError("CandidateQualification.to_dict() must return a dict")
        considerations.append(
            QualificationEvidence(
                candidate=candidate,
                qualification_sha256=_canonical_sha256(qualification_dict),
                qualified=qualification.qualified,
                validation_passed=selected_pair is not None,
                confidence_threshold=threshold,
                source_pt_sha256=_hash(
                    qualification.source_pt_sha256,
                    f"qualification[{candidate}].source_pt_sha256",
                ),
                onnx_sha256=_hash(
                    qualification.onnx_sha256,
                    f"qualification[{candidate}].onnx_sha256",
                ),
                executed_engine_sha256=_hash(
                    qualification.executed_engine_sha256,
                    f"qualification[{candidate}].executed_engine_sha256",
                ),
                report_hashes=report_hashes,
            )
        )
        if selected_pair is not None and qualification.qualified:
            eligible.append(
                (
                    candidate,
                    qualification,
                    selected_pair[0],
                    selected_pair[1],
                    real,
                    synthetic,
                )
            )

    if not eligible:
        raise SelectionError(
            "no candidate passes both validation provenance gates and the actual Jetson gate"
        )

    # No latency term appears here: Jetson performance is a pass/fail gate and
    # cannot make a less accurate passing candidate win.
    chosen = sorted(
        eligible,
        key=lambda item: (
            *_metric_sort_key(item[2]),
            *_metric_sort_key(item[3]),
            item[2].threshold,
            item[0],
        ),
    )[0]
    selected_name, qualification, real_point, synthetic_point, real, synthetic = chosen

    runtime_raw = qualification.runtime
    if not isinstance(runtime_raw, Mapping) or not all(
        type(key) is str and type(value) is str
        for key, value in runtime_raw.items()
    ):
        raise SelectionError("selected qualification runtime must map strings to strings")
    executed_engine_path = _string(
        qualification.executed_engine_path,
        "selected qualification executed_engine_path",
    )
    return SelectedCandidate(
        candidate=selected_name,
        architecture=qualification.architecture,
        input_shape_nchw=tuple(qualification.input_shape_nchw),
        training_plan_sha256=qualification.training_plan_sha256,
        confidence_threshold=real_point.threshold,
        source_pt_sha256=qualification.source_pt_sha256,
        onnx_sha256=qualification.onnx_sha256,
        executed_engine_path=executed_engine_path,
        executed_engine_sha256=qualification.executed_engine_sha256,
        runtime=tuple(sorted(runtime_raw.items())),
        real_aerial=ValidationEvidence(
            data_kind=real.data_kind,
            dataset_id=real.dataset_id,
            dataset_sha256=real.dataset_sha256,
            validation_report_sha256=real.sha256,
            inference_evidence=real.inference_evidence,
            metrics=real_point,
        ),
        synthetic_disaster=ValidationEvidence(
            data_kind=synthetic.data_kind,
            dataset_id=synthetic.dataset_id,
            dataset_sha256=synthetic.dataset_sha256,
            validation_report_sha256=synthetic.sha256,
            inference_evidence=synthetic.inference_evidence,
            metrics=synthetic_point,
        ),
        considered_candidates=tuple(considerations),
    )
