"""Create raw, replayable Jetson qualification evidence.

This module is deliberately an evidence *producer*, not a form for entering
benchmark results.  The production path executes TensorRT build/inspection,
captures the target identity before and after the run, and runs the complete
camera-to-event command while ``tegrastats`` is active.  Counts, reliability,
duration, and latency are derived exclusively from a nonce-bound per-frame
JSONL trace.

An injected runner exists only for focused tests.  Evidence produced through
that seam is permanently marked test-only and can never pass the production
qualification bit, even when all derived performance gates are healthy.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from .artifact_io import (
    ArtifactIOError,
    atomic_create_json,
    canonical_json_bytes,
    require_external_workspace,
    require_path_within_workspace,
    sha256_file,
)
from .contracts import MODEL_ID, PERSON_CLASS_MAP


JETSON_EVIDENCE_SCHEMA = "veriswarm.rescue.jetson_qualification_evidence.v1"
JETSON_FRAME_TRACE_SCHEMA = "veriswarm.rescue.jetson_frame_trace.v1"
JETSON_TELEMETRY_ENVELOPE_SCHEMA = "veriswarm.rescue.tegrastats_sample.v1"
JETSON_COMMAND_CAPTURE_SCHEMA = "veriswarm.rescue.jetson_command_capture.v1"

PRODUCTION_CAPTURE_MODE = "production_subprocess"
TEST_CAPTURE_MODE = "test_injected_runner"
JETSON_HARDWARE = "Jetson Orin Nano 8GB"
PRECISION = "FP16"

QUALIFICATION_DURATION_SECONDS = 900
QUALIFICATION_DURATION_NS = QUALIFICATION_DURATION_SECONDS * 1_000_000_000
TEGRASTATS_INTERVAL_MS = 1_000
MINIMUM_WARMUP_FRAMES = 50
MINIMUM_COMPLETED_FRAMES = 4_500
MINIMUM_PROCESSING_FPS = 5.0
MINIMUM_AVAILABLE_RAM_BYTES = 1024**3
MINIMUM_8GB_DEVICE_RAM_BYTES = 7 * 1024**3
MAXIMUM_8GB_DEVICE_RAM_BYTES = 9 * 1024**3
MAX_TENSORRT_P95_MS = 180.0
MAX_SELECTED_FRAME_AGE_P95_MS = 100.0
MAX_SELECTED_FRAME_AGE_MS = 250.0
MAX_CAMERA_TO_EVENT_P95_MS = 250.0
MAX_BAD_FRAME_RATIO = 0.01
MAX_TRACE_BOUNDARY_GAP_NS = 5_000_000_000

PIPELINE_STAGES = (
    "camera_decode",
    "preprocess",
    "tensorrt_fp16",
    "nms_tracking",
    "rescue_event_creation",
    "sqlite_outbox_enqueue",
    "model_receipt_bookkeeping",
)

_CANDIDATE_CONTRACTS: Mapping[str, tuple[str, tuple[int, int, int, int]]] = {
    "yolov8n-640": ("yolov8n.pt", (1, 3, 640, 640)),
    "yolov8n-960": ("yolov8n.pt", (1, 3, 960, 960)),
    "yolov8s-640": ("yolov8s.pt", (1, 3, 640, 640)),
}
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_ERROR_CODE = re.compile(r"[a-z0-9][a-z0-9_.-]{0,127}\Z")
_WATT_TOKEN = re.compile(
    r"(?<![A-Za-z0-9_])(\d+(?:\.\d+)?)\s*[Ww](?![A-Za-z0-9_])"
)
_MEMORY_TOKEN = re.compile(
    r"\b(RAM|SWAP)\s+(\d+(?:\.\d+)?)/(\d+(?:\.\d+)?)\s*([KMGT]?B)\b",
    re.IGNORECASE,
)
_GPU_TOKEN = re.compile(r"\bGR3D_FREQ\s+(\d+(?:\.\d+)?)%", re.IGNORECASE)
_CPU_TEMP_TOKEN = re.compile(r"\bCPU@(\d+(?:\.\d+)?)C\b", re.IGNORECASE)
_GPU_TEMP_TOKEN = re.compile(r"\bGPU@(\d+(?:\.\d+)?)C\b", re.IGNORECASE)
_POWER_TOKEN = re.compile(
    r"\bVDD_IN\s+(\d+(?:\.\d+)?)(mW|W)/(\d+(?:\.\d+)?)(mW|W)\b",
    re.IGNORECASE,
)
_OOM_CODES = frozenset(
    {"cuda_oom", "tensorrt_oom", "out_of_memory", "cuda_out_of_memory"}
)
_OOM_CODE_TOKEN = re.compile(r"(?:^|[._-])oom(?:$|[._-])")
_INTERPRETER_NAMES = frozenset(
    {"python", "python3", "bash", "sh", "node", "pwsh", "powershell"}
)

_SYSTEM_FILES = {
    "device_tree_model": Path("/proc/device-tree/model"),
    "nv_tegra_release": Path("/etc/nv_tegra_release"),
    "machine_id": Path("/etc/machine-id"),
    "boot_id": Path("/proc/sys/kernel/random/boot_id"),
}

_TRACE_TIMESTAMP_FIELDS = (
    "captured_monotonic_ns",
    "camera_decode_completed_monotonic_ns",
    "selected_monotonic_ns",
    "preprocess_completed_monotonic_ns",
    "tensorrt_started_monotonic_ns",
    "tensorrt_completed_monotonic_ns",
    "nms_tracking_completed_monotonic_ns",
    "rescue_event_creation_completed_monotonic_ns",
    "sqlite_outbox_enqueue_completed_monotonic_ns",
    "model_receipt_bookkeeping_completed_monotonic_ns",
)
_TRACE_FIELDS = {
    "schema",
    "run_nonce",
    "sequence",
    "frame_id",
    "source_timestamp_ns",
    "process_instance",
    "engine_sha256",
    "modality",
    "disposition",
    "warmup",
    "error_code",
    "timestamps",
    "event_count",
    "outbox_enqueue_count",
    "model_receipt_bookkeeping_count",
}
_DISPOSITIONS = frozenset(
    {"completed", "stale_replaced", "error_dropped", "invalid"}
)

_RESERVED_CAMERA_ARGUMENT_PREFIXES = (
    "--engine-path",
    "--engine-sha256",
    "--qualification-trace-jsonl",
    "--qualification-run-nonce",
    "--qualification-duration-seconds",
    "--qualification-source",
    "--newest-frame-policy",
    "--cloud-or-network-dependency",
    "--pipeline-stages",
    "--trace-create-once",
    "--passed",
    "--summary",
    "--metrics",
    "--completed-frames",
    "--fps",
    "--latency",
    "--errors",
    "--oom-events",
    "--process-restarts",
)


class JetsonEvidenceError(RuntimeError):
    """Raw evidence is missing, malformed, stale, or inconsistent."""


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Raw result returned by a Jetson command runner.

    Test doubles may construct this type, but merely injecting a runner marks
    the complete evidence bundle test-only.
    """

    argv: tuple[str, ...]
    returncode: int
    stdout: bytes
    stderr: bytes
    started_monotonic_ns: int
    completed_monotonic_ns: int


@dataclass(frozen=True, slots=True)
class PipelineArtifactPaths:
    """Fresh paths the camera/telemetry runner must create exactly once."""

    frame_trace_jsonl: Path
    tegrastats_jsonl: Path


@dataclass(frozen=True, slots=True)
class PipelineResult:
    """Raw process outcome; it intentionally contains no summary metrics."""

    camera_argv: tuple[str, ...]
    tegrastats_argv: tuple[str, ...]
    camera_returncode: int
    tegrastats_returncode: int
    camera_stdout: bytes
    camera_stderr: bytes
    tegrastats_stderr: bytes
    started_monotonic_ns: int
    completed_monotonic_ns: int
    collection_completed_monotonic_ns: int


@dataclass(frozen=True, slots=True)
class JetsonEvidenceResult:
    """Location and in-memory form of one create-once evidence report."""

    report_path: Path
    report: Mapping[str, Any]


class JetsonRunner(Protocol):
    """Execution surface used internally and by the explicit test-only seam."""

    def resolve_executable(self, name_or_path: str) -> Path: ...

    def read_bytes(self, path: Path) -> bytes: ...

    def run_command(
        self, argv: Sequence[str], *, timeout_seconds: int
    ) -> CommandResult: ...

    def run_camera_pipeline(
        self,
        camera_argv: Sequence[str],
        tegrastats_argv: Sequence[str],
        *,
        artifact_paths: PipelineArtifactPaths,
        timeout_seconds: int,
    ) -> PipelineResult: ...


