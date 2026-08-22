from __future__ import annotations

import csv
import hashlib
import inspect
import json
import sys
from pathlib import Path

import numpy as np
import pytest
import rescue_training.cloud_environment as cloud_environment_module
import rescue_training.ultralytics_runner as ultralytics_runner_module
import rescue_training.review_gate as review_gate_module

from rescue_training.cloud_environment import (
    CLOUD_ENVIRONMENT_SCHEMA,
    COMMAND_CAPTURE_SCHEMA,
    CONTROL_PLANE_TRUST_LEVEL,
    DURABLE_RELOAD_TRUST_LEVEL,
    GPU_QUERY_ARGUMENTS,
    MOUNT_QUERY_ARGUMENTS,
    RUNPOD_CONTAINER_IMAGE_REFERENCE,
    RUNPOD_DATA_CENTER_ID,
    RUNPOD_MCP_CAPTURE_METHOD,
    RUNPOD_NETWORK_VOLUME_ID,
    RUNPOD_NETWORK_VOLUME_NAME,
    RUNPOD_NETWORK_VOLUME_SIZE_GB,
    RUNPOD_NETWORK_VOLUME_TYPE,
    RUNPOD_POD_NAME,
    RUNPOD_POD_RECORD_SCHEMA,
    RUNTIME_PROBE_CODE,
)
from rescue_training.contracts import TrainingPlan
from rescue_training.artifact_io import canonical_json_bytes
from rescue_training.review_gate import create_automated_dataset_qualification
from rescue_training.ultralytics_runner import (
    TrainingExecutionError,
    frozen_train_arguments,
    run_training,
    verify_runpod_runtime,
    verify_visdrone_audit,
)
from tools.audit_yolo_dataset import (
    SplitSpec,
    audit_dataset,
    montage_membership,
    montage_membership_sha256,
    render_montage,
    select_montage_samples,
)
def _plan() -> TrainingPlan:
    config = Path(__file__).resolve().parents[1] / "config" / "sar_rgb_person_training.json"
    return TrainingPlan.load(config)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
                "sha256": _sha(dataset_yaml),
            },
            "class_map": {"0": "person_candidate"},
            "source_lineage": {"train": {}, "val": {}},
            "derived_sample_set_sha256": {"train": "d" * 64, "val": "e" * 64},
        }

    monkeypatch.setattr(
        review_gate_module, "validate_visdrone_conversion_report", validate
    )


