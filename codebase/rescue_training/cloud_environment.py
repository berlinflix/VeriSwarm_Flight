"""Raw-evidence-bound RunPod RTX 5090 environment qualification.

The manifest is deliberately not a collection of caller-supplied claims. Its
GPU, package, CUDA, image, and persistent-mount identities are derived from
create-once evidence files whose SHA-256 values are recomputed whenever the
manifest is validated. Training therefore fails closed if an evidence file is
missing, edited, internally inconsistent, or reports a broken environment.
"""

from __future__ import annotations

import json
import hashlib
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Mapping
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Sequence

from .artifact_io import (
    ArtifactIOError,
    canonical_json_bytes,
    create_external_json,
    require_external_workspace,
    require_path_within_workspace,
    sha256_file,
)
from .contracts import FROZEN_RUNTIME, RUNPOD_WORKSPACE_ROOT


CLOUD_ENVIRONMENT_SCHEMA = "veriswarm.rescue.cloud_environment.v3"
RUNPOD_MCP_CONTROL_PLANE_SCHEMA = (
    "veriswarm.rescue.runpod_mcp_control_plane.v1"
)
# Backward-compatible public name while the uncommitted callers migrate.  The
# value intentionally identifies the new evidence format, not the impossible
# runpodctl export it replaces.
RUNPOD_POD_RECORD_SCHEMA = RUNPOD_MCP_CONTROL_PLANE_SCHEMA
COMMAND_CAPTURE_SCHEMA = "veriswarm.rescue.command_capture.v2"
RTX_5090_NAME = "NVIDIA GeForce RTX 5090"
RTX_5090_MEMORY_BYTES = 32 * 1024**3
RTX_5090_MIN_MEMORY_BYTES = 31 * 1024**3
CUDA_RUNTIME = "13.0"
PERSISTENT_MOUNT_PATH = "/workspace"
RUNPOD_POD_NAME = "samik-rescue-person-training"
RUNPOD_DATA_CENTER_ID = "EU-RO-1"
RUNPOD_NETWORK_VOLUME_ID = "e6vat4wz37"
RUNPOD_NETWORK_VOLUME_NAME = "samik-rescue-person-model-20260822"
RUNPOD_NETWORK_VOLUME_SIZE_GB = 100
RUNPOD_NETWORK_VOLUME_TYPE = "STANDARD"
RUNPOD_ON_DEMAND_PRICE_PER_HR_USD = "0.99"
RUNPOD_CONTAINER_DISK_GB = 30
RUNPOD_CONTAINER_IMAGE_REFERENCE = (
    "runpod/pytorch@sha256:"
    "c7ff5829fb34e42557edf949396d3875a74b3a896f7835268670cc62fdd3e60a"
)
CONTROL_PLANE_TRUST_LEVEL = "authenticated_official_mcp_capture_unattested"
DURABLE_RELOAD_TRUST_LEVEL = "hash_bound_mcp_capture_not_provider_signed"
PRODUCTION_CAPTURE_MODE = "production_subprocess"
TEST_CAPTURE_MODE = "test_injected_executor"
RUNPOD_MCP_CAPTURE_METHOD = (
    "mcp__runpod__get_pod+mcp__runpod__get_network_volume"
)

