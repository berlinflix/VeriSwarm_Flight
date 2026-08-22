from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from rescue_training.batch_gate import (
    BATCH_DECISION_SCHEMA,
    BatchDecisionError,
    authorized_batch,
    create_batch_decision,
    load_batch_decision,
)


PLAN_HASH = "1" * 64
COMMIT = "2" * 40
YAML_HASH = "3" * 64
AUDIT_HASH = "4" * 64
BASE_HASH = "5" * 64


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _smoke_report(workspace: Path, *, batch: int = 18, passed: bool = True) -> Path:
    target = workspace / "runs" / "smoke" / "veriswarm_training_run.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {
                "schema": "veriswarm.rescue.training_run.v1",
                "status": "completed",
                "accepted_by_human": False,
                "model_id": "sar-rgb-person-v1",
                "training_plan_sha256": PLAN_HASH,
                "source": {"commit": COMMIT, "branch": "test", "clean": True},
                "class_map": {"0": "person_candidate"},
                "candidate": {
                    "name": "yolov8n-640-smoke",
                    "architecture": "yolov8n.pt",
                    "imgsz": 640,
                    "epochs": 5,
                    "batch": 0.7,
                    "required": True,
                    "stage": "smoke",
                },
                "dataset": {
                    "yaml_path": str(workspace / "dataset.yaml"),
                    "yaml_sha256": YAML_HASH,
                    "audit": {"report_sha256": AUDIT_HASH},
                    "visual_review": {"decision": "accepted"},
                },
                "base_checkpoint": {
                    "intake_path": str(workspace / "yolov8n.pt"),
                    "staged_path": str(workspace / "staged" / "yolov8n.pt"),
                    "sha256": BASE_HASH,
                    "trusted_intake": {"sha256": BASE_HASH},
                },
                "runtime": {"gpu_name": "NVIDIA GeForce RTX 5090"},
                "arguments": {},
                "effective_arguments": {},
                "resolved_batch_size": batch,
                "started_at_utc": "2026-08-22T10:00:00+00:00",
                "finished_at_utc": "2026-08-22T10:05:00+00:00",
                "duration_seconds": 300.0,
                "cuda_peak_bytes": {"allocated": 1, "reserved": 1},
                "metrics": {},
                "smoke_gate": {
                    "passed": passed,
                    "recall_is_positive": True,
                    "cuda_peak_recorded": True,
                    "vram_headroom_at_least_10_percent": True,
                    "all_epoch_losses_finite": True,
                },
                "artifacts": {},
            },
            allow_nan=False,
        ),
        encoding="utf-8",
    )
    return target


def _create(workspace: Path, repository: Path, report: Path, output: Path) -> Path:
    return create_batch_decision(
        smoke_report_path=report,
        output_path=output,
        expected_training_plan_sha256=PLAN_HASH,
        expected_source_commit=COMMIT,
        expected_dataset_yaml_sha256=YAML_HASH,
        expected_dataset_audit_sha256=AUDIT_HASH,
        expected_base_checkpoint_sha256=BASE_HASH,
        created_at_utc="2026-08-22T10:06:00Z",
        workspace_root=workspace,
        repository_root=repository,
    )


def test_batch_decision_is_bound_and_derived_conservatively(tmp_path):
    workspace = (tmp_path / "workspace").resolve()
    repository = (tmp_path / "repository").resolve()
    workspace.mkdir()
    repository.mkdir()
    smoke = _smoke_report(workspace, batch=18)
    output = workspace / "manifests" / "batch-decision.json"

    result = _create(workspace, repository, smoke, output)
    decision = load_batch_decision(
        result, workspace_root=workspace, repository_root=repository
    )

    assert decision["schema"] == BATCH_DECISION_SCHEMA
    assert decision["source_smoke_report"] == {
        "path": str(smoke.resolve()),
        "sha256": _sha(smoke),
    }
    assert authorized_batch(decision, "yolov8n-640") == 18
    assert authorized_batch(decision, "yolov8n-960") == 8
    with pytest.raises(BatchDecisionError, match="architecture-specific"):
        authorized_batch(decision, "yolov8s-640")
    assert decision["candidates"]["yolov8s-640"]["authorized"] is False


