"""Command-line entry point for deterministic rescue-person model preparation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from .artifact_io import ArtifactIOError
from .c2a import C2ADataError, prepare_c2a_group_safe_dataset
from .contracts import TrainingContractError, TrainingPlan
from .dataset_intake import (
    DatasetIntakeError,
    extract_registered_zip,
    register_dataset_archive,
)
from .review_gate import (
    DatasetQualificationError,
    create_automated_dataset_qualification,
    validate_automated_dataset_qualification,
)
from .ultralytics_runner import (
    TrainingExecutionError,
    run_training,
    verify_runpod_runtime,
    verify_visdrone_audit,
)
from .visdrone import VisDroneConversionError, convert_visdrone_det


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Fail-closed preparation and training for the one-class "
            "sar-rgb-person-v1 detector"
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    verify_plan = subparsers.add_parser("verify-plan")
    verify_plan.add_argument("--plan", type=Path, required=True)

    verify_runtime = subparsers.add_parser("verify-runtime")
    verify_runtime.add_argument("--plan", type=Path, required=True)

    convert = subparsers.add_parser("convert-visdrone")
    convert.add_argument("--plan", type=Path, required=True)
    convert.add_argument("--train-extraction-manifest", type=Path, required=True)
    convert.add_argument("--val-extraction-manifest", type=Path, required=True)
    convert.add_argument("--output-root", type=Path, required=True)
    convert.add_argument("--repository-root", type=Path, required=True)
    convert.add_argument("--transfer-mode", choices=("hardlink", "copy"), default="copy")

    audit = subparsers.add_parser("verify-visdrone-audit")
    audit.add_argument("--report", type=Path, required=True)

    qualify = subparsers.add_parser(
        "qualify-visdrone-dataset",
        help=(
            "recompute the VisDrone audit and create the immutable automated "
            "training-qualification receipt"
        ),
    )
    qualify.add_argument("--audit-report", type=Path, required=True)
    qualify.add_argument("--dataset-yaml", type=Path, required=True)
    qualify.add_argument("--target", type=Path, required=True)
    qualify.add_argument("--workspace-root", type=Path, required=True)
    qualify.add_argument("--repository-root", type=Path, required=True)
    qualify.add_argument("--qualified-at-utc")

    register = subparsers.add_parser(
        "register-dataset",
        help="hash and register an already-present frozen dataset archive",
    )
    register.add_argument("--dataset-key", required=True)
    register.add_argument("--archive", type=Path, required=True)
    register.add_argument("--registration", type=Path, required=True)
    register.add_argument("--response-metadata", type=Path, required=True)
    register.add_argument("--workspace-root", type=Path, required=True)
    register.add_argument("--repository-root", type=Path, required=True)
    register.add_argument(
        "--origin-kind",
        choices=("download", "local_user_provided"),
        required=True,
    )
    register.add_argument(
        "--dataset-use-authorization-status",
        choices=(
            "authorization_recorded",
            "not_applicable",
            "authorization_missing",
            "rejected",
        ),
        required=True,
    )
    register.add_argument("--terms-recorded-by", required=True)
    register.add_argument("--terms-reference")
    register.add_argument("--source-url")
    register.add_argument("--supplied-by")
    register.add_argument("--expected-bytes", type=int)
    register.add_argument("--expected-sha256")
    register.add_argument("--retrieved-at-utc")
    register.add_argument("--retrieval-tool", required=True)
    register.add_argument("--http-status", type=int)
    register.add_argument("--final-url")
    register.add_argument("--content-length", type=int)
    register.add_argument("--etag")

    extract = subparsers.add_parser(
        "extract-dataset",
        help="safely extract a registered ZIP and bind the complete output tree",
    )
    extract.add_argument("--registration", type=Path, required=True)
    extract.add_argument("--output-root", type=Path, required=True)
    extract.add_argument("--inventory", type=Path, required=True)
    extract.add_argument("--manifest", type=Path, required=True)
    extract.add_argument("--workspace-root", type=Path, required=True)
    extract.add_argument("--repository-root", type=Path, required=True)
    extract.add_argument("--expected-member-count", type=int)
    extract.add_argument("--expected-image-count", type=int)
    extract.add_argument(
        "--required-top-level-prefix",
        action="append",
        default=[],
    )
    extract.add_argument("--extracted-at-utc")

    prepare_c2a = subparsers.add_parser(
        "prepare-c2a",
        help="audit C2A v2 and create the deterministic group-safe detector split",
    )
    prepare_c2a.add_argument("--extracted-root", type=Path, required=True)
    prepare_c2a.add_argument("--output-root", type=Path, required=True)
    prepare_c2a.add_argument("--workspace-root", type=Path, required=True)
    prepare_c2a.add_argument("--repository-root", type=Path, required=True)
    prepare_c2a.add_argument(
        "--transfer-mode", choices=("hardlink", "copy"), default="copy"
    )

    train = subparsers.add_parser("train")
    train.add_argument("--plan", type=Path, required=True)
    train.add_argument("--candidate", required=True)
    train.add_argument("--dataset-yaml", type=Path, required=True)
    train.add_argument("--dataset-audit", type=Path, required=True)
    train.add_argument("--dataset-qualification", type=Path, required=True)
    train.add_argument("--cloud-environment", type=Path, required=True)
    train.add_argument("--base-checkpoint", type=Path, required=True)
    train.add_argument("--base-checkpoint-sha256", required=True)
    train.add_argument("--run-directory", type=Path, required=True)
    train.add_argument("--repository-root", type=Path, required=True)
    train.add_argument("--batch-decision", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "verify-plan":
            plan = TrainingPlan.load(args.plan.resolve())
            print(
                json.dumps(
                    {
                        "valid": True,
                        "schema": plan.schema,
                        "model_id": plan.model_id,
                        "class_map": {str(key): value for key, value in plan.class_map.items()},
                        "candidates": list(plan.candidates),
                    },
                    sort_keys=True,
                )
            )
            return 0

        if args.command == "verify-runtime":
            plan = TrainingPlan.load(args.plan.resolve())
            print(json.dumps(verify_runpod_runtime(plan), sort_keys=True))
            return 0

        if args.command == "convert-visdrone":
            plan = TrainingPlan.load(args.plan.resolve())
            report = convert_visdrone_det(
                {
                    "train": args.train_extraction_manifest.resolve(),
                    "val": args.val_extraction_manifest.resolve(),
                },
                args.output_root,
                workspace_root=plan.workspace_root,
                repository_root=args.repository_root.resolve(),
                transfer_mode=args.transfer_mode,
            )
            print(json.dumps(report.to_dict(), sort_keys=True))
            return 0

        if args.command == "verify-visdrone-audit":
            print(json.dumps(verify_visdrone_audit(args.report.resolve()), sort_keys=True))
            return 0

        if args.command == "qualify-visdrone-dataset":
            workspace = args.workspace_root.resolve()
            repository = args.repository_root.resolve()
            audit_report = args.audit_report.resolve()
            dataset_yaml = args.dataset_yaml.resolve()
            audit_evidence = verify_visdrone_audit(
                audit_report,
                dataset_yaml=dataset_yaml,
            )
            splits = {
                split: {
                    "images": audit_evidence[f"{split}_images"],
                    "sample_set_sha256": audit_evidence["sample_set_sha256"][split],
                }
                for split in ("train", "val")
            }
            target = create_automated_dataset_qualification(
                args.target.resolve(),
                audit_report_path=audit_report,
                audit_report_sha256=audit_evidence["report_sha256"],
                dataset_yaml_path=dataset_yaml,
                dataset_yaml_sha256=audit_evidence["dataset_yaml_sha256"],
                montage_path=audit_evidence["montage_path"],
                montage_sha256=audit_evidence["montage_sha256"],
                splits=splits,
                workspace_root=workspace,
                repository_root=repository,
                qualified_at_utc=args.qualified_at_utc,
            )
            qualification = validate_automated_dataset_qualification(
                target,
                expected_audit_path=audit_report,
                expected_audit_sha256=audit_evidence["report_sha256"],
                expected_dataset_yaml_path=dataset_yaml,
                expected_dataset_yaml_sha256=audit_evidence[
                    "dataset_yaml_sha256"
                ],
                expected_montage_path=audit_evidence["montage_path"],
                expected_montage_sha256=audit_evidence["montage_sha256"],
                expected_splits=splits,
                workspace_root=workspace,
                repository_root=repository,
            )
            print(
                json.dumps(
                    {
                        "qualified": True,
                        "schema": qualification["schema"],
                        "decision": qualification["decision"],
                        "accepted_by_human": qualification["accepted_by_human"],
                        "qualification_path": qualification[
                            "qualification_path"
                        ],
                        "qualification_sha256": qualification[
                            "qualification_sha256"
                        ],
                    },
                    sort_keys=True,
                )
            )
            return 0

        if args.command == "register-dataset":
            response_metadata = {
                "retrieval_tool": args.retrieval_tool,
                **(
                    {"http_status": args.http_status}
                    if args.http_status is not None
                    else {}
                ),
                **({"final_url": args.final_url} if args.final_url is not None else {}),
                **(
                    {"content_length": args.content_length}
                    if args.content_length is not None
                    else {}
                ),
                **({"etag": args.etag} if args.etag is not None else {}),
            }
            result = register_dataset_archive(
                args.archive.resolve(),
                args.registration.resolve(),
                args.response_metadata.resolve(),
                dataset_key=args.dataset_key,
                workspace_root=args.workspace_root.resolve(),
                repository_root=args.repository_root.resolve(),
                origin_kind=args.origin_kind,
                dataset_use_authorization_status=(
                    args.dataset_use_authorization_status
                ),
                terms_recorded_by=args.terms_recorded_by,
                response_metadata=response_metadata,
                source_url=args.source_url,
                supplied_by=args.supplied_by,
                terms_reference=args.terms_reference,
                expected_bytes=args.expected_bytes,
                expected_sha256=args.expected_sha256,
                retrieved_at_utc=args.retrieved_at_utc,
            )
            print(
                json.dumps(
                    {
                        "registered": True,
                        "dataset": result.dataset.to_dict(),
                        "archive_path": str(result.archive_path),
                        "archive_bytes": result.archive_bytes,
                        "archive_sha256": result.archive_sha256,
                        "registration_path": str(result.evidence_path),
                        "response_metadata_path": str(result.response_metadata_path),
                    },
                    sort_keys=True,
                )
            )
            return 0

        if args.command == "extract-dataset":
            result = extract_registered_zip(
                args.registration.resolve(),
                args.output_root.resolve(),
                args.inventory.resolve(),
                args.manifest.resolve(),
                workspace_root=args.workspace_root.resolve(),
                repository_root=args.repository_root.resolve(),
                expected_member_count=args.expected_member_count,
                expected_image_count=args.expected_image_count,
                required_top_level_prefixes=tuple(args.required_top_level_prefix),
                extracted_at_utc=args.extracted_at_utc,
            )
            print(
                json.dumps(
                    {
                        "extracted": True,
                        "extracted_root": str(result.extracted_root),
                        "file_count": result.file_count,
                        "image_count": result.image_count,
                        "extracted_bytes": result.extracted_bytes,
                        "tree_sha256": result.tree_sha256,
                        "inventory_path": str(result.inventory_path),
                        "manifest_path": str(result.manifest_path),
                    },
                    sort_keys=True,
                )
            )
            return 0

        if args.command == "prepare-c2a":
            result = prepare_c2a_group_safe_dataset(
                args.extracted_root.resolve(),
                args.output_root.resolve(),
                workspace_root=args.workspace_root.resolve(),
                repository_root=args.repository_root.resolve(),
                transfer_mode=args.transfer_mode,
            )
            print(
                json.dumps(
                    {
                        "prepared": True,
                        "output_root": str(result.output_root),
                        "publisher_test_untouched": result.publisher_test_untouched,
                        "derived_split_counts": dict(result.derived_split_counts),
                        "report_path": str(result.report_path),
                        "report_sha256": result.report_sha256,
                        "inventory_path": str(result.inventory_path),
                        "inventory_sha256": result.inventory_sha256,
                        "dataset_yaml_path": str(result.dataset_yaml_path),
                        "dataset_yaml_sha256": result.dataset_yaml_sha256,
                        "artifact_hashes_path": str(result.artifact_hashes_path),
                        "artifact_hashes_sha256": result.artifact_hashes_sha256,
                    },
                    sort_keys=True,
                )
            )
            return 0

        if args.command == "train":
            plan = TrainingPlan.load(args.plan.resolve())
            report = run_training(
                plan,
                candidate_name=args.candidate,
                dataset_yaml=args.dataset_yaml.resolve(),
                dataset_audit=args.dataset_audit.resolve(),
                dataset_qualification=args.dataset_qualification.resolve(),
                cloud_environment=args.cloud_environment.resolve(),
                base_checkpoint=args.base_checkpoint.resolve(),
                base_checkpoint_sha256=args.base_checkpoint_sha256,
                run_directory=args.run_directory.resolve(),
                workspace_root=plan.workspace_root,
                repository_root=args.repository_root.resolve(),
                batch_decision=(
                    args.batch_decision.resolve() if args.batch_decision else None
                ),
            )
            print(f"TRAINING_COMPLETE_AUTOMATED_EVIDENCE report={report}")
            return 0
        raise AssertionError(f"unhandled command: {args.command}")
    except (
        ArtifactIOError,
        C2ADataError,
        DatasetIntakeError,
        DatasetQualificationError,
        TrainingContractError,
        TrainingExecutionError,
        VisDroneConversionError,
    ) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
