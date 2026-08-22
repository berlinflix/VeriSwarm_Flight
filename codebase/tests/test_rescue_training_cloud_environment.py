from __future__ import annotations

import json
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest

import rescue_training.cloud_environment as cloud_environment
from rescue_training.artifact_io import sha256_file
from rescue_training.cloud_environment import (
    CLOUD_ENVIRONMENT_SCHEMA,
    COMMAND_CAPTURE_SCHEMA,
    CONTROL_PLANE_TRUST_LEVEL,
    DURABLE_RELOAD_TRUST_LEVEL,
    GPU_QUERY_ARGUMENTS,
    MOUNT_QUERY_ARGUMENTS,
    RUNPOD_CONTAINER_IMAGE_REFERENCE,
    RUNPOD_CONTAINER_DISK_GB,
    RUNPOD_DATA_CENTER_ID,
    RUNPOD_MCP_CAPTURE_METHOD,
    RUNPOD_MCP_CONTROL_PLANE_SCHEMA,
    RUNPOD_NETWORK_VOLUME_ID,
    RUNPOD_NETWORK_VOLUME_NAME,
    RUNPOD_NETWORK_VOLUME_SIZE_GB,
    RUNPOD_NETWORK_VOLUME_TYPE,
    RUNPOD_POD_NAME,
    RTX_5090_MEMORY_BYTES,
    RUNTIME_PROBE_CODE,
    CloudEnvironmentError,
    create_cloud_environment_manifest,
    create_runpod_mcp_control_plane_evidence,
    load_cloud_environment,
    validate_cloud_environment,
)
from rescue_training.contracts import FROZEN_RUNTIME


DIGEST = RUNPOD_CONTAINER_IMAGE_REFERENCE.rsplit(":", 1)[1]
STARTED = "2026-08-22T10:00:00+00:00"
OBSERVED = "2026-08-22T10:00:30+00:00"
CAPTURED = "2026-08-22T10:01:00+00:00"


def _runtime() -> dict[str, str]:
    return {
        "python": FROZEN_RUNTIME["python"],
        "torch": FROZEN_RUNTIME["torch"],
        "torchvision": FROZEN_RUNTIME["torchvision"],
        "ultralytics": FROZEN_RUNTIME["ultralytics"],
        "cuda_runtime": "13.0",
    }


def _mcp_projection(*, pod_id: str = "pod-5090-001") -> dict:
    return {
        "id": pod_id,
        "name": RUNPOD_POD_NAME,
        "status": "RUNNING",
        "cloud": "SECURE",
        "cost": 0.99,
        "createdAt": "2026-08-22T09:55:00+00:00",
        "startedAt": STARTED,
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
    }


def _mcp_volume_projection() -> dict:
    return {
        "dataCenter": RUNPOD_DATA_CENTER_ID,
        "id": RUNPOD_NETWORK_VOLUME_ID,
        "name": RUNPOD_NETWORK_VOLUME_NAME,
        "size": RUNPOD_NETWORK_VOLUME_SIZE_GB,
        "type": RUNPOD_NETWORK_VOLUME_TYPE,
    }


