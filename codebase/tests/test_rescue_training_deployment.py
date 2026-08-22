from __future__ import annotations

import json
import hashlib
import csv
import zipfile
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import rescue_training.review_gate as review_gate_module
import rescue_training.deployment as deployment_module
import rescue_training.cloud_environment as cloud_environment_module

from rescue_training.artifact_io import sha256_file
from rescue_training.artifact_io import canonical_json_bytes
from rescue_training.batch_gate import (
    authorized_batch,
    create_batch_decision,
    load_batch_decision,
)
from rescue_training.cloud_environment import (
    CLOUD_ENVIRONMENT_SCHEMA,
    COMMAND_CAPTURE_SCHEMA,
    CONTROL_PLANE_TRUST_LEVEL,
    DURABLE_RELOAD_TRUST_LEVEL,
    GPU_QUERY_ARGUMENTS,
    MOUNT_QUERY_ARGUMENTS,
    RTX_5090_MEMORY_BYTES,
    RUNPOD_CONTAINER_DISK_GB,
    RUNPOD_CONTAINER_IMAGE_REFERENCE,
    RUNPOD_DATA_CENTER_ID,
    RUNPOD_MCP_CAPTURE_METHOD,
    RUNPOD_MCP_CONTROL_PLANE_SCHEMA,
    RUNPOD_NETWORK_VOLUME_ID,
    RUNPOD_NETWORK_VOLUME_NAME,
    RUNPOD_NETWORK_VOLUME_SIZE_GB,
    RUNPOD_NETWORK_VOLUME_TYPE,
    RUNPOD_POD_NAME,
    RUNTIME_PROBE_CODE,
    validate_cloud_environment,
)
from rescue_training.contracts import FROZEN_RUNTIME, MODEL_ID, TrainingPlan
from rescue_training.evaluation import (
    INFERENCE_CONFIDENCE_FLOOR,
    INFERENCE_REPORT_SCHEMA,
    create_evaluation_dataset_manifest,
    generate_validation_report,
)
from rescue_training.deployment import (
    DEPLOYMENT_MANIFEST_SCHEMA,
    ENGINE_IDENTITY_SCHEMA,
    JETSON_BENCHMARK_SCHEMA,
    MANDATORY_COMPLETION_SCHEMA,
    ONNX_EXPORT_SCHEMA,
    THRESHOLD_SELECTION_SCHEMA,
    UNTOUCHED_TEST_ACCURACY_SCHEMA,
    VALIDATION_ACCURACY_EQUIVALENCE_SCHEMA,
    DeploymentValidationError,
    build_candidate_qualification,
    build_deployment_manifest,
    build_final_selection_report,
    build_jetson_trtexec_command,
    validate_candidate_qualification,
    validate_deployment_manifest,
    validate_engine_identity_report,
    is_validated_candidate_qualification,
    validate_jetson_benchmark_report,
    validate_onnx_export_report,
    validate_untouched_test_accuracy_report,
    validate_validation_accuracy_equivalence_report,
)
from rescue_training.selection import (
    QualificationEvidence,
    SelectedCandidate,
    ValidationEvidence,
)
from rescue_training.onnx_export import export_static_onnx
from rescue_training.review_gate import (
    create_automated_dataset_qualification,
    validate_automated_dataset_qualification,
)
from rescue_training.ultralytics_runner import (
    _read_final_metrics,
    frozen_train_arguments,
    verify_trusted_base_checkpoint,
)


PLAN_PATH = Path(__file__).resolve().parents[1] / "config" / "sar_rgb_person_training.json"
TRAINING_PLAN = TrainingPlan.load(PLAN_PATH)
PLAN_HASH = hashlib.sha256(canonical_json_bytes(TRAINING_PLAN.to_dict())).hexdigest()


@pytest.fixture(autouse=True)
def _validated_conversion_lineage_test_seam(monkeypatch):
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
                "sha256": sha256_file(dataset_yaml),
            },
            "class_map": {"0": "person_candidate"},
            "source_lineage": {"train": {}, "val": {}},
            "derived_sample_set_sha256": {"train": "d" * 64, "val": "e" * 64},
        }

    monkeypatch.setattr(
        review_gate_module, "validate_visdrone_conversion_report", validate
    )


def _artifact(path: Path) -> dict[str, str]:
    return {"path": str(path.resolve()), "sha256": sha256_file(path)}