def test_small_smoke_batch_never_derives_zero_for_960(tmp_path):
    workspace = (tmp_path / "workspace").resolve()
    repository = (tmp_path / "repository").resolve()
    workspace.mkdir()
    repository.mkdir()
    smoke = _smoke_report(workspace, batch=1)
    decision_path = _create(
        workspace, repository, smoke, workspace / "batch-decision.json"
    )
    decision = load_batch_decision(
        decision_path, workspace_root=workspace, repository_root=repository
    )
    assert authorized_batch(decision, "yolov8n-960") == 1


def test_failed_or_wrong_lineage_smoke_report_is_rejected(tmp_path):
    workspace = (tmp_path / "workspace").resolve()
    repository = (tmp_path / "repository").resolve()
    workspace.mkdir()
    repository.mkdir()
    failed = _smoke_report(workspace, passed=False)
    with pytest.raises(BatchDecisionError, match="did not pass"):
        _create(workspace, repository, failed, workspace / "failed.json")

    failed.unlink()
    smoke = _smoke_report(workspace)
    with pytest.raises(BatchDecisionError, match="lineage"):
        create_batch_decision(
            smoke_report_path=smoke,
            output_path=workspace / "wrong-lineage.json",
            expected_training_plan_sha256="9" * 64,
            expected_source_commit=COMMIT,
            expected_dataset_yaml_sha256=YAML_HASH,
            expected_dataset_audit_sha256=AUDIT_HASH,
            expected_base_checkpoint_sha256=BASE_HASH,
            created_at_utc="2026-08-22T10:06:00+00:00",
            workspace_root=workspace,
            repository_root=repository,
        )


def test_decision_is_create_once_and_detects_later_smoke_mutation(tmp_path):
    workspace = (tmp_path / "workspace").resolve()
    repository = (tmp_path / "repository").resolve()
    workspace.mkdir()
    repository.mkdir()
    smoke = _smoke_report(workspace)
    output = workspace / "batch-decision.json"
    _create(workspace, repository, smoke, output)
    original = output.read_bytes()
    with pytest.raises(BatchDecisionError, match="overwrite"):
        _create(workspace, repository, smoke, output)
    assert output.read_bytes() == original

    smoke.write_text(smoke.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(BatchDecisionError, match="SHA-256 mismatch"):
        load_batch_decision(
            output, workspace_root=workspace, repository_root=repository
        )


@pytest.mark.parametrize(
    "text,message",
    [
        ('{"schema":"a","schema":"b"}', "duplicate JSON field"),
        ('{"resolved_batch_size":NaN}', "non-finite JSON constant"),
    ],
)
def test_strict_smoke_json_rejects_duplicates_and_nonfinite(tmp_path, text, message):
    workspace = (tmp_path / "workspace").resolve()
    repository = (tmp_path / "repository").resolve()
    workspace.mkdir()
    repository.mkdir()
    smoke = workspace / "smoke.json"
    smoke.write_text(text, encoding="utf-8")
    with pytest.raises(BatchDecisionError, match=message):
        _create(workspace, repository, smoke, workspace / "decision.json")


@pytest.mark.parametrize(
    "text,message",
    [
        ('{"schema":"a","schema":"b"}', "duplicate JSON field"),
        ('{"resolved_batch_size":Infinity}', "non-finite JSON constant"),
    ],
)
def test_saved_decision_rejects_duplicates_and_nonfinite(tmp_path, text, message):
    workspace = (tmp_path / "workspace").resolve()
    repository = (tmp_path / "repository").resolve()
    workspace.mkdir()
    repository.mkdir()
    decision = workspace / "decision.json"
    decision.write_text(text, encoding="utf-8")
    with pytest.raises(BatchDecisionError, match=message):
        load_batch_decision(
            decision, workspace_root=workspace, repository_root=repository
        )
