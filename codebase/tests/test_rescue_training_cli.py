from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from rescue_training import cli
import rescue_training.review_gate as review_gate
from rescue_training.dataset_intake import DatasetIntakeError, FrozenDatasetIdentity


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_convert_visdrone_cli_requires_extraction_manifests_not_raw_roots() -> None:
    args = cli._parser().parse_args(
        [
            "convert-visdrone",
            "--plan",
            "plan.json",
            "--train-extraction-manifest",
            "train-extraction.json",
            "--val-extraction-manifest",
            "val-extraction.json",
            "--output-root",
            "derived",
            "--repository-root",
            "repository",
        ]
    )
    assert args.train_extraction_manifest == Path("train-extraction.json")
    assert args.val_extraction_manifest == Path("val-extraction.json")
    assert not hasattr(args, "train_root")
    assert not hasattr(args, "val_root")


def test_train_cli_requires_automated_dataset_qualification_argument() -> None:
    args = cli._parser().parse_args(
        [
            "train",
            "--plan",
            "plan.json",
            "--candidate",
            "yolov8n-640-smoke",
            "--dataset-yaml",
            "dataset.yaml",
            "--dataset-audit",
            "audit.json",
            "--dataset-qualification",
            "qualification.json",
            "--cloud-environment",
            "cloud.json",
            "--base-checkpoint",
            "yolov8n.pt",
            "--base-checkpoint-sha256",
            "a" * 64,
            "--run-directory",
            "run",
            "--repository-root",
            "repository",
        ]
    )

    assert args.dataset_qualification == Path("qualification.json")
    assert not hasattr(args, "label_review")


