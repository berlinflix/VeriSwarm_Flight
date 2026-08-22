from __future__ import annotations

import hashlib
import json

import pytest
import rescue_training.review_gate as review_gate

from rescue_training.review_gate import (
    DatasetQualificationError,
    create_automated_dataset_qualification,
    validate_automated_dataset_qualification,
)
from rescue_training.visdrone import validate_visdrone_conversion_report as real_conversion_validator


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _roots(tmp_path):
    workspace = tmp_path / "workspace"
    repository = tmp_path / "repository"
    workspace.mkdir()
    repository.mkdir()
    return workspace, repository


@pytest.fixture(autouse=True)
def _validated_conversion_test_seam(monkeypatch):
    def validate(report_path, *, workspace_root, repository_root, **_kwargs):
        dataset_yaml = report_path.parent / "dataset.yaml"
        return {
            "schema": "veriswarm.rescue.visdrone_lineage_validation.v1",
            "conversion_report": {
                "path": str(report_path.resolve()),
                "sha256": "c" * 64,
                "schema": "veriswarm.rescue.visdrone_conversion.v2",
            },
            "output_root": str(report_path.parent.resolve()),
            "dataset_yaml": {
                "path": str(dataset_yaml.resolve()),
                "sha256": _sha(dataset_yaml),
            },
            "class_map": {"0": "person_candidate"},
            "source_lineage": {"train": {}, "val": {}},
            "derived_sample_set_sha256": {"train": "d" * 64, "val": "e" * 64},
        }

    monkeypatch.setattr(review_gate, "validate_visdrone_conversion_report", validate)


def _artifacts(tmp_path):
    audit = tmp_path / "audit.json"
    dataset_yaml = tmp_path / "dataset.yaml"
    montage = tmp_path / "montage.jpg"
    dataset_yaml.write_text("names:\n  0: person_candidate\n", encoding="utf-8")
    montage.write_bytes(b"deterministic-montage")
    splits = {
        "train": {"images": 6471, "sample_set_sha256": "1" * 64},
        "val": {"images": 548, "sample_set_sha256": "2" * 64},
    }
    audit.write_text(
        json.dumps(
            {
                "schema": "veriswarm.dataset_audit.v1",
                "passed": True,
                "class_map": {"0": "person_candidate"},
                "splits": {
                    split: {
                        "image_root": str((tmp_path / "images" / split).resolve()),
                        "label_root": str((tmp_path / "labels" / split).resolve()),
                        "images": evidence["images"],
                        "labels": evidence["images"],
                        "paired": evidence["images"],
                        "valid_samples": evidence["images"],
                        "missing_labels": 0,
                        "orphan_labels": 0,
                        "sample_set_sha256": evidence["sample_set_sha256"],
                    }
                    for split, evidence in splits.items()
                },
                "totals": {
                    "valid_samples": 7019,
                    "boxes": 10,
                    "class_box_counts": {"0": 10},
                    "cross_split_leakage_groups": 0,
                    "errors": 0,
                },
                "cross_split_leakage": [],
                "errors": [],
                "montage": {
                    "requested_samples": 100,
                    "rendered_samples": 100,
                    "path": montage.name,
                    "sha256": _sha(montage),
                },
            }
        ),
        encoding="utf-8",
    )
    return audit, dataset_yaml, montage, splits


def _create(tmp_path):
    workspace, repository = _roots(tmp_path)
    audit, dataset_yaml, montage, splits = _artifacts(workspace)
    qualification = workspace / "automated_dataset_qualification.json"
    create_automated_dataset_qualification(
        qualification,
        audit_report_path=audit,
        audit_report_sha256=_sha(audit),
        dataset_yaml_path=dataset_yaml,
        dataset_yaml_sha256=_sha(dataset_yaml),
        montage_path=montage,
        montage_sha256=_sha(montage),
        splits=splits,
        workspace_root=workspace,
        repository_root=repository,
        qualified_at_utc="2026-08-22T10:30:00+00:00",
    )
    return audit, dataset_yaml, montage, splits, qualification


def _validate(audit, dataset_yaml, montage, splits, qualification):
    return validate_automated_dataset_qualification(
        qualification,
        expected_audit_path=audit,
        expected_audit_sha256=_sha(audit),
        expected_dataset_yaml_path=dataset_yaml,
        expected_dataset_yaml_sha256=_sha(dataset_yaml),
        expected_montage_path=montage,
        expected_montage_sha256=_sha(montage),
        expected_splits=splits,
        workspace_root=dataset_yaml.parent,
        repository_root=dataset_yaml.parent.parent / "repository",
    )


def test_automated_qualification_is_hash_bound_and_never_human(tmp_path):
    audit, dataset_yaml, montage, splits, qualification = _create(tmp_path)

    result = _validate(audit, dataset_yaml, montage, splits, qualification)

    assert result["schema"] == "veriswarm.rescue.dataset_qualification.v3"
    assert result["qualification_mode"] == "fully_automated"
    assert result["decision"] == "qualified"
    assert result["accepted_by_human"] is False
    assert result["checks"] == {
        "canonical_structure_and_counts": True,
        "image_label_pairing_complete": True,
        "label_syntax_and_bounds_valid": True,
        "corrupt_or_unreadable_samples": 0,
        "cross_split_duplicate_groups": 0,
        "sample_sets_rehashed_before_training": True,
        "rendered_sample_artifact_hash_bound": True,
        "registered_archive_lineage_revalidated": True,
    }
    assert result["qualification_sha256"] == _sha(qualification)


