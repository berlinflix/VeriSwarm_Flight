from __future__ import annotations

import hashlib
import json
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest

import rescue_training.cloud_environment as cloud_environment_module
import rescue_training.state_classifier as state_classifier_module
from rescue_training.cloud_environment import (
    CLOUD_ENVIRONMENT_SCHEMA,
    COMMAND_CAPTURE_SCHEMA,
    CONTROL_PLANE_TRUST_LEVEL,
    DURABLE_RELOAD_TRUST_LEVEL,
    GPU_QUERY_ARGUMENTS,
    MOUNT_QUERY_ARGUMENTS,
    RTX_5090_MEMORY_BYTES,
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
from rescue_training.artifact_io import canonical_json_bytes
from rescue_training.contracts import FROZEN_RUNTIME, RUNPOD_WORKSPACE_ROOT
from rescue_training.state_classifier import (
    STATE_CLASSIFIER_BASE_WEIGHTS_SCHEMA,
    STATE_EVALUATION_SCHEMA,
    STATE_INFERENCE_SCHEMA,
    STATE_SELECTION_SCHEMA,
    STATE_TEST_REPORT_SCHEMA,
    STATE_TRAINING_PLAN_SCHEMA,
    StateClassifierError,
    evaluate_state_classifier_test,
    evaluate_state_classifier_validation,
    run_state_classifier_inference,
    run_state_classifier_training,
    select_state_classifier,
    validate_state_training_plan,
)
from rescue_training.state_dataset import (
    STATE_DATASET_SCHEMA,
    STATE_MODEL_ID,
    STATE_RECORD_SCHEMA,
)


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")


_OFFICIAL_CLASSIFIER_BYTES = 5_563_076
_OFFICIAL_CLASSIFIER_SHA256 = (
    "11fa19f2aea79bc960d680a13f82f22105982b325eb9e17a4a5e1a9f8245980a"
)
_FAKE_TRUSTED_CHECKPOINT_PREFIX = b"unit-test-substitute-for-official-yolov8n-cls"


@pytest.fixture(autouse=True)
def _substitute_official_checkpoint_bytes(
    monkeypatch: pytest.MonkeyPatch,
):
    """Avoid committing/downloading a pickle checkpoint in unit tests.

    Production still executes the real SHA-256 implementation.  This narrow
    test substitution recognizes only the exact-size sparse fixture with a
    private marker; changed bytes fall back to the real hasher and therefore
    exercise the rejection path.
    """

    real_sha256_file = state_classifier_module.sha256_file

    def fixture_sha256_file(path: str | Path) -> str:
        candidate = Path(path)
        try:
            is_fixture = (
                candidate.name == "yolov8n-cls.pt"
                and candidate.is_file()
                and not candidate.is_symlink()
                and candidate.stat().st_size == _OFFICIAL_CLASSIFIER_BYTES
            )
            if is_fixture:
                with candidate.open("rb") as stream:
                    if stream.read(len(_FAKE_TRUSTED_CHECKPOINT_PREFIX)) == (
                        _FAKE_TRUSTED_CHECKPOINT_PREFIX
                    ):
                        return _OFFICIAL_CLASSIFIER_SHA256
        except OSError:
            pass
        return real_sha256_file(candidate)

    monkeypatch.setattr(state_classifier_module, "sha256_file", fixture_sha256_file)


def _fixture(tmp_path: Path) -> dict:
    repository = tmp_path / "repository"
    workspace = tmp_path / "workspace"
    repository.mkdir()
    workspace.mkdir()
    classifier_manifest = (
        repository
        / "codebase"
        / "config"
        / "ultralytics_yolov8_classifier_base_weights.json"
    )
    _write_json(
        classifier_manifest,
        {
            "schema": STATE_CLASSIFIER_BASE_WEIGHTS_SCHEMA,
            "publisher": "Ultralytics",
            "repository": "https://github.com/ultralytics/assets",
            "release_tag": "v8.4.0",
            "entries": {
                "yolov8n-cls.pt": {
                    "url": (
                        "https://github.com/ultralytics/assets/releases/download/"
                        "v8.4.0/yolov8n-cls.pt"
                    ),
                    "bytes": _OFFICIAL_CLASSIFIER_BYTES,
                    "sha256": _OFFICIAL_CLASSIFIER_SHA256,
                }
            },
        },
    )
    archives = workspace / "archives"
    crops = workspace / "crops"
    media = workspace / "media"
    archives.mkdir()
    media.mkdir()
    labeler_artifact = workspace / "automated-state-labeler.bin"
    labeler_artifact.write_bytes(b"deterministic-visual-labeler-v1")
    label_policy = workspace / "automated-state-label-policy.json"
    _write_json(
        label_policy,
        {
            "classes": ["disaster_stressed", "safe_walking"],
            "dataset_origin_allowed": False,
        },
    )
    weak_supervision = {
        "schema": "veriswarm.rescue.person_state_weak_supervision.v1",
        "evidence_status": "weak_supervision_unverified",
        "label_source": "automated_weak_supervision",
        "labeler_type": "automated",
        "labeler_id": "visual-cue-labeler",
        "labeler_version": "1.0.0",
        "labeler_artifact_path": str(labeler_artifact.resolve()),
        "labeler_artifact_sha256": _hash(labeler_artifact),
        "policy_path": str(label_policy.resolve()),
        "policy_sha256": _hash(label_policy),
        "forbidden_inputs": [
            "source_dataset_id",
            "source_archive_identity",
            "dataset_origin",
        ],
    }
    sources = []
    for dataset_id in ("c2a-v2", "adilshamim8-people-detection-v1"):
        archive = archives / f"{dataset_id}.zip"
        archive.write_bytes(f"archive:{dataset_id}".encode())
        sources.append(
            {
                "dataset_id": dataset_id,
                "archive_path": str(archive.resolve()),
                "archive_sha256": _hash(archive),
                "dataset_use_authorization_status": "authorization_recorded",
                "terms_recorded_by": "automated-dataset-intake-v1",
                "role": "candidate_media_only",
            }
        )

    records: list[dict] = []
    for split_index, split in enumerate(("train", "val", "test")):
        for source_index, dataset_id in enumerate(
            ("c2a-v2", "adilshamim8-people-detection-v1")
        ):
            for label_index, label in enumerate(("disaster_stressed", "safe_walking")):
                sample_id = f"{split}-{source_index}-{label}"
                source = media / f"{sample_id}.jpg"
                source.write_bytes(f"source:{sample_id}".encode())
                crop_dir = crops / split / label
                crop_dir.mkdir(parents=True, exist_ok=True)
                crop = crop_dir / f"{sample_id}.jpg"
                crop.write_bytes(f"crop:{sample_id}".encode())
                records.append(
                    {
                        "schema": STATE_RECORD_SCHEMA,
                        "sample_id": sample_id,
                        "split": split,
                        "source_dataset_id": dataset_id,
                        "source_group_id": f"scene-{split_index}-{source_index}-{label_index}",
                        "source_media_path": str(source.resolve()),
                        "source_media_sha256": _hash(source),
                        "frame_index": None,
                        "person_bbox_xyxy_abs": [1, 2, 20, 30],
                        "crop_path": str(crop.resolve()),
                        "crop_sha256": _hash(crop),
                        "state_label": label,
                        "state_reason": (
                            "lying"
                            if label == "disaster_stressed"
                            else "normal_walking_visual_cue"
                        ),
                        "source_pose_hint": (
                            "lying" if label == "disaster_stressed" else None
                        ),
                        "label_source": "automated_weak_supervision",
                        "labeler_id": weak_supervision["labeler_id"],
                        "labeler_version": weak_supervision["labeler_version"],
                        "labeler_artifact_sha256": weak_supervision[
                            "labeler_artifact_sha256"
                        ],
                        "label_policy_sha256": weak_supervision["policy_sha256"],
                        "label_confidence": 0.75,
                        "labeled_at_utc": "2026-08-22T12:00:00+00:00",
                        "context_notes": "automated weak-supervision fixture",
                    }
                )
    records_path = workspace / "records.jsonl"
    records_path.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )
    manifest_path = workspace / "state-dataset.json"
    _write_json(
        manifest_path,
        {
            "schema": STATE_DATASET_SCHEMA,
            "model_id": STATE_MODEL_ID,
            "classes": {"0": "disaster_stressed", "1": "safe_walking"},
            "dataset_origin_is_label": False,
            "source_datasets": sources,
            "records_path": str(records_path.resolve()),
            "records_sha256": _hash(records_path),
            "crop_root": str(crops.resolve()),
            "crop_policy": {
                "context_margin_ratio": 0.35,
                "output_size_hw": [224, 224],
                "padding": "constant_black",
                "interpolation": "bilinear",
            },
            "split_policy": {
                "group_key": "source_group_id",
                "splits": ["train", "val", "test"],
                "test_used_for_selection": False,
            },
            "weak_supervision": weak_supervision,
            "counts": {
                split: {"disaster_stressed": 2, "safe_walking": 2}
                for split in ("train", "val", "test")
            },
            "created_at_utc": "2026-08-22T12:01:00+00:00",
        },
    )

    environment_path = workspace / "cloud-environment.json"
    image_hash = RUNPOD_CONTAINER_IMAGE_REFERENCE.rsplit(":", 1)[1]
    evidence_root = workspace / "cloud-evidence"
    evidence_root.mkdir()
    executable_root = evidence_root / "executables"
    executable_root.mkdir()
    executables: dict[str, Path] = {}
    for name in ("nvidia-smi", "python", "findmnt"):
        executable = executable_root / name
        executable.write_bytes(f"state-classifier-fixture:{name}".encode())
        executables[name] = executable.resolve()
    python_executable = str(executables["python"])
    observed_at = "2026-08-22T11:00:30+00:00"

    def capture(name: str, argv: list[str], stdout: str) -> Path:
        path = evidence_root / f"{name}.json"
        _write_json(
            path,
            {
                "schema": COMMAND_CAPTURE_SCHEMA,
                "capture_mode": "production_subprocess",
                "test_only": False,
                "argv": argv,
                "executable_sha256": _hash(Path(argv[0])),
                "exit_code": 0,
                "stdout": stdout,
                "stderr": "",
                "started_at_utc": observed_at,
                "completed_at_utc": observed_at,
            },
        )
        return path

    pod_projection = {
        "id": "pod-fixture",
        "name": RUNPOD_POD_NAME,
        "status": "RUNNING",
        "cloud": "SECURE",
        "cost": 0.99,
        "createdAt": "2026-08-22T10:55:00+00:00",
        "startedAt": "2026-08-22T11:00:00+00:00",
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
    provider_record = evidence_root / "runpod-pod-record.json"
    _write_json(
        provider_record,
        {
            "schema": RUNPOD_POD_RECORD_SCHEMA,
            "provider": "runpod",
            "capture_method": RUNPOD_MCP_CAPTURE_METHOD,
            "trust_level": CONTROL_PLANE_TRUST_LEVEL,
            "provider_signature_present": False,
            "request": {
                "getPod": {
                    "podId": "pod-fixture",
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
            "captured_at_utc": observed_at,
        },
    )
    gpu_probe = capture(
        "gpu-probe",
        [str(executables["nvidia-smi"]), *GPU_QUERY_ARGUMENTS],
        "NVIDIA GeForce RTX 5090, 32768, GPU-12345678-abcd\n",
    )
    runtime_probe = capture(
        "runtime-probe",
        [python_executable, "-c", RUNTIME_PROBE_CODE],
        json.dumps(
            {
                "python": FROZEN_RUNTIME["python"],
                "torch": FROZEN_RUNTIME["torch"],
                "torchvision": FROZEN_RUNTIME["torchvision"],
                "ultralytics": FROZEN_RUNTIME["ultralytics"],
                "cuda_runtime": "13.0",
                "python_executable": python_executable,
                "cuda_available": True,
            },
            sort_keys=True,
        ),
    )
    mount_probe = capture(
        "mount-probe",
        [str(executables["findmnt"]), *MOUNT_QUERY_ARGUMENTS],
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
    pip_freeze = capture(
        "pip-freeze",
        [python_executable, "-m", "pip", "freeze", "--all"],
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
    pip_check = capture(
        "pip-check",
        [python_executable, "-m", "pip", "check"],
        "No broken requirements found.\n",
    )
    evidence_paths = {
        "runpod_pod_record": provider_record,
        "gpu_probe": gpu_probe,
        "runtime_probe": runtime_probe,
        "mount_probe": mount_probe,
        "pip_freeze": pip_freeze,
        "pip_check": pip_check,
    }
    evidence = {
        name: {"path": str(path.resolve()), "sha256": _hash(path)}
        for name, path in evidence_paths.items()
    }
    _write_json(
        environment_path,
        {
            "schema": CLOUD_ENVIRONMENT_SCHEMA,
            "provider": "runpod",
            "control_plane_capture_trust": CONTROL_PLANE_TRUST_LEVEL,
            "durable_reload_trust": DURABLE_RELOAD_TRUST_LEVEL,
            "pod_id": "pod-fixture",
            "network_volume_id": RUNPOD_NETWORK_VOLUME_ID,
            "workspace_root": RUNPOD_WORKSPACE_ROOT,
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
                "digest": image_hash,
            },
            "persistent_mount": {
                "volume_id": RUNPOD_NETWORK_VOLUME_ID,
                "mount_path": "/workspace",
                "mount_identity": (
                    f"0:77|10.0.0.5:/runpod/{RUNPOD_NETWORK_VOLUME_ID}"
                ),
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
            "pod_started_at_utc": "2026-08-22T11:00:00+00:00",
            "captured_at_utc": "2026-08-22T11:05:00+00:00",
            "passed": True,
        },
    )
    base = workspace / "yolov8n-cls.pt"
    with base.open("wb") as stream:
        stream.write(_FAKE_TRUSTED_CHECKPOINT_PREFIX)
        stream.truncate(_OFFICIAL_CLASSIFIER_BYTES)
    plan_path = workspace / "state-training-plan.json"
    _write_json(
        plan_path,
        {
            "schema": STATE_TRAINING_PLAN_SCHEMA,
            "model_id": STATE_MODEL_ID,
            "candidate_id": "yolov8n-cls-224",
            "architecture": "yolov8n-cls.pt",
            "input_size_hw": [224, 224],
            "classes": {"0": "disaster_stressed", "1": "safe_walking"},
            "epochs": 5,
            "batch": 16,
            "seed": 0,
            "deterministic": True,
            "cache": False,
            "dataset_manifest_sha256": _hash(manifest_path),
            "base_weights_sha256": _OFFICIAL_CLASSIFIER_SHA256,
            "cloud_environment_sha256": _hash(environment_path),
            "safe_thresholds": [0.5, 0.8],
        },
    )
    return {
        "workspace": workspace,
        "repository": repository,
        "manifest": manifest_path,
        "records": records,
        "records_path": records_path,
        "environment": environment_path,
        "base": base,
        "base_manifest": classifier_manifest,
        "plan": plan_path,
        "sources": sources,
        "labeler_artifact": labeler_artifact,
        "label_policy": label_policy,
    }


class _FakeResult:
    def __init__(self, save_dir: Path) -> None:
        self.save_dir = save_dir


class _FakeModel:
    def __init__(self, base_path: str) -> None:
        self.base_path = base_path
        self.kwargs: dict | None = None
        self.trainer = None

    def train(self, **kwargs):
        self.kwargs = kwargs
        save_dir = Path(kwargs["project"]) / kwargs["name"]
        (save_dir / "weights").mkdir(parents=True)
        (save_dir / "weights" / "best.pt").write_bytes(b"trained-state-model")
        (save_dir / "weights" / "last.pt").write_bytes(b"last-state-model")
        (save_dir / "args.yaml").write_text(
            json.dumps({**kwargs, "model": self.base_path}), encoding="utf-8"
        )
        rows = ["epoch,train/loss,val/loss,metrics/accuracy_top1"]
        for epoch in range(1, kwargs["epochs"] + 1):
            rows.append(f"{epoch},{1 / epoch},{1 / (epoch + 1)},0.75")
        (save_dir / "results.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
        self.trainer = type(
            "FakeTrainer", (), {"save_dir": save_dir, "batch_size": kwargs["batch"]}
        )()
        return _FakeResult(save_dir)


def _runtime() -> dict:
    return {
        "python": FROZEN_RUNTIME["python"],
        "torch": FROZEN_RUNTIME["torch"],
        "torchvision": FROZEN_RUNTIME["torchvision"],
        "ultralytics": FROZEN_RUNTIME["ultralytics"],
        "cuda_runtime": "13.0",
        "cudnn_version": 9100,
        "visible_gpu_count": 1,
        "gpu_name": "NVIDIA GeForce RTX 5090",
        "gpu_total_memory_bytes": 32 * 1024**3,
    }


def _source() -> dict:
    return {"commit": "a" * 40, "branch": "test", "clean": True}


def _train(fixture: dict) -> tuple[Path, _FakeModel]:
    holder: list[_FakeModel] = []

    def factory(path: str) -> _FakeModel:
        model = _FakeModel(path)
        holder.append(model)
        return model

    report = run_state_classifier_training(
        training_plan_path=fixture["plan"],
        state_dataset_manifest_path=fixture["manifest"],
        cloud_environment_path=fixture["environment"],
        base_weights_path=fixture["base"],
        classifier_base_weights_manifest_path=fixture["base_manifest"],
        run_directory=fixture["workspace"] / "runs" / "state-n",
        output_report_path=fixture["workspace"] / "reports" / "training.json",
        workspace_root=fixture["workspace"],
        repository_root=fixture["repository"],
        model_factory=factory,
        now=iter(
            ["2026-08-22T12:10:00+00:00", "2026-08-22T12:20:00+00:00"]
        ).__next__,
        runtime_evidence=_runtime(),
        source_identity=_source(),
        effective_arguments_loader=lambda path: json.loads(path.read_text(encoding="utf-8")),
    )
    return report, holder[0]


def _assert_rejected_before_training_model_load(
    fixture: dict,
    *,
    match: str,
    base_weights_path: Path | None = None,
    classifier_manifest_path: Path | None = None,
) -> None:
    called = False

    def factory(_path: str):
        nonlocal called
        called = True
        raise AssertionError("untrusted checkpoint reached model deserialization")

    with pytest.raises(StateClassifierError, match=match):
        run_state_classifier_training(
            training_plan_path=fixture["plan"],
            state_dataset_manifest_path=fixture["manifest"],
            cloud_environment_path=fixture["environment"],
            base_weights_path=base_weights_path or fixture["base"],
            classifier_base_weights_manifest_path=(
                classifier_manifest_path or fixture["base_manifest"]
            ),
            run_directory=fixture["workspace"] / "runs" / "rejected",
            output_report_path=fixture["workspace"] / "reports" / "rejected.json",
            workspace_root=fixture["workspace"],
            repository_root=fixture["repository"],
            model_factory=factory,
            runtime_evidence=_runtime(),
            source_identity=_source(),
        )
    assert called is False


class _FakeInferenceModel:
    names = {0: "disaster_stressed", 1: "safe_walking"}

    def __init__(
        self,
        model_path: str,
        *,
        nonfinite: bool = False,
        wrong_result_path: bool = False,
        wrong_names: bool = False,
    ) -> None:
        self.model_path = model_path
        self.nonfinite = nonfinite
        self.wrong_result_path = wrong_result_path
        self.calls: list[dict] = []
        if wrong_names:
            self.names = {0: "safe_walking", 1: "disaster_stressed"}

    def predict(self, *, source, **kwargs):
        self.calls.append({"source": list(source), **kwargs})
        for crop in source:
            crop_path = Path(crop)
            safe = 0.9 if crop_path.parent.name == "safe_walking" else 0.1
            values = [float("nan"), safe] if self.nonfinite else [1.0 - safe, safe]
            yield SimpleNamespace(
                path=(str(crop_path.with_name("wrong.jpg")) if self.wrong_result_path else str(crop_path)),
                names=self.names,
                probs=SimpleNamespace(data=values),
            )


def _infer(
    fixture: dict,
    training_path: Path,
    split: str,
    *,
    selection_path: Path | None = None,
    stem: str | None = None,
    model_builder=None,
) -> tuple[Path, _FakeInferenceModel]:
    holder: list[_FakeInferenceModel] = []

    def factory(path: str) -> _FakeInferenceModel:
        model = (
            model_builder(path)
            if model_builder is not None
            else _FakeInferenceModel(path)
        )
        holder.append(model)
        return model

    stem = stem or split
    report = run_state_classifier_inference(
        training_report_path=training_path,
        state_dataset_manifest_path=fixture["manifest"],
        split=split,
        predictions_output_path=fixture["workspace"] / f"{stem}-predictions.jsonl",
        output_report_path=fixture["workspace"] / "reports" / f"{stem}-inference.json",
        workspace_root=fixture["workspace"],
        repository_root=fixture["repository"],
        selection_report_path=selection_path,
        model_factory=factory,
        runtime_evidence=_runtime(),
        now=lambda: "2026-08-22T12:30:00+00:00",
    )
    return report, holder[0]


def test_trains_only_from_frozen_weak_supervision_manifest(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    report_path, model = _train(fixture)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["model_id"] == STATE_MODEL_ID
    assert report["input_size_hw"] == [224, 224]
    assert report["dataset_origin_is_label"] is False
    assert report["state_evidence_status"] == "weak_supervision_unverified"
    assert report["weak_supervision_records"] == 12
    assert report["weak_supervision_provenance"]["labeler_id"] == (
        "visual-cue-labeler"
    )
    assert report["test_used_during_training_or_selection"] is False
    assert report["model_sha256"] == _hash(Path(report["model_path"]))
    assert report["base_weights_manifest_path"] == str(
        fixture["base_manifest"].resolve()
    )
    assert report["base_weights_manifest_sha256"] == _hash(
        fixture["base_manifest"]
    )
    assert report["base_weights_provenance"] == {
        "manifest_path": str(fixture["base_manifest"].resolve()),
        "manifest_sha256": _hash(fixture["base_manifest"]),
        "publisher": "Ultralytics",
        "repository": "https://github.com/ultralytics/assets",
        "release_tag": "v8.4.0",
        "architecture": "yolov8n-cls.pt",
        "url": (
            "https://github.com/ultralytics/assets/releases/download/"
            "v8.4.0/yolov8n-cls.pt"
        ),
        "bytes": _OFFICIAL_CLASSIFIER_BYTES,
        "sha256": _OFFICIAL_CLASSIFIER_SHA256,
    }
    assert model.base_path != str(fixture["base"].resolve())
    assert Path(model.base_path).name == fixture["base"].name
    assert Path(model.base_path).parent.name == ".state-n.verified-inputs"
    assert model.kwargs == {
        "data": str((fixture["workspace"] / "crops").resolve()),
        "imgsz": 224,
        "epochs": 5,
        "batch": 16,
        "seed": 0,
        "deterministic": True,
        "cache": False,
        "device": 0,
        "project": str((fixture["workspace"] / "runs").resolve()),
        "name": "state-n",
        "exist_ok": False,
        "save": True,
        "save_period": 1,
        "amp": False,
        "val": True,
        "plots": False,
        "workers": 8,
        "resume": False,
    }


def test_rejects_unknown_classifier_architecture_before_deserialization(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    plan = json.loads(fixture["plan"].read_text(encoding="utf-8"))
    plan["architecture"] = "yolov8s-cls.pt"
    _write_json(fixture["plan"], plan)
    _assert_rejected_before_training_model_load(
        fixture,
        match="architecture must be a frozen local YOLOv8 classifier",
    )


def test_rejects_tampered_classifier_allowlist_before_deserialization(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    manifest = json.loads(fixture["base_manifest"].read_text(encoding="utf-8"))
    manifest["release_tag"] = "v8.4.1"
    _write_json(fixture["base_manifest"], manifest)
    _assert_rejected_before_training_model_load(
        fixture,
        match="manifest release_tag is not frozen",
    )


def test_inference_recursively_rejects_classifier_allowlist_tamper(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    training_path, _ = _train(fixture)
    manifest = json.loads(fixture["base_manifest"].read_text(encoding="utf-8"))
    manifest["publisher"] = "Untrusted"
    _write_json(fixture["base_manifest"], manifest)
    with pytest.raises(StateClassifierError, match="manifest publisher is not frozen"):
        _infer(fixture, training_path, "val")


def test_rejects_classifier_checkpoint_byte_size_before_deserialization(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    with fixture["base"].open("r+b") as stream:
        stream.truncate(_OFFICIAL_CLASSIFIER_BYTES - 1)
    _assert_rejected_before_training_model_load(
        fixture,
        match="checkpoint byte-size mismatch",
    )


def test_rejects_classifier_checkpoint_hash_before_deserialization(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    with fixture["base"].open("r+b") as stream:
        stream.seek(0)
        stream.write(b"X")
    _assert_rejected_before_training_model_load(
        fixture,
        match="does not match the canonical allowlist",
    )


def test_rejects_classifier_checkpoint_plan_hash_before_deserialization(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    plan = json.loads(fixture["plan"].read_text(encoding="utf-8"))
    plan["base_weights_sha256"] = "0" * 64
    _write_json(fixture["plan"], plan)
    _assert_rejected_before_training_model_load(
        fixture,
        match="differs from training plan",
    )


def test_rejects_classifier_manifest_symlink_before_deserialization(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    canonical = fixture["base_manifest"]
    target = canonical.with_name("classifier-allowlist-target.json")
    target.write_bytes(canonical.read_bytes())
    canonical.unlink()
    try:
        canonical.symlink_to(target)
    except OSError as error:
        pytest.skip(f"file symlinks are unavailable on this Windows host: {error}")
    _assert_rejected_before_training_model_load(
        fixture,
        match="manifest must be one regular file",
    )


def test_rejects_classifier_checkpoint_symlink_before_deserialization(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    checkpoint = fixture["base"]
    target = checkpoint.with_name("trusted-checkpoint-target.pt")
    checkpoint.replace(target)
    try:
        checkpoint.symlink_to(target)
    except OSError as error:
        pytest.skip(f"file symlinks are unavailable on this Windows host: {error}")
    _assert_rejected_before_training_model_load(
        fixture,
        match="base weights must be one local regular file",
    )


def test_rejects_mismatched_weak_labeler_before_model_factory(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    fixture["records"][0]["labeler_artifact_sha256"] = "f" * 64
    fixture["records_path"].write_text(
        "".join(json.dumps(row) + "\n" for row in fixture["records"]), encoding="utf-8"
    )
    manifest = json.loads(fixture["manifest"].read_text(encoding="utf-8"))
    manifest["records_sha256"] = _hash(fixture["records_path"])
    _write_json(fixture["manifest"], manifest)
    called = False

    def factory(_path: str):
        nonlocal called
        called = True
        raise AssertionError

    with pytest.raises(StateClassifierError, match="provenance differs"):
        run_state_classifier_training(
            training_plan_path=fixture["plan"],
            state_dataset_manifest_path=fixture["manifest"],
            cloud_environment_path=fixture["environment"],
            base_weights_path=fixture["base"],
            classifier_base_weights_manifest_path=fixture["base_manifest"],
            run_directory=fixture["workspace"] / "run",
            output_report_path=fixture["workspace"] / "report.json",
            workspace_root=fixture["workspace"],
            repository_root=fixture["repository"],
            model_factory=factory,
        )
    assert called is False


def test_rejects_dataset_origin_and_pose_as_label_provenance(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    fixture["records"][0]["label_source"] = "c2a_pose"
    fixture["records_path"].write_text(
        "".join(json.dumps(row) + "\n" for row in fixture["records"]), encoding="utf-8"
    )
    manifest = json.loads(fixture["manifest"].read_text(encoding="utf-8"))
    manifest["records_sha256"] = _hash(fixture["records_path"])
    _write_json(fixture["manifest"], manifest)
    with pytest.raises(StateClassifierError, match="dataset origin cannot"):
        _train(fixture)


def test_rejects_tampered_weak_labeler_artifact_before_model_factory(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    fixture["labeler_artifact"].write_bytes(b"tampered-labeler")
    called = False

    def factory(_path: str):
        nonlocal called
        called = True
        raise AssertionError

    with pytest.raises(StateClassifierError, match="labeler_artifact SHA-256 mismatch"):
        run_state_classifier_training(
            training_plan_path=fixture["plan"],
            state_dataset_manifest_path=fixture["manifest"],
            cloud_environment_path=fixture["environment"],
            base_weights_path=fixture["base"],
            classifier_base_weights_manifest_path=fixture["base_manifest"],
            run_directory=fixture["workspace"] / "run",
            output_report_path=fixture["workspace"] / "report.json",
            workspace_root=fixture["workspace"],
            repository_root=fixture["repository"],
            model_factory=factory,
        )
    assert called is False


def test_rejects_manifest_or_source_tampering(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    Path(fixture["sources"][0]["archive_path"]).write_bytes(b"tampered archive")
    with pytest.raises(StateClassifierError, match="SHA-256 mismatch"):
        _train(fixture)


def test_plan_rejects_wrong_input_size_and_nonfinite_threshold(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    plan = json.loads(fixture["plan"].read_text(encoding="utf-8"))
    plan["input_size_hw"] = [640, 640]
    _write_json(fixture["plan"], plan)
    with pytest.raises(StateClassifierError, match="224x224"):
        validate_state_training_plan(fixture["plan"])
    plan["input_size_hw"] = [224, 224]
    plan["safe_thresholds"] = [float("nan")]
    fixture["plan"].write_text(json.dumps(plan), encoding="utf-8")
    with pytest.raises(StateClassifierError, match="finite"):
        validate_state_training_plan(fixture["plan"])


def test_validation_selects_threshold_and_test_cannot_change_it(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    training_path, _ = _train(fixture)
    training = json.loads(training_path.read_text(encoding="utf-8"))
    val_inference, inference_model = _infer(fixture, training_path, "val")
    validation_path = evaluate_state_classifier_validation(
        training_report_path=training_path,
        state_dataset_manifest_path=fixture["manifest"],
        inference_report_path=val_inference,
        output_report_path=fixture["workspace"] / "reports" / "validation.json",
        workspace_root=fixture["workspace"],
        repository_root=fixture["repository"],
    )
    val_inference_report = json.loads(val_inference.read_text(encoding="utf-8"))
    validation = json.loads(validation_path.read_text(encoding="utf-8"))
    assert val_inference_report["state_evidence_status"] == (
        "weak_supervision_unverified"
    )
    assert validation["state_evidence_status"] == "weak_supervision_unverified"
    selection_path = select_state_classifier(
        validation_report_paths=[validation_path],
        output_report_path=fixture["workspace"] / "reports" / "selection.json",
        workspace_root=fixture["workspace"],
        repository_root=fixture["repository"],
    )
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    assert selection["schema"] == STATE_SELECTION_SCHEMA
    assert selection["state_evidence_status"] == "weak_supervision_unverified"
    assert selection["safe_threshold"] == 0.8  # tie chooses fail-closed direction
    assert inference_model.model_path == training["model_path"]
    assert inference_model.calls[0]["source"] == [
        record["crop_path"]
        for record in sorted(fixture["records"], key=lambda item: item["sample_id"])
        if record["split"] == "val"
    ]
    assert {
        key: value
        for key, value in inference_model.calls[0].items()
        if key != "source"
    } == {
        "imgsz": 224,
        "batch": 64,
        "device": 0,
        "half": False,
        "augment": False,
        "save": False,
        "stream": True,
        "verbose": False,
    }
    test_inference, _ = _infer(
        fixture,
        training_path,
        "test",
        selection_path=selection_path,
    )
    test_path = evaluate_state_classifier_test(
        selection_report_path=selection_path,
        state_dataset_manifest_path=fixture["manifest"],
        inference_report_path=test_inference,
        output_report_path=fixture["workspace"] / "reports" / "test.json",
        workspace_root=fixture["workspace"],
        repository_root=fixture["repository"],
    )
    report = json.loads(test_path.read_text(encoding="utf-8"))
    assert report["schema"] == STATE_TEST_REPORT_SCHEMA
    assert report["state_evidence_status"] == "weak_supervision_unverified"
    assert report["safe_threshold"] == selection["safe_threshold"]
    assert report["threshold_source"] == "validation_selection_report"
    assert report["test_used_for_selection"] is False
    assert report["inference_report_sha256"] == _hash(test_inference)


def test_selection_rejects_test_report_leakage(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    path = fixture["workspace"] / "fake-test-evaluation.json"
    _write_json(path, {"schema": STATE_EVALUATION_SCHEMA, "split": "test"})
    with pytest.raises(StateClassifierError, match="test leakage"):
        select_state_classifier(
            validation_report_paths=[path],
            output_report_path=fixture["workspace"] / "selection.json",
            workspace_root=fixture["workspace"],
            repository_root=fixture["repository"],
        )


def test_rejects_nonfinite_prediction_metrics(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    training_path, _ = _train(fixture)
    with pytest.raises(StateClassifierError, match="finite"):
        _infer(
            fixture,
            training_path,
            "val",
            model_builder=lambda path: _FakeInferenceModel(path, nonfinite=True),
        )


def test_test_evaluation_rejects_tampered_selection_lineage(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    training_path, _ = _train(fixture)
    val_inference, _ = _infer(fixture, training_path, "val")
    validation_path = evaluate_state_classifier_validation(
        training_report_path=training_path,
        state_dataset_manifest_path=fixture["manifest"],
        inference_report_path=val_inference,
        output_report_path=fixture["workspace"] / "reports" / "validation.json",
        workspace_root=fixture["workspace"],
        repository_root=fixture["repository"],
    )
    selection_path = select_state_classifier(
        validation_report_paths=[validation_path],
        output_report_path=fixture["workspace"] / "reports" / "selection.json",
        workspace_root=fixture["workspace"],
        repository_root=fixture["repository"],
    )
    test_inference, _ = _infer(
        fixture, training_path, "test", selection_path=selection_path
    )
    selected = json.loads(selection_path.read_text(encoding="utf-8"))
    selected["validation_report_sha256"] = "0" * 64
    selection_path.write_text(json.dumps(selected), encoding="utf-8")
    with pytest.raises(StateClassifierError, match="validation report was tampered"):
        evaluate_state_classifier_test(
            selection_report_path=selection_path,
            state_dataset_manifest_path=fixture["manifest"],
            inference_report_path=test_inference,
            output_report_path=fixture["workspace"] / "reports" / "test.json",
            workspace_root=fixture["workspace"],
            repository_root=fixture["repository"],
        )


def test_test_evaluation_rejects_threshold_changed_after_validation(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    training_path, _ = _train(fixture)
    val_inference, _ = _infer(fixture, training_path, "val")
    validation_path = evaluate_state_classifier_validation(
        training_report_path=training_path,
        state_dataset_manifest_path=fixture["manifest"],
        inference_report_path=val_inference,
        output_report_path=fixture["workspace"] / "reports" / "validation.json",
        workspace_root=fixture["workspace"],
        repository_root=fixture["repository"],
    )
    selection_path = select_state_classifier(
        validation_report_paths=[validation_path],
        output_report_path=fixture["workspace"] / "reports" / "selection.json",
        workspace_root=fixture["workspace"],
        repository_root=fixture["repository"],
    )
    test_inference, _ = _infer(
        fixture, training_path, "test", selection_path=selection_path
    )
    selected = json.loads(selection_path.read_text(encoding="utf-8"))
    selected["safe_threshold"] = 0.5
    selection_path.write_text(json.dumps(selected), encoding="utf-8")
    with pytest.raises(StateClassifierError, match="validation-selected threshold"):
        evaluate_state_classifier_test(
            selection_report_path=selection_path,
            state_dataset_manifest_path=fixture["manifest"],
            inference_report_path=test_inference,
            output_report_path=fixture["workspace"] / "reports" / "test.json",
            workspace_root=fixture["workspace"],
            repository_root=fixture["repository"],
        )


def test_default_factory_is_lazy_and_receives_only_staged_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _fixture(tmp_path)
    models: list[_FakeModel] = []

    def yolo(path: str) -> _FakeModel:
        model = _FakeModel(path)
        models.append(model)
        return model

    monkeypatch.setitem(sys.modules, "ultralytics", SimpleNamespace(YOLO=yolo))
    report = run_state_classifier_training(
        training_plan_path=fixture["plan"],
        state_dataset_manifest_path=fixture["manifest"],
        cloud_environment_path=fixture["environment"],
        base_weights_path=fixture["base"],
        classifier_base_weights_manifest_path=fixture["base_manifest"],
        run_directory=fixture["workspace"] / "runs" / "lazy-default",
        output_report_path=fixture["workspace"] / "reports" / "lazy-default.json",
        workspace_root=fixture["workspace"],
        repository_root=fixture["repository"],
        runtime_evidence=_runtime(),
        source_identity=_source(),
        effective_arguments_loader=lambda path: json.loads(path.read_text(encoding="utf-8")),
        now=iter(
            ["2026-08-22T12:10:00+00:00", "2026-08-22T12:20:00+00:00"]
        ).__next__,
    )
    evidence = json.loads(report.read_text(encoding="utf-8"))
    assert len(models) == 1
    assert models[0].base_path == evidence["staged_base_weights_path"]
    assert evidence["staged_base_weights_sha256"] == evidence["base_weights_sha256"]


def test_rejects_live_runtime_or_dirty_source_before_deserialization(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    called = False

    def factory(_path: str):
        nonlocal called
        called = True
        raise AssertionError

    wrong_runtime = _runtime()
    wrong_runtime["gpu_name"] = "NVIDIA GeForce RTX 4090"
    with pytest.raises(StateClassifierError, match="GPU identity"):
        run_state_classifier_training(
            training_plan_path=fixture["plan"],
            state_dataset_manifest_path=fixture["manifest"],
            cloud_environment_path=fixture["environment"],
            base_weights_path=fixture["base"],
            classifier_base_weights_manifest_path=fixture["base_manifest"],
            run_directory=fixture["workspace"] / "runtime-fail",
            output_report_path=fixture["workspace"] / "runtime-fail.json",
            workspace_root=fixture["workspace"],
            repository_root=fixture["repository"],
            model_factory=factory,
            runtime_evidence=wrong_runtime,
            source_identity=_source(),
        )
    assert called is False

    with pytest.raises(StateClassifierError, match="must be clean"):
        run_state_classifier_training(
            training_plan_path=fixture["plan"],
            state_dataset_manifest_path=fixture["manifest"],
            cloud_environment_path=fixture["environment"],
            base_weights_path=fixture["base"],
            classifier_base_weights_manifest_path=fixture["base_manifest"],
            run_directory=fixture["workspace"] / "source-fail",
            output_report_path=fixture["workspace"] / "source-fail.json",
            workspace_root=fixture["workspace"],
            repository_root=fixture["repository"],
            model_factory=factory,
            runtime_evidence=_runtime(),
            source_identity={"commit": "a" * 40, "branch": "test", "clean": False},
        )
    assert called is False


def test_rejects_changed_effective_args_and_nonfinite_epoch_results(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)

    with pytest.raises(StateClassifierError, match="effective argument 'amp' changed"):
        run_state_classifier_training(
            training_plan_path=fixture["plan"],
            state_dataset_manifest_path=fixture["manifest"],
            cloud_environment_path=fixture["environment"],
            base_weights_path=fixture["base"],
            classifier_base_weights_manifest_path=fixture["base_manifest"],
            run_directory=fixture["workspace"] / "runs" / "bad-args",
            output_report_path=fixture["workspace"] / "reports" / "bad-args.json",
            workspace_root=fixture["workspace"],
            repository_root=fixture["repository"],
            model_factory=_FakeModel,
            runtime_evidence=_runtime(),
            source_identity=_source(),
            effective_arguments_loader=lambda path: {
                **json.loads(path.read_text(encoding="utf-8")),
                "amp": True,
            },
        )

    finite_root = tmp_path / "finite"
    finite_root.mkdir()
    fixture = _fixture(finite_root)

    class NonFiniteModel(_FakeModel):
        def train(self, **kwargs):
            result = super().train(**kwargs)
            results = Path(result.save_dir) / "results.csv"
            text = results.read_text(encoding="utf-8").replace("1.0,0.5", "nan,0.5", 1)
            results.write_text(text, encoding="utf-8")
            return result

    with pytest.raises(StateClassifierError, match="non-finite"):
        run_state_classifier_training(
            training_plan_path=fixture["plan"],
            state_dataset_manifest_path=fixture["manifest"],
            cloud_environment_path=fixture["environment"],
            base_weights_path=fixture["base"],
            classifier_base_weights_manifest_path=fixture["base_manifest"],
            run_directory=fixture["workspace"] / "runs" / "bad-results",
            output_report_path=fixture["workspace"] / "reports" / "bad-results.json",
            workspace_root=fixture["workspace"],
            repository_root=fixture["repository"],
            model_factory=NonFiniteModel,
            runtime_evidence=_runtime(),
            source_identity=_source(),
            effective_arguments_loader=lambda path: json.loads(path.read_text(encoding="utf-8")),
        )


def test_rejects_training_artifact_path_outside_workspace(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    outside = tmp_path / "outside.pt"
    outside.write_bytes(fixture["base"].read_bytes())
    with pytest.raises(StateClassifierError, match="escapes external workspace"):
        run_state_classifier_training(
            training_plan_path=fixture["plan"],
            state_dataset_manifest_path=fixture["manifest"],
            cloud_environment_path=fixture["environment"],
            base_weights_path=outside,
            classifier_base_weights_manifest_path=fixture["base_manifest"],
            run_directory=fixture["workspace"] / "run",
            output_report_path=fixture["workspace"] / "report.json",
            workspace_root=fixture["workspace"],
            repository_root=fixture["repository"],
            model_factory=_FakeModel,
            runtime_evidence=_runtime(),
            source_identity=_source(),
        )


def test_selection_rejects_structurally_forged_validation_metrics(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    training_path, _ = _train(fixture)
    val_inference, _ = _infer(fixture, training_path, "val")
    validation_path = evaluate_state_classifier_validation(
        training_report_path=training_path,
        state_dataset_manifest_path=fixture["manifest"],
        inference_report_path=val_inference,
        output_report_path=fixture["workspace"] / "reports" / "validation.json",
        workspace_root=fixture["workspace"],
        repository_root=fixture["repository"],
    )
    forged = json.loads(validation_path.read_text(encoding="utf-8"))
    forged["threshold_points"][0]["accuracy"] = 0.123
    validation_path.write_text(json.dumps(forged), encoding="utf-8")
    with pytest.raises(StateClassifierError, match="disagrees with confusion matrix"):
        select_state_classifier(
            validation_report_paths=[validation_path],
            output_report_path=fixture["workspace"] / "reports" / "selection.json",
            workspace_root=fixture["workspace"],
            repository_root=fixture["repository"],
        )


def test_validation_rejects_caller_fabricated_probabilities_without_inference_report(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    training_path, _ = _train(fixture)
    training = json.loads(training_path.read_text(encoding="utf-8"))
    fabricated = fixture["workspace"] / "fabricated-probabilities.json"
    _write_json(
        fabricated,
        {
            "sample_id": "val-0-disaster_stressed",
            "split": "val",
            "model_sha256": training["model_sha256"],
            "probabilities": {
                "disaster_stressed": 0.0,
                "safe_walking": 1.0,
            },
        },
    )
    with pytest.raises(StateClassifierError, match="inference report fields differ"):
        evaluate_state_classifier_validation(
            training_report_path=training_path,
            state_dataset_manifest_path=fixture["manifest"],
            inference_report_path=fabricated,
            output_report_path=fixture["workspace"] / "validation.json",
            workspace_root=fixture["workspace"],
            repository_root=fixture["repository"],
        )


def test_validation_recursively_rejects_tampered_sealed_predictions(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    training_path, _ = _train(fixture)
    inference_path, _ = _infer(fixture, training_path, "val")
    inference = json.loads(inference_path.read_text(encoding="utf-8"))
    predictions = Path(inference["predictions_path"])
    predictions.write_bytes(predictions.read_bytes() + b"\n")
    with pytest.raises(StateClassifierError, match="predictions SHA-256 mismatch"):
        evaluate_state_classifier_validation(
            training_report_path=training_path,
            state_dataset_manifest_path=fixture["manifest"],
            inference_report_path=inference_path,
            output_report_path=fixture["workspace"] / "validation.json",
            workspace_root=fixture["workspace"],
            repository_root=fixture["repository"],
        )


@pytest.mark.parametrize(
    "model_builder,error",
    [
        (
            lambda path: _FakeInferenceModel(path, wrong_result_path=True),
            "wrong crop",
        ),
        (
            lambda path: _FakeInferenceModel(path, wrong_names=True),
            "frozen state class map",
        ),
    ],
)
def test_inference_rejects_unbound_model_results(
    tmp_path: Path, model_builder, error: str
) -> None:
    fixture = _fixture(tmp_path)
    training_path, _ = _train(fixture)
    with pytest.raises(StateClassifierError, match=error):
        _infer(
            fixture,
            training_path,
            "val",
            model_builder=model_builder,
        )


def test_test_inference_requires_selection_and_is_once_per_selection(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    training_path, _ = _train(fixture)
    with pytest.raises(StateClassifierError, match="requires frozen validation selection"):
        _infer(fixture, training_path, "test")

    val_inference, _ = _infer(fixture, training_path, "val")
    validation = evaluate_state_classifier_validation(
        training_report_path=training_path,
        state_dataset_manifest_path=fixture["manifest"],
        inference_report_path=val_inference,
        output_report_path=fixture["workspace"] / "reports" / "validation.json",
        workspace_root=fixture["workspace"],
        repository_root=fixture["repository"],
    )
    selection = select_state_classifier(
        validation_report_paths=[validation],
        output_report_path=fixture["workspace"] / "reports" / "selection.json",
        workspace_root=fixture["workspace"],
        repository_root=fixture["repository"],
    )
    first, _ = _infer(
        fixture, training_path, "test", selection_path=selection, stem="test-first"
    )
    first_report = json.loads(first.read_text(encoding="utf-8"))
    assert first_report["test_once_receipt_sha256"] == _hash(
        Path(first_report["test_once_receipt_path"])
    )
    with pytest.raises(StateClassifierError, match="already reserved"):
        _infer(
            fixture,
            training_path,
            "test",
            selection_path=selection,
            stem="test-second",
        )


def test_inference_default_factory_is_lazy_and_loads_exact_trained_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _fixture(tmp_path)
    training_path, _ = _train(fixture)
    training = json.loads(training_path.read_text(encoding="utf-8"))
    models: list[_FakeInferenceModel] = []

    def yolo(path: str) -> _FakeInferenceModel:
        model = _FakeInferenceModel(path)
        models.append(model)
        return model

    monkeypatch.setitem(sys.modules, "ultralytics", SimpleNamespace(YOLO=yolo))
    report = run_state_classifier_inference(
        training_report_path=training_path,
        state_dataset_manifest_path=fixture["manifest"],
        split="val",
        predictions_output_path=fixture["workspace"] / "lazy-predictions.jsonl",
        output_report_path=fixture["workspace"] / "reports" / "lazy-inference.json",
        workspace_root=fixture["workspace"],
        repository_root=fixture["repository"],
        runtime_evidence=_runtime(),
        now=lambda: "2026-08-22T12:30:00+00:00",
    )
    evidence = json.loads(report.read_text(encoding="utf-8"))
    assert evidence["schema"] == STATE_INFERENCE_SCHEMA
    assert len(models) == 1
    assert models[0].model_path == training["model_path"]
    assert evidence["model_sha256"] == _hash(Path(models[0].model_path))