def _cloud_environment(root: Path) -> dict:
    evidence_root = root / "cloud-evidence"
    evidence_root.mkdir()
    digest = RUNPOD_CONTAINER_IMAGE_REFERENCE.rsplit(":", 1)[1]
    observed = "2026-08-22T10:00:30+00:00"
    executables = evidence_root / "executables"
    executables.mkdir()
    executable_paths = {
        name: (executables / name).resolve()
        for name in ("nvidia-smi", "python", "findmnt")
    }
    for name, path in executable_paths.items():
        path.write_bytes(f"fixture executable {name}".encode("utf-8"))
    python = str(executable_paths["python"])

    def write_json(name: str, value: dict) -> Path:
        path = evidence_root / name
        path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
        return path

    def capture(name: str, argv: list[str], stdout: str) -> Path:
        return write_json(
            name,
            {
                "schema": COMMAND_CAPTURE_SCHEMA,
                "capture_mode": "production_subprocess",
                "test_only": False,
                "argv": argv,
                "executable_sha256": _sha(Path(argv[0])),
                "exit_code": 0,
                "stdout": stdout,
                "stderr": "",
                "started_at_utc": observed,
                "completed_at_utc": observed,
            },
        )

    pod_projection = {
        "id": "test-pod",
        "name": RUNPOD_POD_NAME,
        "status": "RUNNING",
        "cloud": "SECURE",
        "cost": 0.99,
        "createdAt": "2026-08-22T09:55:00+00:00",
        "startedAt": "2026-08-22T10:00:00+00:00",
        "cudaVersion": "13.0",
        "dataCenterId": RUNPOD_DATA_CENTER_ID,
        "disk": 30,
        "gpu": {"id": "NVIDIA GeForce RTX 5090", "count": 1},
        "image": RUNPOD_CONTAINER_IMAGE_REFERENCE,
        "locked": False,
        "mounts": {
            "network": [
                {"path": "/workspace", "volumeId": RUNPOD_NETWORK_VOLUME_ID}
            ]
        },
        "runtime": {"gpus": [{"memoryUtil": 0, "util": 0}]},
    }
    volume_projection = {
        "dataCenter": RUNPOD_DATA_CENTER_ID,
        "id": RUNPOD_NETWORK_VOLUME_ID,
        "name": RUNPOD_NETWORK_VOLUME_NAME,
        "size": RUNPOD_NETWORK_VOLUME_SIZE_GB,
        "type": RUNPOD_NETWORK_VOLUME_TYPE,
    }
    control_plane_projection = {
        "pod": pod_projection,
        "networkVolume": volume_projection,
    }
    provider = write_json(
        "runpod-pod-record.json",
        {
            "schema": RUNPOD_POD_RECORD_SCHEMA,
            "provider": "runpod",
            "capture_method": RUNPOD_MCP_CAPTURE_METHOD,
            "trust_level": CONTROL_PLANE_TRUST_LEVEL,
            "provider_signature_present": False,
            "request": {
                "getPod": {
                    "podId": "test-pod",
                    "includeMachine": True,
                    "includeNetworkVolume": True,
                },
                "getNetworkVolume": {
                    "networkVolumeId": RUNPOD_NETWORK_VOLUME_ID,
                },
            },
            "response_projection": control_plane_projection,
            "response_projection_sha256": hashlib.sha256(
                canonical_json_bytes(control_plane_projection)
            ).hexdigest(),
            "captured_at_utc": observed,
        },
    )
    gpu = capture(
        "gpu-probe.json",
        [str(executable_paths["nvidia-smi"]), *GPU_QUERY_ARGUMENTS],
        "NVIDIA GeForce RTX 5090, 32768, GPU-test-5090\n",
    )
    runtime = {
        "python": "3.12.13",
        "torch": "2.13.0+cu130",
        "torchvision": "0.28.0+cu130",
        "ultralytics": "8.4.56",
        "cuda_runtime": "13.0",
    }
    runtime_probe = capture(
        "runtime-probe.json",
        [python, "-c", RUNTIME_PROBE_CODE],
        json.dumps(
            {
                **runtime,
                "python_executable": python,
                "cuda_available": True,
            },
            sort_keys=True,
        ),
    )
    mount = capture(
        "mount-probe.json",
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
        "pip-freeze.json",
        [python, "-m", "pip", "freeze", "--all"],
        "torch @ "
        + cloud_environment_module._FROZEN_PACKAGE_DIRECT_REFERENCES["torch"]
        + "\ntorchvision @ "
        + cloud_environment_module._FROZEN_PACKAGE_DIRECT_REFERENCES[
            "torchvision"
        ]
        + "\nultralytics==8.4.56\npip==26.0\n",
    )
    check = capture(
        "pip-check.json",
        [python, "-m", "pip", "check"],
        "No broken requirements found.\n",
    )
    evidence_paths = {
        "runpod_pod_record": provider,
        "gpu_probe": gpu,
        "runtime_probe": runtime_probe,
        "mount_probe": mount,
        "pip_freeze": freeze,
        "pip_check": check,
    }
    evidence = {
        name: {"path": str(path.resolve()), "sha256": _sha(path)}
        for name, path in evidence_paths.items()
    }
    return {
        "schema": CLOUD_ENVIRONMENT_SCHEMA,
        "provider": "runpod",
        "control_plane_capture_trust": CONTROL_PLANE_TRUST_LEVEL,
        "durable_reload_trust": DURABLE_RELOAD_TRUST_LEVEL,
        "pod_id": "test-pod",
        "network_volume_id": RUNPOD_NETWORK_VOLUME_ID,
        "workspace_root": "/workspace/samik-rescue-person-model-20260822",
        "gpu": {
            "name": "NVIDIA GeForce RTX 5090",
            "memory_bytes": 32 * 1024**3,
            "uuid": "GPU-test-5090",
        },
        "runtime": runtime,
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
            "pip_freeze_sha256": evidence["pip_freeze"]["sha256"],
            "pip_check_sha256": evidence["pip_check"]["sha256"],
            "pip_check_passed": True,
        },
        "evidence": evidence,
        "repository": {"path": "/workspace/VeriSwarm_SIH"},
        "pod_started_at_utc": "2026-08-22T10:00:00+00:00",
        "captured_at_utc": "2026-08-22T10:01:00+00:00",
        "passed": True,
    }