def _write_json(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
    return path


def _capture(
    path: Path,
    argv: list[str],
    stdout: str,
    *,
    exit_code: int = 0,
    stderr: str = "",
) -> Path:
    return _write_json(
        path,
        {
            "schema": COMMAND_CAPTURE_SCHEMA,
            "capture_mode": "production_subprocess",
            "test_only": False,
            "argv": argv,
            "executable_sha256": sha256_file(argv[0]),
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "started_at_utc": OBSERVED,
            "completed_at_utc": OBSERVED,
        },
    )


@pytest.mark.parametrize(
    "executable_name",
    (
        "python",
        "python.exe",
        "python3",
        "python3.exe",
        "python3.12",
        "python3.12.exe",
    ),
)
def test_python_probe_accepts_canonical_cpython_executable_names(
    tmp_path: Path, executable_name: str
):
    executable = (tmp_path / executable_name).resolve()
    executable.write_bytes(b"python fixture")

    assert (
        cloud_environment._executable(
            [str(executable)], "python", "runtime_probe.argv"
        )
        == str(executable)
    )


@pytest.mark.parametrize(
    "executable_name",
    (
        "python-config",
        "python3-config",
        "python3.12-config",
        "pythonx",
        "python312",
        "python0",
        "python999",
        "python3.13",
        "python3.12.13",
        "python3.12m",
        "python3.12d",
        "python.exe.bak",
        "ipython",
        "pypy3",
    ),
)
def test_python_probe_rejects_non_interpreter_executable_names(
    tmp_path: Path, executable_name: str
):
    executable = (tmp_path / executable_name).resolve()
    executable.write_bytes(b"not a python interpreter")

    with pytest.raises(CloudEnvironmentError, match="must execute 'python'"):
        cloud_environment._executable(
            [str(executable)], "python", "runtime_probe.argv"
        )


def _make_evidence(root: Path) -> dict[str, Path]:
    root.mkdir(parents=True)
    executables = root / "executables"
    executables.mkdir()
    executable_paths = {
        name: (executables / name).resolve()
        for name in ("nvidia-smi", "python", "findmnt")
    }
    for name, path in executable_paths.items():
        path.write_bytes(f"fixture executable {name}".encode("utf-8"))
    python = str(executable_paths["python"])
    projection = {
        "pod": _mcp_projection(),
        "networkVolume": _mcp_volume_projection(),
    }
    provider = _write_json(
        root / "runpod-pod-record.json",
        {
            "schema": RUNPOD_MCP_CONTROL_PLANE_SCHEMA,
            "provider": "runpod",
            "capture_method": RUNPOD_MCP_CAPTURE_METHOD,
            "trust_level": CONTROL_PLANE_TRUST_LEVEL,
            "provider_signature_present": False,
            "request": {
                "getPod": {
                    "podId": "pod-5090-001",
                    "includeMachine": True,
                    "includeNetworkVolume": True,
                },
                "getNetworkVolume": {
                    "networkVolumeId": RUNPOD_NETWORK_VOLUME_ID,
                },
            },
            "response_projection": projection,
            "response_projection_sha256": cloud_environment._projection_sha256(
                projection
            ),
            "captured_at_utc": OBSERVED,
        },
    )
    gpu = _capture(
        root / "gpu-probe.json",
        [str(executable_paths["nvidia-smi"]), *GPU_QUERY_ARGUMENTS],
        "NVIDIA GeForce RTX 5090, 32768, GPU-12345678-abcd\n",
    )
    runtime_stdout = json.dumps(
        {
            **_runtime(),
            "python_executable": python,
            "cuda_available": True,
        },
        sort_keys=True,
    )
    runtime = _capture(
        root / "runtime-probe.json",
        [python, "-c", RUNTIME_PROBE_CODE],
        runtime_stdout,
    )
    mount_stdout = json.dumps(
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
    )
    mount = _capture(
        root / "mount-probe.json",
        [str(executable_paths["findmnt"]), *MOUNT_QUERY_ARGUMENTS],
        mount_stdout,
    )
    freeze = _capture(
        root / "pip-freeze.json",
        [python, "-m", "pip", "freeze", "--all"],
        "\n".join(
            [
                f"torch=={FROZEN_RUNTIME['torch']}",
                f"torchvision=={FROZEN_RUNTIME['torchvision']}",
                f"ultralytics=={FROZEN_RUNTIME['ultralytics']}",
                "pip==26.0",
            ]
        )
        + "\n",
    )
    check = _capture(
        root / "pip-check.json",
        [python, "-m", "pip", "check"],
        "No broken requirements found.\n",
    )
    return {
        "runpod_pod_record": provider,
        "gpu_probe": gpu,
        "runtime_probe": runtime,
        "mount_probe": mount,
        "pip_freeze": freeze,
        "pip_check": check,
    }


def _references(paths: dict[str, Path]) -> dict[str, dict[str, str]]:
    return {
        name: {"path": str(path.resolve()), "sha256": sha256_file(path)}
        for name, path in paths.items()
    }


def _payload(root: Path) -> tuple[dict, dict[str, Path]]:
    paths = _make_evidence(root)
    references = _references(paths)
    return (
        {
            "schema": CLOUD_ENVIRONMENT_SCHEMA,
            "provider": "runpod",
            "control_plane_capture_trust": CONTROL_PLANE_TRUST_LEVEL,
            "durable_reload_trust": DURABLE_RELOAD_TRUST_LEVEL,
            "pod_id": "pod-5090-001",
            "network_volume_id": RUNPOD_NETWORK_VOLUME_ID,
            "workspace_root": "/workspace/samik-rescue-person-model-20260822",
            "gpu": {
                "name": "NVIDIA GeForce RTX 5090",
                "memory_bytes": RTX_5090_MEMORY_BYTES,
                "uuid": "GPU-12345678-abcd",
            },
            "runtime": _runtime(),
            "container_image": {
                "reference": RUNPOD_CONTAINER_IMAGE_REFERENCE,
                "digest": DIGEST,
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
            "pod_started_at_utc": STARTED,
            "captured_at_utc": CAPTURED,
            "passed": True,
        },
        paths,
    )


def _create_arguments(tmp_path: Path) -> dict:
    artifact_workspace = (tmp_path / "artifacts").resolve()
    repository = (tmp_path / "repository").resolve()
    artifact_workspace.mkdir()
    repository.mkdir()
    control_plane_path = (
        artifact_workspace / "manifests" / "runpod-mcp-control-plane.json"
    )
    create_runpod_mcp_control_plane_evidence(
        output_path=control_plane_path,
        pod_id="pod-5090-001",
        mcp_get_pod_response_projection=_mcp_projection(),
        mcp_get_network_volume_response_projection=_mcp_volume_projection(),
        artifact_workspace_root=artifact_workspace,
        artifact_repository_root=repository,
    )
    return {
        "output_path": artifact_workspace / "manifests" / "cloud-environment.json",
        "pod_id": "pod-5090-001",
        "control_plane_evidence_path": control_plane_path,
        "control_plane_evidence_sha256": sha256_file(control_plane_path),
        "cloud_repository_path": "/workspace/VeriSwarm_SIH",
        "artifact_workspace_root": artifact_workspace,
        "artifact_repository_root": repository,
    }


def _install_fake_production(
    monkeypatch, root: Path, *, python_name: str = "python"
) -> None:
    executables = root / "production-executables"
    executables.mkdir(parents=True)
    executable_names = {
        "nvidia-smi": "nvidia-smi",
        "python": python_name,
        "findmnt": "findmnt",
    }
    resolved = {
        role: (executables / executable_name).resolve()
        for role, executable_name in executable_names.items()
    }
    for role, path in resolved.items():
        path.write_bytes(f"executable:{role}".encode("utf-8"))

    def resolve(value: str) -> Path:
        supplied = Path(value)
        if value == cloud_environment.sys.executable:
            return resolved["python"]
        if supplied.is_absolute() and supplied.exists():
            return supplied.resolve()
        name = supplied.name
        try:
            return resolved[name]
        except KeyError as error:
            raise CloudEnvironmentError(f"unexpected executable: {value}") from error

    def runner(argv) -> subprocess.CompletedProcess[str]:
        name = Path(argv[0]).name
        arguments = list(argv[1:])
        resolved_python_name = resolved["python"].name
        if name == "nvidia-smi":
            stdout = "NVIDIA GeForce RTX 5090, 32768, GPU-12345678-abcd\n"
        elif name == "findmnt":
            stdout = json.dumps(
                {
                    "filesystems": [
                        {
                            "target": "/workspace",
                            "source": (
                                f"10.0.0.5:/runpod/{RUNPOD_NETWORK_VOLUME_ID}"
                            ),
                            "fstype": "nfs4",
                            "options": "rw,relatime,vers=4.1",
                            "maj:min": "0:77",
                        }
                    ]
                },
                sort_keys=True,
            )
        elif name == resolved_python_name and arguments == [
            "-c",
            RUNTIME_PROBE_CODE,
        ]:
            stdout = json.dumps(
                {
                    **_runtime(),
                    "python_executable": str(resolved["python"]),
                    "cuda_available": True,
                },
                sort_keys=True,
            )
        elif name == resolved_python_name and arguments == [
            "-m",
            "pip",
            "freeze",
            "--all",
        ]:
            stdout = (
                f"torch=={FROZEN_RUNTIME['torch']}\n"
                f"torchvision=={FROZEN_RUNTIME['torchvision']}\n"
                f"ultralytics=={FROZEN_RUNTIME['ultralytics']}\n"
            )
        elif name == resolved_python_name and arguments == ["-m", "pip", "check"]:
            stdout = "No broken requirements found.\n"
        else:
            return subprocess.CompletedProcess(argv, 2, "", "unexpected command")
        return subprocess.CompletedProcess(argv, 0, stdout, "")

    monkeypatch.setattr(cloud_environment, "_resolve_executable", resolve)
    monkeypatch.setattr(cloud_environment, "_production_runner", runner)


def test_valid_hashed_runpod_environment_is_derived_and_create_once(
    tmp_path: Path, monkeypatch
):
    _install_fake_production(monkeypatch, tmp_path)
    arguments = _create_arguments(tmp_path)
    result = create_cloud_environment_manifest(**arguments)
    loaded = load_cloud_environment(result)
    assert loaded["schema"] == CLOUD_ENVIRONMENT_SCHEMA
    assert loaded["gpu"]["uuid"] == "GPU-12345678-abcd"
    assert loaded["persistent_mount"]["mount_path"] == "/workspace"
    assert loaded["control_plane_capture_trust"] == CONTROL_PLANE_TRUST_LEVEL
    assert loaded["durable_reload_trust"] == DURABLE_RELOAD_TRUST_LEVEL
    assert loaded["package_integrity"]["pip_check_passed"] is True
    freeze_path = Path(loaded["evidence"]["pip_freeze"]["path"])
    assert loaded["package_integrity"]["pip_freeze_sha256"] == sha256_file(freeze_path)

    original = result.read_bytes()
    with pytest.raises(CloudEnvironmentError, match="overwrite"):
        create_cloud_environment_manifest(**arguments)
    assert result.read_bytes() == original


def test_manifest_creation_accepts_frozen_versioned_python_executable(
    tmp_path: Path, monkeypatch
):
    _install_fake_production(monkeypatch, tmp_path, python_name="python3.12")
    result = create_cloud_environment_manifest(**_create_arguments(tmp_path))
    loaded = load_cloud_environment(result)

    for evidence_name in ("runtime_probe", "pip_freeze", "pip_check"):
        evidence_path = Path(loaded["evidence"][evidence_name]["path"])
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        assert Path(evidence["argv"][0]).name == "python3.12"


def test_official_mcp_projection_is_secret_free_hash_bound_and_create_once(
    tmp_path: Path,
):
    workspace = (tmp_path / "artifacts").resolve()
    repository = (tmp_path / "repository").resolve()
    workspace.mkdir()
    repository.mkdir()
    output = workspace / "control-plane.json"
    projection = _mcp_projection()
    result = create_runpod_mcp_control_plane_evidence(
        output_path=output,
        pod_id="pod-5090-001",
        mcp_get_pod_response_projection=projection,
        mcp_get_network_volume_response_projection=_mcp_volume_projection(),
        artifact_workspace_root=workspace,
        artifact_repository_root=repository,
    )
    artifact = json.loads(result.read_text(encoding="utf-8"))
    assert artifact["schema"] == RUNPOD_MCP_CONTROL_PLANE_SCHEMA
    assert artifact["capture_method"] == RUNPOD_MCP_CAPTURE_METHOD
    assert artifact["provider_signature_present"] is False
    combined_projection = {
        "pod": projection,
        "networkVolume": _mcp_volume_projection(),
    }
    assert artifact["response_projection"] == combined_projection
    assert artifact["response_projection_sha256"] == (
        cloud_environment._projection_sha256(combined_projection)
    )
    rendered = result.read_text(encoding="utf-8")
    assert "JUPYTER_PASSWORD" not in rendered
    assert '"env"' not in rendered
    with pytest.raises(CloudEnvironmentError, match="overwrite"):
        create_runpod_mcp_control_plane_evidence(
            output_path=output,
            pod_id="pod-5090-001",
            mcp_get_pod_response_projection=projection,
            mcp_get_network_volume_response_projection=_mcp_volume_projection(),
            artifact_workspace_root=workspace,
            artifact_repository_root=repository,
        )


@pytest.mark.parametrize(
    "forbidden_field",
    ["env", "ssh", "ports", "registry", "signedUrl", "credentials"],
)
def test_mcp_evidence_rejects_secret_bearing_or_unallowlisted_fields(
    tmp_path: Path, forbidden_field: str
):
    workspace = (tmp_path / "artifacts").resolve()
    repository = (tmp_path / "repository").resolve()
    workspace.mkdir()
    repository.mkdir()
    projection = _mcp_projection()
    projection[forbidden_field] = {"secret": "must-not-persist"}
    output = workspace / "control-plane.json"
    with pytest.raises(CloudEnvironmentError, match="fields are not frozen"):
        create_runpod_mcp_control_plane_evidence(
            output_path=output,
            pod_id="pod-5090-001",
            mcp_get_pod_response_projection=projection,
            mcp_get_network_volume_response_projection=_mcp_volume_projection(),
            artifact_workspace_root=workspace,
            artifact_repository_root=repository,
        )
    assert not output.exists()


def test_mcp_evidence_rejects_unsealed_normalized_summary(tmp_path: Path):
    workspace = (tmp_path / "artifacts").resolve()
    repository = (tmp_path / "repository").resolve()
    workspace.mkdir()
    repository.mkdir()
    with pytest.raises(CloudEnvironmentError, match="fields are not frozen"):
        create_runpod_mcp_control_plane_evidence(
            output_path=workspace / "control-plane.json",
            pod_id="pod-5090-001",
            mcp_get_pod_response_projection={
                "pod_id": "pod-5090-001",
                "gpu_name": "NVIDIA GeForce RTX 5090",
                "volume_id": RUNPOD_NETWORK_VOLUME_ID,
                "passed": True,
            },
            mcp_get_network_volume_response_projection=_mcp_volume_projection(),
            artifact_workspace_root=workspace,
            artifact_repository_root=repository,
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("size", 99, "wrong capacity"),
        ("type", "HIGH_PERFORMANCE", "wrong storage tier"),
        ("dataCenter", "US-TX-1", "wrong data center"),
    ],
)
def test_mcp_network_volume_facts_are_frozen(
    tmp_path: Path, field: str, value, message: str
):
    workspace = (tmp_path / "artifacts").resolve()
    repository = (tmp_path / "repository").resolve()
    workspace.mkdir()
    repository.mkdir()
    volume = _mcp_volume_projection()
    volume[field] = value
    with pytest.raises(CloudEnvironmentError, match=message):
        create_runpod_mcp_control_plane_evidence(
            output_path=workspace / "control-plane.json",
            pod_id="pod-5090-001",
            mcp_get_pod_response_projection=_mcp_projection(),
            mcp_get_network_volume_response_projection=volume,
            artifact_workspace_root=workspace,
            artifact_repository_root=repository,
        )


def test_manifest_requires_independently_recorded_control_plane_hash(tmp_path: Path):
    arguments = _create_arguments(tmp_path)
    arguments["control_plane_evidence_sha256"] = "0" * 64
    with pytest.raises(CloudEnvironmentError, match="control_plane_evidence SHA-256 mismatch"):
        create_cloud_environment_manifest(**arguments)
    assert not Path(arguments["output_path"]).exists()


def test_mcp_projection_internal_hash_tampering_is_rejected(tmp_path: Path):
    payload, paths = _payload(tmp_path / "evidence")
    provider = json.loads(paths["runpod_pod_record"].read_text(encoding="utf-8"))
    provider["response_projection"]["pod"]["runtime"]["gpus"][0]["util"] = 7
    _write_json(paths["runpod_pod_record"], provider)
    payload["evidence"]["runpod_pod_record"]["sha256"] = sha256_file(
        paths["runpod_pod_record"]
    )
    with pytest.raises(CloudEnvironmentError, match="projection SHA-256 mismatch"):
        validate_cloud_environment(payload)


def test_summary_claims_cannot_override_hashed_evidence(tmp_path: Path):
    payload, _ = _payload(tmp_path / "evidence")
    for mutation in (
        lambda item: item["gpu"].update(name="NVIDIA A100"),
        lambda item: item["runtime"].update(torch="latest"),
        lambda item: item["container_image"].update(digest="b" * 64),
        lambda item: item.update(network_volume_id="different-volume"),
        lambda item: item["package_integrity"].update(pip_check_passed=False),
    ):
        candidate = deepcopy(payload)
        mutation(candidate)
        with pytest.raises(CloudEnvironmentError):
            validate_cloud_environment(candidate)


def test_gpu_and_provider_identity_come_from_separate_evidence(tmp_path: Path):
    payload, paths = _payload(tmp_path / "evidence")
    capture = json.loads(paths["gpu_probe"].read_text(encoding="utf-8"))
    capture["stdout"] = "NVIDIA A100-SXM4-80GB, 81920, GPU-12345678-abcd\n"
    _write_json(paths["gpu_probe"], capture)
    payload["evidence"]["gpu_probe"]["sha256"] = sha256_file(paths["gpu_probe"])
    with pytest.raises(CloudEnvironmentError, match="RTX 5090"):
        validate_cloud_environment(payload)


def test_runtime_and_pip_freeze_must_both_match_frozen_packages(tmp_path: Path):
    payload, paths = _payload(tmp_path / "evidence")
    freeze = json.loads(paths["pip_freeze"].read_text(encoding="utf-8"))
    freeze["stdout"] = freeze["stdout"].replace(
        f"torch=={FROZEN_RUNTIME['torch']}", "torch==0.0.0"
    )
    _write_json(paths["pip_freeze"], freeze)
    digest = sha256_file(paths["pip_freeze"])
    payload["evidence"]["pip_freeze"]["sha256"] = digest
    payload["package_integrity"]["pip_freeze_sha256"] = digest
    with pytest.raises(CloudEnvironmentError, match="pip-freeze evidence"):
        validate_cloud_environment(payload)


def test_pip_check_must_be_same_python_and_successful(tmp_path: Path):
    payload, paths = _payload(tmp_path / "evidence")
    check = json.loads(paths["pip_check"].read_text(encoding="utf-8"))
    check.update(exit_code=1, stdout="torch has requirement missing-package\n")
    _write_json(paths["pip_check"], check)
    digest = sha256_file(paths["pip_check"])
    payload["evidence"]["pip_check"]["sha256"] = digest
    payload["package_integrity"]["pip_check_sha256"] = digest
    with pytest.raises(CloudEnvironmentError, match="pip check did not pass"):
        validate_cloud_environment(payload)


@pytest.mark.parametrize(
    ("filesystem", "source", "options", "message"),
    [
        ("overlay", "overlay", "rw,relatime", "ephemeral"),
        ("nfs4", "10.0.0.5:/volume", "ro,relatime", "writable"),
    ],
)
def test_persistent_mount_must_be_real_and_writable(
    tmp_path: Path, filesystem: str, source: str, options: str, message: str
):
    payload, paths = _payload(tmp_path / "evidence")
    capture = json.loads(paths["mount_probe"].read_text(encoding="utf-8"))
    stdout = json.loads(capture["stdout"])
    stdout["filesystems"][0].update(
        fstype=filesystem,
        source=source,
        options=options,
    )
    capture["stdout"] = json.dumps(stdout, sort_keys=True)
    _write_json(paths["mount_probe"], capture)
    payload["evidence"]["mount_probe"]["sha256"] = sha256_file(paths["mount_probe"])
    with pytest.raises(CloudEnvironmentError, match=message):
        validate_cloud_environment(payload)


def test_provider_image_and_volume_are_live_hash_bound(tmp_path: Path):
    payload, paths = _payload(tmp_path / "evidence")
    provider = json.loads(paths["runpod_pod_record"].read_text(encoding="utf-8"))
    provider["response_projection"]["pod"]["image"] = (
        f"wrong/image@sha256:{'b' * 64}"
    )
    provider["response_projection_sha256"] = cloud_environment._projection_sha256(
        provider["response_projection"]
    )
    _write_json(paths["runpod_pod_record"], provider)
    payload["evidence"]["runpod_pod_record"]["sha256"] = sha256_file(
        paths["runpod_pod_record"]
    )
    with pytest.raises(CloudEnvironmentError, match="wrong image digest"):
        validate_cloud_environment(payload)


def test_self_attested_or_wrong_command_evidence_is_rejected(tmp_path: Path):
    payload, paths = _payload(tmp_path / "provider")
    provider = json.loads(paths["runpod_pod_record"].read_text(encoding="utf-8"))
    provider["trust_level"] = "literal_self_attested"
    _write_json(paths["runpod_pod_record"], provider)
    payload["evidence"]["runpod_pod_record"]["sha256"] = sha256_file(
        paths["runpod_pod_record"]
    )
    with pytest.raises(CloudEnvironmentError, match="official MCP provenance"):
        validate_cloud_environment(payload)

    payload, paths = _payload(tmp_path / "gpu-command")
    capture = json.loads(paths["gpu_probe"].read_text(encoding="utf-8"))
    capture["argv"].append("--fake")
    _write_json(paths["gpu_probe"], capture)
    payload["evidence"]["gpu_probe"]["sha256"] = sha256_file(paths["gpu_probe"])
    with pytest.raises(CloudEnvironmentError, match="frozen nvidia-smi query"):
        validate_cloud_environment(payload)


def test_private_injected_executor_is_marked_test_only_and_cannot_qualify(
    tmp_path: Path,
) -> None:
    executable = (tmp_path / "nvidia-smi").resolve()
    executable.write_bytes(b"fixture executable")
    moments = iter(
        (
            cloud_environment._utc(OBSERVED, "test.started"),
            cloud_environment._utc(OBSERVED, "test.completed"),
        )
    )

    capture = cloud_environment._execute_capture(
        [str(executable), *GPU_QUERY_ARGUMENTS],
        _runner=lambda argv: subprocess.CompletedProcess(
            argv,
            0,
            "NVIDIA GeForce RTX 5090, 32768, GPU-12345678-abcd\n",
            "",
        ),
        _clock=moments.__next__,
    )
    assert capture["capture_mode"] == "test_injected_executor"
    assert capture["test_only"] is True
    path = _write_json(tmp_path / "test-capture.json", capture)
    with pytest.raises(CloudEnvironmentError, match="test-injected"):
        cloud_environment._command_capture(
            path,
            "test capture",
            started=cloud_environment._utc(STARTED, "test.window.start"),
            captured=cloud_environment._utc(CAPTURED, "test.window.end"),
        )


def test_evidence_tampering_after_manifest_is_detected(tmp_path: Path):
    payload, paths = _payload(tmp_path / "evidence")
    paths["gpu_probe"].write_bytes(b"tampered")
    with pytest.raises(CloudEnvironmentError, match="SHA-256 mismatch"):
        validate_cloud_environment(payload)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ('{"schema":"a","schema":"b"}', "duplicate JSON field"),
        ('{"memory_bytes":NaN}', "non-finite JSON constant"),
    ],
)
def test_saved_environment_rejects_duplicate_keys_and_nonfinite(
    tmp_path: Path, text: str, message: str
):
    source = tmp_path / "environment.json"
    source.write_text(text, encoding="utf-8")
    with pytest.raises(CloudEnvironmentError, match=message):
        load_cloud_environment(source)


def test_environment_requires_exact_fields_and_utc_timestamps(tmp_path: Path):
    payload, _ = _payload(tmp_path / "first")
    payload["extra"] = "ambiguous"
    with pytest.raises(CloudEnvironmentError, match="fields are not frozen"):
        validate_cloud_environment(payload)

    payload, _ = _payload(tmp_path / "second")
    payload["captured_at_utc"] = "2026-08-22T10:01:00"
    with pytest.raises(CloudEnvironmentError, match="UTC explicitly"):
        validate_cloud_environment(payload)