class _SubprocessJetsonRunner:
    """Production implementation that invokes tools without a shell."""

    def resolve_executable(self, name_or_path: str) -> Path:
        candidate = Path(name_or_path).expanduser()
        located = str(candidate) if candidate.is_absolute() else shutil.which(name_or_path)
        if not located:
            raise JetsonEvidenceError(f"required executable is unavailable: {name_or_path}")
        try:
            resolved = Path(located).resolve(strict=True)
        except OSError as error:
            raise JetsonEvidenceError(
                f"cannot resolve executable {name_or_path!r}: {error}"
            ) from error
        if resolved.is_symlink() or not resolved.is_file():
            raise JetsonEvidenceError(f"executable is not one regular file: {resolved}")
        if os.name != "nt" and not os.access(resolved, os.X_OK):
            raise JetsonEvidenceError(f"executable is not executable: {resolved}")
        return resolved

    def read_bytes(self, path: Path) -> bytes:
        try:
            if path.is_symlink() or not path.is_file():
                raise JetsonEvidenceError(f"device trust source is not a file: {path}")
            return path.read_bytes()
        except OSError as error:
            raise JetsonEvidenceError(f"cannot read device trust source {path}: {error}") from error

    def run_command(
        self, argv: Sequence[str], *, timeout_seconds: int
    ) -> CommandResult:
        started = time.monotonic_ns()
        try:
            result = subprocess.run(
                list(argv),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                check=False,
                timeout=timeout_seconds,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise JetsonEvidenceError(
                f"command execution failed for {argv[0]!r}: {error}"
            ) from error
        completed = time.monotonic_ns()
        return CommandResult(
            tuple(str(item) for item in argv),
            result.returncode,
            bytes(result.stdout),
            bytes(result.stderr),
            started,
            completed,
        )

    def run_camera_pipeline(
        self,
        camera_argv: Sequence[str],
        tegrastats_argv: Sequence[str],
        *,
        artifact_paths: PipelineArtifactPaths,
        timeout_seconds: int,
    ) -> PipelineResult:
        for path in (
            artifact_paths.frame_trace_jsonl,
            artifact_paths.tegrastats_jsonl,
        ):
            if os.path.lexists(path):
                raise JetsonEvidenceError(f"refusing stale pipeline artifact: {path}")

        telemetry_process: subprocess.Popen[bytes] | None = None
        camera_process: subprocess.Popen[bytes] | None = None
        telemetry_stderr_parts: list[bytes] = []
        reader_errors: list[BaseException] = []
        telemetry_thread: threading.Thread | None = None
        stderr_thread: threading.Thread | None = None

        try:
            telemetry_process = subprocess.Popen(
                list(tegrastats_argv),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
            )
            assert telemetry_process.stdout is not None
            assert telemetry_process.stderr is not None

            def capture_telemetry() -> None:
                try:
                    with artifact_paths.tegrastats_jsonl.open("xb") as stream:
                        for raw_line in iter(telemetry_process.stdout.readline, b""):
                            line = raw_line.decode("utf-8", errors="strict").rstrip("\r\n")
                            if not line:
                                continue
                            envelope = {
                                "schema": JETSON_TELEMETRY_ENVELOPE_SCHEMA,
                                "monotonic_ns": time.monotonic_ns(),
                                "raw": line,
                            }
                            stream.write(
                                json.dumps(
                                    envelope,
                                    allow_nan=False,
                                    ensure_ascii=False,
                                    sort_keys=True,
                                    separators=(",", ":"),
                                ).encode("utf-8")
                                + b"\n"
                            )
                            stream.flush()
                        os.fsync(stream.fileno())
                except BaseException as error:  # relayed after process cleanup
                    reader_errors.append(error)

            def capture_telemetry_stderr() -> None:
                try:
                    while chunk := telemetry_process.stderr.read(64 * 1024):
                        telemetry_stderr_parts.append(bytes(chunk))
                except BaseException as error:  # relayed after process cleanup
                    reader_errors.append(error)

            telemetry_thread = threading.Thread(
                target=capture_telemetry, name="veriswarm-tegrastats-reader", daemon=True
            )
            stderr_thread = threading.Thread(
                target=capture_telemetry_stderr,
                name="veriswarm-tegrastats-stderr-reader",
                daemon=True,
            )
            telemetry_thread.start()
            stderr_thread.start()

            started = time.monotonic_ns()
            camera_process = subprocess.Popen(
                list(camera_argv),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
            )
            try:
                camera_stdout, camera_stderr = camera_process.communicate(
                    timeout=timeout_seconds
                )
            except subprocess.TimeoutExpired:
                camera_process.kill()
                camera_stdout, camera_stderr = camera_process.communicate()
                camera_stderr += b"\nqualification runner timeout"
            completed = time.monotonic_ns()
        except OSError as error:
            raise JetsonEvidenceError(f"camera-to-event execution failed: {error}") from error
        finally:
            if camera_process is not None and camera_process.poll() is None:
                camera_process.kill()
                camera_process.wait()
            if telemetry_process is not None and telemetry_process.poll() is None:
                telemetry_process.terminate()
                try:
                    telemetry_process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    telemetry_process.kill()
                    telemetry_process.wait()
            if telemetry_thread is not None:
                telemetry_thread.join(timeout=10)
            if stderr_thread is not None:
                stderr_thread.join(timeout=10)

        collection_completed = time.monotonic_ns()
        if reader_errors:
            raise JetsonEvidenceError(
                f"tegrastats capture failed: {reader_errors[0]}"
            ) from reader_errors[0]
        if telemetry_thread is not None and telemetry_thread.is_alive():
            raise JetsonEvidenceError("tegrastats stdout reader did not stop")
        if stderr_thread is not None and stderr_thread.is_alive():
            raise JetsonEvidenceError("tegrastats stderr reader did not stop")
        assert camera_process is not None
        assert telemetry_process is not None
        return PipelineResult(
            tuple(str(item) for item in camera_argv),
            tuple(str(item) for item in tegrastats_argv),
            int(camera_process.returncode),
            int(telemetry_process.returncode),
            bytes(camera_stdout),
            bytes(camera_stderr),
            b"".join(telemetry_stderr_parts),
            started,
            completed,
            collection_completed,
        )


def _integer(value: Any, field: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise JetsonEvidenceError(f"{field} must be an integer >= {minimum}")
    return value


def _number(
    value: Any, field: str, *, minimum: float = 0.0, maximum: float | None = None
) -> float:
    if isinstance(value, bool) or type(value) not in {int, float}:
        raise JetsonEvidenceError(f"{field} must be a finite number")
    result = float(value)
    if not math.isfinite(result) or result < minimum:
        raise JetsonEvidenceError(f"{field} is outside its permitted range")
    if maximum is not None and result > maximum:
        raise JetsonEvidenceError(f"{field} is outside its permitted range")
    return result


def _text(value: Any, field: str) -> str:
    if type(value) is not str or not value or value.strip() != value:
        raise JetsonEvidenceError(f"{field} must be non-empty trimmed text")
    if "\x00" in value or "\r" in value or "\n" in value:
        raise JetsonEvidenceError(f"{field} must be single-line text")
    return value


def _hash(value: Any, field: str) -> str:
    digest = _text(value, field)
    if _SHA256.fullmatch(digest) is None:
        raise JetsonEvidenceError(f"{field} must be a lowercase SHA-256")
    return digest


def _object(value: Any, field: str, fields: set[str]) -> dict[str, Any]:
    if type(value) is not dict or not all(type(key) is str for key in value):
        raise JetsonEvidenceError(f"{field} must be a JSON object")
    if set(value) != fields:
        raise JetsonEvidenceError(
            f"{field} fields differ: missing={sorted(fields - set(value))}, "
            f"unknown={sorted(set(value) - fields)}"
        )
    return value


def _reject_constant(value: str) -> None:
    raise JetsonEvidenceError(f"non-finite JSON constant is forbidden: {value}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise JetsonEvidenceError(f"duplicate JSON key is forbidden: {key!r}")
        result[key] = value
    return result


def _decode(raw: bytes, field: str) -> str:
    if type(raw) is not bytes:
        raise JetsonEvidenceError(f"{field} must be raw bytes")
    try:
        return raw.decode("utf-8", errors="strict")
    except UnicodeError as error:
        raise JetsonEvidenceError(f"{field} is not strict UTF-8: {error}") from error


def _create_bytes_once(path: Path, data: bytes) -> Path:
    if not path.is_absolute():
        raise JetsonEvidenceError("raw evidence path must be absolute")
    if type(data) is not bytes:
        raise JetsonEvidenceError("raw evidence payload must be bytes")
    if os.path.lexists(path):
        raise JetsonEvidenceError(f"refusing to overwrite raw evidence: {path}")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError as error:
        raise JetsonEvidenceError(f"refusing to overwrite raw evidence: {path}") from error
    except OSError as error:
        raise JetsonEvidenceError(f"cannot create raw evidence {path}: {error}") from error
    return path


def _regular_file(path: Path, field: str, *, allow_empty: bool = False) -> Path:
    expanded = path.expanduser()
    if expanded.is_symlink():
        raise JetsonEvidenceError(f"{field} must not be a symbolic link")
    try:
        resolved = expanded.resolve(strict=True)
    except OSError as error:
        raise JetsonEvidenceError(f"cannot resolve {field}: {error}") from error
    if resolved.is_symlink() or not resolved.is_file():
        raise JetsonEvidenceError(f"{field} must be one regular file")
    if not allow_empty and resolved.stat().st_size <= 0:
        raise JetsonEvidenceError(f"{field} must be non-empty")
    return resolved


def _reject_git_containment(path: Path, field: str) -> None:
    """Reject an artifact path below any discoverable Git worktree."""

    resolved = path.resolve(strict=False)
    for directory in (resolved, *resolved.parents):
        if (directory / ".git").exists():
            raise JetsonEvidenceError(f"{field} must remain outside every Git worktree")


def _artifact(path: Path, field: str, *, allow_empty: bool = False) -> dict[str, Any]:
    resolved = _regular_file(path, field, allow_empty=allow_empty)
    try:
        digest = sha256_file(resolved)
    except ArtifactIOError as error:
        raise JetsonEvidenceError(str(error)) from error
    return {
        "path": str(resolved),
        "sha256": digest,
        "bytes": resolved.stat().st_size,
    }


def _make_read_only(path: Path) -> None:
    try:
        path.chmod(path.stat().st_mode & ~0o222)
    except OSError as error:
        raise JetsonEvidenceError(f"cannot make evidence read-only {path}: {error}") from error


def _validate_command_result(
    result: Any, expected_argv: tuple[str, ...], field: str
) -> CommandResult:
    if not isinstance(result, CommandResult):
        raise JetsonEvidenceError(f"{field} runner returned the wrong result type")
    if result.argv != expected_argv:
        raise JetsonEvidenceError(f"{field} runner executed different argv")
    if type(result.returncode) is not int:
        raise JetsonEvidenceError(f"{field} return code must be an integer")
    if type(result.stdout) is not bytes or type(result.stderr) is not bytes:
        raise JetsonEvidenceError(f"{field} output must be raw bytes")
    started = _integer(result.started_monotonic_ns, f"{field}.started", minimum=1)
    completed = _integer(result.completed_monotonic_ns, f"{field}.completed", minimum=1)
    if completed < started:
        raise JetsonEvidenceError(f"{field} monotonic interval is reversed")
    return result


def _run_and_capture(
    runner: JetsonRunner,
    argv: tuple[str, ...],
    *,
    name: str,
    directory: Path,
    capture_mode: str,
    timeout_seconds: int,
) -> tuple[CommandResult, dict[str, Any]]:
    result = _validate_command_result(
        runner.run_command(argv, timeout_seconds=timeout_seconds), argv, name
    )
    stdout_path = _create_bytes_once(directory / f"{name}.stdout.log", result.stdout)
    stderr_path = _create_bytes_once(directory / f"{name}.stderr.log", result.stderr)
    executable = _regular_file(Path(argv[0]), f"{name} executable")
    capture = {
        "schema": JETSON_COMMAND_CAPTURE_SCHEMA,
        "capture_mode": capture_mode,
        "test_only": capture_mode == TEST_CAPTURE_MODE,
        "argv": list(argv),
        "executable": _artifact(executable, f"{name} executable"),
        "exit_code": result.returncode,
        "started_monotonic_ns": result.started_monotonic_ns,
        "completed_monotonic_ns": result.completed_monotonic_ns,
        "stdout": _artifact(stdout_path, f"{name} stdout", allow_empty=True),
        "stderr": _artifact(stderr_path, f"{name} stderr", allow_empty=True),
    }
    capture_path = directory / f"{name}.capture.json"
    try:
        atomic_create_json(capture_path, capture)
    except ArtifactIOError as error:
        raise JetsonEvidenceError(str(error)) from error
    return result, _artifact(capture_path, f"{name} command capture")


def _clean_device_text(raw: bytes, field: str) -> str:
    text = _decode(raw, field).strip("\x00\r\n \t")
    if not text:
        raise JetsonEvidenceError(f"{field} is empty")
    return text


def _device_snapshot(
    runner: JetsonRunner,
    *,
    phase: str,
    directory: Path,
    capture_mode: str,
    trtexec: Path,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    raw_files: dict[str, bytes] = {}
    file_artifacts: dict[str, dict[str, Any]] = {}
    for name, path in _SYSTEM_FILES.items():
        raw = runner.read_bytes(path)
        if type(raw) is not bytes or not raw:
            raise JetsonEvidenceError(f"{phase} {name} source must return non-empty bytes")
        raw_files[name] = raw
        if name in {"device_tree_model", "nv_tegra_release"}:
            stored = _create_bytes_once(directory / f"{phase}.{name}.raw", raw)
            file_artifacts[f"{phase}_{name}"] = _artifact(
                stored, f"{phase} {name} raw"
            )

    command_specs = {
        "uname": (str(runner.resolve_executable("uname")), "-a"),
        "architecture": (str(runner.resolve_executable("uname")), "-m"),
        "nvpmodel": (str(runner.resolve_executable("nvpmodel")), "-q"),
        "package_runtime": (
            str(runner.resolve_executable("dpkg-query")),
            "-W",
            "-f=${binary:Package}=${Version}\\n",
        ),
        "trtexec_version": (str(trtexec), "--version"),
    }
    results: dict[str, CommandResult] = {}
    for name, argv in command_specs.items():
        result, reference = _run_and_capture(
            runner,
            tuple(argv),
            name=f"{phase}.{name}",
            directory=directory,
            capture_mode=capture_mode,
            timeout_seconds=120,
        )
        if result.returncode != 0:
            raise JetsonEvidenceError(f"{phase} {name} probe failed")
        results[name] = result
        file_artifacts[f"{phase}_{name}_capture"] = reference

    model = _clean_device_text(raw_files["device_tree_model"], f"{phase} device model")
    release = _clean_device_text(raw_files["nv_tegra_release"], f"{phase} release")
    machine_id = _clean_device_text(raw_files["machine_id"], f"{phase} machine ID")
    boot_id = _clean_device_text(raw_files["boot_id"], f"{phase} boot ID")
    uname = _clean_device_text(results["uname"].stdout, f"{phase} uname")
    architecture = _clean_device_text(
        results["architecture"].stdout, f"{phase} architecture"
    )
    nvpmodel = _clean_device_text(
        results["nvpmodel"].stdout + results["nvpmodel"].stderr,
        f"{phase} nvpmodel",
    )
    trtexec_version = _clean_device_text(
        results["trtexec_version"].stdout + results["trtexec_version"].stderr,
        f"{phase} trtexec version",
    )
    package_runtime = _clean_device_text(
        results["package_runtime"].stdout,
        f"{phase} package runtime",
    )
    if "Jetson Orin Nano" not in model:
        raise JetsonEvidenceError("device-tree model is not a Jetson Orin Nano")
    if architecture != "aarch64" or "aarch64" not in uname:
        raise JetsonEvidenceError("qualification target must report aarch64")
    if not release.startswith("# R"):
        raise JetsonEvidenceError("nv_tegra_release is malformed")
    watts = [float(value) for value in _WATT_TOKEN.findall(nvpmodel)]
    if watts != [15.0]:
        raise JetsonEvidenceError("nvpmodel must report exactly one standalone 15W token")
    if "tensorrt" not in trtexec_version.lower():
        raise JetsonEvidenceError("trtexec version output does not identify TensorRT")
    package_versions: dict[str, str] = {}
    for line in package_runtime.splitlines():
        if "=" not in line:
            raise JetsonEvidenceError("dpkg-query runtime output is malformed")
        name, version = line.split("=", 1)
        if not name or not version or name in package_versions:
            raise JetsonEvidenceError("dpkg-query runtime output is malformed or duplicated")
        package_versions[name] = version
    required_package_groups = {
        "l4t": ("nvidia-l4t-core",),
        "tensorrt": ("libnvinfer", "tensorrt"),
        "cudnn": ("libcudnn",),
        "cuda": ("cuda-cudart", "cuda-toolkit", "cuda-compat"),
    }
    selected_packages: dict[str, dict[str, str]] = {}
    for group, prefixes in required_package_groups.items():
        matches = {
            name: version
            for name, version in package_versions.items()
            if any(name.startswith(prefix) for prefix in prefixes)
        }
        if not matches:
            raise JetsonEvidenceError(
                f"dpkg-query runtime output lacks the {group} package group"
            )
        selected_packages[group] = dict(sorted(matches.items()))

    normalized = {
        "hardware": JETSON_HARDWARE,
        "device_tree_model": model,
        "nv_tegra_release": release,
        "machine_id_sha256": hashlib.sha256(raw_files["machine_id"]).hexdigest(),
        "boot_id_sha256": hashlib.sha256(raw_files["boot_id"]).hexdigest(),
        "uname": uname,
        "architecture": architecture,
        "nvpmodel_output": nvpmodel,
        "trtexec_version_output": trtexec_version,
        "trtexec_sha256": sha256_file(trtexec),
        "runtime_packages": selected_packages,
        "package_runtime_sha256": hashlib.sha256(
            results["package_runtime"].stdout
        ).hexdigest(),
    }
    normalized["trust_anchor_sha256"] = hashlib.sha256(
        canonical_json_bytes(normalized)
    ).hexdigest()
    return normalized, file_artifacts


def _parse_trtexec_log(result: CommandResult, *, phase: str) -> dict[str, Any]:
    text = _decode(result.stdout + b"\n" + result.stderr, f"trtexec {phase} log")
    lowered = text.lower()
    if result.returncode != 0:
        raise JetsonEvidenceError(f"trtexec {phase} returned {result.returncode}")
    if re.search(r"&&&&\s+failed\s+tensorrt\.trtexec", text, re.IGNORECASE):
        raise JetsonEvidenceError(f"trtexec {phase} emitted a failure marker")
    if re.search(r"&&&&\s+passed\s+tensorrt\.trtexec", text, re.IGNORECASE) is None:
        raise JetsonEvidenceError(f"trtexec {phase} lacks its success marker")
    if phase == "build":
        if "fp16" not in lowered:
            raise JetsonEvidenceError("trtexec build log does not bind FP16")
        if not ("engine built" in lowered or "serialized engine" in lowered):
            raise JetsonEvidenceError("trtexec build log lacks engine serialization evidence")
    elif phase == "inspection":
        if not ("engine loaded" in lowered or "deserializ" in lowered):
            raise JetsonEvidenceError("trtexec inspection log lacks engine-load evidence")
    else:  # defensive internal contract
        raise JetsonEvidenceError(f"unknown trtexec phase: {phase}")
    versions = re.findall(
        r"TensorRT(?:\s+version|\s+v)?\s*[:=]?\s*(\d+(?:\.\d+){1,3})",
        text,
        re.IGNORECASE,
    )
    return {
        "success_marker": "TensorRT.trtexec",
        "phase": phase,
        "tensorrt_versions": sorted(set(versions)),
        "log_sha256": hashlib.sha256(result.stdout + b"\n" + result.stderr).hexdigest(),
    }


def _shape_value(value: Any) -> tuple[int, int, int, int] | None:
    if type(value) is list and len(value) == 4 and all(
        type(item) is int and item > 0 for item in value
    ):
        return tuple(value)  # type: ignore[return-value]
    if type(value) is str:
        numbers = [int(item) for item in re.findall(r"\d+", value)]
        if len(numbers) == 4 and all(number > 0 for number in numbers):
            return tuple(numbers)  # type: ignore[return-value]
    return None


def _walk_json(value: Any):
    yield value
    if type(value) is dict:
        for nested in value.values():
            yield from _walk_json(nested)
    elif type(value) is list:
        for nested in value:
            yield from _walk_json(nested)


def _inspect_layer_information(
    path: Path, expected_shape: tuple[int, int, int, int]
) -> dict[str, Any]:
    raw = _regular_file(path, "trtexec layer information").read_bytes()
    try:
        parsed = json.loads(
            _decode(raw, "trtexec layer information"),
            parse_constant=_reject_constant,
            object_pairs_hook=_unique_object,
        )
    except (json.JSONDecodeError, JetsonEvidenceError) as error:
        raise JetsonEvidenceError(f"trtexec layer information is invalid: {error}") from error
    input_shapes: set[tuple[int, int, int, int]] = set()
    strings: list[str] = []
    for node in _walk_json(parsed):
        if type(node) is str:
            strings.append(node)
        if type(node) is not dict:
            continue
        name = node.get("Name", node.get("name"))
        if name != "images":
            continue
        for key in ("Dimensions", "dimensions", "Shape", "shape"):
            if key in node:
                shape = _shape_value(node[key])
                if shape is not None:
                    input_shapes.add(shape)
    if input_shapes != {expected_shape}:
        raise JetsonEvidenceError(
            "engine inspection does not prove the exact static 'images' binding shape"
        )
    rendered_strings = " ".join(strings).lower()
    if "fp16" not in rendered_strings and "half" not in rendered_strings:
        raise JetsonEvidenceError("engine layer information contains no FP16 binding")
    return {
        "input_name": "images",
        "input_shape_nchw": list(expected_shape),
        "precision_evidence": "FP16",
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def _json_line(line: str, field: str) -> dict[str, Any]:
    try:
        parsed = json.loads(
            line,
            parse_constant=_reject_constant,
            object_pairs_hook=_unique_object,
        )
    except (json.JSONDecodeError, JetsonEvidenceError) as error:
        raise JetsonEvidenceError(f"{field} is invalid JSON: {error}") from error
    if type(parsed) is not dict:
        raise JetsonEvidenceError(f"{field} must be a JSON object")
    return parsed


def _nearest_rank(values: Sequence[float], percentile: float) -> float:
    if not values:
        raise JetsonEvidenceError("latency series must not be empty")
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return float(ordered[rank - 1])


def _latency_summary(values: Sequence[float]) -> dict[str, float]:
    return {
        "p50": _nearest_rank(values, 0.50),
        "p95": _nearest_rank(values, 0.95),
        "p99": _nearest_rank(values, 0.99),
        "max": float(max(values)),
    }


def _timestamp_prefix(
    timestamps: dict[str, Any], field: str
) -> tuple[int, list[int | None]]:
    parsed: list[int | None] = []
    seen_null = False
    for name in _TRACE_TIMESTAMP_FIELDS:
        value = timestamps[name]
        if value is None:
            seen_null = True
            parsed.append(None)
            continue
        number = _integer(value, f"{field}.{name}", minimum=1)
        if seen_null:
            raise JetsonEvidenceError(f"{field} stage timestamps must form one prefix")
        parsed.append(number)
    captured = parsed[0]
    assert captured is not None
    non_null = [number for number in parsed if number is not None]
    if non_null != sorted(non_null):
        raise JetsonEvidenceError(f"{field} stage timestamps are out of order")
    return captured, parsed


def _inspect_frame_trace(
    path: Path,
    *,
    nonce: str,
    engine_sha256: str,
    benchmark_start_ns: int,
    benchmark_end_ns: int,
) -> dict[str, Any]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise JetsonEvidenceError(f"cannot read frame trace: {error}") from error
    if not lines or any(not line.strip() or line.strip() != line for line in lines):
        raise JetsonEvidenceError("frame trace must contain non-empty trimmed JSONL records")

    counts = {name: 0 for name in sorted(_DISPOSITIONS)}
    warmups = 0
    events = 0
    outbox = 0
    receipts = 0
    errors = 0
    oom_events = 0
    frame_ids: set[str] = set()
    prior_capture = 0
    prior_source_timestamp = -1
    prior_process_instance = 0
    selected_ages: list[float] = []
    tensorrt_latencies: list[float] = []
    camera_to_event: list[float] = []
    camera_to_bookkeeping: list[float] = []
    first_capture: int | None = None
    last_terminal = 0

    for index, line in enumerate(lines):
        record = _object(_json_line(line, f"frame trace line {index}"), f"frame trace line {index}", _TRACE_FIELDS)
        if record["schema"] != JETSON_FRAME_TRACE_SCHEMA:
            raise JetsonEvidenceError(f"frame trace line {index} uses the wrong schema")
        if record["run_nonce"] != nonce:
            raise JetsonEvidenceError(f"frame trace line {index} is stale or nonce-mismatched")
        if _integer(record["sequence"], f"frame trace line {index}.sequence") != index:
            raise JetsonEvidenceError("frame trace sequence must be contiguous from zero")
        frame_id = _text(record["frame_id"], f"frame trace line {index}.frame_id")
        if frame_id in frame_ids:
            raise JetsonEvidenceError(f"duplicate frame ID in trace: {frame_id}")
        frame_ids.add(frame_id)
        source_timestamp = _integer(
            record["source_timestamp_ns"],
            f"frame trace line {index}.source_timestamp_ns",
        )
        if source_timestamp < prior_source_timestamp:
            raise JetsonEvidenceError("source timestamps must be nondecreasing")
        prior_source_timestamp = source_timestamp
        process_instance = _integer(
            record["process_instance"],
            f"frame trace line {index}.process_instance",
        )
        if index == 0 and process_instance != 0:
            raise JetsonEvidenceError("first process instance must be zero")
        if process_instance not in {prior_process_instance, prior_process_instance + 1}:
            raise JetsonEvidenceError("process instances must be contiguous and monotonic")
        prior_process_instance = process_instance
        if _hash(record["engine_sha256"], f"frame trace line {index}.engine_sha256") != engine_sha256:
            raise JetsonEvidenceError("frame trace executed a different TensorRT engine")
        if record["modality"] != "rgb":
            raise JetsonEvidenceError("Jetson frame modality must be exactly 'rgb'")
        disposition = record["disposition"]
        if disposition not in _DISPOSITIONS:
            raise JetsonEvidenceError(f"unknown frame disposition: {disposition!r}")
        warmup = record["warmup"]
        if type(warmup) is not bool:
            raise JetsonEvidenceError(f"frame trace line {index}.warmup must be boolean")
        if warmup and disposition != "completed":
            raise JetsonEvidenceError("only completed frames may be warmups")
        error_code = record["error_code"]
        if disposition in {"error_dropped", "invalid"}:
            if type(error_code) is not str or _ERROR_CODE.fullmatch(error_code) is None:
                raise JetsonEvidenceError("failed frames require a normalized error code")
            errors += 1
            oom_events += int(
                error_code in _OOM_CODES
                or "out_of_memory" in error_code
                or _OOM_CODE_TOKEN.search(error_code) is not None
            )
        elif error_code is not None:
            raise JetsonEvidenceError("successful/stale frames cannot carry an error code")

        timestamps = _object(
            record["timestamps"],
            f"frame trace line {index}.timestamps",
            set(_TRACE_TIMESTAMP_FIELDS),
        )
        captured, parsed = _timestamp_prefix(
            timestamps, f"frame trace line {index}.timestamps"
        )
        if captured <= prior_capture:
            raise JetsonEvidenceError("frame capture timestamps must strictly increase")
        if captured < benchmark_start_ns or captured > benchmark_end_ns:
            raise JetsonEvidenceError("frame timestamp falls outside executed benchmark")
        prior_capture = captured
        first_capture = captured if first_capture is None else first_capture
        for timestamp in parsed:
            if timestamp is not None and not benchmark_start_ns <= timestamp <= benchmark_end_ns:
                raise JetsonEvidenceError("frame stage timestamp falls outside benchmark")
        last_terminal = max(last_terminal, max(value for value in parsed if value is not None))

        event_count = _integer(
            record["event_count"], f"frame trace line {index}.event_count"
        )
        enqueue_count = _integer(
            record["outbox_enqueue_count"],
            f"frame trace line {index}.outbox_enqueue_count",
        )
        receipt_count = _integer(
            record["model_receipt_bookkeeping_count"],
            f"frame trace line {index}.model_receipt_bookkeeping_count",
        )
        if not event_count == enqueue_count == receipt_count:
            raise JetsonEvidenceError("every frame event must be enqueued and bookkept exactly once")
        if disposition != "completed" and any(
            value != 0 for value in (event_count, enqueue_count, receipt_count)
        ):
            raise JetsonEvidenceError("non-completed frames cannot emit rescue events")
        if warmup and event_count:
            raise JetsonEvidenceError("warmup frames cannot emit rescue events")
        if disposition == "completed" and any(value is None for value in parsed):
            raise JetsonEvidenceError("completed frames require every pipeline stage timestamp")

        counts[disposition] += 1
        warmups += int(warmup)
        events += event_count
        outbox += enqueue_count
        receipts += receipt_count
        if disposition == "completed" and not warmup:
            concrete = [value for value in parsed if value is not None]
            assert len(concrete) == len(_TRACE_TIMESTAMP_FIELDS)
            selected_ages.append((concrete[2] - concrete[0]) / 1_000_000)
            tensorrt_latencies.append((concrete[5] - concrete[4]) / 1_000_000)
            camera_to_event.append((concrete[7] - concrete[0]) / 1_000_000)
            camera_to_bookkeeping.append((concrete[9] - concrete[0]) / 1_000_000)

    if not selected_ages or first_capture is None:
        raise JetsonEvidenceError("trace contains no measured completed frames")
    if not (events == outbox == receipts):
        raise JetsonEvidenceError("trace event totals are internally inconsistent")
    return {
        "integrity": {
            "schema": JETSON_FRAME_TRACE_SCHEMA,
            "run_nonce_sha256": hashlib.sha256(nonce.encode("ascii")).hexdigest(),
            "record_count": len(lines),
            "first_capture_monotonic_ns": first_capture,
            "last_terminal_monotonic_ns": last_terminal,
            "trace_span_seconds": (last_terminal - first_capture) / 1_000_000_000,
            "sha256": sha256_file(path),
        },
        "counts": {
            "total_input_frames": len(lines),
            "completed_frames": counts["completed"],
            "measured_completed_frames": counts["completed"] - warmups,
            "warmup_frames": warmups,
            "stale_replaced_frames": counts["stale_replaced"],
            "error_dropped_frames": counts["error_dropped"],
            "invalid_frames": counts["invalid"],
            "event_count": events,
            "outbox_enqueue_count": outbox,
            "model_receipt_bookkeeping_count": receipts,
        },
        "reliability": {
            "errors": errors,
            "oom_events": oom_events,
            "process_restarts": prior_process_instance,
        },
        "latency_ms": {
            "method": "nearest_rank_excluding_warmup",
            "selected_frame_age": _latency_summary(selected_ages),
            "tensorrt": _latency_summary(tensorrt_latencies),
            "camera_to_event": _latency_summary(camera_to_event),
            "camera_to_bookkeeping": _latency_summary(camera_to_bookkeeping),
        },
    }


def _unit_bytes(value: float, unit: str) -> int:
    factors = {
        "KB": 1024,
        "MB": 1024**2,
        "GB": 1024**3,
        "TB": 1024**4,
    }
    normalized = unit.upper()
    if normalized not in factors:
        raise JetsonEvidenceError(f"unsupported tegrastats memory unit: {unit}")
    return int(value * factors[normalized])


def _power_w(value: float, unit: str) -> float:
    return value / 1000.0 if unit.lower() == "mw" else value


def _parse_tegrastats_line(line: str, field: str) -> dict[str, Any]:
    memory: dict[str, tuple[int, int]] = {}
    for name, used, total, unit in _MEMORY_TOKEN.findall(line):
        normalized = name.upper()
        if normalized in memory:
            raise JetsonEvidenceError(f"{field} duplicates {normalized}")
        memory[normalized] = (
            _unit_bytes(float(used), unit),
            _unit_bytes(float(total), unit),
        )
    gpu = _GPU_TOKEN.search(line)
    cpu_temp = _CPU_TEMP_TOKEN.search(line)
    gpu_temp = _GPU_TEMP_TOKEN.search(line)
    power = _POWER_TOKEN.search(line)
    if set(memory) != {"RAM", "SWAP"} or not all(
        (gpu, cpu_temp, gpu_temp, power)
    ):
        raise JetsonEvidenceError(
            f"{field} lacks RAM/SWAP/GR3D/CPU/GPU/VDD_IN telemetry"
        )
    ram_used, ram_total = memory["RAM"]
    swap_used, swap_total = memory["SWAP"]
    if ram_used > ram_total or swap_used > swap_total:
        raise JetsonEvidenceError(f"{field} reports impossible memory usage")
    assert gpu is not None and cpu_temp is not None and gpu_temp is not None
    assert power is not None
    gpu_percent = _number(float(gpu.group(1)), f"{field}.gpu", maximum=100.0)
    return {
        "ram_used_bytes": ram_used,
        "ram_total_bytes": ram_total,
        "available_ram_bytes": ram_total - ram_used,
        "swap_used_bytes": swap_used,
        "swap_total_bytes": swap_total,
        "gpu_utilization_percent": gpu_percent,
        "cpu_temperature_c": _number(float(cpu_temp.group(1)), f"{field}.cpu_temp"),
        "gpu_temperature_c": _number(float(gpu_temp.group(1)), f"{field}.gpu_temp"),
        "power_w": _power_w(float(power.group(1)), power.group(2)),
    }


def _inspect_tegrastats(
    path: Path, *, benchmark_start_ns: int, benchmark_end_ns: int
) -> dict[str, Any]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise JetsonEvidenceError(f"cannot read tegrastats log: {error}") from error
    if not lines or any(not line.strip() or line.strip() != line for line in lines):
        raise JetsonEvidenceError("tegrastats evidence must be non-empty trimmed JSONL")
    samples: list[dict[str, Any]] = []
    timestamps: list[int] = []
    for index, line in enumerate(lines):
        envelope = _object(
            _json_line(line, f"tegrastats line {index}"),
            f"tegrastats line {index}",
            {"schema", "monotonic_ns", "raw"},
        )
        if envelope["schema"] != JETSON_TELEMETRY_ENVELOPE_SCHEMA:
            raise JetsonEvidenceError("tegrastats envelope uses the wrong schema")
        timestamp = _integer(
            envelope["monotonic_ns"], f"tegrastats line {index}.monotonic_ns", minimum=1
        )
        raw = _text(envelope["raw"], f"tegrastats line {index}.raw")
        timestamps.append(timestamp)
        samples.append(_parse_tegrastats_line(raw, f"tegrastats line {index}"))
    if timestamps != sorted(set(timestamps)):
        raise JetsonEvidenceError("tegrastats sample timestamps must strictly increase")
    relevant = [
        sample
        for timestamp, sample in zip(timestamps, samples)
        if benchmark_start_ns <= timestamp <= benchmark_end_ns
    ]
    relevant_times = [
        timestamp
        for timestamp in timestamps
        if benchmark_start_ns <= timestamp <= benchmark_end_ns
    ]
    if len(relevant) < 2:
        raise JetsonEvidenceError("tegrastats has fewer than two in-run samples")
    ram_totals = {sample["ram_total_bytes"] for sample in relevant}
    swap_totals = {sample["swap_total_bytes"] for sample in relevant}
    if len(ram_totals) != 1 or len(swap_totals) != 1:
        raise JetsonEvidenceError("tegrastats total RAM/SWAP changed during the run")
    gpu_values = [sample["gpu_utilization_percent"] for sample in relevant]
    power_values = [sample["power_w"] for sample in relevant]
    return {
        "integrity": {
            "schema": JETSON_TELEMETRY_ENVELOPE_SCHEMA,
            "sample_count": len(relevant),
            "first_monotonic_ns": relevant_times[0],
            "last_monotonic_ns": relevant_times[-1],
            "sha256": sha256_file(path),
        },
        "resources": {
            "ram_total_bytes": next(iter(ram_totals)),
            "ram_used_peak_bytes": max(sample["ram_used_bytes"] for sample in relevant),
            "min_available_ram_bytes": min(
                sample["available_ram_bytes"] for sample in relevant
            ),
            "swap_total_bytes": next(iter(swap_totals)),
            "swap_start_bytes": relevant[0]["swap_used_bytes"],
            "swap_peak_bytes": max(sample["swap_used_bytes"] for sample in relevant),
            "swap_end_bytes": relevant[-1]["swap_used_bytes"],
            "gpu_utilization_percent": _latency_summary(gpu_values),
            "max_cpu_temperature_c": max(
                sample["cpu_temperature_c"] for sample in relevant
            ),
            "max_gpu_temperature_c": max(
                sample["gpu_temperature_c"] for sample in relevant
            ),
            "power_min_w": min(power_values),
            "power_mean_w": sum(power_values) / len(power_values),
            "power_max_w": max(power_values),
        },
    }


def _pipeline_result(
    value: Any,
    *,
    expected_camera_argv: tuple[str, ...],
    expected_tegrastats_argv: tuple[str, ...],
) -> PipelineResult:
    if not isinstance(value, PipelineResult):
        raise JetsonEvidenceError("camera pipeline runner returned the wrong result type")
    if value.camera_argv != expected_camera_argv:
        raise JetsonEvidenceError("camera runner executed different argv")
    if value.tegrastats_argv != expected_tegrastats_argv:
        raise JetsonEvidenceError("tegrastats runner executed different argv")
    for field, result in (
        ("camera_returncode", value.camera_returncode),
        ("tegrastats_returncode", value.tegrastats_returncode),
    ):
        if type(result) is not int:
            raise JetsonEvidenceError(f"{field} must be an integer")
    for field, raw in (
        ("camera_stdout", value.camera_stdout),
        ("camera_stderr", value.camera_stderr),
        ("tegrastats_stderr", value.tegrastats_stderr),
    ):
        if type(raw) is not bytes:
            raise JetsonEvidenceError(f"{field} must be raw bytes")
    started = _integer(value.started_monotonic_ns, "pipeline.started", minimum=1)
    completed = _integer(value.completed_monotonic_ns, "pipeline.completed", minimum=1)
    collection = _integer(
        value.collection_completed_monotonic_ns,
        "pipeline.collection_completed",
        minimum=1,
    )
    if not started < completed <= collection:
        raise JetsonEvidenceError("camera pipeline monotonic interval is invalid")
    return value


def _argument_file_hashes(argv: Sequence[str]) -> list[dict[str, Any]]:
    references: dict[Path, dict[str, Any]] = {}
    for index, argument in enumerate(argv):
        candidates = [argument]
        if "=" in argument:
            candidates.append(argument.split("=", 1)[1])
        for candidate in candidates:
            path = Path(candidate).expanduser()
            if not path.is_absolute() or not path.exists():
                continue
            resolved = _regular_file(path, f"camera argv[{index}] file")
            references.setdefault(
                resolved,
                {
                    "argv_index": index,
                    "path": str(resolved),
                    "sha256": sha256_file(resolved),
                    "bytes": resolved.stat().st_size,
                },
            )
    return [references[path] for path in sorted(references, key=lambda item: str(item))]


def _qualification_failures(
    *,
    test_only: bool,
    pipeline: PipelineResult,
    duration_seconds: float,
    trace: Mapping[str, Any],
    telemetry: Mapping[str, Any],
) -> list[str]:
    failures: list[str] = []
    counts = trace["counts"]
    reliability = trace["reliability"]
    latency = trace["latency_ms"]
    trace_integrity = trace["integrity"]
    telemetry_integrity = telemetry["integrity"]
    resources = telemetry["resources"]
    bad_frames = counts["error_dropped_frames"] + counts["invalid_frames"]
    total = counts["total_input_frames"]
    processing_fps = counts["completed_frames"] / duration_seconds

    checks = (
        (duration_seconds >= QUALIFICATION_DURATION_SECONDS, "duration_below_15_minutes"),
        (pipeline.camera_returncode == 0, "camera_runner_failed"),
        (
            pipeline.tegrastats_returncode in {0, -15, 143},
            "tegrastats_runner_failed",
        ),
        (counts["warmup_frames"] >= MINIMUM_WARMUP_FRAMES, "insufficient_warmup_frames"),
        (
            counts["completed_frames"] >= MINIMUM_COMPLETED_FRAMES,
            "insufficient_completed_frames",
        ),
        (processing_fps >= MINIMUM_PROCESSING_FPS, "processing_fps_below_gate"),
        (
            latency["tensorrt"]["p95"] <= MAX_TENSORRT_P95_MS,
            "tensorrt_p95_above_gate",
        ),
        (
            latency["selected_frame_age"]["p95"] <= MAX_SELECTED_FRAME_AGE_P95_MS,
            "selected_frame_age_p95_above_gate",
        ),
        (
            latency["selected_frame_age"]["max"] <= MAX_SELECTED_FRAME_AGE_MS,
            "selected_frame_age_max_above_gate",
        ),
        (
            latency["camera_to_event"]["p95"] <= MAX_CAMERA_TO_EVENT_P95_MS,
            "camera_to_event_p95_above_gate",
        ),
        (bad_frames / total <= MAX_BAD_FRAME_RATIO, "bad_frame_ratio_above_gate"),
        (reliability["errors"] == 0, "frame_errors_observed"),
        (reliability["oom_events"] == 0, "oom_observed"),
        (reliability["process_restarts"] == 0, "process_restart_observed"),
        (counts["event_count"] > 0, "no_rescue_events_emitted"),
        (
            trace_integrity["first_capture_monotonic_ns"]
            <= pipeline.started_monotonic_ns + MAX_TRACE_BOUNDARY_GAP_NS,
            "trace_started_too_late",
        ),
        (
            trace_integrity["last_terminal_monotonic_ns"]
            >= pipeline.completed_monotonic_ns - MAX_TRACE_BOUNDARY_GAP_NS,
            "trace_ended_too_early",
        ),
        (
            telemetry_integrity["first_monotonic_ns"]
            <= pipeline.started_monotonic_ns + MAX_TRACE_BOUNDARY_GAP_NS,
            "telemetry_started_too_late",
        ),
        (
            telemetry_integrity["last_monotonic_ns"]
            >= pipeline.completed_monotonic_ns - MAX_TRACE_BOUNDARY_GAP_NS,
            "telemetry_ended_too_early",
        ),
        (
            telemetry_integrity["sample_count"] >= max(2, int(duration_seconds // 2)),
            "insufficient_telemetry_density",
        ),
        (
            resources["min_available_ram_bytes"] >= MINIMUM_AVAILABLE_RAM_BYTES,
            "available_ram_below_gate",
        ),
        (
            MINIMUM_8GB_DEVICE_RAM_BYTES
            <= resources["ram_total_bytes"]
            <= MAXIMUM_8GB_DEVICE_RAM_BYTES,
            "target_is_not_8gb_memory_tier",
        ),
        (
            resources["swap_start_bytes"]
            == resources["swap_peak_bytes"]
            == resources["swap_end_bytes"],
            "swap_growth_observed",
        ),
    )
    failures.extend(name for passed, name in checks if not passed)
    if test_only:
        failures.append("test_only_execution")
    return failures


def _normalized_argv(
    runner: JetsonRunner, argv: Sequence[str], field: str
) -> tuple[str, ...]:
    if isinstance(argv, (str, bytes)) or not argv:
        raise JetsonEvidenceError(f"{field} must be a non-empty argv sequence")
    values = tuple(str(value) for value in argv)
    if any(not value or "\x00" in value or "\r" in value or "\n" in value for value in values):
        raise JetsonEvidenceError(f"{field} contains an invalid argument")
    executable = runner.resolve_executable(values[0])
    return (str(executable), *values[1:])


def produce_jetson_qualification_evidence(
    *,
    report_id: str,
    candidate: str,
    architecture: str,
    training_plan_sha256: str,
    confidence_threshold: float,
    onnx_path: str | os.PathLike[str],
    engine_path: str | os.PathLike[str],
    trtexec_executable: str | os.PathLike[str],
    tegrastats_executable: str | os.PathLike[str],
    camera_runner_argv: Sequence[str],
    evidence_directory: str | os.PathLike[str],
    workspace_root: str | os.PathLike[str],
    repository_root: str | os.PathLike[str],
    _test_only_runner: JetsonRunner | None = None,
) -> JetsonEvidenceResult:
    """Execute and record one Jetson build plus camera-to-event qualification.

    There are intentionally no parameters for frame counts, FPS, latency,
    errors, OOMs, restarts, resource summaries, or a pass claim.  Those values
    are recomputed from raw artifacts.  ``_test_only_runner`` is the sole
    injection seam and irrevocably makes the resulting report non-production.
    """

    report_id = _text(report_id, "report_id")
    candidate = _text(candidate, "candidate")
    architecture = _text(architecture, "architecture")
    plan_hash = _hash(training_plan_sha256, "training_plan_sha256")
    threshold = _number(
        confidence_threshold, "confidence_threshold", maximum=1.0
    )
    if candidate not in _CANDIDATE_CONTRACTS:
        raise JetsonEvidenceError(f"unsupported candidate: {candidate!r}")
    expected_architecture, shape = _CANDIDATE_CONTRACTS[candidate]
    if architecture != expected_architecture:
        raise JetsonEvidenceError("candidate architecture differs from frozen contract")

    try:
        workspace = require_external_workspace(workspace_root, repository_root)
        output = require_path_within_workspace(evidence_directory, workspace)
        engine_target = require_path_within_workspace(engine_path, workspace)
        onnx_candidate = require_path_within_workspace(onnx_path, workspace)
    except ArtifactIOError as error:
        raise JetsonEvidenceError(str(error)) from error
    if not Path(workspace_root).is_absolute() or not Path(repository_root).is_absolute():
        raise JetsonEvidenceError("workspace and repository roots must be absolute")
    if os.path.lexists(output):
        raise JetsonEvidenceError(f"refusing to reuse evidence directory: {output}")
    if engine_target.suffix.lower() != ".engine":
        raise JetsonEvidenceError("engine_path must end in .engine")
    if os.path.lexists(engine_target):
        raise JetsonEvidenceError(f"refusing to overwrite TensorRT engine: {engine_target}")
    _reject_git_containment(output, "evidence_directory")
    _reject_git_containment(engine_target, "engine_path")
    _reject_git_containment(onnx_candidate, "onnx_path")
    onnx = onnx_candidate
    if not onnx.is_absolute() or onnx.suffix.lower() != ".onnx":
        raise JetsonEvidenceError("onnx_path must be an absolute .onnx file")
    onnx = _regular_file(onnx, "ONNX artifact")

    runner: JetsonRunner = _SubprocessJetsonRunner() if _test_only_runner is None else _test_only_runner
    test_only = _test_only_runner is not None
    capture_mode = TEST_CAPTURE_MODE if test_only else PRODUCTION_CAPTURE_MODE
    trtexec = runner.resolve_executable(str(trtexec_executable))
    tegrastats = runner.resolve_executable(str(tegrastats_executable))
    base_camera_argv = _normalized_argv(runner, camera_runner_argv, "camera_runner_argv")
    for argument in base_camera_argv[1:]:
        if any(
            argument == prefix or argument.startswith(prefix + "=")
            for prefix in _RESERVED_CAMERA_ARGUMENT_PREFIXES
        ):
            raise JetsonEvidenceError(
                f"camera_runner_argv attempts to supply derived evidence: {argument}"
            )

    try:
        output.mkdir(parents=True, exist_ok=False)
        engine_target.parent.mkdir(parents=True, exist_ok=True)
    except FileExistsError as error:
        raise JetsonEvidenceError(f"refusing to reuse evidence path: {error}") from error
    except OSError as error:
        raise JetsonEvidenceError(f"cannot create evidence workspace: {error}") from error

    raw_directory = output / "raw"
    raw_directory.mkdir()
    raw_directory_stat = raw_directory.stat()
    raw_directory_identity = (raw_directory_stat.st_dev, raw_directory_stat.st_ino)
    trace_path = raw_directory / "frame-trace.jsonl"
    tegrastats_path = raw_directory / "tegrastats.jsonl"
    layer_info_path = raw_directory / "trtexec-layer-info.json"
    nonce = secrets.token_hex(32)
    onnx_hash_before = sha256_file(onnx)
    tool_hashes_before = {
        "trtexec": sha256_file(trtexec),
        "tegrastats": sha256_file(tegrastats),
    }
    camera_input_hashes_before = _argument_file_hashes(base_camera_argv)
    executable_stem = Path(base_camera_argv[0]).stem.lower()
    is_interpreter = executable_stem in _INTERPRETER_NAMES or executable_stem.startswith(
        "python"
    )
    if is_interpreter and len(camera_input_hashes_before) < 2:
        raise JetsonEvidenceError(
            "interpreted camera runner must name an absolute, hashable script artifact"
        )

    pre_anchor, raw_artifacts = _device_snapshot(
        runner,
        phase="pre",
        directory=raw_directory,
        capture_mode=capture_mode,
        trtexec=trtexec,
    )

    shape_text = "x".join(str(item) for item in shape)
    build_argv = (
        str(trtexec),
        f"--onnx={onnx}",
        f"--saveEngine={engine_target}",
        "--fp16",
        f"--minShapes=images:{shape_text}",
        f"--optShapes=images:{shape_text}",
        f"--maxShapes=images:{shape_text}",
    )
    build_result, build_capture = _run_and_capture(
        runner,
        build_argv,
        name="trtexec-build",
        directory=raw_directory,
        capture_mode=capture_mode,
        timeout_seconds=3_600,
    )
    raw_artifacts["trtexec_build_capture"] = build_capture
    build_parsed = _parse_trtexec_log(build_result, phase="build")
    engine = _regular_file(engine_target, "built TensorRT engine")
    engine_hash_before_benchmark = sha256_file(engine)
    if sha256_file(onnx) != onnx_hash_before:
        raise JetsonEvidenceError("ONNX artifact changed during TensorRT build")

    inspection_argv = (
        str(trtexec),
        f"--loadEngine={engine}",
        "--dumpLayerInfo",
        f"--exportLayerInfo={layer_info_path}",
        "--profilingVerbosity=detailed",
        "--skipInference",
    )
    inspection_result, inspection_capture = _run_and_capture(
        runner,
        inspection_argv,
        name="trtexec-inspection",
        directory=raw_directory,
        capture_mode=capture_mode,
        timeout_seconds=600,
    )
    raw_artifacts["trtexec_inspection_capture"] = inspection_capture
    inspection_parsed = _parse_trtexec_log(
        inspection_result, phase="inspection"
    )
    layer_contract = _inspect_layer_information(layer_info_path, shape)

    camera_argv = (
        *base_camera_argv,
        f"--engine-path={engine}",
        f"--engine-sha256={engine_hash_before_benchmark}",
        f"--qualification-trace-jsonl={trace_path}",
        f"--qualification-run-nonce={nonce}",
        f"--qualification-duration-seconds={QUALIFICATION_DURATION_SECONDS}",
        "--qualification-source=live_camera",
        "--newest-frame-policy=process_newest_drop_stale",
        "--cloud-or-network-dependency=false",
        f"--pipeline-stages={','.join(PIPELINE_STAGES)}",
        "--trace-create-once=true",
    )
    tegrastats_argv = (str(tegrastats), "--interval", str(TEGRASTATS_INTERVAL_MS))
    pipeline = _pipeline_result(
        runner.run_camera_pipeline(
            camera_argv,
            tegrastats_argv,
            artifact_paths=PipelineArtifactPaths(trace_path, tegrastats_path),
            timeout_seconds=QUALIFICATION_DURATION_SECONDS + 180,
        ),
        expected_camera_argv=camera_argv,
        expected_tegrastats_argv=tegrastats_argv,
    )
    camera_stdout_path = _create_bytes_once(
        raw_directory / "camera-runner.stdout.log", pipeline.camera_stdout
    )
    camera_stderr_path = _create_bytes_once(
        raw_directory / "camera-runner.stderr.log", pipeline.camera_stderr
    )
    tegrastats_stderr_path = _create_bytes_once(
        raw_directory / "tegrastats.stderr.log", pipeline.tegrastats_stderr
    )
    raw_artifacts.update(
        {
            "camera_runner_stdout": _artifact(
                camera_stdout_path, "camera runner stdout", allow_empty=True
            ),
            "camera_runner_stderr": _artifact(
                camera_stderr_path, "camera runner stderr", allow_empty=True
            ),
            "tegrastats_stderr": _artifact(
                tegrastats_stderr_path, "tegrastats stderr", allow_empty=True
            ),
        }
    )

    trace_path = _regular_file(trace_path, "per-frame trace")
    tegrastats_path = _regular_file(tegrastats_path, "tegrastats trace")
    current_raw_stat = raw_directory.stat()
    if raw_directory.is_symlink() or (
        current_raw_stat.st_dev,
        current_raw_stat.st_ino,
    ) != raw_directory_identity:
        raise JetsonEvidenceError("raw evidence directory identity changed during execution")
    engine_hash_after_benchmark = sha256_file(engine)
    if engine_hash_after_benchmark != engine_hash_before_benchmark:
        raise JetsonEvidenceError("TensorRT engine changed during camera qualification")
    if sha256_file(onnx) != onnx_hash_before:
        raise JetsonEvidenceError("ONNX artifact changed during camera qualification")
    if tool_hashes_before != {
        "trtexec": sha256_file(trtexec),
        "tegrastats": sha256_file(tegrastats),
    }:
        raise JetsonEvidenceError("Jetson tool binary changed during qualification")
    camera_input_hashes_after = _argument_file_hashes(base_camera_argv)
    if camera_input_hashes_after != camera_input_hashes_before:
        raise JetsonEvidenceError("camera runner input artifact changed during qualification")

    post_anchor, post_raw_artifacts = _device_snapshot(
        runner,
        phase="post",
        directory=raw_directory,
        capture_mode=capture_mode,
        trtexec=trtexec,
    )
    raw_artifacts.update(post_raw_artifacts)
    if post_anchor != pre_anchor:
        raise JetsonEvidenceError("device/runtime trust anchor changed during qualification")

    trace = _inspect_frame_trace(
        trace_path,
        nonce=nonce,
        engine_sha256=engine_hash_before_benchmark,
        benchmark_start_ns=pipeline.started_monotonic_ns,
        benchmark_end_ns=pipeline.completed_monotonic_ns,
    )
    camera_log = pipeline.camera_stdout + b"\n" + pipeline.camera_stderr
    camera_log_oom = re.search(
        rb"CUDA(?:_ERROR_)?_?OUT_OF_MEMORY|OutOfMemoryError|"
        rb"CUBLAS_STATUS_ALLOC_FAILED|std::bad_alloc|CUDA out of memory",
        camera_log,
        re.IGNORECASE,
    )
    if camera_log_oom is not None and trace["reliability"]["oom_events"] == 0:
        raise JetsonEvidenceError(
            "camera log reports OOM but the per-frame trace omits the OOM event"
        )
    telemetry = _inspect_tegrastats(
        tegrastats_path,
        benchmark_start_ns=pipeline.started_monotonic_ns,
        benchmark_end_ns=pipeline.completed_monotonic_ns,
    )
    duration_seconds = (
        pipeline.completed_monotonic_ns - pipeline.started_monotonic_ns
    ) / 1_000_000_000
    counts = dict(trace["counts"])
    counts["capture_fps"] = counts["total_input_frames"] / duration_seconds
    counts["fps"] = counts["completed_frames"] / duration_seconds

    failures = _qualification_failures(
        test_only=test_only,
        pipeline=pipeline,
        duration_seconds=duration_seconds,
        trace=trace,
        telemetry=telemetry,
    )

    raw_artifacts.update(
        {
            "frame_trace": _artifact(trace_path, "per-frame trace"),
            "tegrastats": _artifact(tegrastats_path, "tegrastats trace"),
            "trtexec_layer_info": _artifact(
                layer_info_path, "trtexec layer information"
            ),
        }
    )
    for path in raw_directory.iterdir():
        if path.is_file() and not path.is_symlink():
            _make_read_only(path)
    _make_read_only(engine)
    engine_artifact = _artifact(engine, "TensorRT engine")
    onnx_artifact = _artifact(onnx, "ONNX artifact")
    final_raw_stat = raw_directory.stat()
    if raw_directory.is_symlink() or (
        final_raw_stat.st_dev,
        final_raw_stat.st_ino,
    ) != raw_directory_identity:
        raise JetsonEvidenceError("raw evidence directory identity changed after execution")
    raw_entries = sorted(raw_directory.iterdir(), key=lambda item: item.name)
    if any(path.is_symlink() or not path.is_file() for path in raw_entries):
        raise JetsonEvidenceError(
            "raw evidence directory contains a symlink or non-file entry"
        )
    raw_inventory = {
        path.name: _artifact(path, f"raw artifact {path.name}", allow_empty=True)
        for path in raw_entries
    }
    raw_artifact_set_hash = hashlib.sha256(
        canonical_json_bytes(raw_inventory)
    ).hexdigest()
    report = {
        "schema": JETSON_EVIDENCE_SCHEMA,
        "report_id": report_id,
        "created_at_utc": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
        "capture_mode": capture_mode,
        "test_only": test_only,
        "summary_source": "derived_from_nonce_bound_hashed_raw_traces",
        "identity": {
            "candidate": candidate,
            "architecture": architecture,
            "model_id": MODEL_ID,
            "training_plan_sha256": plan_hash,
            "class_map": {str(key): value for key, value in PERSON_CLASS_MAP.items()},
            "input_shape_nchw": list(shape),
            "confidence_threshold": threshold,
            "precision": PRECISION,
        },
        "artifacts": {
            "onnx": onnx_artifact,
            "tensorrt_engine": engine_artifact,
            "tools": {
                "trtexec": _artifact(trtexec, "trtexec executable"),
                "tegrastats": _artifact(tegrastats, "tegrastats executable"),
            },
            "raw": raw_inventory,
            "semantic_raw_bindings": dict(sorted(raw_artifacts.items())),
            "raw_artifact_set_sha256": raw_artifact_set_hash,
        },
        "device_runtime_trust_anchor": {
            **pre_anchor,
            "pre_sha256": pre_anchor["trust_anchor_sha256"],
            "post_sha256": post_anchor["trust_anchor_sha256"],
            "unchanged": True,
        },
        "engine_build": {
            "argv": list(build_argv),
            "parsed_log": build_parsed,
            "onnx_sha256_before": onnx_hash_before,
            "engine_sha256": engine_hash_before_benchmark,
        },
        "engine_inspection": {
            "argv": list(inspection_argv),
            "parsed_log": inspection_parsed,
            "layer_contract": layer_contract,
        },
        "pipeline": {
            "camera_argv": list(camera_argv),
            "tegrastats_argv": list(tegrastats_argv),
            "camera_runner_input_artifacts": camera_input_hashes_before,
            "stages": list(PIPELINE_STAGES),
            "source": "live_camera",
            "newest_frame_policy": "process_newest_drop_stale",
            "cloud_or_network_dependency": False,
            "trace_create_once": True,
            "camera_returncode": pipeline.camera_returncode,
            "tegrastats_returncode": pipeline.tegrastats_returncode,
        },
        "measurement": {
            "monotonic_start_ns": pipeline.started_monotonic_ns,
            "monotonic_end_ns": pipeline.completed_monotonic_ns,
            "collection_completed_monotonic_ns": pipeline.collection_completed_monotonic_ns,
            "duration_seconds": duration_seconds,
            **counts,
        },
        "trace_integrity": trace["integrity"],
        "telemetry_integrity": telemetry["integrity"],
        "latency_ms": trace["latency_ms"],
        "reliability": trace["reliability"],
        "resources": telemetry["resources"],
        "engine_execution_binding": {
            "before_camera_sha256": engine_hash_before_benchmark,
            "after_camera_sha256": engine_hash_after_benchmark,
            "unchanged": True,
        },
        "qualification": {
            "derived_gate_passed": not [
                failure for failure in failures if failure != "test_only_execution"
            ],
            "production_eligible": not test_only,
            "failures": failures,
            "passed": not failures,
        },
        "human_reviewed": False,
    }
    report_path = output / "jetson-qualification-evidence.json"
    try:
        atomic_create_json(report_path, report)
    except ArtifactIOError as error:
        raise JetsonEvidenceError(str(error)) from error
    _make_read_only(report_path)
    return JetsonEvidenceResult(report_path.resolve(), report)


__all__ = [
    "CommandResult",
    "JETSON_COMMAND_CAPTURE_SCHEMA",
    "JETSON_EVIDENCE_SCHEMA",
    "JETSON_FRAME_TRACE_SCHEMA",
    "JETSON_TELEMETRY_ENVELOPE_SCHEMA",
    "JetsonEvidenceError",
    "JetsonEvidenceResult",
    "JetsonRunner",
    "PipelineArtifactPaths",
    "PipelineResult",
    "produce_jetson_qualification_evidence",
]