def test_qualify_visdrone_cli_creates_and_revalidates_v3_receipt(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    workspace = tmp_path / "workspace"
    repository = tmp_path / "repository"
    dataset_root = workspace / "derived-person"
    dataset_root.mkdir(parents=True)
    repository.mkdir()
    dataset_yaml = dataset_root / "dataset.yaml"
    dataset_yaml.write_text("names:\n  0: person_candidate\n", encoding="utf-8")
    montage = dataset_root / "montage.jpg"
    montage.write_bytes(b"deterministic-montage")
    split_hashes = {"train": "1" * 64, "val": "2" * 64}
    audit = dataset_root / "audit.json"
    audit.write_text(
        json.dumps(
            {
                "schema": "veriswarm.dataset_audit.v1",
                "passed": True,
                "class_map": {"0": "person_candidate"},
                "splits": {
                    split: {
                        "image_root": str(
                            (dataset_root / "images" / split).resolve()
                        ),
                        "label_root": str(
                            (dataset_root / "labels" / split).resolve()
                        ),
                        "images": images,
                        "labels": images,
                        "paired": images,
                        "valid_samples": images,
                        "missing_labels": 0,
                        "orphan_labels": 0,
                        "sample_set_sha256": split_hashes[split],
                    }
                    for split, images in (("train", 6471), ("val", 548))
                },
                "totals": {
                    "valid_samples": 7019,
                    "boxes": 7019,
                    "class_box_counts": {"0": 7019},
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

    def fake_verify(report_path, *, dataset_yaml):
        return {
            "report_path": str(Path(report_path).resolve()),
            "report_sha256": _sha(Path(report_path)),
            "montage_path": str(montage.resolve()),
            "montage_sha256": _sha(montage),
            "train_images": 6471,
            "val_images": 548,
            "dataset_yaml_path": str(Path(dataset_yaml).resolve()),
            "dataset_yaml_sha256": _sha(Path(dataset_yaml)),
            "sample_set_sha256": split_hashes,
        }

    def fake_conversion_validator(
        report_path, *, workspace_root, repository_root, **_kwargs
    ):
        assert Path(report_path) == dataset_root / "conversion_report.json"
        assert Path(workspace_root) == workspace
        assert Path(repository_root) == repository
        return {
            "schema": "veriswarm.rescue.visdrone_lineage_validation.v1",
            "conversion_report": {
                "path": str(Path(report_path).resolve()),
                "sha256": "c" * 64,
                "schema": "veriswarm.rescue.visdrone_conversion.v2",
            },
            "output_root": str(dataset_root.resolve()),
            "dataset_yaml": {
                "path": str(dataset_yaml.resolve()),
                "sha256": _sha(dataset_yaml),
            },
            "class_map": {"0": "person_candidate"},
            "source_lineage": {"train": {}, "val": {}},
            "derived_sample_set_sha256": {
                "train": "d" * 64,
                "val": "e" * 64,
            },
        }

    monkeypatch.setattr(cli, "verify_visdrone_audit", fake_verify)
    monkeypatch.setattr(
        review_gate,
        "validate_visdrone_conversion_report",
        fake_conversion_validator,
    )
    target = workspace / "qualification.json"
    result = cli.main(
        [
            "qualify-visdrone-dataset",
            "--audit-report",
            str(audit),
            "--dataset-yaml",
            str(dataset_yaml),
            "--target",
            str(target),
            "--workspace-root",
            str(workspace),
            "--repository-root",
            str(repository),
            "--qualified-at-utc",
            "2026-08-22T10:30:00+00:00",
        ]
    )

    assert result == 0
    receipt = json.loads(target.read_text(encoding="utf-8"))
    assert receipt["schema"] == "veriswarm.rescue.dataset_qualification.v3"
    assert receipt["accepted_by_human"] is False
    assert receipt["checks"]["registered_archive_lineage_revalidated"] is True
    output = json.loads(capsys.readouterr().out)
    assert output == {
        "accepted_by_human": False,
        "decision": "qualified",
        "qualification_path": str(target.resolve()),
        "qualification_sha256": _sha(target),
        "qualified": True,
        "schema": "veriswarm.rescue.dataset_qualification.v3",
    }


def test_register_dataset_cli_builds_nonsecret_retrieval_metadata(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    captured = {}
    identity = FrozenDatasetIdentity(
        key="c2a-v2",
        dataset_id="rgbnihal/c2a-dataset",
        version="2",
        archive_role="disaster_human_source",
        canonical_source_url="https://example.invalid/c2a",
        archive_name_hint="C2A_Dataset.zip",
        known_expected_bytes=None,
        known_expected_sha256=None,
        known_identity_trust=None,
    )

    def fake_register(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return SimpleNamespace(
            dataset=identity,
            archive_path=Path(args[0]),
            archive_bytes=123,
            archive_sha256="a" * 64,
            evidence_path=Path(args[1]),
            response_metadata_path=Path(args[2]),
        )

    monkeypatch.setattr(cli, "register_dataset_archive", fake_register)
    archive = tmp_path / "C2A_Dataset.zip"
    registration = tmp_path / "registration.json"
    response = tmp_path / "response.json"
    workspace = tmp_path / "workspace"
    repository = tmp_path / "repository"
    result = cli.main(
        [
            "register-dataset",
            "--dataset-key",
            "c2a-v2",
            "--archive",
            str(archive),
            "--registration",
            str(registration),
            "--response-metadata",
            str(response),
            "--workspace-root",
            str(workspace),
            "--repository-root",
            str(repository),
            "--origin-kind",
            "local_user_provided",
            "--dataset-use-authorization-status",
            "authorization_missing",
            "--terms-recorded-by",
            "automated-intake-v1",
            "--supplied-by",
            "Samik",
            "--retrieval-tool",
            "local-user-handoff",
            "--retrieved-at-utc",
            "2026-08-22T15:00:00+05:30",
        ]
    )

    assert result == 0
    assert captured["kwargs"]["response_metadata"] == {
        "retrieval_tool": "local-user-handoff"
    }
    assert (
        captured["kwargs"]["dataset_use_authorization_status"]
        == "authorization_missing"
    )
    assert captured["kwargs"]["terms_recorded_by"] == "automated-intake-v1"
    assert captured["kwargs"]["supplied_by"] == "Samik"
    output = json.loads(capsys.readouterr().out)
    assert output["registered"] is True
    assert output["archive_sha256"] == "a" * 64


def test_extract_dataset_cli_preserves_repeated_required_prefixes(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    captured = {}

    def fake_extract(*args, **kwargs):
        captured["kwargs"] = kwargs
        return SimpleNamespace(
            extracted_root=Path(args[1]),
            file_count=30_649,
            image_count=10_215,
            extracted_bytes=4_961_894_351,
            tree_sha256="b" * 64,
            inventory_path=Path(args[2]),
            manifest_path=Path(args[3]),
        )

    monkeypatch.setattr(cli, "extract_registered_zip", fake_extract)
    result = cli.main(
        [
            "extract-dataset",
            "--registration",
            str(tmp_path / "registration.json"),
            "--output-root",
            str(tmp_path / "raw"),
            "--inventory",
            str(tmp_path / "inventory.jsonl"),
            "--manifest",
            str(tmp_path / "manifest.json"),
            "--workspace-root",
            str(tmp_path),
            "--repository-root",
            str(tmp_path / "repository"),
            "--expected-member-count",
            "30649",
            "--expected-image-count",
            "10215",
            "--required-top-level-prefix",
            "new_dataset3",
            "--required-top-level-prefix",
            "new_dataset3/train",
        ]
    )

    assert result == 0
    assert captured["kwargs"]["required_top_level_prefixes"] == (
        "new_dataset3",
        "new_dataset3/train",
    )
    output = json.loads(capsys.readouterr().out)
    assert output["extracted"] is True
    assert output["image_count"] == 10_215


def test_prepare_c2a_cli_reports_group_safe_outputs(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    output_root = tmp_path / "derived"

    def fake_prepare(*args, **kwargs):
        return SimpleNamespace(
            output_root=output_root,
            publisher_test_untouched=False,
            derived_split_counts={"train": 6130, "val": 2045, "test": 2040},
            report_path=output_root / "report.json",
            report_sha256="c" * 64,
            inventory_path=output_root / "inventory.jsonl",
            inventory_sha256="d" * 64,
            dataset_yaml_path=output_root / "dataset.yaml",
            dataset_yaml_sha256="e" * 64,
            artifact_hashes_path=output_root / "artifact_hashes.json",
            artifact_hashes_sha256="f" * 64,
        )

    monkeypatch.setattr(cli, "prepare_c2a_group_safe_dataset", fake_prepare)
    result = cli.main(
        [
            "prepare-c2a",
            "--extracted-root",
            str(tmp_path / "raw"),
            "--output-root",
            str(output_root),
            "--workspace-root",
            str(tmp_path),
            "--repository-root",
            str(tmp_path / "repository"),
            "--transfer-mode",
            "copy",
        ]
    )

    assert result == 0
    output = json.loads(capsys.readouterr().out)
    assert output["prepared"] is True
    assert output["publisher_test_untouched"] is False
    assert sum(output["derived_split_counts"].values()) == 10_215


def test_cli_returns_two_for_dataset_intake_failure(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    def fail(*args, **kwargs):
        raise DatasetIntakeError("terms gate blocks extraction")

    monkeypatch.setattr(cli, "extract_registered_zip", fail)
    result = cli.main(
        [
            "extract-dataset",
            "--registration",
            str(tmp_path / "registration.json"),
            "--output-root",
            str(tmp_path / "raw"),
            "--inventory",
            str(tmp_path / "inventory.jsonl"),
            "--manifest",
            str(tmp_path / "manifest.json"),
            "--workspace-root",
            str(tmp_path),
            "--repository-root",
            str(tmp_path / "repository"),
        ]
    )

    assert result == 2
    assert "terms gate blocks extraction" in capsys.readouterr().err