def _runtime_evidence() -> dict:
    return {
        "python": "3.12.13",
        "torch": "2.13.0+cu130",
        "torchvision": "0.28.0+cu130",
        "ultralytics": "8.4.56",
        "cuda_runtime": "13.0",
        "gpu_name": "NVIDIA GeForce RTX 5090",
        "gpu_total_memory_bytes": 32 * 1024**3,
    }


def _audit(root: Path, *, train: int = 6471, val: int = 548) -> Path:
    audit_dir = root / "audit"
    audit_dir.mkdir(parents=True)
    montage = audit_dir / "label_montage.jpg"
    montage.write_bytes(b"deterministic-montage")
    montage_samples = [
        {
            "split": "train" if index < 90 else "val",
            "stem": f"sample-{index:03d}",
            "image_sha256": f"{index:064x}",
            "label_sha256": f"{index + 100:064x}",
        }
        for index in range(100)
    ]
    report = {
        "schema": "veriswarm.dataset_audit.v1",
        "generated_at_utc": "2026-08-22T00:00:00+00:00",
        "passed": True,
        "class_map": {"0": "person_candidate"},
        "splits": {
            "train": {
                "image_root": str((root / "images" / "train").resolve()),
                "label_root": str((root / "labels" / "train").resolve()),
                "images": train,
                "labels": train,
                "paired": train,
                "valid_samples": train,
                "boxes": 90,
                "class_box_counts": {"0": 90},
                "empty_label_samples": 0,
                "missing_labels": 0,
                "orphan_labels": 0,
                "sample_set_sha256": "1" * 64,
            },
            "val": {
                "image_root": str((root / "images" / "val").resolve()),
                "label_root": str((root / "labels" / "val").resolve()),
                "images": val,
                "labels": val,
                "paired": val,
                "valid_samples": val,
                "boxes": 10,
                "class_box_counts": {"0": 10},
                "empty_label_samples": 0,
                "missing_labels": 0,
                "orphan_labels": 0,
                "sample_set_sha256": "2" * 64,
            },
        },
        "totals": {
            "valid_samples": train + val,
            "boxes": 100,
            "class_box_counts": {"0": 100},
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
            "sample_set_sha256": montage_membership_sha256(montage_samples),
            "path": montage.name,
            "sha256": _sha(montage),
        },
    }
    target = audit_dir / "audit_report.json"
    target.write_text(json.dumps(report), encoding="utf-8")
    return target


def _claimed_authoritative_integrity(audit: Path) -> dict:
    report = json.loads(audit.read_text(encoding="utf-8"))
    return {
        "class_map": report["class_map"],
        "splits": report["splits"],
        "totals": report["totals"],
        "duplicates": report["duplicates"],
        "cross_split_leakage": report["cross_split_leakage"],
        "errors": report["errors"],
        "warnings": report["warnings"],
        "montage": {
            field: report["montage"][field]
            for field in (
                "requested_samples",
                "rendered_samples",
                "samples",
                "sample_set_sha256",
                "sha256",
            )
        },
    }


def _install_authoritative_audit_test_seam(
    monkeypatch: pytest.MonkeyPatch,
    audit: Path,
) -> list[tuple[SplitSpec, ...]]:
    calls: list[tuple[SplitSpec, ...]] = []
    evidence = _claimed_authoritative_integrity(audit)

    def recompute(
        specs: tuple[SplitSpec, ...], *, required_montage_samples: int
    ) -> dict:
        assert required_montage_samples == 100
        calls.append(specs)
        return json.loads(json.dumps(evidence))

    monkeypatch.setattr(
        ultralytics_runner_module,
        "recompute_visdrone_dataset_integrity",
        recompute,
    )
    return calls


