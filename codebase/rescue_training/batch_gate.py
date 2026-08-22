"""Immutable batch-size decisions derived from the mandatory smoke run.

The full training commands must not accept an operator's batch-size guess.  A
decision created here is bound to one completed, passing ``yolov8n-640-smoke``
report and is re-derived whenever the artifact is validated.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .artifact_io import (
    ArtifactIOError,
    create_external_json,
    require_external_workspace,
    require_path_within_workspace,
    sha256_file,
)
from .contracts import MODEL_ID


BATCH_DECISION_SCHEMA = "veriswarm.rescue.batch_decision.v1"
TRAINING_RUN_SCHEMA = "veriswarm.rescue.training_run.v1"
SMOKE_CANDIDATE = "yolov8n-640-smoke"
FULL_640_CANDIDATE = "yolov8n-640"
FULL_960_CANDIDATE = "yolov8n-960"
OPTIONAL_S_CANDIDATE = "yolov8s-640"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_ROOT_FIELDS = frozenset(
    {
        "schema",
        "model_id",
        "created_at_utc",
        "source_smoke_report",
        "lineage",
        "measurement",
        "candidates",
        "passed",
    }
)
_LINEAGE_FIELDS = frozenset(
    {
        "training_plan_sha256",
        "source_commit",
        "dataset_yaml_sha256",
        "dataset_audit_sha256",
        "base_checkpoint_sha256",
    }
)


class BatchDecisionError(ValueError):
    """The smoke evidence or derived batch decision is unsafe or ambiguous."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise BatchDecisionError(f"duplicate JSON field is forbidden: {key!r}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise BatchDecisionError(f"non-finite JSON constant is forbidden: {value}")