def test_qualification_is_create_once(tmp_path):
    audit, dataset_yaml, montage, splits, qualification = _create(tmp_path)

    with pytest.raises(DatasetQualificationError, match="refusing to overwrite"):
        create_automated_dataset_qualification(
            qualification,
            audit_report_path=audit,
            audit_report_sha256=_sha(audit),
            dataset_yaml_path=dataset_yaml,
            dataset_yaml_sha256=_sha(dataset_yaml),
            montage_path=montage,
            montage_sha256=_sha(montage),
            splits=splits,
            workspace_root=dataset_yaml.parent,
            repository_root=dataset_yaml.parent.parent / "repository",
        )


def test_qualification_cannot_be_created_without_registered_conversion_lineage(
    tmp_path, monkeypatch
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repository = tmp_path / "repository"
    repository.mkdir()
    audit, dataset_yaml, montage, splits = _artifacts(workspace)
    monkeypatch.setattr(
        review_gate,
        "validate_visdrone_conversion_report",
        real_conversion_validator,
    )
    with pytest.raises(DatasetQualificationError, match="source lineage is invalid"):
        create_automated_dataset_qualification(
            workspace / "must-not-exist.json",
            audit_report_path=audit,
            audit_report_sha256=_sha(audit),
            dataset_yaml_path=dataset_yaml,
            dataset_yaml_sha256=_sha(dataset_yaml),
            montage_path=montage,
            montage_sha256=_sha(montage),
            splits=splits,
            workspace_root=workspace,
            repository_root=repository,
        )


def test_failed_or_corrupt_dataset_audit_cannot_create_receipt(tmp_path):
    workspace, repository = _roots(tmp_path)
    audit, dataset_yaml, montage, splits = _artifacts(workspace)
    value = json.loads(audit.read_text(encoding="utf-8"))
    value["passed"] = False
    value["totals"]["errors"] = 1
    value["errors"] = [{"reason": "unreadable_image"}]
    audit.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(DatasetQualificationError, match="audit did not pass"):
        create_automated_dataset_qualification(
            workspace / "must-not-exist.json",
            audit_report_path=audit,
            audit_report_sha256=_sha(audit),
            dataset_yaml_path=dataset_yaml,
            dataset_yaml_sha256=_sha(dataset_yaml),
            montage_path=montage,
            montage_sha256=_sha(montage),
            splits=splits,
            workspace_root=workspace,
            repository_root=repository,
        )


def test_tampered_montage_fails_closed(tmp_path):
    audit, dataset_yaml, montage, splits, qualification = _create(tmp_path)
    expected_montage_hash = _sha(montage)
    montage.write_bytes(b"tampered")

    with pytest.raises(DatasetQualificationError, match="montage SHA-256 mismatch"):
        validate_automated_dataset_qualification(
            qualification,
            expected_audit_path=audit,
            expected_audit_sha256=_sha(audit),
            expected_dataset_yaml_path=dataset_yaml,
            expected_dataset_yaml_sha256=_sha(dataset_yaml),
            expected_montage_path=montage,
            expected_montage_sha256=expected_montage_hash,
            expected_splits=splits,
            workspace_root=dataset_yaml.parent,
            repository_root=dataset_yaml.parent.parent / "repository",
        )


def test_tampered_qualification_check_fails_closed(tmp_path):
    audit, dataset_yaml, montage, splits, qualification = _create(tmp_path)
    value = json.loads(qualification.read_text(encoding="utf-8"))
    value["checks"]["corrupt_or_unreadable_samples"] = 1
    qualification.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(
        DatasetQualificationError,
        match="does not match the verified automated evidence",
    ):
        _validate(audit, dataset_yaml, montage, splits, qualification)


def test_legacy_human_review_record_cannot_authorize_training(tmp_path):
    workspace, _repository = _roots(tmp_path)
    audit, dataset_yaml, montage, splits = _artifacts(workspace)
    legacy = workspace / "legacy-review.json"
    legacy.write_text(
        json.dumps(
            {
                "schema": "veriswarm.rescue.label_review.v1",
                "audit_report_path": str(audit.resolve()),
                "audit_report_sha256": _sha(audit),
                "montage_path": str(montage.resolve()),
                "montage_sha256": _sha(montage),
                "reviewed_samples": 100,
                "reviewer": "someone",
                "reviewer_type": "human",
                "decision": "accepted",
                "reviewed_at_utc": "2026-08-22T10:30:00+00:00",
                "notes": "Legacy evidence must not be accepted.",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(DatasetQualificationError, match="frozen v3 schema"):
        _validate(audit, dataset_yaml, montage, splits, legacy)