class _AuditCV2:
    IMREAD_COLOR = 1
    FONT_HERSHEY_SIMPLEX = 0
    LINE_AA = 0

    @staticmethod
    def imread(path: str, _mode: int):
        payload = Path(path).read_bytes()
        if payload.startswith(b"not-an-image"):
            return None
        return np.full((32, 48, 3), payload[0], dtype=np.uint8)

    @staticmethod
    def resize(image, size):
        width, height = size
        return np.full((height, width, 3), int(image[0, 0, 0]), dtype=np.uint8)

    @staticmethod
    def rectangle(*_args, **_kwargs):
        return None

    @staticmethod
    def putText(*_args, **_kwargs):
        return None

    @staticmethod
    def imwrite(path: str, image) -> bool:
        payload = (
            str(tuple(image.shape)).encode("ascii")
            + b"\x00"
            + hashlib.sha256(image.tobytes()).digest()
        )
        Path(path).write_bytes(payload)
        return True


def _real_audit(root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setitem(sys.modules, "cv2", _AuditCV2)
    specs: list[SplitSpec] = []
    for split, count, offset in (("train", 2, 10), ("val", 1, 30)):
        images = root / "images" / split
        labels = root / "labels" / split
        images.mkdir(parents=True)
        labels.mkdir(parents=True)
        spec = SplitSpec(split, images.resolve(), labels.resolve())
        specs.append(spec)
        for index in range(count):
            (images / f"sample-{index}.jpg").write_bytes(
                bytes([offset + index]) + b"-valid-image"
            )
            (labels / f"sample-{index}.txt").write_text(
                "0 0.5 0.5 0.5 0.5\n", encoding="utf-8"
            )
    report, samples = audit_dataset(
        tuple(specs), {0: "person_candidate"}, _AuditCV2
    )
    selected = select_montage_samples(samples, 3)
    members = montage_membership(selected)
    audit_dir = root / "audit"
    audit_dir.mkdir()
    montage = audit_dir / "label_montage.jpg"
    render_montage(selected, {0: "person_candidate"}, montage, _AuditCV2)
    report["montage"] = {
        "requested_samples": 3,
        "rendered_samples": 3,
        "samples": members,
        "sample_set_sha256": montage_membership_sha256(members),
        "path": montage.name,
        "sha256": _sha(montage),
    }
    target = audit_dir / "audit_report.json"
    target.write_text(json.dumps(report, sort_keys=True), encoding="utf-8")
    return target


def _qualification(audit: Path, dataset_yaml: Path) -> Path:
    report = json.loads(audit.read_text(encoding="utf-8"))
    montage = audit.parent / report["montage"]["path"]
    target = audit.parent / "automated_dataset_qualification.json"
    create_automated_dataset_qualification(
        target,
        audit_report_path=audit,
        audit_report_sha256=_sha(audit),
        dataset_yaml_path=dataset_yaml,
        dataset_yaml_sha256=_sha(dataset_yaml),
        montage_path=montage,
        montage_sha256=_sha(montage),
        splits={
            "train": {
                "images": report["splits"]["train"]["images"],
                "sample_set_sha256": report["splits"]["train"][
                    "sample_set_sha256"
                ],
            },
            "val": {
                "images": report["splits"]["val"]["images"],
                "sample_set_sha256": report["splits"]["val"][
                    "sample_set_sha256"
                ],
            },
        },
        workspace_root=dataset_yaml.parent,
        repository_root=dataset_yaml.parent.parent / "repository",
        qualified_at_utc="2026-08-22T10:30:00+00:00",
    )
    return target


def _legacy_human_review(audit: Path) -> Path:
    report = json.loads(audit.read_text(encoding="utf-8"))
    montage = audit.parent / report["montage"]["path"]
    target = audit.parent / "legacy_label_review.json"
    target.write_text(
        json.dumps(
            {
                "schema": "veriswarm.rescue.label_review.v1",
                "audit_report_path": str(audit.resolve()),
                "audit_report_sha256": _sha(audit),
                "montage_path": str(montage.resolve()),
                "montage_sha256": _sha(montage),
                "reviewed_samples": 100,
                "reviewer": "Legacy human fixture",
                "reviewer_type": "human",
                "decision": "accepted",
                "reviewed_at_utc": "2026-08-22T10:30:00+00:00",
                "notes": "Must not authorize v2 training.",
            }
        ),
        encoding="utf-8",
    )
    return target


class _Cuda:
    @staticmethod
    def is_available():
        return True

    @staticmethod
    def device_count():
        return 1

    @staticmethod
    def get_device_name(index):
        assert index == 0
        return "NVIDIA GeForce RTX 5090"

    @staticmethod
    def get_device_properties(index):
        assert index == 0
        return type("Properties", (), {"total_memory": 32 * 1024**3})()

    @staticmethod
    def reset_peak_memory_stats(index):
        assert index == 0

    @staticmethod
    def max_memory_allocated(index):
        assert index == 0
        return 10 * 1024**3

    @staticmethod
    def max_memory_reserved(index):
        assert index == 0
        return 12 * 1024**3


class _Torch:
    cuda = _Cuda()
    version = type("Version", (), {"cuda": "13.0"})()
    backends = type(
        "Backends",
        (),
        {"cudnn": type("Cudnn", (), {"version": staticmethod(lambda: 99999)})()},
    )()


def test_runtime_requires_exact_pin_and_one_5090():
    evidence = verify_runpod_runtime(
        _plan(),
        python_version="3.12.13",
        package_versions={
            "torch": "2.13.0+cu130",
            "torchvision": "0.28.0+cu130",
            "ultralytics": "8.4.56",
        },
        torch_module=_Torch,
    )
    assert evidence["gpu_name"] == "NVIDIA GeForce RTX 5090"
    assert evidence["visible_gpu_count"] == 1
    assert evidence["cuda_runtime"] == "13.0"


def test_training_api_has_no_deprecated_label_review_alias():
    assert "label_review" not in inspect.signature(run_training).parameters


def test_audit_gate_requires_exact_official_counts_and_hashed_montage(
    tmp_path, monkeypatch
):
    report = _real_audit(tmp_path, monkeypatch)
    evidence = verify_visdrone_audit(
        report,
        expected_train_images=2,
        expected_val_images=1,
        required_montage_samples=3,
    )
    assert evidence["train_images"] == 2
    assert evidence["val_images"] == 1
    assert evidence["authoritative_integrity"]["totals"]["boxes"] == 3

    value = json.loads(report.read_text(encoding="utf-8"))
    value["splits"]["train"]["images"] = 1
    report.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(TrainingExecutionError, match="train images mismatch"):
        verify_visdrone_audit(
            report,
            expected_train_images=2,
            expected_val_images=1,
            required_montage_samples=3,
        )


def test_audit_gate_rejects_fabricated_class_counts_and_stale_bytes(
    tmp_path, monkeypatch
):
    report = _real_audit(tmp_path, monkeypatch)
    value = json.loads(report.read_text(encoding="utf-8"))
    value["totals"]["boxes"] = 999
    value["totals"]["class_box_counts"] = {"0": 999}
    report.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(TrainingExecutionError, match="authoritative recomputation"):
        verify_visdrone_audit(
            report,
            expected_train_images=2,
            expected_val_images=1,
            required_montage_samples=3,
        )

    report = _real_audit(tmp_path / "stale", monkeypatch)
    stale_image = tmp_path / "stale" / "images" / "train" / "sample-0.jpg"
    stale_image.write_bytes(b"not-an-image")
    with pytest.raises(TrainingExecutionError, match="authoritative dataset recomputation"):
        verify_visdrone_audit(
            report,
            expected_train_images=2,
            expected_val_images=1,
            required_montage_samples=3,
        )


def test_audit_gate_rejects_fabricated_montage_membership(tmp_path, monkeypatch):
    report = _real_audit(tmp_path, monkeypatch)
    value = json.loads(report.read_text(encoding="utf-8"))
    value["montage"]["samples"][0]["stem"] = "fabricated"
    value["montage"]["sample_set_sha256"] = montage_membership_sha256(
        value["montage"]["samples"]
    )
    report.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(TrainingExecutionError, match="montage.samples"):
        verify_visdrone_audit(
            report,
            expected_train_images=2,
            expected_val_images=1,
            required_montage_samples=3,
        )


def test_frozen_arguments_keep_smoke_and_full_batch_rules(tmp_path):
    plan = _plan()
    smoke = frozen_train_arguments(
        plan.candidate("yolov8n-640-smoke"),
        dataset_yaml=tmp_path / "data.yaml",
        run_directory=tmp_path / "run",
        requested_batch=None,
    )
    assert smoke["batch"] == 0.70
    assert smoke["imgsz"] == 640
    assert smoke["epochs"] == 5
    assert smoke["cache"] is False
    assert smoke["deterministic"] is True
    assert smoke["exist_ok"] is False
    assert smoke["plots"] is False

    with pytest.raises(TrainingExecutionError, match="positive integer batch"):
        frozen_train_arguments(
            plan.candidate("yolov8n-960"),
            dataset_yaml=tmp_path / "data.yaml",
            run_directory=tmp_path / "run",
            requested_batch=None,
        )


def test_training_uses_only_local_hashed_weights_and_creates_evidence(
    tmp_path,
    monkeypatch,
):
    workspace = tmp_path / "workspace"
    repository = tmp_path / "repository"
    workspace.mkdir()
    repository.mkdir()
    dataset_yaml = workspace / "dataset.yaml"
    dataset_yaml.write_text(
        f'path: "{workspace.resolve().as_posix()}"\n'
        "train: images/train\n"
        "val: images/val\n"
        "names:\n"
        "  0: person_candidate\n",
        encoding="utf-8",
    )
    checkpoint = workspace / "yolov8n.pt"
    checkpoint.write_bytes(b"local-base-checkpoint")
    audit = _audit(workspace)
    audit_recomputations = _install_authoritative_audit_test_seam(
        monkeypatch, audit
    )
    qualification = _qualification(audit, dataset_yaml)
    lineage_calls = []
    conversion_validator = review_gate_module.validate_visdrone_conversion_report

    def tracked_conversion_validator(*args, **kwargs):
        lineage_calls.append((args, kwargs))
        return conversion_validator(*args, **kwargs)

    monkeypatch.setattr(
        review_gate_module,
        "validate_visdrone_conversion_report",
        tracked_conversion_validator,
    )
    run_dir = workspace / "runs" / "smoke"
    calls = []

    class FakeModel:
        trainer = None

        def train(self, **kwargs):
            calls.append(kwargs)
            run_dir.mkdir(parents=True)
            (run_dir / "weights").mkdir()
            effective = dict(kwargs)
            effective["model"] = str(Path(factory_inputs[-1]))
            (run_dir / "args.yaml").write_text(
                json.dumps(effective), encoding="utf-8"
            )
            fields = [
                "epoch",
                "train/box_loss",
                "train/cls_loss",
                "train/dfl_loss",
                "metrics/precision(B)",
                "metrics/recall(B)",
                "metrics/mAP50(B)",
                "metrics/mAP50-95(B)",
            ]
            with (run_dir / "results.csv").open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=fields)
                writer.writeheader()
                for epoch in range(5):
                    writer.writerow(
                        {
                            "epoch": epoch + 1,
                            "train/box_loss": 1.0,
                            "train/cls_loss": 0.5,
                            "train/dfl_loss": 0.8,
                            "metrics/precision(B)": 0.7,
                            "metrics/recall(B)": 0.8,
                            "metrics/mAP50(B)": 0.75,
                            "metrics/mAP50-95(B)": 0.4,
                        }
                    )
            (run_dir / "weights" / "best.pt").write_bytes(b"best")
            (run_dir / "weights" / "last.pt").write_bytes(b"last")
            self.trainer = type(
                "Trainer", (), {"save_dir": run_dir, "batch_size": 8}
            )()

    factory_inputs = []

    def factory(value):
        factory_inputs.append(value)
        return FakeModel()

    ticks = iter((10.0, 20.0))
    report_path = run_training(
        _plan(),
        candidate_name="yolov8n-640-smoke",
        dataset_yaml=dataset_yaml,
        dataset_audit=audit,
        dataset_qualification=qualification,
        cloud_environment=None,
        base_checkpoint=checkpoint,
        base_checkpoint_sha256=_sha(checkpoint),
        run_directory=run_dir,
        workspace_root=workspace,
        repository_root=repository,
        yolo_factory=factory,
        runtime_evidence=_runtime_evidence(),
        cloud_environment_evidence=_cloud_environment(tmp_path),
        source_identity={"commit": "a" * 40, "branch": "test", "clean": True},
        trusted_base_verifier=lambda *args, **kwargs: {
            "sha256": _sha(checkpoint),
            "manifest_path": "test",
        },
        effective_arguments_loader=lambda path: json.loads(
            path.read_text(encoding="utf-8")
        ),
        torch_module=_Torch,
        monotonic=lambda: next(ticks),
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert len(factory_inputs) == 1
    assert Path(factory_inputs[0]).name == "yolov8n.pt"
    assert Path(factory_inputs[0]).parent.name == ".smoke.verified-inputs"
    assert _sha(Path(factory_inputs[0])) == _sha(checkpoint)
    assert len(calls) == 1
    assert calls[0]["exist_ok"] is False
    assert calls[0]["amp"] is False
    assert calls[0]["resume"] is False
    assert report["status"] == "completed"
    assert report["accepted_by_human"] is False
    assert report["dataset"]["automated_qualification"]["accepted_by_human"] is False
    assert report["dataset"]["automated_qualification"]["source_conversion"][
        "conversion_report"
    ]["schema"] == "veriswarm.rescue.visdrone_conversion.v2"
    assert len(lineage_calls) == 2
    assert "visual_review" not in report["dataset"]
    assert len(audit_recomputations) == 2
    assert report["dataset"]["audit"]["integrity_unchanged"] is True
    assert report["dataset"]["audit"][
        "pre_training_authoritative_integrity_sha256"
    ] == report["dataset"]["audit"][
        "post_training_authoritative_integrity_sha256"
    ]
    assert report["duration_seconds"] == 10.0
    assert report["smoke_gate"]["passed"] is True
    assert report["resolved_batch_size"] == 8
    assert report["artifacts"]["best_pt"]["sha256"] == _sha(run_dir / "weights" / "best.pt")


def test_hash_mismatch_blocks_model_construction(tmp_path):
    workspace = tmp_path / "workspace"
    repository = tmp_path / "repository"
    workspace.mkdir()
    repository.mkdir()
    data = workspace / "dataset.yaml"
    data.write_text(
        f'path: "{workspace.resolve().as_posix()}"\n'
        "train: images/train\n"
        "val: images/val\n"
        "names:\n"
        "  0: person_candidate\n",
        encoding="utf-8",
    )
    weights = workspace / "yolov8n.pt"
    weights.write_bytes(b"weights")
    audit = _audit(workspace)
    qualification = _qualification(audit, data)
    constructed = []

    with pytest.raises(TrainingExecutionError, match="SHA-256 mismatch"):
        run_training(
            _plan(),
            candidate_name="yolov8n-640-smoke",
            dataset_yaml=data,
            dataset_audit=audit,
            dataset_qualification=qualification,
            cloud_environment=None,
            base_checkpoint=weights,
            base_checkpoint_sha256="0" * 64,
            run_directory=workspace / "run",
            workspace_root=workspace,
            repository_root=repository,
            yolo_factory=lambda value: constructed.append(value),
            runtime_evidence=_runtime_evidence(),
            cloud_environment_evidence=_cloud_environment(tmp_path),
            source_identity={"commit": "a" * 40, "branch": "test", "clean": True},
            trusted_base_verifier=lambda *args, **kwargs: {
                "sha256": _sha(weights),
                "manifest_path": "test",
            },
        )
    assert constructed == []


def test_legacy_human_review_cannot_authorize_training(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    repository = tmp_path / "repository"
    workspace.mkdir()
    repository.mkdir()
    data = workspace / "dataset.yaml"
    data.write_text(
        f'path: "{workspace.resolve().as_posix()}"\n'
        "train: images/train\n"
        "val: images/val\n"
        "names:\n"
        "  0: person_candidate\n",
        encoding="utf-8",
    )
    weights = workspace / "yolov8n.pt"
    weights.write_bytes(b"weights")
    audit = _audit(workspace)
    _install_authoritative_audit_test_seam(monkeypatch, audit)
    legacy_review = _legacy_human_review(audit)
    constructed = []

    with pytest.raises(TrainingExecutionError, match="frozen v3 schema"):
        run_training(
            _plan(),
            candidate_name="yolov8n-640-smoke",
            dataset_yaml=data,
            dataset_audit=audit,
            dataset_qualification=legacy_review,
            cloud_environment=None,
            base_checkpoint=weights,
            base_checkpoint_sha256=_sha(weights),
            run_directory=workspace / "run",
            workspace_root=workspace,
            repository_root=repository,
            yolo_factory=lambda value: constructed.append(value),
            runtime_evidence=_runtime_evidence(),
            cloud_environment_evidence=_cloud_environment(tmp_path),
            source_identity={"commit": "a" * 40, "branch": "test", "clean": True},
            trusted_base_verifier=lambda *args, **kwargs: {
                "sha256": _sha(weights),
                "manifest_path": "test",
            },
        )
    assert constructed == []