def _load_json(path: Path, field: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise BatchDecisionError(f"{field} must be one regular file: {path}")
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except BatchDecisionError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise BatchDecisionError(f"cannot read strict JSON {path}: {error}") from error
    if type(value) is not dict:
        raise BatchDecisionError(f"{field} must contain one JSON object")
    return value


def _object(value: Any, field: str, expected: frozenset[str] | None = None) -> dict[str, Any]:
    if type(value) is not dict or not all(type(key) is str for key in value):
        raise BatchDecisionError(f"{field} must be a JSON object with string keys")
    if expected is not None and frozenset(value) != expected:
        missing = sorted(expected - frozenset(value))
        unknown = sorted(frozenset(value) - expected)
        raise BatchDecisionError(
            f"{field} fields are not frozen; missing={missing}, unknown={unknown}"
        )
    return value


def _text(value: Any, field: str) -> str:
    if type(value) is not str or not value or value.strip() != value:
        raise BatchDecisionError(f"{field} must be a non-empty trimmed string")
    return value


def _sha256(value: Any, field: str) -> str:
    digest = _text(value, field)
    if not _SHA256_RE.fullmatch(digest):
        raise BatchDecisionError(f"{field} must be a lowercase SHA-256")
    return digest


def _commit(value: Any, field: str) -> str:
    commit = _text(value, field)
    if not _COMMIT_RE.fullmatch(commit):
        raise BatchDecisionError(f"{field} must be a lowercase 40-character Git commit")
    return commit


def _positive_integer(value: Any, field: str) -> int:
    if type(value) is not int or value <= 0:
        raise BatchDecisionError(f"{field} must be a positive integer")
    return value


def _utc_timestamp(value: Any, field: str) -> str:
    text = _text(value, field)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise BatchDecisionError(f"{field} must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise BatchDecisionError(f"{field} must identify UTC explicitly")
    return text


def _expected_960_batch(smoke_batch: int) -> int:
    # Integer arithmetic implements floor(smoke_batch * (640 / 960) ** 2)
    # exactly, without a binary floating-point boundary error.
    return max(1, (smoke_batch * 640 * 640) // (960 * 960))


def _lineage_from_smoke(report: Mapping[str, Any]) -> dict[str, str]:
    source = _object(report.get("source"), "smoke report.source")
    dataset = _object(report.get("dataset"), "smoke report.dataset")
    audit = _object(dataset.get("audit"), "smoke report.dataset.audit")
    checkpoint = _object(report.get("base_checkpoint"), "smoke report.base_checkpoint")
    return {
        "training_plan_sha256": _sha256(
            report.get("training_plan_sha256"), "smoke report.training_plan_sha256"
        ),
        "source_commit": _commit(source.get("commit"), "smoke report.source.commit"),
        "dataset_yaml_sha256": _sha256(
            dataset.get("yaml_sha256"), "smoke report.dataset.yaml_sha256"
        ),
        "dataset_audit_sha256": _sha256(
            audit.get("report_sha256"), "smoke report.dataset.audit.report_sha256"
        ),
        "base_checkpoint_sha256": _sha256(
            checkpoint.get("sha256"), "smoke report.base_checkpoint.sha256"
        ),
    }


def _validate_smoke_report(
    report: Mapping[str, Any], expected_lineage: Mapping[str, str]
) -> int:
    if report.get("schema") != TRAINING_RUN_SCHEMA:
        raise BatchDecisionError("unsupported smoke training-report schema")
    if report.get("status") != "completed":
        raise BatchDecisionError("smoke training report is not completed")
    if report.get("model_id") != MODEL_ID:
        raise BatchDecisionError("smoke training report has the wrong model_id")
    if report.get("class_map") != {"0": "person_candidate"}:
        raise BatchDecisionError("smoke training report has the wrong class_map")

    candidate = _object(report.get("candidate"), "smoke report.candidate")
    expected_candidate = {
        "name": SMOKE_CANDIDATE,
        "architecture": "yolov8n.pt",
        "imgsz": 640,
        "epochs": 5,
        "batch": 0.70,
        "required": True,
        "stage": "smoke",
    }
    if candidate != expected_candidate:
        raise BatchDecisionError(
            "batch evidence must come from the frozen yolov8n-640 smoke candidate"
        )

    smoke_gate = _object(report.get("smoke_gate"), "smoke report.smoke_gate")
    if smoke_gate.get("passed") is not True:
        raise BatchDecisionError("smoke training gate did not pass")
    for field in (
        "recall_is_positive",
        "cuda_peak_recorded",
        "vram_headroom_at_least_10_percent",
        "all_epoch_losses_finite",
    ):
        if smoke_gate.get(field) is not True:
            raise BatchDecisionError(f"smoke training gate did not prove {field}")

    actual_lineage = _lineage_from_smoke(report)
    if actual_lineage != dict(expected_lineage):
        raise BatchDecisionError(
            "smoke training lineage does not match the requested frozen inputs"
        )
    source = _object(report.get("source"), "smoke report.source")
    if source.get("clean") is not True:
        raise BatchDecisionError("smoke training source checkout was not clean")
    return _positive_integer(report.get("resolved_batch_size"), "smoke report.resolved_batch_size")


def _candidate_decisions(smoke_batch: int) -> dict[str, dict[str, Any]]:
    return {
        FULL_640_CANDIDATE: {
            "authorized": True,
            "batch": smoke_batch,
            "method": "exact_resolved_yolov8n_640_smoke_batch",
        },
        FULL_960_CANDIDATE: {
            "authorized": True,
            "batch": _expected_960_batch(smoke_batch),
            "method": "floor(smoke_batch*(640/960)^2),minimum_1",
        },
        OPTIONAL_S_CANDIDATE: {
            "authorized": False,
            "batch": None,
            "reason": "architecture_specific_sizing_evidence_absent",
        },
    }


def create_batch_decision(
    *,
    smoke_report_path: str | os.PathLike[str],
    output_path: str | os.PathLike[str],
    expected_training_plan_sha256: str,
    expected_source_commit: str,
    expected_dataset_yaml_sha256: str,
    expected_dataset_audit_sha256: str,
    expected_base_checkpoint_sha256: str,
    created_at_utc: str,
    workspace_root: str | os.PathLike[str],
    repository_root: str | os.PathLike[str],
) -> Path:
    """Create one immutable decision after revalidating the complete smoke lineage."""

    expected_lineage = {
        "training_plan_sha256": _sha256(
            expected_training_plan_sha256, "expected_training_plan_sha256"
        ),
        "source_commit": _commit(expected_source_commit, "expected_source_commit"),
        "dataset_yaml_sha256": _sha256(
            expected_dataset_yaml_sha256, "expected_dataset_yaml_sha256"
        ),
        "dataset_audit_sha256": _sha256(
            expected_dataset_audit_sha256, "expected_dataset_audit_sha256"
        ),
        "base_checkpoint_sha256": _sha256(
            expected_base_checkpoint_sha256, "expected_base_checkpoint_sha256"
        ),
    }
    try:
        workspace = require_external_workspace(workspace_root, repository_root)
        source = require_path_within_workspace(smoke_report_path, workspace)
        destination = require_path_within_workspace(output_path, workspace)
        source_hash = sha256_file(source)
    except ArtifactIOError as error:
        raise BatchDecisionError(str(error)) from error
    report = _load_json(source, "smoke training report")
    smoke_batch = _validate_smoke_report(report, expected_lineage)
    payload = {
        "schema": BATCH_DECISION_SCHEMA,
        "model_id": MODEL_ID,
        "created_at_utc": _utc_timestamp(created_at_utc, "created_at_utc"),
        "source_smoke_report": {"path": str(source), "sha256": source_hash},
        "lineage": expected_lineage,
        "measurement": {
            "candidate": SMOKE_CANDIDATE,
            "imgsz": 640,
            "resolved_batch_size": smoke_batch,
        },
        "candidates": _candidate_decisions(smoke_batch),
        "passed": True,
    }
    validate_batch_decision(payload, workspace_root=workspace, repository_root=repository_root)
    try:
        return create_external_json(
            destination,
            payload,
            workspace_root=workspace,
            repository_root=repository_root,
        )
    except ArtifactIOError as error:
        raise BatchDecisionError(str(error)) from error


def validate_batch_decision(
    value: Mapping[str, Any],
    *,
    workspace_root: str | os.PathLike[str],
    repository_root: str | os.PathLike[str],
) -> dict[str, Any]:
    """Validate and re-derive a batch decision against its hashed smoke report."""

    raw = _object(value, "batch decision", _ROOT_FIELDS)
    if raw["schema"] != BATCH_DECISION_SCHEMA or raw["model_id"] != MODEL_ID:
        raise BatchDecisionError("unsupported batch-decision identity")
    _utc_timestamp(raw["created_at_utc"], "created_at_utc")
    if raw["passed"] is not True:
        raise BatchDecisionError("batch decision must be a derived passing artifact")
    lineage = _object(raw["lineage"], "lineage", _LINEAGE_FIELDS)
    normalized_lineage = {
        "training_plan_sha256": _sha256(
            lineage["training_plan_sha256"], "lineage.training_plan_sha256"
        ),
        "source_commit": _commit(lineage["source_commit"], "lineage.source_commit"),
        "dataset_yaml_sha256": _sha256(
            lineage["dataset_yaml_sha256"], "lineage.dataset_yaml_sha256"
        ),
        "dataset_audit_sha256": _sha256(
            lineage["dataset_audit_sha256"], "lineage.dataset_audit_sha256"
        ),
        "base_checkpoint_sha256": _sha256(
            lineage["base_checkpoint_sha256"], "lineage.base_checkpoint_sha256"
        ),
    }
    source_record = _object(
        raw["source_smoke_report"],
        "source_smoke_report",
        frozenset({"path", "sha256"}),
    )
    try:
        workspace = require_external_workspace(workspace_root, repository_root)
        source = require_path_within_workspace(
            _text(source_record["path"], "source_smoke_report.path"), workspace
        )
        actual_source_hash = sha256_file(source)
    except ArtifactIOError as error:
        raise BatchDecisionError(str(error)) from error
    if actual_source_hash != _sha256(source_record["sha256"], "source_smoke_report.sha256"):
        raise BatchDecisionError("smoke training report SHA-256 mismatch")
    smoke_report = _load_json(source, "smoke training report")
    smoke_batch = _validate_smoke_report(smoke_report, normalized_lineage)

    measurement = _object(
        raw["measurement"],
        "measurement",
        frozenset({"candidate", "imgsz", "resolved_batch_size"}),
    )
    if measurement != {
        "candidate": SMOKE_CANDIDATE,
        "imgsz": 640,
        "resolved_batch_size": smoke_batch,
    }:
        raise BatchDecisionError("batch measurement differs from the hashed smoke report")
    candidates = _object(
        raw["candidates"],
        "candidates",
        frozenset({FULL_640_CANDIDATE, FULL_960_CANDIDATE, OPTIONAL_S_CANDIDATE}),
    )
    if candidates != _candidate_decisions(smoke_batch):
        raise BatchDecisionError("candidate batch decisions are not the required derivation")
    return dict(raw)


def load_batch_decision(
    path: str | os.PathLike[str],
    *,
    workspace_root: str | os.PathLike[str],
    repository_root: str | os.PathLike[str],
) -> dict[str, Any]:
    """Load strict JSON and validate its live source-report binding."""

    try:
        workspace = require_external_workspace(workspace_root, repository_root)
        source = require_path_within_workspace(path, workspace)
    except ArtifactIOError as error:
        raise BatchDecisionError(str(error)) from error
    value = _load_json(source, "batch decision")
    return validate_batch_decision(
        value, workspace_root=workspace_root, repository_root=repository_root
    )


def authorized_batch(value: Mapping[str, Any], candidate: str) -> int:
    """Return one authorized mandatory batch and reject unsupported candidates."""

    if candidate not in {FULL_640_CANDIDATE, FULL_960_CANDIDATE, OPTIONAL_S_CANDIDATE}:
        raise BatchDecisionError(f"candidate {candidate!r} is not declared by the batch gate")
    candidates = _object(value.get("candidates"), "candidates")
    record = _object(candidates.get(candidate), f"candidates.{candidate}")
    if record.get("authorized") is not True:
        raise BatchDecisionError(
            f"candidate {candidate!r} has no architecture-specific batch evidence"
        )
    expected_method = (
        "exact_resolved_yolov8n_640_smoke_batch"
        if candidate == FULL_640_CANDIDATE
        else "floor(smoke_batch*(640/960)^2),minimum_1"
    )
    if set(record) != {"authorized", "batch", "method"} or record.get("method") != expected_method:
        raise BatchDecisionError(f"candidate {candidate!r} has malformed batch evidence")
    return _positive_integer(record.get("batch"), f"candidates.{candidate}.batch")


__all__ = [
    "BATCH_DECISION_SCHEMA",
    "BatchDecisionError",
    "authorized_batch",
    "create_batch_decision",
    "load_batch_decision",
    "validate_batch_decision",
]
