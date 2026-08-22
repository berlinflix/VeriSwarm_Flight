import inspect
import json
import os
from pathlib import Path

import pytest

from rescue_training.artifact_io import sha256_file
from rescue_training.jetson_evidence import (
    JETSON_EVIDENCE_SCHEMA,
    JETSON_FRAME_TRACE_SCHEMA,
    JETSON_TELEMETRY_ENVELOPE_SCHEMA,
    CommandResult,
    JetsonEvidenceError,
    PipelineArtifactPaths,
    PipelineResult,
    produce_jetson_qualification_evidence,
)


PLAN_HASH = "a" * 64
RUN_START_NS = 10_000_000_000
RUN_END_NS = RUN_START_NS + 900_000_000_000


def _make_executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(f"fixture executable {path.name}".encode("utf-8"))
    path.chmod(0o755)
    return path.resolve()


class _TestOnlyJetsonRunner:
    def __init__(
        self,
        root: Path,
        *,
        reliability_failure: bool = False,
        wrong_nonce: bool = False,
        mutate_engine: bool = False,
        bad_layer_contract: bool = False,
        bad_build_log: bool = False,
        change_boot_identity: bool = False,
        untraced_oom_log: bool = False,
    ) -> None:
        self.root = root
        self.reliability_failure = reliability_failure
        self.wrong_nonce = wrong_nonce
        self.mutate_engine = mutate_engine
        self.bad_layer_contract = bad_layer_contract
        self.bad_build_log = bad_build_log
        self.change_boot_identity = change_boot_identity
        self.untraced_oom_log = untraced_oom_log
        self.read_counts: dict[str, int] = {}
        self.clock_ns = 1_000_000_000
        self.tools = {
            name: _make_executable(root / "tools" / name)
            for name in (
                "trtexec",
                "tegrastats",
                "camera-runner",
                "uname",
                "nvpmodel",
                "dpkg-query",
            )
        }

    def resolve_executable(self, name_or_path: str) -> Path:
        value = str(name_or_path)
        if value in self.tools:
            return self.tools[value]
        candidate = Path(value)
        for path in self.tools.values():
            if candidate == path:
                return path
        raise JetsonEvidenceError(f"unknown fixture executable: {name_or_path}")

    def read_bytes(self, path: Path) -> bytes:
        normalized = str(path).replace("\\", "/")
        key = normalized.rsplit("/", 1)[-1]
        self.read_counts[key] = self.read_counts.get(key, 0) + 1
        if normalized.endswith("/proc/device-tree/model"):
            return b"NVIDIA Jetson Orin Nano Engineering Reference Developer Kit\x00"
        if normalized.endswith("/etc/nv_tegra_release"):
            return b"# R36 (release), REVISION: 4.3\n"
        if normalized.endswith("/etc/machine-id"):
            return b"fixture-machine-id\n"
        if normalized.endswith("/proc/sys/kernel/random/boot_id"):
            if self.change_boot_identity and self.read_counts[key] > 1:
                return b"22222222-2222-2222-2222-222222222222\n"
            return b"11111111-1111-1111-1111-111111111111\n"
        raise JetsonEvidenceError(f"unknown fixture system file: {path}")

    def _result(
        self,
        argv: tuple[str, ...],
        stdout: bytes,
        *,
        stderr: bytes = b"",
        returncode: int = 0,
    ) -> CommandResult:
        started = self.clock_ns
        self.clock_ns += 1_000_000
        completed = self.clock_ns
        self.clock_ns += 1_000_000
        return CommandResult(argv, returncode, stdout, stderr, started, completed)

    def run_command(
        self, argv, *, timeout_seconds: int
    ) -> CommandResult:
        del timeout_seconds
        values = tuple(str(item) for item in argv)
        executable = Path(values[0]).name
        if executable == "uname":
            if values[-1] == "-m":
                return self._result(values, b"aarch64\n")
            return self._result(values, b"Linux orin 5.15.148-tegra aarch64 GNU/Linux\n")
        if executable == "nvpmodel":
            return self._result(values, b"NV Power Mode: 15W\n1\n")
        if executable == "dpkg-query":
            return self._result(
                values,
                b"cuda-cudart-12-6=12.6.0-1\n"
                b"libcudnn9-cuda-12:arm64=9.3.0\n"
                b"libnvinfer10:arm64=10.3.0\n"
                b"nvidia-l4t-core=36.4.3\n",
            )
        if executable != "trtexec":
            raise AssertionError(f"unexpected executable {values[0]}")
        if values[-1] == "--version":
            return self._result(values, b"TensorRT version: 10.3.0\n")
        save = next((value for value in values if value.startswith("--saveEngine=")), None)
        if save is not None:
            engine = Path(save.split("=", 1)[1])
            engine.parent.mkdir(parents=True, exist_ok=True)
            with engine.open("xb") as stream:
                stream.write(b"exact-fixture-fp16-engine")
            if self.bad_build_log:
                return self._result(values, b"Engine built\n&&&& PASSED TensorRT.trtexec\n")
            return self._result(
                values,
                b"TensorRT version: 10.3.0\nPrecision: FP16\n"
                b"Engine built in 2.0 sec\n&&&& PASSED TensorRT.trtexec\n",
            )
        export = next(
            (value for value in values if value.startswith("--exportLayerInfo=")), None
        )
        if export is not None:
            layer_path = Path(export.split("=", 1)[1])
            shape = [1, 3, 320, 320] if self.bad_layer_contract else [1, 3, 640, 640]
            with layer_path.open("x", encoding="utf-8") as stream:
                json.dump(
                    {
                        "Bindings": [
                            {
                                "Name": "images",
                                "Dimensions": shape,
                                "Format/Datatype": "Row major linear FP16",
                            }
                        ]
                    },
                    stream,
                    sort_keys=True,
                )
            return self._result(
                values,
                b"TensorRT version: 10.3.0\nEngine loaded\n"
                b"&&&& PASSED TensorRT.trtexec\n",
            )
        raise AssertionError(f"unexpected trtexec argv: {values}")

    def run_camera_pipeline(
        self,
        camera_argv,
        tegrastats_argv,
        *,
        artifact_paths: PipelineArtifactPaths,
        timeout_seconds: int,
    ) -> PipelineResult:
        del timeout_seconds
        camera_values = tuple(str(item) for item in camera_argv)
        telemetry_values = tuple(str(item) for item in tegrastats_argv)
        option = {
            value.split("=", 1)[0]: value.split("=", 1)[1]
            for value in camera_values
            if value.startswith("--") and "=" in value
        }
        assert option["--qualification-duration-seconds"] == "900"
        assert option["--qualification-source"] == "live_camera"
        assert option["--newest-frame-policy"] == "process_newest_drop_stale"
        assert option["--cloud-or-network-dependency"] == "false"
        assert option["--trace-create-once"] == "true"
        nonce = option["--qualification-run-nonce"]
        if self.wrong_nonce:
            nonce = "0" * 64
        engine_hash = option["--engine-sha256"]
        frame_count = 4_550
        first_capture = RUN_START_NS + 100_000_000
        last_capture = RUN_END_NS - 200_000_000
        step = (last_capture - first_capture) // (frame_count - 1)
        timestamp_names = (
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
        offsets = (
            0,
            1_000_000,
            2_000_000,
            3_000_000,
            4_000_000,
            100_000_000,
            105_000_000,
            120_000_000,
            121_000_000,
            122_000_000,
        )
        with artifact_paths.frame_trace_jsonl.open("x", encoding="utf-8") as stream:
            for index in range(frame_count):
                capture = first_capture + step * index
                warmup = index < 50
                failed = self.reliability_failure and index == 100
                process_instance = 1 if self.reliability_failure and index >= 2_000 else 0
                if failed:
                    disposition = "error_dropped"
                    error_code = "cuda_oom"
                    timestamps = {
                        name: capture + offsets[position] if position <= 1 else None
                        for position, name in enumerate(timestamp_names)
                    }
                else:
                    disposition = "completed"
                    error_code = None
                    timestamps = {
                        name: capture + offsets[position]
                        for position, name in enumerate(timestamp_names)
                    }
                event_count = int(not warmup and not failed and index % 100 == 0)
                record = {
                    "schema": JETSON_FRAME_TRACE_SCHEMA,
                    "run_nonce": nonce,
                    "sequence": index,
                    "frame_id": f"camera-0:{index}",
                    "source_timestamp_ns": index * 33_333_333,
                    "process_instance": process_instance,
                    "engine_sha256": engine_hash,
                    "modality": "rgb",
                    "disposition": disposition,
                    "warmup": warmup,
                    "error_code": error_code,
                    "timestamps": timestamps,
                    "event_count": event_count,
                    "outbox_enqueue_count": event_count,
                    "model_receipt_bookkeeping_count": event_count,
                }
                stream.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")

        with artifact_paths.tegrastats_jsonl.open("x", encoding="utf-8") as stream:
            for index in range(901):
                monotonic_ns = RUN_START_NS + 50_000_000 + index * 999_000_000
                if monotonic_ns > RUN_END_NS:
                    monotonic_ns = RUN_END_NS - (901 - index) * 1_000_000
                raw = (
                    "RAM 2048/7760MB (lfb 100x4MB) "
                    "SWAP 0/3880MB (cached 0MB) CPU [5%@729] "
                    "GR3D_FREQ 35%@[612] CPU@60C GPU@58C "
                    "VDD_IN 12000mW/12000mW"
                )
                envelope = {
                    "schema": JETSON_TELEMETRY_ENVELOPE_SCHEMA,
                    "monotonic_ns": monotonic_ns,
                    "raw": raw,
                }
                stream.write(json.dumps(envelope, sort_keys=True, separators=(",", ":")) + "\n")

        if self.mutate_engine:
            engine = Path(option["--engine-path"])
            engine.chmod(0o644)
            with engine.open("ab") as stream:
                stream.write(b"tamper")
        return PipelineResult(
            camera_values,
            telemetry_values,
            0,
            -15,
            (
                b"CUDA out of memory\n"
                if self.untraced_oom_log
                else b"untrusted summary: fps=99999 passed=true errors=0\n"
            ),
            b"",
            b"",
            RUN_START_NS,
            RUN_END_NS,
            RUN_END_NS + 1_000_000,
        )


def _inputs(tmp_path: Path, runner: _TestOnlyJetsonRunner) -> dict:
    workspace = tmp_path / "external-workspace"
    workspace.mkdir(parents=True)
    onnx = workspace / "models" / "candidate.onnx"
    onnx.parent.mkdir()
    onnx.write_bytes(b"fixture-static-onnx")
    repository = Path(__file__).resolve().parents[2]
    return {
        "report_id": "jetson-yolov8n-640-run-001",
        "candidate": "yolov8n-640",
        "architecture": "yolov8n.pt",
        "training_plan_sha256": PLAN_HASH,
        "confidence_threshold": 0.37,
        "onnx_path": onnx,
        "engine_path": workspace / "models" / "candidate.engine",
        "trtexec_executable": runner.tools["trtexec"],
        "tegrastats_executable": runner.tools["tegrastats"],
        "camera_runner_argv": [
            str(runner.tools["camera-runner"]),
            "--camera-index=0",
        ],
        "evidence_directory": workspace / "evidence" / "run-001",
        "workspace_root": workspace,
        "repository_root": repository,
        "_test_only_runner": runner,
    }


def test_producer_derives_metrics_and_marks_injected_runner_test_only(tmp_path: Path) -> None:
    runner = _TestOnlyJetsonRunner(tmp_path / "fixture")
    inputs = _inputs(tmp_path, runner)

    result = produce_jetson_qualification_evidence(**inputs)
    report = result.report

    assert report["schema"] == JETSON_EVIDENCE_SCHEMA
    assert report["capture_mode"] == "test_injected_runner"
    assert report["test_only"] is True
    assert report["summary_source"] == "derived_from_nonce_bound_hashed_raw_traces"
    assert report["measurement"]["total_input_frames"] == 4_550
    assert report["measurement"]["completed_frames"] == 4_550
    assert report["measurement"]["warmup_frames"] == 50
    assert report["measurement"]["event_count"] == 45
    assert report["measurement"]["fps"] == pytest.approx(4_550 / 900)
    assert report["latency_ms"]["selected_frame_age"]["p95"] == 2.0
    assert report["latency_ms"]["tensorrt"]["p95"] == 96.0
    assert report["latency_ms"]["camera_to_event"]["p95"] == 120.0
    assert report["reliability"] == {
        "errors": 0,
        "oom_events": 0,
        "process_restarts": 0,
    }
    assert report["qualification"]["derived_gate_passed"] is True
    assert report["qualification"]["production_eligible"] is False
    assert report["qualification"]["passed"] is False
    assert report["qualification"]["failures"] == ["test_only_execution"]
    assert report["human_reviewed"] is False
    assert report["engine_execution_binding"]["unchanged"] is True
    assert report["pipeline"]["source"] == "live_camera"
    assert report["pipeline"]["cloud_or_network_dependency"] is False

    inventory = report["artifacts"]["raw"]
    assert "frame-trace.jsonl" in inventory
    assert "tegrastats.jsonl" in inventory
    assert "camera-runner.stdout.log" in inventory
    for artifact in inventory.values():
        path = Path(artifact["path"])
        assert artifact["sha256"] == sha256_file(path)
        assert artifact["bytes"] == path.stat().st_size
    assert report["artifacts"]["tensorrt_engine"]["sha256"] == sha256_file(
        inputs["engine_path"]
    )
    assert result.report_path.is_file()


def test_error_oom_and_restart_are_derived_from_frame_trace(tmp_path: Path) -> None:
    runner = _TestOnlyJetsonRunner(
        tmp_path / "fixture", reliability_failure=True
    )
    result = produce_jetson_qualification_evidence(**_inputs(tmp_path, runner))
    report = result.report

    assert report["measurement"]["error_dropped_frames"] == 1
    assert report["measurement"]["completed_frames"] == 4_549
    assert report["reliability"] == {
        "errors": 1,
        "oom_events": 1,
        "process_restarts": 1,
    }
    assert "frame_errors_observed" in report["qualification"]["failures"]
    assert "oom_observed" in report["qualification"]["failures"]
    assert "process_restart_observed" in report["qualification"]["failures"]
    assert report["qualification"]["derived_gate_passed"] is False


def test_nonce_mismatched_stale_trace_is_rejected(tmp_path: Path) -> None:
    runner = _TestOnlyJetsonRunner(tmp_path / "fixture", wrong_nonce=True)
    with pytest.raises(JetsonEvidenceError, match="stale or nonce-mismatched"):
        produce_jetson_qualification_evidence(**_inputs(tmp_path, runner))


def test_engine_mutation_during_camera_run_is_rejected(tmp_path: Path) -> None:
    runner = _TestOnlyJetsonRunner(tmp_path / "fixture", mutate_engine=True)
    with pytest.raises(JetsonEvidenceError, match="engine changed"):
        produce_jetson_qualification_evidence(**_inputs(tmp_path, runner))


@pytest.mark.parametrize(
    ("runner_option", "message"),
    [
        ("bad_layer_contract", "exact static 'images' binding shape"),
        ("bad_build_log", "does not bind FP16"),
        ("change_boot_identity", "trust anchor changed"),
        ("untraced_oom_log", "per-frame trace omits the OOM"),
    ],
)
def test_tampered_build_inspection_or_device_identity_fails_closed(
    tmp_path: Path, runner_option: str, message: str
) -> None:
    runner = _TestOnlyJetsonRunner(
        tmp_path / "fixture", **{runner_option: True}
    )
    with pytest.raises(JetsonEvidenceError, match=message):
        produce_jetson_qualification_evidence(**_inputs(tmp_path, runner))


def test_caller_cannot_supply_summary_or_pass_claims(tmp_path: Path) -> None:
    forbidden = {
        "passed",
        "fps",
        "latency_ms",
        "completed_frames",
        "errors",
        "oom_events",
        "process_restarts",
    }
    signature = inspect.signature(produce_jetson_qualification_evidence)
    assert forbidden.isdisjoint(signature.parameters)

    runner = _TestOnlyJetsonRunner(tmp_path / "fixture")
    with pytest.raises(TypeError, match="unexpected keyword argument 'passed'"):
        produce_jetson_qualification_evidence(
            **_inputs(tmp_path, runner), passed=True  # type: ignore[call-arg]
        )


def test_reserved_summary_argument_and_existing_output_are_rejected(tmp_path: Path) -> None:
    runner = _TestOnlyJetsonRunner(tmp_path / "fixture")
    inputs = _inputs(tmp_path, runner)
    inputs["camera_runner_argv"] = [
        str(runner.tools["camera-runner"]),
        "--summary=passed",
    ]
    with pytest.raises(JetsonEvidenceError, match="derived evidence"):
        produce_jetson_qualification_evidence(**inputs)

    runner = _TestOnlyJetsonRunner(tmp_path / "fixture-two")
    inputs = _inputs(tmp_path / "second", runner)
    Path(inputs["evidence_directory"]).mkdir(parents=True)
    with pytest.raises(JetsonEvidenceError, match="reuse evidence directory"):
        produce_jetson_qualification_evidence(**inputs)