GPU_QUERY_ARGUMENTS = (
    "--query-gpu=name,memory.total,uuid",
    "--format=csv,noheader,nounits",
)
MOUNT_QUERY_ARGUMENTS = (
    "--json",
    "--target",
    PERSISTENT_MOUNT_PATH,
    "--output",
    "TARGET,SOURCE,FSTYPE,OPTIONS,MAJ:MIN",
)
RUNTIME_PROBE_CODE = (
    "import json,platform,sys,torch,torchvision,ultralytics;"
    "print(json.dumps({'cuda_available':torch.cuda.is_available(),"
    "'cuda_runtime':torch.version.cuda,'python':platform.python_version(),"
    "'python_executable':sys.executable,'torch':torch.__version__,"
    "'torchvision':torchvision.__version__,"
    "'ultralytics':ultralytics.__version__},sort_keys=True))"
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_GPU_UUID_RE = re.compile(r"^GPU-[A-Za-z0-9-]{8,}$")
_MAJOR_MINOR_RE = re.compile(r"^[0-9]+:[0-9]+$")
_FROZEN_PACKAGE_VERSIONS = {
    "torch": FROZEN_RUNTIME["torch"],
    "torchvision": FROZEN_RUNTIME["torchvision"],
    "ultralytics": FROZEN_RUNTIME["ultralytics"],
}
_EPHEMERAL_FILESYSTEMS = frozenset(
    {"overlay", "tmpfs", "ramfs", "rootfs", "squashfs"}
)
_EPHEMERAL_SOURCES = frozenset({"overlay", "tmpfs", "rootfs", "/dev/root"})
_EVIDENCE_NAMES = frozenset(
    {
        "runpod_pod_record",
        "gpu_probe",
        "runtime_probe",
        "mount_probe",
        "pip_freeze",
        "pip_check",
    }
)
_ROOT_FIELDS = frozenset(
    {
        "schema",
        "provider",
        "control_plane_capture_trust",
        "durable_reload_trust",
        "pod_id",
        "network_volume_id",
        "workspace_root",
        "gpu",
        "runtime",
        "container_image",
        "persistent_mount",
        "package_integrity",
        "evidence",
        "repository",
        "pod_started_at_utc",
        "captured_at_utc",
        "passed",
    }
)
_RUNTIME_FIELDS = frozenset(
    {"python", "torch", "torchvision", "ultralytics", "cuda_runtime"}
)


class CloudEnvironmentError(ValueError):
    """RunPod environment evidence does not match the frozen training target."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CloudEnvironmentError(f"duplicate JSON field is forbidden: {key!r}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise CloudEnvironmentError(f"non-finite JSON constant is forbidden: {value}")


def _object(value: Any, field: str, expected: frozenset[str]) -> dict[str, Any]:
    if type(value) is not dict or not all(type(key) is str for key in value):
        raise CloudEnvironmentError(f"{field} must be a JSON object with string keys")
    actual = frozenset(value)
    if actual != expected:
        raise CloudEnvironmentError(
            f"{field} fields are not frozen; "
            f"missing={sorted(expected - actual)}, unknown={sorted(actual - expected)}"
        )
    return value


def _text(value: Any, field: str) -> str:
    if type(value) is not str or not value or value.strip() != value:
        raise CloudEnvironmentError(f"{field} must be a non-empty trimmed string")
    return value


def _sha256(value: Any, field: str) -> str:
    digest = _text(value, field)
    if not _SHA256_RE.fullmatch(digest):
        raise CloudEnvironmentError(f"{field} must be a lowercase SHA-256")
    return digest


def _integer(value: Any, field: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise CloudEnvironmentError(f"{field} must be an integer >= {minimum}")
    return value


def _number(value: Any, field: str, *, minimum: float = 0.0) -> int | float:
    if type(value) not in {int, float} or value < minimum:
        raise CloudEnvironmentError(f"{field} must be a number >= {minimum}")
    return value


def _utc(value: Any, field: str) -> datetime:
    text = _text(value, field)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise CloudEnvironmentError(f"{field} must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise CloudEnvironmentError(f"{field} must identify UTC explicitly")
    return parsed


def _strict_json_text(text: str, field: str) -> dict[str, Any]:
    try:
        value = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except CloudEnvironmentError:
        raise
    except json.JSONDecodeError as error:
        raise CloudEnvironmentError(f"{field} is not strict JSON: {error}") from error
    if type(value) is not dict:
        raise CloudEnvironmentError(f"{field} must contain one JSON object")
    return value


def _strict_json_file(path: Path, field: str) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise CloudEnvironmentError(f"cannot read {field}: {error}") from error
    return _strict_json_text(text, field)


def _artifact_reference(value: Any, field: str) -> tuple[Path, str]:
    reference = _object(value, field, frozenset({"path", "sha256"}))
    text = _text(reference["path"], f"{field}.path")
    path = Path(text)
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise CloudEnvironmentError(f"{field}.path must be one absolute regular file")
    resolved = path.resolve()
    if str(resolved) != text:
        raise CloudEnvironmentError(f"{field}.path must be normalized")
    expected = _sha256(reference["sha256"], f"{field}.sha256")
    try:
        actual = sha256_file(resolved)
    except ArtifactIOError as error:
        raise CloudEnvironmentError(str(error)) from error
    if actual != expected:
        raise CloudEnvironmentError(f"{field} SHA-256 mismatch")
    return resolved, actual


def _reference_for_path(value: str | os.PathLike[str], field: str) -> dict[str, str]:
    path = Path(value)
    if not path.is_absolute():
        path = path.resolve()
    if path.is_symlink() or not path.is_file():
        raise CloudEnvironmentError(f"{field} must be one regular evidence file")
    path = path.resolve()
    try:
        digest = sha256_file(path)
    except ArtifactIOError as error:
        raise CloudEnvironmentError(str(error)) from error
    return {"path": str(path), "sha256": digest}


def _reference_for_path_with_expected_sha256(
    value: str | os.PathLike[str],
    expected_sha256: str,
    field: str,
) -> dict[str, str]:
    reference = _reference_for_path(value, field)
    expected = _sha256(expected_sha256, f"{field}_sha256")
    if reference["sha256"] != expected:
        raise CloudEnvironmentError(f"{field} SHA-256 mismatch")
    return {"path": reference["path"], "sha256": expected}


def _command_capture(
    path: Path,
    field: str,
    *,
    started: datetime,
    captured: datetime,
) -> dict[str, Any]:
    value = _object(
        _strict_json_file(path, field),
        field,
        frozenset(
            {
                "schema",
                "capture_mode",
                "test_only",
                "argv",
                "executable_sha256",
                "exit_code",
                "stdout",
                "stderr",
                "started_at_utc",
                "completed_at_utc",
            }
        ),
    )
    if value["schema"] != COMMAND_CAPTURE_SCHEMA:
        raise CloudEnvironmentError(f"{field} has an unsupported command-capture schema")
    if (
        value["capture_mode"] != PRODUCTION_CAPTURE_MODE
        or value["test_only"] is not False
    ):
        raise CloudEnvironmentError(
            f"{field} is test-injected and cannot qualify a production environment"
        )
    argv = value["argv"]
    if type(argv) is not list or not argv or not all(
        type(item) is str and item and item.strip() == item for item in argv
    ):
        raise CloudEnvironmentError(f"{field}.argv must be a non-empty string array")
    executable_path = Path(argv[0])
    if (
        not executable_path.is_absolute()
        or executable_path.is_symlink()
        or not executable_path.is_file()
        or executable_path.resolve(strict=False) != executable_path
    ):
        raise CloudEnvironmentError(
            f"{field}.argv[0] must be one normalized absolute executable file"
        )
    _integer(value["exit_code"], f"{field}.exit_code")
    if type(value["stdout"]) is not str or type(value["stderr"]) is not str:
        raise CloudEnvironmentError(f"{field} stdout/stderr must be strings")
    executable_hash = _sha256(
        value["executable_sha256"], f"{field}.executable_sha256"
    )
    try:
        live_executable_hash = sha256_file(executable_path)
    except ArtifactIOError as error:
        raise CloudEnvironmentError(str(error)) from error
    if live_executable_hash != executable_hash:
        raise CloudEnvironmentError(f"{field} executable SHA-256 mismatch")
    command_started = _utc(value["started_at_utc"], f"{field}.started_at_utc")
    command_completed = _utc(
        value["completed_at_utc"], f"{field}.completed_at_utc"
    )
    if not started <= command_started <= command_completed <= captured:
        raise CloudEnvironmentError(f"{field} timestamps are outside the Pod evidence window")
    return value


def _executable(argv: list[str], name: str, field: str) -> str:
    executable = Path(argv[0])
    executable_name = executable.name
    if executable_name.lower().endswith(".exe"):
        executable_name = executable_name[:-4]
    if name == "python":
        major, minor, _patch = FROZEN_RUNTIME["python"].split(".")
        name_matches = executable_name in {
            "python",
            f"python{major}",
            f"python{major}.{minor}",
        }
    else:
        name_matches = executable_name == name
    if not name_matches:
        raise CloudEnvironmentError(f"{field} must execute {name!r}")
    if not executable.is_absolute() or str(executable.resolve(strict=False)) != argv[0]:
        raise CloudEnvironmentError(f"{field} executable path must be normalized and absolute")
    return argv[0]


def _immutable_image(value: Any, field: str) -> dict[str, str]:
    image = _object(value, field, frozenset({"reference", "digest"}))
    digest = _sha256(image["digest"], f"{field}.digest")
    reference = _text(image["reference"], f"{field}.reference")
    if (
        any(character.isspace() for character in reference)
        or reference == f"@sha256:{digest}"
        or not reference.endswith(f"@sha256:{digest}")
    ):
        raise CloudEnvironmentError(
            f"{field}.reference must end with its exact immutable sha256 digest"
        )
    return {"reference": reference, "digest": digest}


def _projection_sha256(value: Mapping[str, Any]) -> str:
    try:
        return hashlib.sha256(canonical_json_bytes(value)).hexdigest()
    except ArtifactIOError as error:
        raise CloudEnvironmentError(str(error)) from error


def _mcp_response_projection(
    value: Any,
    *,
    expected_pod_id: str,
    captured: datetime,
) -> dict[str, Any]:
    """Validate the exact secret-free projection emitted by official get_pod.

    This deliberately accepts neither a normalized qualification summary nor
    arbitrary provider-response fields.  In particular, ``env``, ``ssh``,
    ``ports``, registry credentials, and template data cannot enter durable
    evidence.
    """

    projection = _object(
        value,
        "runpod_mcp_control_plane.response_projection",
        frozenset(
            {
                "id",
                "name",
                "status",
                "cloud",
                "cost",
                "createdAt",
                "startedAt",
                "cudaVersion",
                "dataCenterId",
                "disk",
                "gpu",
                "image",
                "locked",
                "mounts",
                "runtime",
            }
        ),
    )
    pod_id = _text(projection["id"], "response_projection.id")
    if pod_id != expected_pod_id:
        raise CloudEnvironmentError("RunPod MCP returned a different Pod ID")
    if projection["name"] != RUNPOD_POD_NAME:
        raise CloudEnvironmentError("RunPod control plane reports the wrong Pod name")
    if projection["status"] != "RUNNING":
        raise CloudEnvironmentError("RunPod MCP evidence must report RUNNING")
    if projection["cloud"] != "SECURE":
        raise CloudEnvironmentError("RunPod Pod must use SECURE cloud")
    if _normalized_price(projection["cost"]) != RUNPOD_ON_DEMAND_PRICE_PER_HR_USD:
        raise CloudEnvironmentError("RunPod Pod price differs from the frozen price")
    created = _utc(projection["createdAt"], "response_projection.createdAt")
    started = _utc(projection["startedAt"], "response_projection.startedAt")
    if not created <= started <= captured:
        raise CloudEnvironmentError("RunPod MCP timestamps are outside the capture window")
    if projection["cudaVersion"] != CUDA_RUNTIME:
        raise CloudEnvironmentError("RunPod control plane reports the wrong CUDA version")
    if projection["dataCenterId"] != RUNPOD_DATA_CENTER_ID:
        raise CloudEnvironmentError("RunPod Pod is in the wrong data center")
    if _integer(projection["disk"], "response_projection.disk") != RUNPOD_CONTAINER_DISK_GB:
        raise CloudEnvironmentError("RunPod Pod has the wrong container-disk size")

    gpu = _object(
        projection["gpu"],
        "response_projection.gpu",
        frozenset({"id", "count"}),
    )
    if gpu["id"] != RTX_5090_NAME or _integer(
        gpu["count"], "response_projection.gpu.count", minimum=1
    ) != 1:
        raise CloudEnvironmentError("RunPod control plane must assign one RTX 5090")

    image_reference = _text(projection["image"], "response_projection.image")
    image_match = re.fullmatch(r"(.+)@sha256:([0-9a-f]{64})", image_reference)
    if image_match is None or image_reference != RUNPOD_CONTAINER_IMAGE_REFERENCE:
        raise CloudEnvironmentError("RunPod control plane reports the wrong image digest")
    if projection["locked"] is not False:
        raise CloudEnvironmentError("RunPod MCP evidence must report an unlocked Pod")

    mounts = _object(
        projection["mounts"],
        "response_projection.mounts",
        frozenset({"network"}),
    )
    network = mounts["network"]
    if type(network) is not list or len(network) != 1:
        raise CloudEnvironmentError("RunPod MCP must report exactly one network mount")
    network_mount = _object(
        network[0],
        "response_projection.mounts.network[0]",
        frozenset({"path", "volumeId"}),
    )
    if (
        network_mount["path"] != PERSISTENT_MOUNT_PATH
        or network_mount["volumeId"] != RUNPOD_NETWORK_VOLUME_ID
    ):
        raise CloudEnvironmentError("RunPod MCP reports the wrong network volume mount")

    runtime = _object(
        projection["runtime"],
        "response_projection.runtime",
        frozenset({"gpus"}),
    )
    runtime_gpus = runtime["gpus"]
    if type(runtime_gpus) is not list or len(runtime_gpus) != 1:
        raise CloudEnvironmentError("RunPod MCP runtime must report exactly one live GPU")
    utilization = _object(
        runtime_gpus[0],
        "response_projection.runtime.gpus[0]",
        frozenset({"memoryUtil", "util"}),
    )
    _number(utilization["memoryUtil"], "response_projection.runtime.gpus[0].memoryUtil")
    _number(utilization["util"], "response_projection.runtime.gpus[0].util")
    return dict(projection)


def _mcp_network_volume_response_projection(
    value: Any,
    *,
    expected_volume_id: str,
) -> dict[str, Any]:
    projection = _object(
        value,
        "runpod_mcp_control_plane.response_projection.networkVolume",
        frozenset({"dataCenter", "id", "name", "size", "type"}),
    )
    if projection["id"] != expected_volume_id:
        raise CloudEnvironmentError("RunPod MCP returned a different network volume")
    if projection["name"] != RUNPOD_NETWORK_VOLUME_NAME:
        raise CloudEnvironmentError("RunPod network volume has the wrong name")
    if projection["dataCenter"] != RUNPOD_DATA_CENTER_ID:
        raise CloudEnvironmentError("RunPod network volume is in the wrong data center")
    if _integer(projection["size"], "response_projection.networkVolume.size") != (
        RUNPOD_NETWORK_VOLUME_SIZE_GB
    ):
        raise CloudEnvironmentError("RunPod network volume has the wrong capacity")
    if projection["type"] != RUNPOD_NETWORK_VOLUME_TYPE:
        raise CloudEnvironmentError("RunPod network volume has the wrong storage tier")
    return dict(projection)


def _provider_record(
    path: Path,
    *,
    started: datetime,
    captured: datetime,
) -> dict[str, Any]:
    value = _object(
        _strict_json_file(path, "runpod_mcp_control_plane evidence"),
        "runpod_mcp_control_plane evidence",
        frozenset(
            {
                "schema",
                "provider",
                "capture_method",
                "trust_level",
                "provider_signature_present",
                "request",
                "response_projection",
                "response_projection_sha256",
                "captured_at_utc",
            }
        ),
    )
    if value["schema"] != RUNPOD_POD_RECORD_SCHEMA:
        raise CloudEnvironmentError("unsupported RunPod MCP control-plane schema")
    if (
        value["trust_level"] != CONTROL_PLANE_TRUST_LEVEL
        or value["provider"] != "runpod"
    ):
        raise CloudEnvironmentError(
            "control-plane evidence lacks the frozen official MCP provenance disclosure"
        )
    if value["capture_method"] != RUNPOD_MCP_CAPTURE_METHOD:
        raise CloudEnvironmentError("control-plane evidence used the wrong MCP method")
    if value["provider_signature_present"] is not False:
        raise CloudEnvironmentError(
            "RunPod MCP evidence must honestly disclose that it is not provider-signed"
        )
    request = _object(
        value["request"],
        "runpod_mcp_control_plane.request",
        frozenset({"getPod", "getNetworkVolume"}),
    )
    get_pod_request = _object(
        request["getPod"],
        "runpod_mcp_control_plane.request.getPod",
        frozenset({"podId", "includeMachine", "includeNetworkVolume"}),
    )
    expected_pod_id = _text(
        get_pod_request["podId"], "runpod_mcp_control_plane.request.getPod.podId"
    )
    if (
        get_pod_request["includeMachine"] is not True
        or get_pod_request["includeNetworkVolume"] is not True
    ):
        raise CloudEnvironmentError("RunPod MCP capture request omitted required expansions")
    get_volume_request = _object(
        request["getNetworkVolume"],
        "runpod_mcp_control_plane.request.getNetworkVolume",
        frozenset({"networkVolumeId"}),
    )
    expected_volume_id = _text(
        get_volume_request["networkVolumeId"],
        "runpod_mcp_control_plane.request.getNetworkVolume.networkVolumeId",
    )
    observed = _utc(
        value["captured_at_utc"], "runpod_mcp_control_plane.captured_at_utc"
    )
    if not started <= observed <= captured:
        raise CloudEnvironmentError("RunPod MCP capture timestamp differs from evidence window")
    combined_projection = _object(
        value["response_projection"],
        "runpod_mcp_control_plane.response_projection",
        frozenset({"pod", "networkVolume"}),
    )
    projection = _mcp_response_projection(
        combined_projection["pod"],
        expected_pod_id=expected_pod_id,
        captured=observed,
    )
    volume_projection = _mcp_network_volume_response_projection(
        combined_projection["networkVolume"],
        expected_volume_id=expected_volume_id,
    )
    mounted_volume_id = projection["mounts"]["network"][0]["volumeId"]
    if mounted_volume_id != volume_projection["id"]:
        raise CloudEnvironmentError("Pod mount and network-volume MCP facts differ")
    recorded_projection_hash = _sha256(
        value["response_projection_sha256"],
        "runpod_mcp_control_plane.response_projection_sha256",
    )
    if _projection_sha256(combined_projection) != recorded_projection_hash:
        raise CloudEnvironmentError("RunPod MCP response projection SHA-256 mismatch")
    provider_started = _utc(projection["startedAt"], "response_projection.startedAt")
    if provider_started != started:
        raise CloudEnvironmentError("RunPod MCP Pod start differs from evidence window")
    image_reference = projection["image"]
    return {
        "pod_id": projection["id"],
        "gpu": {"type": projection["gpu"]["id"], "count": projection["gpu"]["count"]},
        "container_image": {
            "reference": image_reference,
            "digest": image_reference.rsplit(":", 1)[1],
        },
        "network_volume": {
            "id": volume_projection["id"],
            "mount_path": projection["mounts"]["network"][0]["path"],
            "data_center_id": projection["dataCenterId"],
        },
        "pod_started_at_utc": projection["startedAt"],
        "observed_at_utc": value["captured_at_utc"],
    }


def _gpu_probe(capture: dict[str, Any]) -> dict[str, Any]:
    argv = capture["argv"]
    _executable(argv, "nvidia-smi", "gpu_probe.argv")
    if tuple(argv[1:]) != GPU_QUERY_ARGUMENTS or capture["exit_code"] != 0:
        raise CloudEnvironmentError("GPU probe did not execute the frozen nvidia-smi query")
    if capture["stderr"].strip():
        raise CloudEnvironmentError("GPU probe wrote to stderr")
    lines = [line for line in capture["stdout"].splitlines() if line.strip()]
    if len(lines) != 1:
        raise CloudEnvironmentError("GPU probe must report exactly one GPU")
    columns = [column.strip() for column in lines[0].split(",")]
    if len(columns) != 3:
        raise CloudEnvironmentError("GPU probe output must contain name, memory MiB, and UUID")
    name, memory_mib_text, uuid = columns
    if name != RTX_5090_NAME:
        raise CloudEnvironmentError("nvidia-smi evidence must identify an RTX 5090")
    try:
        memory_mib = int(memory_mib_text)
    except ValueError as error:
        raise CloudEnvironmentError("GPU memory evidence must be integer MiB") from error
    memory_bytes = memory_mib * 1024**2
    if not RTX_5090_MIN_MEMORY_BYTES <= memory_bytes <= RTX_5090_MEMORY_BYTES:
        raise CloudEnvironmentError("GPU evidence must report approximately 32 GiB")
    if not _GPU_UUID_RE.fullmatch(uuid):
        raise CloudEnvironmentError("GPU probe UUID is invalid")
    return {"name": name, "memory_bytes": memory_bytes, "uuid": uuid}


def _runtime_probe(capture: dict[str, Any]) -> tuple[dict[str, str], str]:
    argv = capture["argv"]
    python_executable = _executable(argv, "python", "runtime_probe.argv")
    if tuple(argv[1:]) != ("-c", RUNTIME_PROBE_CODE) or capture["exit_code"] != 0:
        raise CloudEnvironmentError("runtime probe did not execute the frozen Python query")
    if capture["stderr"].strip():
        raise CloudEnvironmentError("runtime probe wrote to stderr")
    observation = _object(
        _strict_json_text(capture["stdout"], "runtime_probe.stdout"),
        "runtime_probe.stdout",
        frozenset(
            {
                "python_executable",
                "python",
                "torch",
                "torchvision",
                "ultralytics",
                "cuda_runtime",
                "cuda_available",
            }
        ),
    )
    if observation["python_executable"] != python_executable:
        raise CloudEnvironmentError("runtime probe Python executable identity mismatch")
    if observation["cuda_available"] is not True:
        raise CloudEnvironmentError("runtime probe reports CUDA unavailable")
    runtime = {name: observation[name] for name in _RUNTIME_FIELDS}
    expected = {
        "python": FROZEN_RUNTIME["python"],
        "torch": FROZEN_RUNTIME["torch"],
        "torchvision": FROZEN_RUNTIME["torchvision"],
        "ultralytics": FROZEN_RUNTIME["ultralytics"],
        "cuda_runtime": CUDA_RUNTIME,
    }
    if runtime != expected:
        raise CloudEnvironmentError("runtime probe does not match the frozen package/CUDA line")
    return runtime, python_executable


def _mount_probe(capture: dict[str, Any]) -> dict[str, Any]:
    argv = capture["argv"]
    _executable(argv, "findmnt", "mount_probe.argv")
    if tuple(argv[1:]) != MOUNT_QUERY_ARGUMENTS or capture["exit_code"] != 0:
        raise CloudEnvironmentError("mount probe did not execute the frozen findmnt query")
    if capture["stderr"].strip():
        raise CloudEnvironmentError("mount probe wrote to stderr")
    output = _object(
        _strict_json_text(capture["stdout"], "mount_probe.stdout"),
        "mount_probe.stdout",
        frozenset({"filesystems"}),
    )
    filesystems = output["filesystems"]
    if type(filesystems) is not list or len(filesystems) != 1:
        raise CloudEnvironmentError("mount probe must identify exactly one /workspace mount")
    mount = _object(
        filesystems[0],
        "mount_probe.filesystems[0]",
        frozenset({"target", "source", "fstype", "options", "maj:min"}),
    )
    if mount["target"] != PERSISTENT_MOUNT_PATH:
        raise CloudEnvironmentError("mount probe target must be exactly /workspace")
    source = _text(mount["source"], "mount_probe.source")
    filesystem = _text(mount["fstype"], "mount_probe.fstype").lower()
    major_minor = _text(mount["maj:min"], "mount_probe.maj:min")
    if not _MAJOR_MINOR_RE.fullmatch(major_minor):
        raise CloudEnvironmentError("mount probe major:minor identity is invalid")
    options_text = _text(mount["options"], "mount_probe.options")
    options = set(options_text.split(","))
    if "rw" not in options or "ro" in options:
        raise CloudEnvironmentError("persistent /workspace mount must be writable")
    if filesystem in _EPHEMERAL_FILESYSTEMS or source.lower() in _EPHEMERAL_SOURCES:
        raise CloudEnvironmentError("/workspace evidence identifies an ephemeral filesystem")
    return {
        "mount_path": PERSISTENT_MOUNT_PATH,
        "mount_identity": f"{major_minor}|{source}",
        "filesystem_type": filesystem,
        "writable": True,
    }


def _pip_freeze(capture: dict[str, Any], python_executable: str) -> None:
    argv = capture["argv"]
    if tuple(argv) != (python_executable, "-m", "pip", "freeze", "--all"):
        raise CloudEnvironmentError("pip-freeze evidence used the wrong Python environment")
    if capture["exit_code"] != 0 or capture["stderr"].strip():
        raise CloudEnvironmentError("pip freeze did not complete cleanly")
    lines = capture["stdout"].splitlines()
    if not lines or any(not line or line.strip() != line for line in lines):
        raise CloudEnvironmentError("pip-freeze output must contain trimmed package records")
    versions: dict[str, str] = {}
    for line in lines:
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([^\s]+)", line)
        if match is None:
            continue
        name = re.sub(r"[-_.]+", "-", match.group(1)).lower()
        if name in versions:
            raise CloudEnvironmentError(f"pip-freeze output duplicates package {name!r}")
        versions[name] = match.group(2)
    for name, expected in _FROZEN_PACKAGE_VERSIONS.items():
        if versions.get(name) != expected:
            raise CloudEnvironmentError(
                f"pip-freeze evidence does not bind {name}=={expected}"
            )


def _pip_check(capture: dict[str, Any], python_executable: str) -> None:
    if tuple(capture["argv"]) != (python_executable, "-m", "pip", "check"):
        raise CloudEnvironmentError("pip-check evidence used the wrong Python environment")
    if (
        capture["exit_code"] != 0
        or capture["stdout"].strip() != "No broken requirements found."
        or capture["stderr"].strip()
    ):
        raise CloudEnvironmentError("pip check did not pass cleanly")


def _repository_path(value: Any) -> str:
    text = _text(value, "repository.path")
    path = PurePosixPath(text)
    workspace = PurePosixPath(RUNPOD_WORKSPACE_ROOT)
    if not path.is_absolute() or path == PurePosixPath("/") or ".." in path.parts:
        raise CloudEnvironmentError("repository.path must be an absolute normalized POSIX path")
    if str(path) != text:
        raise CloudEnvironmentError("repository.path must be normalized")
    if path == workspace or workspace in path.parents or path in workspace.parents:
        raise CloudEnvironmentError("repository must be outside the model workspace")
    if path.parent != workspace.parent:
        raise CloudEnvironmentError("repository and model workspace must be siblings")
    return text


CommandRunner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None:
        raise CloudEnvironmentError("capture clock must return timezone-aware UTC")
    value = value.astimezone(timezone.utc)
    return value.isoformat(timespec="microseconds")


def _resolve_executable(name_or_path: str) -> Path:
    candidate = Path(name_or_path)
    located = str(candidate) if candidate.is_absolute() else shutil.which(name_or_path)
    if not located:
        raise CloudEnvironmentError(f"required executable is unavailable: {name_or_path}")
    resolved = Path(located).resolve(strict=True)
    if resolved.is_symlink() or not resolved.is_file():
        raise CloudEnvironmentError(f"executable is not one regular file: {resolved}")
    return resolved


def _production_runner(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(argv),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="strict",
            shell=False,
            check=False,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError, UnicodeError) as error:
        raise CloudEnvironmentError(
            f"environment probe could not execute {argv[0]!r}: {error}"
        ) from error


def _execute_capture(
    argv: Sequence[str],
    *,
    _runner: CommandRunner | None = None,
    _clock: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Execute a frozen command; injected runners are permanently test-only."""

    if not argv:
        raise CloudEnvironmentError("probe argv must not be empty")
    executable = _resolve_executable(str(argv[0]))
    normalized_argv = [str(executable), *(str(value) for value in argv[1:])]
    runner = _production_runner if _runner is None else _runner
    clock = _now_utc if _clock is None else _clock
    test_only = _runner is not None or _clock is not None
    started = clock()
    result = runner(normalized_argv)
    completed = clock()
    if not isinstance(result, subprocess.CompletedProcess):
        raise CloudEnvironmentError("command runner returned the wrong result type")
    if type(result.returncode) is not int:
        raise CloudEnvironmentError("command runner return code must be an integer")
    if type(result.stdout) is not str or type(result.stderr) is not str:
        raise CloudEnvironmentError("command runner must capture text stdout and stderr")
    return {
        "schema": COMMAND_CAPTURE_SCHEMA,
        "capture_mode": TEST_CAPTURE_MODE if test_only else PRODUCTION_CAPTURE_MODE,
        "test_only": test_only,
        "argv": normalized_argv,
        "executable_sha256": sha256_file(executable),
        "exit_code": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "started_at_utc": _utc_text(started),
        "completed_at_utc": _utc_text(completed),
    }


def _normalized_price(value: Any) -> str:
    if isinstance(value, bool) or type(value) not in {str, int, float}:
        raise CloudEnvironmentError("RunPod Pod costPerHr must be numeric")
    try:
        price = Decimal(str(value))
    except InvalidOperation as error:
        raise CloudEnvironmentError("RunPod Pod costPerHr is invalid") from error
    if not price.is_finite() or price <= 0:
        raise CloudEnvironmentError("RunPod Pod costPerHr must be positive")
    return format(price.quantize(Decimal("0.01")), "f")


def _derive_evidence(
    evidence_value: Any,
    *,
    started: datetime,
    captured: datetime,
) -> dict[str, Any]:
    evidence = _object(evidence_value, "evidence", _EVIDENCE_NAMES)
    paths: dict[str, Path] = {}
    hashes: dict[str, str] = {}
    for name in sorted(_EVIDENCE_NAMES):
        path, digest = _artifact_reference(evidence[name], f"evidence.{name}")
        paths[name] = path
        hashes[name] = digest
    if len(set(paths.values())) != len(paths) or len(set(hashes.values())) != len(hashes):
        raise CloudEnvironmentError("cloud-environment evidence artifacts must be distinct")

    provider = _provider_record(paths["runpod_pod_record"], started=started, captured=captured)
    captures = {
        name: _command_capture(paths[name], f"{name} evidence", started=started, captured=captured)
        for name in ("gpu_probe", "runtime_probe", "mount_probe", "pip_freeze", "pip_check")
    }
    gpu = _gpu_probe(captures["gpu_probe"])
    if provider["gpu"]["type"] != gpu["name"]:
        raise CloudEnvironmentError("RunPod and nvidia-smi GPU identities differ")
    runtime, python_executable = _runtime_probe(captures["runtime_probe"])
    mount = _mount_probe(captures["mount_probe"])
    _pip_freeze(captures["pip_freeze"], python_executable)
    _pip_check(captures["pip_check"], python_executable)
    return {
        "pod_id": provider["pod_id"],
        "network_volume_id": provider["network_volume"]["id"],
        "gpu": gpu,
        "runtime": runtime,
        "container_image": _immutable_image(
            provider["container_image"], "runpod_pod_record.container_image"
        ),
        "persistent_mount": {
            "volume_id": provider["network_volume"]["id"],
            **mount,
        },
        "package_integrity": {
            "pip_freeze_sha256": hashes["pip_freeze"],
            "pip_check_sha256": hashes["pip_check"],
            "pip_check_passed": True,
        },
    }


def validate_cloud_environment(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate live, raw evidence without contacting RunPod or NVIDIA."""

    raw = _object(value, "cloud environment", _ROOT_FIELDS)
    if raw["schema"] != CLOUD_ENVIRONMENT_SCHEMA:
        raise CloudEnvironmentError("unsupported cloud-environment schema")
    if raw["provider"] != "runpod":
        raise CloudEnvironmentError("provider must be exactly 'runpod'")
    if raw["control_plane_capture_trust"] != CONTROL_PLANE_TRUST_LEVEL:
        raise CloudEnvironmentError("control-plane capture trust level is not frozen")
    if raw["durable_reload_trust"] != DURABLE_RELOAD_TRUST_LEVEL:
        raise CloudEnvironmentError(
            "durable reload must disclose that RunPod is not reauthenticated"
        )
    started = _utc(raw["pod_started_at_utc"], "pod_started_at_utc")
    captured = _utc(raw["captured_at_utc"], "captured_at_utc")
    if captured < started:
        raise CloudEnvironmentError("captured_at_utc precedes pod_started_at_utc")
    derived = _derive_evidence(raw["evidence"], started=started, captured=captured)

    _text(raw["pod_id"], "pod_id")
    _text(raw["network_volume_id"], "network_volume_id")
    if raw["workspace_root"] != RUNPOD_WORKSPACE_ROOT:
        raise CloudEnvironmentError(f"workspace_root must be exactly {RUNPOD_WORKSPACE_ROOT!r}")
    gpu = _object(raw["gpu"], "gpu", frozenset({"name", "memory_bytes", "uuid"}))
    runtime = _object(raw["runtime"], "runtime", _RUNTIME_FIELDS)
    image = _immutable_image(raw["container_image"], "container_image")
    mount = _object(
        raw["persistent_mount"],
        "persistent_mount",
        frozenset(
            {"volume_id", "mount_path", "mount_identity", "filesystem_type", "writable"}
        ),
    )
    integrity = _object(
        raw["package_integrity"],
        "package_integrity",
        frozenset({"pip_freeze_sha256", "pip_check_sha256", "pip_check_passed"}),
    )
    _sha256(integrity["pip_freeze_sha256"], "package_integrity.pip_freeze_sha256")
    _sha256(integrity["pip_check_sha256"], "package_integrity.pip_check_sha256")
    if integrity["pip_check_passed"] is not True:
        raise CloudEnvironmentError("package integrity does not record a passing pip check")

    claimed = {
        "pod_id": raw["pod_id"],
        "network_volume_id": raw["network_volume_id"],
        "gpu": gpu,
        "runtime": runtime,
        "container_image": image,
        "persistent_mount": mount,
        "package_integrity": integrity,
    }
    if claimed != derived:
        raise CloudEnvironmentError(
            "cloud-environment identity differs from live hashed evidence"
        )
    repository = _object(raw["repository"], "repository", frozenset({"path"}))
    _repository_path(repository["path"])
    if raw["passed"] is not True:
        raise CloudEnvironmentError("cloud environment did not pass")
    return dict(raw)


def create_runpod_mcp_control_plane_evidence(
    *,
    output_path: str | os.PathLike[str],
    pod_id: str,
    mcp_get_pod_response_projection: Mapping[str, Any],
    mcp_get_network_volume_response_projection: Mapping[str, Any],
    artifact_workspace_root: str | os.PathLike[str],
    artifact_repository_root: str | os.PathLike[str],
) -> Path:
    """Seal official Pod and network-volume projections as create-once evidence.

    The caller must project both official MCP results to the exact, secret-free
    response shapes validated here.  This boundary never accepts normalized
    claims such as ``gpu_name`` or ``volume_id``.  The resulting artifact is
    still explicitly *unattested*: RunPod MCP currently supplies no provider
    signature.  Its integrity after creation is established by the external
    file SHA-256 required by :func:`create_cloud_environment_manifest`.
    """

    try:
        workspace = require_external_workspace(
            artifact_workspace_root, artifact_repository_root
        )
        output = require_path_within_workspace(output_path, workspace)
    except ArtifactIOError as error:
        raise CloudEnvironmentError(str(error)) from error
    expected_pod_id = _text(pod_id, "pod_id")
    captured = _now_utc()
    captured_text = _utc_text(captured)
    pod_projection = _mcp_response_projection(
        mcp_get_pod_response_projection,
        expected_pod_id=expected_pod_id,
        captured=captured,
    )
    volume_projection = _mcp_network_volume_response_projection(
        mcp_get_network_volume_response_projection,
        expected_volume_id=RUNPOD_NETWORK_VOLUME_ID,
    )
    if (
        pod_projection["mounts"]["network"][0]["volumeId"]
        != volume_projection["id"]
    ):
        raise CloudEnvironmentError("Pod mount and network-volume MCP facts differ")
    projection = {"pod": pod_projection, "networkVolume": volume_projection}
    payload = {
        "schema": RUNPOD_MCP_CONTROL_PLANE_SCHEMA,
        "provider": "runpod",
        "capture_method": RUNPOD_MCP_CAPTURE_METHOD,
        "trust_level": CONTROL_PLANE_TRUST_LEVEL,
        "provider_signature_present": False,
        "request": {
            "getPod": {
                "podId": expected_pod_id,
                "includeMachine": True,
                "includeNetworkVolume": True,
            },
            "getNetworkVolume": {
                "networkVolumeId": RUNPOD_NETWORK_VOLUME_ID,
            },
        },
        "response_projection": projection,
        "response_projection_sha256": _projection_sha256(projection),
        "captured_at_utc": captured_text,
    }
    try:
        return create_external_json(
            output,
            payload,
            workspace_root=workspace,
            repository_root=artifact_repository_root,
        )
    except ArtifactIOError as error:
        raise CloudEnvironmentError(str(error)) from error


def create_cloud_environment_manifest(
    *,
    output_path: str | os.PathLike[str],
    pod_id: str,
    control_plane_evidence_path: str | os.PathLike[str],
    control_plane_evidence_sha256: str,
    cloud_repository_path: str,
    artifact_workspace_root: str | os.PathLike[str],
    artifact_repository_root: str | os.PathLike[str],
) -> Path:
    """Execute all probes and publish one production RunPod qualification.

    The caller supplies a create-once official-MCP evidence artifact and its
    independently recorded SHA-256, never normalized control-plane claims or
    runtime probe results.  All on-Pod probes execute here as fixed argv arrays.
    There is deliberately no public command-runner or prewritten-runtime-
    capture parameter.
    """

    try:
        workspace = require_external_workspace(
            artifact_workspace_root, artifact_repository_root
        )
        output = require_path_within_workspace(output_path, workspace)
    except ArtifactIOError as error:
        raise CloudEnvironmentError(str(error)) from error
    _repository_path(cloud_repository_path)
    expected_pod_id = _text(pod_id, "pod_id")
    try:
        provider_path = require_path_within_workspace(
            control_plane_evidence_path, workspace
        )
    except ArtifactIOError as error:
        raise CloudEnvironmentError(str(error)) from error
    provider_reference = _reference_for_path_with_expected_sha256(
        provider_path,
        control_plane_evidence_sha256,
        "control_plane_evidence",
    )
    provider_artifact = _strict_json_file(
        provider_path, "runpod_mcp_control_plane evidence"
    )
    response = provider_artifact.get("response_projection")
    if type(response) is not dict:
        raise CloudEnvironmentError(
            "RunPod MCP evidence response_projection must be an object"
        )
    pod_response = response.get("pod")
    if type(pod_response) is not dict:
        raise CloudEnvironmentError(
            "RunPod MCP evidence response_projection.pod must be an object"
        )
    started_text = _text(
        pod_response.get("startedAt"), "response_projection.pod.startedAt"
    )
    started = _utc(started_text, "response_projection.startedAt")

    evidence_directory = output.parent / f"{output.stem}.evidence"
    capture_paths = {
        "gpu_probe": evidence_directory / "nvidia-smi.json",
        "runtime_probe": evidence_directory / "python-runtime.json",
        "mount_probe": evidence_directory / "workspace-mount.json",
        "pip_freeze": evidence_directory / "installed-distributions.json",
        "pip_check": evidence_directory / "pip-check.json",
    }
    probe_argv = {
        "gpu_probe": [str(_resolve_executable("nvidia-smi")), *GPU_QUERY_ARGUMENTS],
        "runtime_probe": [
            str(_resolve_executable(sys.executable)),
            "-c",
            RUNTIME_PROBE_CODE,
        ],
        "mount_probe": [str(_resolve_executable("findmnt")), *MOUNT_QUERY_ARGUMENTS],
        "pip_freeze": [
            str(_resolve_executable(sys.executable)),
            "-m",
            "pip",
            "freeze",
            "--all",
        ],
        "pip_check": [
            str(_resolve_executable(sys.executable)),
            "-m",
            "pip",
            "check",
        ],
    }
    try:
        for name in (
            "gpu_probe",
            "runtime_probe",
            "mount_probe",
            "pip_freeze",
            "pip_check",
        ):
            capture = _execute_capture(probe_argv[name])
            create_external_json(
                capture_paths[name],
                capture,
                workspace_root=workspace,
                repository_root=artifact_repository_root,
            )
    except ArtifactIOError as error:
        raise CloudEnvironmentError(str(error)) from error

    evidence_paths = {"runpod_pod_record": provider_path, **capture_paths}
    evidence = {
        name: _reference_for_path(path, f"{name}_path")
        for name, path in evidence_paths.items()
    }
    evidence["runpod_pod_record"] = provider_reference
    captured_at_utc = _utc_text(_now_utc())
    captured = _utc(captured_at_utc, "captured_at_utc")
    if captured < started:
        raise CloudEnvironmentError("captured_at_utc precedes pod_started_at_utc")
    derived = _derive_evidence(evidence, started=started, captured=captured)
    if derived["pod_id"] != expected_pod_id:
        raise CloudEnvironmentError("control-plane evidence identifies a different Pod")
    payload = {
        "schema": CLOUD_ENVIRONMENT_SCHEMA,
        "provider": "runpod",
        "control_plane_capture_trust": CONTROL_PLANE_TRUST_LEVEL,
        "durable_reload_trust": DURABLE_RELOAD_TRUST_LEVEL,
        "pod_id": derived["pod_id"],
        "network_volume_id": derived["network_volume_id"],
        "workspace_root": RUNPOD_WORKSPACE_ROOT,
        "gpu": derived["gpu"],
        "runtime": derived["runtime"],
        "container_image": derived["container_image"],
        "persistent_mount": derived["persistent_mount"],
        "package_integrity": derived["package_integrity"],
        "evidence": evidence,
        "repository": {"path": cloud_repository_path},
        "pod_started_at_utc": started_text,
        "captured_at_utc": captured_at_utc,
        "passed": True,
    }
    validate_cloud_environment(payload)
    try:
        return create_external_json(
            output,
            payload,
            workspace_root=artifact_workspace_root,
            repository_root=artifact_repository_root,
        )
    except ArtifactIOError as error:
        raise CloudEnvironmentError(str(error)) from error


def load_cloud_environment(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Load strict JSON and revalidate every referenced evidence artifact."""

    source = Path(path)
    if source.is_symlink() or not source.is_file():
        raise CloudEnvironmentError(f"cloud environment must be one regular file: {source}")
    value = _strict_json_file(source, "cloud environment")
    return validate_cloud_environment(value)


__all__ = [
    "CLOUD_ENVIRONMENT_SCHEMA",
    "COMMAND_CAPTURE_SCHEMA",
    "CONTROL_PLANE_TRUST_LEVEL",
    "CUDA_RUNTIME",
    "DURABLE_RELOAD_TRUST_LEVEL",
    "GPU_QUERY_ARGUMENTS",
    "MOUNT_QUERY_ARGUMENTS",
    "PERSISTENT_MOUNT_PATH",
    "RTX_5090_MEMORY_BYTES",
    "RTX_5090_MIN_MEMORY_BYTES",
    "RTX_5090_NAME",
    "RUNPOD_CONTAINER_IMAGE_REFERENCE",
    "RUNPOD_CONTAINER_DISK_GB",
    "RUNPOD_DATA_CENTER_ID",
    "RUNPOD_MCP_CAPTURE_METHOD",
    "RUNPOD_MCP_CONTROL_PLANE_SCHEMA",
    "RUNPOD_NETWORK_VOLUME_ID",
    "RUNPOD_NETWORK_VOLUME_NAME",
    "RUNPOD_NETWORK_VOLUME_SIZE_GB",
    "RUNPOD_NETWORK_VOLUME_TYPE",
    "RUNPOD_ON_DEMAND_PRICE_PER_HR_USD",
    "RUNPOD_POD_RECORD_SCHEMA",
    "RUNPOD_POD_NAME",
    "RUNTIME_PROBE_CODE",
    "CloudEnvironmentError",
    "create_cloud_environment_manifest",
    "create_runpod_mcp_control_plane_evidence",
    "load_cloud_environment",
    "validate_cloud_environment",
]