def test_evaluation_inventory_accepts_conversion_v2_and_rejects_legacy_v1(
    tmp_path, monkeypatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repository = tmp_path / "repository"
    repository.mkdir()
    source_path = workspace / "conversion.json"
    source_path.write_text(
        json.dumps({"schema": "veriswarm.rescue.visdrone_conversion.v2"}),
        encoding="utf-8",
    )
    source_reference = {
        "path": str(source_path.resolve()),
        "sha256": sha256_file(source_path),
        "schema": "veriswarm.rescue.visdrone_conversion.v2",
    }
    monkeypatch.setattr(
        deployment_module, "_ground_truth_image_hashes", lambda *_args: {"a" * 64}
    )
    monkeypatch.setattr(
        deployment_module,
        "_dataset_manifest_payload",
        lambda *_args, **_kwargs: {"source_evidence": source_reference},
    )
    inventory = deployment_module._empty_split_inventory()
    inventory["val"]["images"] = {"a" * 64}
    monkeypatch.setattr(
        deployment_module,
        "_visdrone_source_inventory",
        lambda *_args, **_kwargs: inventory,
    )
    report = SimpleNamespace(split="val")
    result = deployment_module._available_split_inventory(
        report,
        "validation.real_aerial",
        workspace=workspace,
        repository=repository,
    )
    assert result["val"]["images"] == {"a" * 64}

    source_path.write_text(
        json.dumps({"schema": "veriswarm.rescue.visdrone_conversion.v1"}),
        encoding="utf-8",
    )
    source_reference.update(
        {
            "sha256": sha256_file(source_path),
            "schema": "veriswarm.rescue.visdrone_conversion.v1",
        }
    )
    with pytest.raises(DeploymentValidationError, match="without archive lineage"):
        deployment_module._available_split_inventory(
            report,
            "validation.real_aerial",
            workspace=workspace,
            repository=repository,
        )


def _write_json(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return path


def _write_torch_checkpoint(path: Path, marker: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("archive/data.pkl", b"\x80\x04N.")
        archive.writestr("archive/version", b"3\n")
        archive.writestr("archive/byteorder", b"little")
        archive.writestr("archive/data/0", marker.encode() * 1024)
    return path


def _make_cloud_environment(
    workspace: Path, repository: Path, root: Path
) -> tuple[Path, dict]:
    evidence_root = workspace / "cloud-evidence" / root.name
    evidence_root.mkdir(parents=True)
    started = "2026-08-22T10:00:00+00:00"
    observed = "2026-08-22T10:00:30+00:00"
    captured = "2026-08-22T10:01:00+00:00"
    digest = RUNPOD_CONTAINER_IMAGE_REFERENCE.rsplit(":", 1)[1]
    executables = evidence_root / "executables"
    executables.mkdir()
    executable_paths = {
        name: (executables / name).resolve()
        for name in ("nvidia-smi", "python", "findmnt")
    }
    for name, path in executable_paths.items():
        path.write_bytes(f"fixture executable {name}".encode("utf-8"))
    python = str(executable_paths["python"])

    def capture(path: Path, argv: list[str], stdout: str) -> Path:
        return _write_json(
            path,
            {
                "schema": COMMAND_CAPTURE_SCHEMA,
                "capture_mode": "production_subprocess",
                "test_only": False,
                "argv": argv,
                "executable_sha256": sha256_file(argv[0]),
                "exit_code": 0,
                "stdout": stdout,
                "stderr": "",
                "started_at_utc": observed,
                "completed_at_utc": observed,
            },
        )

    pod_id = f"pod-{root.name}"
    response_projection = {
        "pod": {
            "id": pod_id,
            "name": RUNPOD_POD_NAME,
            "status": "RUNNING",
            "cloud": "SECURE",
            "cost": 0.99,
            "createdAt": "2026-08-22T09:55:00+00:00",
            "startedAt": started,
            "cudaVersion": "13.0",
            "dataCenterId": RUNPOD_DATA_CENTER_ID,
            "disk": RUNPOD_CONTAINER_DISK_GB,
            "gpu": {"id": "NVIDIA GeForce RTX 5090", "count": 1},
            "image": RUNPOD_CONTAINER_IMAGE_REFERENCE,
            "locked": False,
            "mounts": {
                "network": [
                    {"path": "/workspace", "volumeId": RUNPOD_NETWORK_VOLUME_ID}
                ]
            },
            "runtime": {"gpus": [{"memoryUtil": 0, "util": 0}]},
        },
        "networkVolume": {
            "dataCenter": RUNPOD_DATA_CENTER_ID,
            "id": RUNPOD_NETWORK_VOLUME_ID,
            "name": RUNPOD_NETWORK_VOLUME_NAME,
            "size": RUNPOD_NETWORK_VOLUME_SIZE_GB,
            "type": RUNPOD_NETWORK_VOLUME_TYPE,
        },
    }
    provider = _write_json(
        evidence_root / "runpod-pod-record.json",
        {
            "schema": RUNPOD_MCP_CONTROL_PLANE_SCHEMA,
            "provider": "runpod",
            "capture_method": RUNPOD_MCP_CAPTURE_METHOD,
            "trust_level": CONTROL_PLANE_TRUST_LEVEL,
            "provider_signature_present": False,
            "request": {
                "getPod": {
                    "podId": pod_id,
                    "includeMachine": True,
                    "includeNetworkVolume": True,
                },
                "getNetworkVolume": {
                    "networkVolumeId": RUNPOD_NETWORK_VOLUME_ID,
                },
            },
            "response_projection": response_projection,
            "response_projection_sha256": hashlib.sha256(
                canonical_json_bytes(response_projection)
            ).hexdigest(),
            "captured_at_utc": observed,
        },
    )
    gpu = capture(
        evidence_root / "gpu-probe.json",
        [str(executable_paths["nvidia-smi"]), *GPU_QUERY_ARGUMENTS],
        "NVIDIA GeForce RTX 5090, 32768, GPU-12345678-abcd\n",
    )
    runtime_stdout = json.dumps(
        {
            "python": FROZEN_RUNTIME["python"],
            "torch": FROZEN_RUNTIME["torch"],
            "torchvision": FROZEN_RUNTIME["torchvision"],
            "ultralytics": FROZEN_RUNTIME["ultralytics"],
            "cuda_runtime": "13.0",
            "python_executable": python,
            "cuda_available": True,
        },
        sort_keys=True,
    )
    runtime = capture(
        evidence_root / "runtime-probe.json",
        [python, "-c", RUNTIME_PROBE_CODE],
        runtime_stdout,
    )
    mount = capture(
        evidence_root / "mount-probe.json",
        [str(executable_paths["findmnt"]), *MOUNT_QUERY_ARGUMENTS],
        json.dumps(
            {
                "filesystems": [
                    {
                        "target": "/workspace",
                        "source": f"10.0.0.5:/runpod/{RUNPOD_NETWORK_VOLUME_ID}",
                        "fstype": "nfs4",
                        "options": "rw,relatime,vers=4.1",
                        "maj:min": "0:77",
                    }
                ]
            },
            sort_keys=True,
        ),
    )
    freeze = capture(
        evidence_root / "pip-freeze.json",
        [python, "-m", "pip", "freeze", "--all"],
        "\n".join(
            [
                "torch @ "
                + cloud_environment_module._FROZEN_PACKAGE_DIRECT_REFERENCES[
                    "torch"
                ],
                "torchvision @ "
                + cloud_environment_module._FROZEN_PACKAGE_DIRECT_REFERENCES[
                    "torchvision"
                ],
                f"ultralytics=={FROZEN_RUNTIME['ultralytics']}",
                "pip==26.0",
            ]
        )
        + "\n",
    )
    check = capture(
        evidence_root / "pip-check.json",
        [python, "-m", "pip", "check"],
        "No broken requirements found.\n",
    )
    evidence_paths = {
        "runpod_pod_record": provider,
        "gpu_probe": gpu,
        "runtime_probe": runtime,
        "mount_probe": mount,
        "pip_freeze": freeze,
        "pip_check": check,
    }
    references = {
        name: {"path": str(path.resolve()), "sha256": sha256_file(path)}
        for name, path in evidence_paths.items()
    }
    payload = {
        "schema": CLOUD_ENVIRONMENT_SCHEMA,
        "provider": "runpod",
        "control_plane_capture_trust": CONTROL_PLANE_TRUST_LEVEL,
        "durable_reload_trust": DURABLE_RELOAD_TRUST_LEVEL,
        "pod_id": f"pod-{root.name}",
        "network_volume_id": RUNPOD_NETWORK_VOLUME_ID,
        "workspace_root": "/workspace/samik-rescue-person-model-20260822",
        "gpu": {
            "name": "NVIDIA GeForce RTX 5090",
            "memory_bytes": RTX_5090_MEMORY_BYTES,
            "uuid": "GPU-12345678-abcd",
        },
        "runtime": {
            "python": FROZEN_RUNTIME["python"],
            "torch": FROZEN_RUNTIME["torch"],
            "torchvision": FROZEN_RUNTIME["torchvision"],
            "ultralytics": FROZEN_RUNTIME["ultralytics"],
            "cuda_runtime": "13.0",
        },
        "container_image": {
            "reference": RUNPOD_CONTAINER_IMAGE_REFERENCE,
            "digest": digest,
        },
        "persistent_mount": {
            "volume_id": RUNPOD_NETWORK_VOLUME_ID,
            "mount_path": "/workspace",
            "mount_identity": f"0:77|10.0.0.5:/runpod/{RUNPOD_NETWORK_VOLUME_ID}",
            "filesystem_type": "nfs4",
            "writable": True,
        },
        "package_integrity": {
            "pip_freeze_sha256": references["pip_freeze"]["sha256"],
            "pip_check_sha256": references["pip_check"]["sha256"],
            "pip_check_passed": True,
        },
        "evidence": references,
        "repository": {"path": "/workspace/VeriSwarm_SIH"},
        "pod_started_at_utc": started,
        "captured_at_utc": captured,
        "passed": True,
    }
    validate_cloud_environment(payload)
    manifest_path = workspace / "manifests" / f"cloud-{root.name}.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(manifest_path, payload)
    return manifest_path, payload


def _make_dataset_evidence(
    workspace: Path, repository: Path, root: Path
) -> tuple[Path, dict, dict]:
    dataset_root = workspace / "datasets" / root.name
    for split in ("train", "val"):
        (dataset_root / "images" / split).mkdir(parents=True)
        (dataset_root / "labels" / split).mkdir(parents=True)
    dataset_yaml = dataset_root / "dataset.yaml"
    dataset_yaml.write_text(
        f"path: {dataset_root.as_posix()}\ntrain: images/train\nval: images/val\nnames:\n  0: person_candidate\n",
        encoding="utf-8",
    )
    audit_dir = workspace / "audit" / root.name
    audit_dir.mkdir(parents=True)
    montage = audit_dir / "label_montage.jpg"
    montage.write_bytes(b"deterministic-100-image-montage")
    sample_hashes = {
        "train": hashlib.sha256(f"{root.name}-train".encode()).hexdigest(),
        "val": hashlib.sha256(f"{root.name}-val".encode()).hexdigest(),
    }
    split_reports = {}
    for split, count in (("train", 6471), ("val", 548)):
        split_reports[split] = {
            "image_root": str((dataset_root / "images" / split).resolve()),
            "label_root": str((dataset_root / "labels" / split).resolve()),
            "images": count,
            "labels": count,
            "paired": count,
            "valid_samples": count,
            "boxes": count,
            "class_box_counts": {"0": count},
            "empty_label_samples": 0,
            "missing_labels": 0,
            "orphan_labels": 0,
            "sample_set_sha256": sample_hashes[split],
        }
    montage_samples = [
        {
            "split": "train" if index < 90 else "val",
            "stem": f"sample-{index:03d}",
            "image_sha256": hashlib.sha256(f"image-{index}".encode()).hexdigest(),
            "label_sha256": hashlib.sha256(f"label-{index}".encode()).hexdigest(),
        }
        for index in range(100)
    ]
    montage_sample_hash = hashlib.sha256(
        json.dumps(
            montage_samples,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    audit_payload = {
        "schema": "veriswarm.dataset_audit.v1",
        "generated_at_utc": "2026-08-22T09:30:00+00:00",
        "passed": True,
        "class_map": {"0": "person_candidate"},
        "splits": split_reports,
        "totals": {
            "valid_samples": 7019,
            "boxes": 7019,
            "class_box_counts": {"0": 7019},
            "duplicate_groups": 0,
            "cross_split_leakage_groups": 0,
            "errors": 0,
            "warnings": 0,
        },
        "duplicates": [],
        "cross_split_leakage": [],
        "errors": [],
        "warnings": [],
        "montage": {
            "requested_samples": 100,
            "rendered_samples": 100,
            "samples": montage_samples,
            "sample_set_sha256": montage_sample_hash,
            "path": montage.name,
            "sha256": sha256_file(montage),
        },
    }
    audit_path = _write_json(audit_dir / "audit_report.json", audit_payload)
    qualification_path = audit_dir / "automated_dataset_qualification.json"
    split_evidence = {
        split: {
            "images": split_reports[split]["images"],
            "sample_set_sha256": sample_hashes[split],
        }
        for split in ("train", "val")
    }
    create_automated_dataset_qualification(
        qualification_path,
        audit_report_path=audit_path,
        audit_report_sha256=sha256_file(audit_path),
        dataset_yaml_path=dataset_yaml,
        dataset_yaml_sha256=sha256_file(dataset_yaml),
        montage_path=montage,
        montage_sha256=sha256_file(montage),
        splits=split_evidence,
        workspace_root=workspace,
        repository_root=repository,
        qualified_at_utc="2026-08-22T09:45:00+00:00",
    )
    qualification = validate_automated_dataset_qualification(
        qualification_path,
        expected_audit_path=audit_path,
        expected_audit_sha256=sha256_file(audit_path),
        expected_dataset_yaml_path=dataset_yaml,
        expected_dataset_yaml_sha256=sha256_file(dataset_yaml),
        expected_montage_path=montage,
        expected_montage_sha256=sha256_file(montage),
        expected_splits=split_evidence,
        workspace_root=workspace,
        repository_root=repository,
    )
    authoritative = {
        "class_map": audit_payload["class_map"],
        "splits": audit_payload["splits"],
        "totals": audit_payload["totals"],
        "duplicates": audit_payload["duplicates"],
        "cross_split_leakage": audit_payload["cross_split_leakage"],
        "errors": audit_payload["errors"],
        "warnings": audit_payload["warnings"],
        "montage": {
            key: audit_payload["montage"][key]
            for key in (
                "requested_samples", "rendered_samples", "samples",
                "sample_set_sha256", "sha256",
            )
        },
    }
    integrity_hash = hashlib.sha256(canonical_json_bytes(authoritative)).hexdigest()
    audit_evidence = {
        "schema": "veriswarm.dataset_audit.v1",
        "report_path": str(audit_path.resolve()),
        "report_sha256": sha256_file(audit_path),
        "montage_path": str(montage.resolve()),
        "montage_sha256": sha256_file(montage),
        "train_images": 6471,
        "val_images": 548,
        "class_map": {"0": "person_candidate"},
        "dataset_root": str(dataset_root.resolve()),
        "dataset_yaml_path": str(dataset_yaml.resolve()),
        "dataset_yaml_sha256": sha256_file(dataset_yaml),
        "sample_set_sha256": sample_hashes,
        "authoritative_integrity": authoritative,
        "authoritative_integrity_sha256": integrity_hash,
        "verified_at_utc": "2026-08-22T09:40:00+00:00",
        "pre_training_authoritative_integrity_sha256": integrity_hash,
        "post_training_authoritative_integrity_sha256": integrity_hash,
        "post_training_verified_at_utc": "2026-08-22T11:05:00+00:00",
        "integrity_unchanged": True,
    }
    return dataset_yaml, audit_evidence, qualification


def _make_training_completion(
    root: Path, candidate: str, architecture: str, shape: int
) -> tuple[Path, Path, dict]:
    workspace = root / "workspace"
    repository = root / "repository"
    workspace.mkdir()
    (repository / "codebase" / "config").mkdir(parents=True)
    dataset_yaml, audit, qualification = _make_dataset_evidence(
        workspace, repository, root
    )
    cloud_path, cloud = _make_cloud_environment(workspace, repository, root)

    intake = _write_torch_checkpoint(
        workspace / "base" / architecture, f"trusted-{architecture}"
    )
    other_architecture = "yolov8s.pt" if architecture == "yolov8n.pt" else "yolov8n.pt"
    other_bytes = f"unused-{other_architecture}".encode()
    base_entries = {
        architecture: {
            "url": f"https://github.com/ultralytics/assets/releases/download/v8.4.0/{architecture}",
            "bytes": intake.stat().st_size,
            "sha256": sha256_file(intake),
        },
        other_architecture: {
            "url": f"https://github.com/ultralytics/assets/releases/download/v8.4.0/{other_architecture}",
            "bytes": len(other_bytes),
            "sha256": hashlib.sha256(other_bytes).hexdigest(),
        },
    }
    base_manifest = _write_json(
        repository / "codebase" / "config" / "ultralytics_yolov8_base_weights.json",
        {
            "schema": "veriswarm.rescue.base_weights.v1",
            "publisher": "Ultralytics",
            "repository": "https://github.com/ultralytics/assets",
            "release_tag": "v8.4.0",
            "entries": base_entries,
        },
    )
    assert base_manifest.is_file()
    trusted_intake = verify_trusted_base_checkpoint(
        intake, architecture=architecture, repository_root=repository
    )

    source = {
        "commit": "1" * 40,
        "branch": "samik/sih26177-perception",
        "clean": True,
    }
    (workspace / "runs").mkdir()
    smoke_report = _write_json(
        workspace / "runs" / f"smoke-{root.name}.json",
        {
            "schema": "veriswarm.rescue.training_run.v1",
            "status": "completed",
            "model_id": MODEL_ID,
            "class_map": {"0": "person_candidate"},
            "training_plan_sha256": PLAN_HASH,
            "source": source,
            "candidate": TRAINING_PLAN.candidate("yolov8n-640-smoke").to_dict(),
            "dataset": {
                "yaml_sha256": sha256_file(dataset_yaml),
                "audit": {"report_sha256": audit["report_sha256"]},
            },
            "base_checkpoint": {"sha256": sha256_file(intake)},
            "resolved_batch_size": 8,
            "smoke_gate": {
                "passed": True,
                "recall_is_positive": True,
                "cuda_peak_recorded": True,
                "vram_headroom_at_least_10_percent": True,
                "all_epoch_losses_finite": True,
            },
        },
    )
    batch_path = create_batch_decision(
        smoke_report_path=smoke_report,
        output_path=workspace / "manifests" / f"batch-{root.name}.json",
        expected_training_plan_sha256=PLAN_HASH,
        expected_source_commit=source["commit"],
        expected_dataset_yaml_sha256=sha256_file(dataset_yaml),
        expected_dataset_audit_sha256=audit["report_sha256"],
        expected_base_checkpoint_sha256=sha256_file(intake),
        created_at_utc="2026-08-22T10:02:00+00:00",
        workspace_root=workspace,
        repository_root=repository,
    )
    batch_decision = load_batch_decision(
        batch_path, workspace_root=workspace, repository_root=repository
    )
    resolved_batch = authorized_batch(batch_decision, candidate)

    run_directory = workspace / "runs" / candidate
    (run_directory / "weights").mkdir(parents=True)
    staged = run_directory.parent / f".{run_directory.name}.verified-inputs" / architecture
    staged.parent.mkdir()
    staged.write_bytes(intake.read_bytes())
    best = _write_torch_checkpoint(
        run_directory / "weights" / "best.pt", f"best-{candidate}"
    )
    last = _write_torch_checkpoint(
        run_directory / "weights" / "last.pt", f"last-{candidate}"
    )
    candidate_spec = TRAINING_PLAN.candidate(candidate)
    arguments = frozen_train_arguments(
        candidate_spec,
        dataset_yaml=dataset_yaml.resolve(),
        run_directory=run_directory.resolve(),
        requested_batch=resolved_batch,
    )
    effective_arguments = {**arguments, "model": str(staged.resolve())}
    args_path = run_directory / "args.yaml"
    args_path.write_text(json.dumps(effective_arguments, sort_keys=True), encoding="utf-8")
    results_path = run_directory / "results.csv"
    fields = [
        "epoch", "train/box_loss", "train/cls_loss", "train/dfl_loss",
        "metrics/precision(B)", "metrics/recall(B)", "metrics/mAP50(B)",
        "metrics/mAP50-95(B)",
    ]
    with results_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for epoch in range(1, candidate_spec.epochs + 1):
            writer.writerow(
                {
                    "epoch": epoch,
                    "train/box_loss": 1.0 / epoch,
                    "train/cls_loss": 0.8 / epoch,
                    "train/dfl_loss": 0.6 / epoch,
                    "metrics/precision(B)": 0.70 + epoch / 1000,
                    "metrics/recall(B)": 0.75 + epoch / 1000,
                    "metrics/mAP50(B)": 0.72 + epoch / 1000,
                    "metrics/mAP50-95(B)": 0.50 + epoch / 1000,
                }
            )
    metrics = _read_final_metrics(results_path, candidate_spec.epochs)
    runtime = {
        "verified_at_utc": "2026-08-22T10:03:00+00:00",
        "python": FROZEN_RUNTIME["python"],
        "torch": FROZEN_RUNTIME["torch"],
        "torchvision": FROZEN_RUNTIME["torchvision"],
        "ultralytics": FROZEN_RUNTIME["ultralytics"],
        "cuda_runtime": "13.0",
        "cudnn_version": 9300,
        "visible_gpu_count": 1,
        "gpu_index": 0,
        "gpu_name": "NVIDIA GeForce RTX 5090",
        "gpu_total_memory_bytes": RTX_5090_MEMORY_BYTES,
    }
    report = {
        "schema": "veriswarm.rescue.training_run.v1",
        "status": "completed",
        "accepted_by_human": False,
        "model_id": MODEL_ID,
        "training_plan_sha256": PLAN_HASH,
        "source": source,
        "class_map": {"0": "person_candidate"},
        "candidate": candidate_spec.to_dict(),
        "dataset": {
            "yaml_path": str(dataset_yaml.resolve()),
            "yaml_sha256": sha256_file(dataset_yaml),
            "audit": audit,
            "automated_qualification": qualification,
        },
        "base_checkpoint": {
            "intake_path": str(intake.resolve()),
            "staged_path": str(staged.resolve()),
            "sha256": sha256_file(intake),
            "trusted_intake": trusted_intake,
        },
        "runtime": runtime,
        "cloud_environment": {
            "path": str(cloud_path.resolve()),
            "sha256": sha256_file(cloud_path),
            "test_seam": False,
            "manifest": cloud,
        },
        "batch_decision": {
            "path": str(batch_path.resolve()),
            "sha256": sha256_file(batch_path),
            "test_seam": False,
            "decision": batch_decision,
        },
        "arguments": arguments,
        "effective_arguments": dict(sorted(effective_arguments.items())),
        "resolved_batch_size": resolved_batch,
        "started_at_utc": "2026-08-22T10:04:00+00:00",
        "finished_at_utc": "2026-08-22T11:04:00+00:00",
        "duration_seconds": 3600.0,
        "cuda_peak_bytes": {"allocated": 1024**3, "reserved": 2 * 1024**3},
        "metrics": metrics,
        "smoke_gate": None,
        "artifacts": {
            "args_yaml": _artifact(args_path),
            "results_csv": _artifact(results_path),
            "best_pt": _artifact(best),
            "last_pt": _artifact(last),
        },
    }
    report_path = _write_json(run_directory / "veriswarm_training_run.json", report)
    return best, report_path, report


def _report_ref(path: Path, report_id: str) -> dict[str, str]:
    return {"report_id": report_id, "path": str(path.resolve()), "sha256": sha256_file(path)}


def _runtime() -> dict[str, str]:
    return {
        "hardware": "Jetson Orin Nano 8GB",
        "device_tree_model": "NVIDIA Jetson Orin Nano Engineering Reference Developer Kit",
        "power_mode_display": "NV Power Mode: 15W",
        "nv_tegra_release": "# R36 (release), REVISION: 4.3",
        "uname": "Linux orin 5.15.148-tegra aarch64 GNU/Linux",
        "architecture": "aarch64",
        "nvpmodel_output": "NV Power Mode: 15W",
        "package_runtime_output": "nvidia-jetpack 6.2; tensorrt 10.3",
        "jetpack": "6.2",
        "l4t": "36.4.3",
        "cuda": "12.6",
        "cudnn": "9.3",
        "tensorrt": "10.3",
    }


def _graph(shape: int) -> dict[str, object]:
    return {
        "input_name": "images",
        "input_dtype": "float32",
        "input_shape_nchw": [1, 3, shape, shape],
        "output_count": 1,
        "output_batch": 1,
        "class_count": 1,
        "task": "detect",
    }


def _inspector(shape: int):
    return lambda _path: _graph(shape)


def _canonical_value_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _make_evaluation_sources(workspace: Path, candidate: str) -> dict[str, object]:
    source_root = workspace / "evaluation" / candidate / "sources"
    source_root.mkdir(parents=True)
    real_source = _write_json(
        source_root / "real-source.json",
        {"schema": "veriswarm.test.real_source.v1", "passed": True},
    )

    c2a_images: dict[str, list[Path]] = {}
    inventory_lines: list[str] = []
    for split in ("train", "val", "test"):
        c2a_images[split] = []
        for index in range(4):
            image_path = source_root / "c2a-images" / split / f"{split}-{index}.jpg"
            image_path.parent.mkdir(parents=True, exist_ok=True)
            image_path.write_bytes(
                f"{candidate}:synthetic:{split}:image:{index}".encode("utf-8")
            )
            c2a_images[split].append(image_path.resolve())
            inventory_lines.append(
                json.dumps(
                    {
                        "schema": "veriswarm.rescue.c2a_inventory_record.v1",
                        "derived_split": split,
                        "scene_group_id": f"{candidate}-{split}-scene-{index}",
                        "source_image_sha256": sha256_file(image_path),
                        "derived_image_path": str(image_path.resolve()),
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
    inventory_path = source_root / "c2a-source-inventory.jsonl"
    inventory_path.write_text("\n".join(inventory_lines) + "\n", encoding="utf-8")
    synthetic_source = _write_json(
        source_root / "c2a-preparation.json",
        {
            "schema": "veriswarm.rescue.c2a_preparation.v1",
            "artifacts": {
                "inventory_path": str(inventory_path.resolve()),
                "inventory_sha256": sha256_file(inventory_path),
            },
        },
    )
    return {
        "real_aerial": {
            "path": real_source,
            "schema": "veriswarm.test.real_source.v1",
        },
        "synthetic_disaster": {
            "path": synthetic_source,
            "schema": "veriswarm.rescue.c2a_preparation.v1",
            "images": c2a_images,
            "inventory": inventory_path,
        },
    }


def _make_evaluation_dataset(
    *,
    workspace: Path,
    repository: Path,
    candidate: str,
    shape: int,
    domain: str,
    split: str,
    source: dict[str, object],
    token: str,
    reuse_images: list[Path] | None = None,
) -> tuple[Path, dict, list[dict]]:
    evidence_root = workspace / "evaluation" / candidate / token / domain / split
    evidence_root.mkdir(parents=True)
    images: list[Path] = []
    if reuse_images is not None:
        images = [Path(path).resolve() for path in reuse_images]
    elif domain == "synthetic_disaster":
        images = [Path(path).resolve() for path in source["images"][split]]
    else:
        for index in range(4):
            image_path = evidence_root / "images" / f"{split}-{index}.jpg"
            image_path.parent.mkdir(parents=True, exist_ok=True)
            image_path.write_bytes(
                f"{candidate}:{domain}:{split}:{token}:{index}".encode("utf-8")
            )
            images.append(image_path.resolve())
    records = [
        {
            "image_id": f"{domain}-{split}-{index}",
            "image_path": str(image_path),
            "image_sha256": sha256_file(image_path),
            "evaluation_width": shape,
            "evaluation_height": shape,
            "boxes": [
                {
                    "class_id": 0,
                    "coordinate_format": "xyxy_abs",
                    "bbox": [10.0, 10.0, 20.0, 20.0],
                }
            ],
        }
        for index, image_path in enumerate(images)
    ]
    manifest_path = evidence_root / "dataset.json"
    create_evaluation_dataset_manifest(
        dataset_id=f"{domain}-{split}-dataset",
        split=split,
        data_kind=domain,
        ground_truth_records=records,
        source_evidence_path=source["path"],
        source_evidence_schema=source["schema"],
        records_path=evidence_root / "ground-truth.json",
        manifest_path=manifest_path,
        workspace_root=workspace,
        repository_root=repository,
    )
    return manifest_path, json.loads(manifest_path.read_text(encoding="utf-8")), records


def _make_evaluator_report(
    *,
    workspace: Path,
    repository: Path,
    candidate: str,
    architecture: str,
    shape: int,
    domain: str,
    split: str,
    token: str,
    model_path: Path,
    manifest_path: Path,
    manifest: dict,
    records: list[dict],
    detected_images: int,
):
    report_root = manifest_path.parent / token
    report_root.mkdir()
    prediction_records = []
    for index, record in enumerate(records):
        boxes = []
        if index < detected_images:
            boxes.append(
                {
                    "class_id": 0,
                    "coordinate_format": "xyxy_abs",
                    "bbox": [10.0, 10.0, 20.0, 20.0],
                    "confidence": 0.9,
                }
            )
        prediction_records.append(
            {
                "image_id": record["image_id"],
                "evaluation_width": shape,
                "evaluation_height": shape,
                "boxes": boxes,
            }
        )
    prediction_path = report_root / "predictions.json"
    prediction_path.write_text(
        json.dumps(prediction_records, sort_keys=True), encoding="utf-8"
    )
    staged_model = report_root / f"staged-{model_path.name}"
    staged_model.write_bytes(model_path.read_bytes())
    input_shape = [1, 3, shape, shape]
    inference_path = report_root / "inference.json"
    inference = {
        "schema": INFERENCE_REPORT_SCHEMA,
        "report_id": f"inference-{candidate}-{domain}-{split}-{token}",
        "candidate": candidate,
        "candidate_stage": "full",
        "architecture": architecture,
        "input_shape_nchw": input_shape,
        "training_plan_sha256": PLAN_HASH,
        "model": {
            "source_path": str(model_path.resolve()),
            "staged_path": str(staged_model.resolve()),
            "sha256": sha256_file(model_path),
        },
        "dataset_manifest": {
            "path": str(manifest_path.resolve()),
            "sha256": sha256_file(manifest_path),
        },
        "dataset_id": manifest["dataset_id"],
        "dataset_sha256": manifest["dataset_sha256"],
        "split": split,
        "data_kind": domain,
        "ordered_image_ids_sha256": _canonical_value_sha256(
            manifest["ordered_image_ids"]
        ),
        "ground_truth_records": {
            "path": manifest["ground_truth_records_path"],
            "sha256": manifest["ground_truth_records_sha256"],
        },
        "prediction_records": {
            "path": str(prediction_path.resolve()),
            "sha256": sha256_file(prediction_path),
            "image_count": len(prediction_records),
        },
        "preprocess": {
            "coordinate_space": "original_source_pixels",
            "resize": "ultralytics_letterbox",
            "input_shape_nchw": input_shape,
        },
        "inference": {
            "imgsz": shape,
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
        },
        "started_at_utc": "2026-08-22T12:00:00+00:00",
        "completed_at_utc": "2026-08-22T12:01:00+00:00",
        "passed": True,
    }
    _write_json(inference_path, inference)
    validation_path = report_root / "validation.json"
    sealed = generate_validation_report(
        candidate=candidate,
        candidate_stage="full",
        split=split,
        data_kind=domain,
        model_checkpoint=model_path,
        expected_model_sha256=sha256_file(model_path),
        training_plan_sha256=PLAN_HASH,
        dataset_manifest=manifest_path,
        expected_dataset_manifest_sha256=sha256_file(manifest_path),
        inference_report=inference_path,
        confidence_thresholds=[0.37],
        output_path=validation_path,
        workspace_root=workspace,
        repository_root=repository,
    )
    return sealed, validation_path


def _make_evaluation_pair(
    evidence: dict[str, object],
    *,
    domain: str,
    split: str,
    token: str,
    passing: bool = True,
    reuse_images: list[Path] | None = None,
) -> dict[str, object]:
    workspace = Path(evidence["workspace"])
    repository = Path(evidence["repository"])
    manifest_path, manifest, records = _make_evaluation_dataset(
        workspace=workspace,
        repository=repository,
        candidate=evidence["candidate"],
        shape=evidence["shape"],
        domain=domain,
        split=split,
        source=evidence["evaluation_sources"][domain],
        token=token,
        reuse_images=reuse_images,
    )
    pt_report, pt_path = _make_evaluator_report(
        workspace=workspace,
        repository=repository,
        candidate=evidence["candidate"],
        architecture=evidence["architecture"],
        shape=evidence["shape"],
        domain=domain,
        split=split,
        token="pt",
        model_path=Path(evidence["pt"]),
        manifest_path=manifest_path,
        manifest=manifest,
        records=records,
        detected_images=len(records),
    )
    fp16_report, fp16_path = _make_evaluator_report(
        workspace=workspace,
        repository=repository,
        candidate=evidence["candidate"],
        architecture=evidence["architecture"],
        shape=evidence["shape"],
        domain=domain,
        split=split,
        token="fp16",
        model_path=Path(evidence["engine_file"]),
        manifest_path=manifest_path,
        manifest=manifest,
        records=records,
        detected_images=len(records) if passing else 2,
    )
    return {
        "pt": pt_report,
        "pt_path": pt_path,
        "fp16": fp16_report,
        "fp16_path": fp16_path,
        "manifest": manifest,
        "manifest_path": manifest_path,
        "records": records,
    }


def _make_candidate(root: Path, candidate: str = "yolov8n-640") -> dict[str, object]:
    root.mkdir()
    if candidate == "yolov8n-960":
        architecture, shape = "yolov8n.pt", 960
    elif candidate == "yolov8s-640":
        architecture, shape = "yolov8s.pt", 640
    else:
        architecture, shape = "yolov8n.pt", 640
    shape_nchw = [1, 3, shape, shape]
    threshold = 0.37

    pt, training_path, training = _make_training_completion(
        root, candidate, architecture, shape
    )
    model_root = root / "workspace" / "models" / candidate
    model_root.mkdir(parents=True)
    onnx = model_root / "best.onnx"
    engine = model_root / "best.engine"
    trtexec = root / "trtexec"
    onnx.write_bytes(f"{candidate}-onnx".encode())
    engine.write_bytes(f"{candidate}-engine".encode())
    trtexec.write_bytes(f"{candidate}-trtexec".encode())
    trtexec.chmod(0o755)

    onnx_report = {
        "schema": ONNX_EXPORT_SCHEMA,
        "report_id": f"onnx-{candidate}",
        "candidate": candidate,
        "architecture": architecture,
        "model_id": MODEL_ID,
        "training_plan_sha256": PLAN_HASH,
        "class_map": {"0": "person_candidate"},
        "input_shape_nchw": shape_nchw,
        "confidence_threshold": threshold,
        "artifacts": {"source_pt": _artifact(pt), "onnx": _artifact(onnx)},
        "export": {"format": "ONNX", "batch": 1, "dynamic": False, "opset": 17, "succeeded": True},
        "graph_contract": _graph(shape),
        "passed": True,
    }
    onnx_path = _write_json(root / "onnx-report.json", onnx_report)
    shape_text = f"1x3x{shape}x{shape}"
    engine_report = {
        "schema": ENGINE_IDENTITY_SCHEMA,
        "report_id": f"engine-{candidate}",
        "candidate": candidate,
        "architecture": architecture,
        "model_id": MODEL_ID,
        "training_plan_sha256": PLAN_HASH,
        "class_map": {"0": "person_candidate"},
        "input_shape_nchw": shape_nchw,
        "confidence_threshold": threshold,
        "precision": "FP16",
        "artifacts": {
            "source_pt": _artifact(pt),
            "onnx": _artifact(onnx),
            "tensorrt_engine": _artifact(engine),
        },
        "runtime": _runtime(),
        "build": {
            "built_on_target": True,
            "succeeded": True,
            "trtexec": {"path": str(trtexec.resolve()), "sha256": sha256_file(trtexec), "version": "10.3.0"},
            "argv": [
                str(trtexec.resolve()),
                f"--onnx={onnx.resolve()}",
                f"--saveEngine={engine.resolve()}",
                "--fp16",
                f"--minShapes=images:{shape_text}",
                f"--optShapes=images:{shape_text}",
                f"--maxShapes=images:{shape_text}",
            ],
            "input_name": "images",
            "min_shape": shape_nchw,
            "opt_shape": shape_nchw,
            "max_shape": shape_nchw,
            "executed_engine_path": str(engine.resolve()),
        },
        "passed": True,
    }
    engine_report_path = _write_json(root / "engine-report.json", engine_report)
    workspace = root / "workspace"
    repository = root / "repository"
    partial_evidence: dict[str, object] = {
        "candidate": candidate,
        "architecture": architecture,
        "shape": shape,
        "pt": pt,
        "engine_file": engine,
        "workspace": workspace,
        "repository": repository,
    }
    partial_evidence["evaluation_sources"] = _make_evaluation_sources(
        workspace, candidate
    )
    validation_pairs = {
        domain: _make_evaluation_pair(
            partial_evidence,
            domain=domain,
            split="val",
            token="qualification",
        )
        for domain in ("real_aerial", "synthetic_disaster")
    }
    threshold_report = {
        "schema": THRESHOLD_SELECTION_SCHEMA,
        "report_id": f"threshold-{candidate}",
        "candidate": candidate,
        "architecture": architecture,
        "model_id": MODEL_ID,
        "training_plan_sha256": PLAN_HASH,
        "class_map": {"0": "person_candidate"},
        "input_shape_nchw": shape_nchw,
        "split": "val",
        "model_sha256": sha256_file(pt),
        "confidence_threshold": threshold,
        "validation_reports": {
            domain: {
                "path": str(validation_pairs[domain]["pt_path"].resolve()),
                "sha256": sha256_file(validation_pairs[domain]["pt_path"]),
            }
            for domain in ("real_aerial", "synthetic_disaster")
        },
        "passed": True,
    }
    threshold_path = _write_json(root / "threshold.json", threshold_report)
    accuracy_report = {
        "schema": VALIDATION_ACCURACY_EQUIVALENCE_SCHEMA,
        "report_id": f"validation-equivalence-{candidate}",
        "candidate": candidate,
        "architecture": architecture,
        "model_id": MODEL_ID,
        "training_plan_sha256": PLAN_HASH,
        "class_map": {"0": "person_candidate"},
        "input_shape_nchw": shape_nchw,
        "confidence_threshold": threshold,
        "threshold_selection": {
            "report_id": threshold_report["report_id"],
            "path": str(threshold_path.resolve()),
            "sha256": sha256_file(threshold_path),
        },
        "split": "val",
        "artifacts": {
            "source_pt_sha256": sha256_file(pt),
            "onnx_sha256": sha256_file(onnx),
            "tensorrt_engine_sha256": sha256_file(engine),
        },
        "evaluation_reports": {
            domain: {
                "pt": {
                    "path": str(validation_pairs[domain]["pt_path"].resolve()),
                    "sha256": sha256_file(validation_pairs[domain]["pt_path"]),
                },
                "fp16": {
                    "path": str(validation_pairs[domain]["fp16_path"].resolve()),
                    "sha256": sha256_file(validation_pairs[domain]["fp16_path"]),
                },
            }
            for domain in ("real_aerial", "synthetic_disaster")
        },
        "passed": True,
    }
    accuracy_path = _write_json(root / "validation-equivalence.json", accuracy_report)

    safety: dict[str, Path] = {}
    safety_names = {
        "offline": ("offline_restart", True),
        "timeout": ("camera_timeout", False),
        "corrupt": ("corrupt_frame", False),
    }
    for name, (test_name, network_disabled) in safety_names.items():
        safety[name] = _write_json(
            root / f"{name}.json",
            {"schema": "veriswarm.rescue.safety_test.v1",
             "report_id": f"safety-{name}", "test": test_name,
             "result": "fail_closed", "passed": True,
             "network_disabled": network_disabled},
        )
    raw_artifacts = {}
    for index, name in enumerate(("raw_capture", "pipeline_log", "telemetry_log", "tegrastats_log", "environment_log")):
        path = root / f"{name}.evidence"
        if name == "telemetry_log":
            samples = [
                {"monotonic_ns": 10_000_000_000, "available_ram_bytes": 2 * 1024**3,
                 "swap_used_bytes": 0, "process_rss_bytes": 1024**3,
                 "cpu_temperature_c": 60.0, "gpu_temperature_c": 58.0,
                 "power_w": 9.0, "cpu_clock_hz": 729_000_000,
                 "gpu_clock_hz": 612_000_000, "throttled": False},
                {"monotonic_ns": 460_000_000_000, "available_ram_bytes": 1024**3,
                 "swap_used_bytes": 0, "process_rss_bytes": 2 * 1024**3,
                 "cpu_temperature_c": 67.0, "gpu_temperature_c": 65.0,
                 "power_w": 12.0, "cpu_clock_hz": 800_000_000,
                 "gpu_clock_hz": 700_000_000, "throttled": False},
                {"monotonic_ns": 910_000_000_000, "available_ram_bytes": 1536 * 1024**2,
                 "swap_used_bytes": 0, "process_rss_bytes": 1536 * 1024**2,
                 "cpu_temperature_c": 64.0, "gpu_temperature_c": 62.0,
                 "power_w": 15.0, "cpu_clock_hz": 750_000_000,
                 "gpu_clock_hz": 650_000_000, "throttled": False},
            ]
            path.write_text("\n".join(json.dumps(sample) for sample in samples), encoding="utf-8")
        elif name == "environment_log":
            path.write_text(json.dumps(_runtime()), encoding="utf-8")
        else:
            path.write_bytes(f"{candidate}-{name}-{index}".encode())
        raw_artifacts[name] = _artifact(path)
    start_ns = 10_000_000_000
    end_ns = start_ns + 900_000_000_000
    benchmark_report = {
        "schema": JETSON_BENCHMARK_SCHEMA,
        "report_id": f"benchmark-{candidate}",
        "candidate": candidate,
        "architecture": architecture,
        "model_id": MODEL_ID,
        "training_plan_sha256": PLAN_HASH,
        "class_map": {"0": "person_candidate"},
        "input_shape_nchw": shape_nchw,
        "confidence_threshold": threshold,
        "precision": "FP16",
        "engine_identity": {
            "report_id": engine_report["report_id"],
            "sha256": sha256_file(engine_report_path),
            "executed_engine_path": str(engine.resolve()),
            "executed_engine_sha256": sha256_file(engine),
        },
        "artifacts": raw_artifacts,
        "target": _runtime(),
        "pipeline": {
            "stages": ["camera_decode", "preprocess", "tensorrt_fp16", "nms_tracking", "rescue_event_creation", "sqlite_outbox_enqueue", "model_receipt_bookkeeping"],
            "newest_frame_policy": "process_newest_drop_stale",
            "cloud_or_network_dependency": False,
        },
        "measurement": {
            "monotonic_start_ns": start_ns,
            "monotonic_end_ns": end_ns,
            "duration_seconds": 900.0,
            "sample_count": 3,
            "warmup_frames": 50,
            "completed_frames": 4500,
            "stale_replaced_frames": 22500,
            "error_dropped_frames": 0,
            "invalid_frames": 0,
            "total_input_frames": 27000,
            "capture_fps": 30.0,
            "fps": 5.0,
            "event_count": 100,
            "outbox_enqueue_count": 100,
            "model_receipt_bookkeeping_count": 100,
        },
        "latency_ms": {
            "selected_frame_age": {
                "p50": 15.0,
                "p95": 30.0,
                "p99": 32.0,
                "max": 33.0,
            },
            "tensorrt": {"p50": 120.0, "p95": 175.0, "p99": 179.0, "max": 180.0},
            "camera_to_event": {"p50": 160.0, "p95": 245.0, "p99": 248.0, "max": 250.0},
        },
        "reliability": {"errors": 0, "oom_events": 0, "process_restarts": 0},
        "resources": {
            "model_load_time_ms": 800.0,
            "process_rss_peak_bytes": 2 * 1024**3,
            "min_available_ram_bytes": 1024**3,
            "swap_start_bytes": 0,
            "swap_peak_bytes": 0,
            "swap_end_bytes": 0,
            "telemetry_sample_count": 3,
            "max_cpu_temperature_c": 67.0,
            "max_gpu_temperature_c": 65.0,
            "power_min_w": 9.0,
            "power_mean_w": 12.0,
            "power_max_w": 15.0,
            "cpu_clock_min_hz": 729_000_000,
            "gpu_clock_min_hz": 612_000_000,
            "thermal_throttling": False,
        },
        "safety_tests": {
            "offline_restart": {**_report_ref(safety["offline"], "safety-offline"), "passed": True, "network_disabled": True},
            "camera_timeout": {**_report_ref(safety["timeout"], "safety-timeout"), "passed": True, "network_disabled": False},
            "corrupt_frame": {**_report_ref(safety["corrupt"], "safety-corrupt"), "passed": True, "network_disabled": False},
        },
        "passed": True,
    }
    benchmark_path = _write_json(root / "benchmark.json", benchmark_report)
    paths = {
        "training": training_path,
        "threshold": threshold_path,
        "onnx": onnx_path,
        "engine": engine_report_path,
        "validation_accuracy": accuracy_path,
        "benchmark": benchmark_path,
    }
    return {
        "candidate": candidate,
        "architecture": architecture,
        "shape": shape,
        "pt": pt,
        "onnx_file": onnx,
        "engine_file": engine,
        "trtexec": trtexec,
        "workspace": workspace,
        "repository": repository,
        "evaluation_sources": partial_evidence["evaluation_sources"],
        "validation_pairs": validation_pairs,
        "reports": {
            "training": training,
            "threshold": threshold_report,
            "onnx": onnx_report,
            "engine": engine_report,
            "validation_accuracy": accuracy_report,
            "benchmark": benchmark_report,
        },
        "paths": paths,
    }


def _build_qualification(evidence: dict[str, object]):
    paths = evidence["paths"]
    return build_candidate_qualification(
        report_id=f"qualification-{evidence['candidate']}",
        candidate=evidence["candidate"],
        architecture=evidence["architecture"],
        training_plan_sha256=PLAN_HASH,
        training_completion_report_path=paths["training"],
        threshold_selection_report_path=paths["threshold"],
        onnx_report_path=paths["onnx"],
        engine_identity_report_path=paths["engine"],
        validation_accuracy_equivalence_report_path=paths["validation_accuracy"],
        nano_benchmark_report_path=paths["benchmark"],
        workspace_root=evidence["workspace"],
        repository_root=evidence["repository"],
        graph_inspector=_inspector(evidence["shape"]),
    )


def _qualification_semantic_hash(qualification) -> str:
    return hashlib.sha256(canonical_json_bytes(qualification.to_dict())).hexdigest()


def _consideration(qualification) -> QualificationEvidence:
    return QualificationEvidence(
        candidate=qualification.candidate,
        qualification_sha256=_qualification_semantic_hash(qualification),
        qualified=qualification.qualified,
        validation_passed=True,
        confidence_threshold=qualification.confidence_threshold,
        source_pt_sha256=qualification.source_pt_sha256,
        onnx_sha256=qualification.onnx_sha256,
        executed_engine_sha256=qualification.executed_engine_sha256,
        report_hashes=tuple(sorted({
            "training_completion": qualification.training_completion_report_sha256,
            "threshold_selection": qualification.threshold_selection_report_sha256,
            "onnx_export": qualification.onnx_report_sha256,
            "engine_identity": qualification.engine_identity_report_sha256,
            "validation_accuracy_equivalence": qualification.validation_accuracy_equivalence_report_sha256,
            "nano_benchmark": qualification.nano_benchmark_report_sha256,
        }.items())),
    )


def _selected_candidate(
    selected, evidence: dict[str, object], all_qualifications
) -> SelectedCandidate:
    real = evidence["validation_pairs"]["real_aerial"]["pt"]
    synthetic = evidence["validation_pairs"]["synthetic_disaster"]["pt"]
    return SelectedCandidate(
        candidate=selected.candidate,
        architecture=selected.architecture,
        input_shape_nchw=selected.input_shape_nchw,
        training_plan_sha256=selected.training_plan_sha256,
        confidence_threshold=selected.confidence_threshold,
        source_pt_sha256=selected.source_pt_sha256,
        onnx_sha256=selected.onnx_sha256,
        executed_engine_path=selected.executed_engine_path,
        executed_engine_sha256=selected.executed_engine_sha256,
        runtime=tuple(sorted(selected.runtime.items())),
        real_aerial=ValidationEvidence(
            data_kind=real.data_kind,
            dataset_id=real.dataset_id,
            dataset_sha256=real.dataset_sha256,
            validation_report_sha256=real.sha256,
            inference_evidence=real.inference_evidence,
            metrics=real.threshold_points[0],
        ),
        synthetic_disaster=ValidationEvidence(
            data_kind=synthetic.data_kind,
            dataset_id=synthetic.dataset_id,
            dataset_sha256=synthetic.dataset_sha256,
            validation_report_sha256=synthetic.sha256,
            inference_evidence=synthetic.inference_evidence,
            metrics=synthetic.threshold_points[0],
        ),
        considered_candidates=tuple(
            _consideration(qualification) for qualification in all_qualifications
        ),
    )


def _make_untouched_test_report(
    root: Path,
    evidence: dict[str, object],
    selected: SelectedCandidate,
    *,
    passing: bool = True,
    overlap_real_validation: bool = False,
) -> tuple[dict[str, object], Path]:
    """Create the single post-selection test report for the frozen candidate."""

    token = f"untouched-{root.name}-{'pass' if passing else 'fail'}"
    test_pairs = {
        domain: _make_evaluation_pair(
            evidence,
            domain=domain,
            split="test",
            token=token,
            passing=passing if domain == "real_aerial" else True,
            reuse_images=(
                [
                    Path(record["image_path"])
                    for record in evidence["validation_pairs"]["real_aerial"]["records"]
                ]
                if domain == "real_aerial" and overlap_real_validation
                else None
            ),
        )
        for domain in ("real_aerial", "synthetic_disaster")
    }
    report = {
        "schema": UNTOUCHED_TEST_ACCURACY_SCHEMA,
        "report_id": f"untouched-test-{selected.candidate}-{root.name}",
        "selected_candidate_sha256": selected.sha256,
        "candidate": evidence["candidate"],
        "architecture": evidence["architecture"],
        "model_id": MODEL_ID,
        "training_plan_sha256": PLAN_HASH,
        "class_map": {"0": "person_candidate"},
        "input_shape_nchw": [1, 3, evidence["shape"], evidence["shape"]],
        "confidence_threshold": selected.confidence_threshold,
        "threshold_selection": {
            "report_id": evidence["reports"]["threshold"]["report_id"],
            "path": str(Path(evidence["paths"]["threshold"]).resolve()),
            "sha256": sha256_file(evidence["paths"]["threshold"]),
        },
        "split": "test",
        "artifacts": {
            "source_pt_sha256": sha256_file(evidence["pt"]),
            "onnx_sha256": sha256_file(evidence["onnx_file"]),
            "tensorrt_engine_sha256": sha256_file(evidence["engine_file"]),
        },
        "evaluation_reports": {
            domain: {
                "pt": {
                    "path": str(test_pairs[domain]["pt_path"].resolve()),
                    "sha256": sha256_file(test_pairs[domain]["pt_path"]),
                },
                "fp16": {
                    "path": str(test_pairs[domain]["fp16_path"].resolve()),
                    "sha256": sha256_file(test_pairs[domain]["fp16_path"]),
                },
            }
            for domain in ("real_aerial", "synthetic_disaster")
        },
        "passed": passing,
    }
    report_path = (
        Path(evidence["workspace"])
        / "evaluation"
        / evidence["candidate"]
        / token
        / "untouched-test-accuracy.json"
    )
    return report, _write_json(report_path, report)


def test_candidate_qualification_binds_all_local_evidence(tmp_path: Path) -> None:
    evidence = _make_candidate(tmp_path / "c640")
    qualification = _build_qualification(evidence)
    assert qualification.qualified is True
    assert qualification.executed_engine_sha256 == sha256_file(evidence["engine_file"])
    assert qualification.runtime["hardware"] == "Jetson Orin Nano 8GB"
    assert is_validated_candidate_qualification(qualification) is True
    assert qualification.validation_accuracy_equivalence_report_sha256 == sha256_file(
        evidence["paths"]["validation_accuracy"]
    )
    assert "untouched_test_accuracy_report_sha256" not in qualification.to_dict()

    directly_reconstructed = replace(qualification)
    assert is_validated_candidate_qualification(directly_reconstructed) is False

    validated = validate_candidate_qualification(
        qualification.to_dict(),
        training_completion_report_path=evidence["paths"]["training"],
        threshold_selection_report_path=evidence["paths"]["threshold"],
        onnx_report_path=evidence["paths"]["onnx"],
        engine_identity_report_path=evidence["paths"]["engine"],
        validation_accuracy_equivalence_report_path=evidence["paths"]["validation_accuracy"],
        nano_benchmark_report_path=evidence["paths"]["benchmark"],
        workspace_root=evidence["workspace"],
        repository_root=evidence["repository"],
        graph_inspector=_inspector(640),
    )
    assert validated == qualification
    assert is_validated_candidate_qualification(validated) is True


def test_training_completion_rejects_old_minimal_hand_authored_report(
    tmp_path: Path,
) -> None:
    evidence = _make_candidate(tmp_path / "minimal")
    report = evidence["reports"]["training"]
    minimal = {
        "schema": report["schema"],
        "status": "completed",
        "model_id": MODEL_ID,
        "training_plan_sha256": PLAN_HASH,
        "class_map": {"0": "person_candidate"},
        "candidate": report["candidate"],
        "artifacts": {"best_pt": report["artifacts"]["best_pt"]},
    }
    _write_json(evidence["paths"]["training"], minimal)
    with pytest.raises(DeploymentValidationError, match="fields differ"):
        _build_qualification(evidence)


def test_training_completion_rejects_arbitrary_bytes_as_best_checkpoint(
    tmp_path: Path,
) -> None:
    evidence = _make_candidate(tmp_path / "arbitrary-checkpoint")
    report = deepcopy(evidence["reports"]["training"])
    best = Path(report["artifacts"]["best_pt"]["path"])
    best.write_bytes(b"not-a-trained-torch-checkpoint")
    report["artifacts"]["best_pt"]["sha256"] = sha256_file(best)
    _write_json(evidence["paths"]["training"], report)
    with pytest.raises(DeploymentValidationError, match="Torch checkpoint container"):
        _build_qualification(evidence)


def test_training_completion_rederives_epoch_count_and_metrics_from_csv(
    tmp_path: Path,
) -> None:
    evidence = _make_candidate(tmp_path / "csv")
    report = deepcopy(evidence["reports"]["training"])
    results = Path(report["artifacts"]["results_csv"]["path"])
    lines = results.read_text(encoding="utf-8").splitlines()
    results.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
    report["artifacts"]["results_csv"]["sha256"] = sha256_file(results)
    _write_json(evidence["paths"]["training"], report)
    with pytest.raises(DeploymentValidationError, match="epoch count mismatch"):
        _build_qualification(evidence)

    evidence = _make_candidate(tmp_path / "claimed-metrics")
    report = deepcopy(evidence["reports"]["training"])
    report["metrics"]["final"]["metrics/recall(B)"] = 1.0
    _write_json(evidence["paths"]["training"], report)
    with pytest.raises(DeploymentValidationError, match="metrics differ"):
        _build_qualification(evidence)


def test_training_completion_requires_frozen_effective_args_and_cuda(
    tmp_path: Path,
) -> None:
    evidence = _make_candidate(tmp_path / "args")
    report = deepcopy(evidence["reports"]["training"])
    report["arguments"]["epochs"] = 1
    _write_json(evidence["paths"]["training"], report)
    with pytest.raises(DeploymentValidationError, match="frozen runner arguments"):
        _build_qualification(evidence)

    evidence = _make_candidate(tmp_path / "cuda")
    report = deepcopy(evidence["reports"]["training"])
    report["cuda_peak_bytes"]["reserved"] = RTX_5090_MEMORY_BYTES + 1
    _write_json(evidence["paths"]["training"], report)
    with pytest.raises(DeploymentValidationError, match="CUDA peak evidence"):
        _build_qualification(evidence)


def test_training_completion_requires_post_training_dataset_integrity(
    tmp_path: Path,
) -> None:
    evidence = _make_candidate(tmp_path / "post-integrity")
    report = deepcopy(evidence["reports"]["training"])
    report["dataset"]["audit"][
        "post_training_authoritative_integrity_sha256"
    ] = "f" * 64
    _write_json(evidence["paths"]["training"], report)
    with pytest.raises(DeploymentValidationError, match="pre/post authoritative"):
        _build_qualification(evidence)


def test_training_completion_rejects_source_or_test_seam_evidence(
    tmp_path: Path,
) -> None:
    evidence = _make_candidate(tmp_path / "dirty-source")
    report = deepcopy(evidence["reports"]["training"])
    report["source"]["clean"] = False
    _write_json(evidence["paths"]["training"], report)
    with pytest.raises(DeploymentValidationError, match="source was not clean"):
        _build_qualification(evidence)

    evidence = _make_candidate(tmp_path / "cloud-test-seam")
    report = deepcopy(evidence["reports"]["training"])
    report["cloud_environment"]["test_seam"] = True
    _write_json(evidence["paths"]["training"], report)
    with pytest.raises(DeploymentValidationError, match="test-seam cloud"):
        _build_qualification(evidence)


def test_onnx_graph_is_inspected_not_trusted_from_report(tmp_path: Path) -> None:
    evidence = _make_candidate(tmp_path / "onnx")
    with pytest.raises(DeploymentValidationError, match="actual frozen graph"):
        validate_onnx_export_report(
            evidence["reports"]["onnx"],
            graph_inspector=lambda _path: {**_graph(640), "class_count": 2},
        )


def test_onnx_export_v2_is_consumed_without_schema_translation(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    repository = tmp_path / "repository"
    workspace.mkdir()
    repository.mkdir()
    checkpoint = workspace / "best.pt"
    checkpoint.write_bytes(b"trained-person-model")
    plan = TrainingPlan.load(
        Path(__file__).resolve().parents[1] / "config" / "sar_rgb_person_training.json"
    )

    class FakeModel:
        names = {0: "person_candidate"}
        task = "detect"

        def __init__(self, path: str):
            self.path = Path(path)

        def export(self, **_kwargs):
            target = self.path.with_suffix(".onnx")
            target.write_bytes(b"static-onnx")
            return str(target)

    report_path = export_static_onnx(
        candidate=plan.candidate("yolov8n-640"),
        source_checkpoint=checkpoint,
        expected_checkpoint_sha256=sha256_file(checkpoint),
        training_plan_sha256=PLAN_HASH,
        confidence_threshold=0.37,
        report_id="onnx-integration-001",
        export_directory=workspace / "export",
        workspace_root=workspace,
        repository_root=repository,
        yolo_factory=FakeModel,
        graph_inspector=_inspector(640),
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert validate_onnx_export_report(
        report, graph_inspector=_inspector(640)
    )["schema"] == ONNX_EXPORT_SCHEMA


@pytest.mark.parametrize("display", ["NV Power Mode: 115W", "MODE_15W", "15W and 25W"])
def test_power_mode_requires_one_standalone_15w(tmp_path: Path, display: str) -> None:
    evidence = _make_candidate(tmp_path / "power")
    report = deepcopy(evidence["reports"]["engine"])
    report["runtime"]["power_mode_display"] = display
    with pytest.raises(DeploymentValidationError, match="standalone 15W"):
        validate_engine_identity_report(report)


@pytest.mark.parametrize("extra", ["--int8", "--fp16", "--best"])
def test_engine_rejects_noncanonical_duplicate_or_contradictory_argv(
    tmp_path: Path, extra: str
) -> None:
    evidence = _make_candidate(tmp_path / "argv")
    report = deepcopy(evidence["reports"]["engine"])
    report["build"]["argv"].append(extra)
    with pytest.raises(DeploymentValidationError, match="non-canonical"):
        validate_engine_identity_report(report)


def test_candidate_accuracy_equivalence_is_validation_only_and_computes_loss(
    tmp_path: Path,
) -> None:
    evidence = _make_candidate(tmp_path / "accuracy")
    report = deepcopy(evidence["reports"]["validation_accuracy"])
    report["split"] = "test"
    with pytest.raises(DeploymentValidationError, match="exactly 'val'"):
        validate_validation_accuracy_equivalence_report(
            report,
            engine_report=evidence["reports"]["engine"],
            threshold_report_path=evidence["paths"]["threshold"],
            workspace_root=evidence["workspace"],
            repository_root=evidence["repository"],
        )

    report = deepcopy(evidence["reports"]["validation_accuracy"])
    report["evaluation"] = {"real_aerial": {"fp16": {"recall": 1.0}}}
    with pytest.raises(DeploymentValidationError, match="fields differ"):
        validate_validation_accuracy_equivalence_report(
            report,
            engine_report=evidence["reports"]["engine"],
            threshold_report_path=evidence["paths"]["threshold"],
            workspace_root=evidence["workspace"],
            repository_root=evidence["repository"],
        )


def test_accuracy_rejects_dataset_or_threshold_substitution(tmp_path: Path) -> None:
    evidence = _make_candidate(tmp_path / "dataset")
    report = deepcopy(evidence["reports"]["validation_accuracy"])
    report["evaluation_reports"]["synthetic_disaster"] = deepcopy(
        report["evaluation_reports"]["real_aerial"]
    )
    with pytest.raises(DeploymentValidationError, match="differs from threshold evidence"):
        validate_validation_accuracy_equivalence_report(
            report,
            engine_report=evidence["reports"]["engine"],
            threshold_report_path=evidence["paths"]["threshold"],
            workspace_root=evidence["workspace"],
            repository_root=evidence["repository"],
        )

    report = deepcopy(evidence["reports"]["validation_accuracy"])
    report["threshold_selection"]["sha256"] = "f" * 64
    with pytest.raises(DeploymentValidationError, match="file hash mismatch"):
        validate_validation_accuracy_equivalence_report(
            report,
            engine_report=evidence["reports"]["engine"],
            threshold_report_path=evidence["paths"]["threshold"],
            workspace_root=evidence["workspace"],
            repository_root=evidence["repository"],
        )


def test_accuracy_reopens_prediction_records_and_checkpoint_lineage(tmp_path: Path) -> None:
    evidence = _make_candidate(tmp_path / "sealed-accuracy")
    fp16 = evidence["validation_pairs"]["real_aerial"]["fp16"]
    prediction_path = Path(fp16.inference_evidence.prediction_records_path)
    prediction_path.write_text("[]", encoding="utf-8")
    with pytest.raises(DeploymentValidationError, match="evaluator-sealed"):
        _build_qualification(evidence)

    fresh = _make_candidate(tmp_path / "checkpoint-swap")
    report = deepcopy(fresh["reports"]["validation_accuracy"])
    report["evaluation_reports"]["real_aerial"]["fp16"] = deepcopy(
        report["evaluation_reports"]["real_aerial"]["pt"]
    )
    with pytest.raises(DeploymentValidationError, match="checkpoint lineage differs"):
        validate_validation_accuracy_equivalence_report(
            report,
            engine_report=fresh["reports"]["engine"],
            threshold_report_path=fresh["paths"]["threshold"],
            workspace_root=fresh["workspace"],
            repository_root=fresh["repository"],
        )


@pytest.mark.parametrize(
    ("section", "field", "value", "message"),
    [
        ("target", "hardware", "NVIDIA RTX 5090 Windows", "Windows or RTX"),
        ("pipeline", "newest_frame_policy", "fifo", "newest"),
        ("measurement", "monotonic_end_ns", 1, "monotonic"),
        ("measurement", "outbox_enqueue_count", 99, "enqueued"),
        ("resources", "swap_peak_bytes", 1, "raw telemetry"),
        ("safety_tests.offline_restart", "network_disabled", False, "raw safety"),
    ],
)
def test_benchmark_target_pipeline_and_safety_are_fail_closed(
    tmp_path: Path, section: str, field: str, value: object, message: str
) -> None:
    evidence = _make_candidate(tmp_path / "benchmark")
    report = deepcopy(evidence["reports"]["benchmark"])
    location = report
    for part in section.split("."):
        location = location[part]
    location[field] = value
    with pytest.raises(DeploymentValidationError, match=message):
        validate_jetson_benchmark_report(
            report,
            engine_report=evidence["reports"]["engine"],
            engine_report_sha256=sha256_file(evidence["paths"]["engine"]),
        )


def test_newest_frame_replacement_is_not_a_bad_frame(tmp_path: Path) -> None:
    evidence = _make_candidate(tmp_path / "newest-frame")
    report = evidence["reports"]["benchmark"]
    measurement = report["measurement"]
    assert (
        measurement["stale_replaced_frames"] / measurement["total_input_frames"]
        > 0.83
    )
    assert validate_jetson_benchmark_report(
        report,
        engine_report=evidence["reports"]["engine"],
        engine_report_sha256=sha256_file(evidence["paths"]["engine"]),
    )["passed"] is True

    bad_frames = deepcopy(report)
    bad_frames["measurement"]["stale_replaced_frames"] -= 271
    bad_frames["measurement"]["error_dropped_frames"] = 271
    with pytest.raises(DeploymentValidationError, match="frozen gates"):
        validate_jetson_benchmark_report(
            bad_frames,
            engine_report=evidence["reports"]["engine"],
            engine_report_sha256=sha256_file(evidence["paths"]["engine"]),
        )


def test_benchmark_gates_selected_frame_freshness(tmp_path: Path) -> None:
    evidence = _make_candidate(tmp_path / "frame-age")
    report = deepcopy(evidence["reports"]["benchmark"])
    report["latency_ms"]["selected_frame_age"].update(
        {"p95": 101.0, "p99": 102.0, "max": 103.0}
    )
    with pytest.raises(DeploymentValidationError, match="frozen gates"):
        validate_jetson_benchmark_report(
            report,
            engine_report=evidence["reports"]["engine"],
            engine_report_sha256=sha256_file(evidence["paths"]["engine"]),
        )


def test_raw_benchmark_or_engine_tampering_is_detected(tmp_path: Path) -> None:
    evidence = _make_candidate(tmp_path / "tamper")
    raw = Path(evidence["reports"]["benchmark"]["artifacts"]["telemetry_log"]["path"])
    raw.write_bytes(b"tampered")
    with pytest.raises(DeploymentValidationError, match="hash mismatch"):
        validate_jetson_benchmark_report(
            evidence["reports"]["benchmark"],
            engine_report=evidence["reports"]["engine"],
            engine_report_sha256=sha256_file(evidence["paths"]["engine"]),
        )


def test_target_only_trtexec_builder_emits_exact_argv(tmp_path: Path) -> None:
    evidence = _make_candidate(tmp_path / "builder")
    marker = tmp_path / "nv_tegra_release"
    marker.write_text("# R36 (release), REVISION: 4.3\n", encoding="utf-8")
    output = tmp_path / "new.engine"
    argv = build_jetson_trtexec_command(
        evidence["reports"]["onnx"],
        output,
        trtexec_binary=evidence["trtexec"],
        trtexec_sha256=sha256_file(evidence["trtexec"]),
        trtexec_version="10.3.0",
        jetson_marker_path=marker,
        graph_inspector=_inspector(640),
    )
    assert argv[0] == str(Path(evidence["trtexec"]).resolve())
    assert argv.count("--fp16") == 1
    assert not any("int8" in item.lower() for item in argv)
    assert "--minShapes=images:1x3x640x640" in argv

    with pytest.raises(DeploymentValidationError, match="hash mismatch"):
        build_jetson_trtexec_command(
            evidence["reports"]["onnx"], output,
            trtexec_binary=evidence["trtexec"], trtexec_sha256="0" * 64,
            trtexec_version="10.3.0", jetson_marker_path=marker,
            graph_inspector=_inspector(640),
        )


def _make_release_pool(tmp_path: Path) -> dict[str, object]:
    c640 = _make_candidate(tmp_path / "c640")
    c960 = _make_candidate(tmp_path / "c960", "yolov8n-960")
    q640 = _build_qualification(c640)
    q960 = _build_qualification(c960)
    q640_path = _write_json(tmp_path / "q640.json", q640.to_dict())
    q960_path = _write_json(tmp_path / "q960.json", q960.to_dict())
    completion = {
        "schema": MANDATORY_COMPLETION_SCHEMA,
        "report_id": "mandatory-completion",
        "training_plan_sha256": PLAN_HASH,
        "candidates": {
            "yolov8n-640": {
                "architecture": "yolov8n.pt", "input_shape_nchw": [1, 3, 640, 640],
                "training_report": _report_ref(c640["paths"]["training"], "training-640"),
                "completed": True,
            },
            "yolov8n-960": {
                "architecture": "yolov8n.pt", "input_shape_nchw": [1, 3, 960, 960],
                "training_report": _report_ref(c960["paths"]["training"], "training-960"),
                "completed": True,
            },
        },
        "passed": True,
    }
    completion_path = _write_json(tmp_path / "completion.json", completion)
    selected = _selected_candidate(q640, c640, (q640, q960))
    qualification_paths = {
        "yolov8n-640": q640_path,
        "yolov8n-960": q960_path,
    }
    return {
        "c640": c640,
        "c960": c960,
        "q640": q640,
        "q960": q960,
        "selected": selected,
        "completion_path": completion_path,
        "qualification_paths": qualification_paths,
    }


def test_manifest_binds_mandatory_pool_and_final_selection(tmp_path: Path) -> None:
    release = _make_release_pool(tmp_path)
    c640 = release["c640"]
    q640 = release["q640"]
    selected = release["selected"]
    completion_path = release["completion_path"]
    qualification_paths = release["qualification_paths"]
    test_report, test_report_path = _make_untouched_test_report(
        tmp_path, c640, selected
    )
    assert validate_untouched_test_accuracy_report(
        test_report,
        selected_candidate=selected,
        qualification=q640,
        workspace_root=c640["workspace"],
        repository_root=c640["repository"],
    )["passed"] is True
    selection = build_final_selection_report(
        selected,
        report_id="selection-001",
        qualification_report_paths=qualification_paths,
        mandatory_completion_report_path=completion_path,
        untouched_test_accuracy_report_path=test_report_path,
        workspace_root=c640["workspace"],
        repository_root=c640["repository"],
    )
    assert selection["selected_candidate"] == "yolov8n-640"
    assert selection["untouched_test_accuracy_report_sha256"] == sha256_file(
        test_report_path
    )
    assert Path(selection["untouched_test_use_ledger_path"]).is_file()
    assert selection["untouched_test_use_ledger_sha256"] == sha256_file(
        selection["untouched_test_use_ledger_path"]
    )
    with pytest.raises(DeploymentValidationError, match="already been evaluated or admitted"):
        build_final_selection_report(
            selected,
            report_id="selection-reused-test",
            qualification_report_paths=qualification_paths,
            mandatory_completion_report_path=completion_path,
            untouched_test_accuracy_report_path=test_report_path,
            workspace_root=c640["workspace"],
            repository_root=c640["repository"],
        )
    selection_path = _write_json(tmp_path / "selection.json", selection)
    manifest = build_deployment_manifest(
        selected_candidate=selected,
        qualification_report_paths=qualification_paths,
        mandatory_completion_report_path=completion_path,
        untouched_test_accuracy_report_path=test_report_path,
        selection_report_path=selection_path,
        workspace_root=c640["workspace"],
        repository_root=c640["repository"],
    )
    assert manifest["schema"] == DEPLOYMENT_MANIFEST_SCHEMA
    assert manifest["validation"]["passed"] is True
    assert validate_deployment_manifest(
        manifest,
        selected_candidate=selected,
        qualification_report_paths=qualification_paths,
        mandatory_completion_report_path=completion_path,
        untouched_test_accuracy_report_path=test_report_path,
        selection_report_path=selection_path,
        workspace_root=c640["workspace"],
        repository_root=c640["repository"],
    ) == manifest

    forged = deepcopy(manifest)
    forged["artifacts"]["tensorrt_engine_sha256"] = "0" * 64
    with pytest.raises(DeploymentValidationError, match="differs from validated"):
        validate_deployment_manifest(
            forged,
            selected_candidate=selected,
            qualification_report_paths=qualification_paths,
            mandatory_completion_report_path=completion_path,
            untouched_test_accuracy_report_path=test_report_path,
            selection_report_path=selection_path,
            workspace_root=c640["workspace"],
            repository_root=c640["repository"],
        )


def test_untouched_test_is_bound_only_after_selection(tmp_path: Path) -> None:
    release = _make_release_pool(tmp_path)
    unbound_report, unbound_path = _make_untouched_test_report(
        tmp_path,
        release["c640"],
        release["selected"],
    )
    unbound_report["selected_candidate_sha256"] = "0" * 64
    _write_json(unbound_path, unbound_report)
    with pytest.raises(DeploymentValidationError, match="already selected candidate"):
        build_final_selection_report(
            release["selected"],
            report_id="selection-unbound-test",
            qualification_report_paths=release["qualification_paths"],
            mandatory_completion_report_path=release["completion_path"],
            untouched_test_accuracy_report_path=unbound_path,
            workspace_root=release["c640"]["workspace"],
            repository_root=release["c640"]["repository"],
        )

    wrong_root = tmp_path / "wrong-test"
    wrong_root.mkdir()
    _wrong, wrong_path = _make_untouched_test_report(
        wrong_root,
        release["c960"],
        release["selected"],
    )
    with pytest.raises(DeploymentValidationError, match="escapes external workspace"):
        build_final_selection_report(
            release["selected"],
            report_id="selection-wrong-test",
            qualification_report_paths=release["qualification_paths"],
            mandatory_completion_report_path=release["completion_path"],
            untouched_test_accuracy_report_path=wrong_path,
            workspace_root=release["c640"]["workspace"],
            repository_root=release["c640"]["repository"],
        )


def test_selected_candidate_test_failure_blocks_release_without_promotion(
    tmp_path: Path,
) -> None:
    release = _make_release_pool(tmp_path)
    selected = release["selected"]
    _test_report, test_report_path = _make_untouched_test_report(
        tmp_path,
        release["c640"],
        selected,
        passing=False,
    )
    selection = build_final_selection_report(
        selected,
        report_id="selection-failing-test",
        qualification_report_paths=release["qualification_paths"],
        mandatory_completion_report_path=release["completion_path"],
        untouched_test_accuracy_report_path=test_report_path,
        workspace_root=release["c640"]["workspace"],
        repository_root=release["c640"]["repository"],
    )
    assert selection["selected_candidate"] == "yolov8n-640"
    assert selection["passed"] is False
    selection_path = _write_json(tmp_path / "failed-selection.json", selection)
    manifest = build_deployment_manifest(
        selected_candidate=selected,
        qualification_report_paths=release["qualification_paths"],
        mandatory_completion_report_path=release["completion_path"],
        untouched_test_accuracy_report_path=test_report_path,
        selection_report_path=selection_path,
        workspace_root=release["c640"]["workspace"],
        repository_root=release["c640"]["repository"],
    )
    assert manifest["candidate"] == "yolov8n-640"
    assert manifest["validation"]["passed"] is False


def test_untouched_test_rejects_validation_image_overlap(tmp_path: Path) -> None:
    release = _make_release_pool(tmp_path)
    _report, report_path = _make_untouched_test_report(
        tmp_path,
        release["c640"],
        release["selected"],
        overlap_real_validation=True,
    )
    with pytest.raises(DeploymentValidationError, match="image overlap exists between val and test"):
        build_final_selection_report(
            release["selected"],
            report_id="selection-overlapping-test",
            qualification_report_paths=release["qualification_paths"],
            mandatory_completion_report_path=release["completion_path"],
            untouched_test_accuracy_report_path=report_path,
            workspace_root=release["c640"]["workspace"],
            repository_root=release["c640"]["repository"],
        )
