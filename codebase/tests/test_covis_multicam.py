from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

from perception.claim import PerceptionClaim  # noqa: E402
from perception.yolo_action import Detection  # noqa: E402
from protocol.covis_features import FeatureAlignmentResult, FeatureMatchResult  # noqa: E402
from tools.covis_live import (  # noqa: E402
    CompositeVideoRecorder,
    DetectorObservation,
    FramePacket,
    _shutdown_workers,
)
from tools.covis_multicam import (  # noqa: E402
    CameraSpec,
    _parser,
    _validate_args,
    assess_all_pairs,
    camera_pairs,
    event_record,
    fit_visible_window,
    observe_once_per_camera,
    parse_camera_specs,
    render_dashboard,
    wait_new_frames,
)


def _frame(seed: int = 1):
    rng = np.random.default_rng(seed)
    return rng.integers(20, 235, size=(120, 160, 3), dtype=np.uint8)


def _packet(frame, sequence: int = 1, received_ns: int = 1_000_000_000):
    return FramePacket(sequence, 2_000_000_000, received_ns, frame)


def _alignment(_a, _b):
    return FeatureAlignmentResult(
        FeatureMatchResult(40, 50, 200, 210), np.eye(3)
    )


def _observation(class_id: int = 2):
    detection = Detection(class_id, 0.5, 0.5, 0.3, 0.25, 0.9)
    return DetectorObservation(
        (detection,),
        PerceptionClaim.from_detections((detection,)),
        ((class_id, "car"),),
    )


def _specs(count: int):
    return tuple(CameraSpec(f"cam{i + 1}", i, "auto") for i in range(count))


def test_camera_parser_accepts_two_to_five_distinct_sources():
    specs = parse_camera_specs(
        [
            ["cam1", "0", "dshow"],
            ["cam2", "http://10.0.0.2/video", "ffmpeg"],
            ["cam3", "http://10.0.0.3/video", "ffmpeg"],
            ["cam4", "http://10.0.0.4/video", "ffmpeg"],
            ["cam5", "4", "msmf"],
        ]
    )

    assert len(specs) == 5
    assert specs[0].source == 0
    assert specs[1].source == "http://10.0.0.2/video"


def test_windows_launcher_resolves_directshow_sources_by_expected_device_name():
    launcher = (
        pathlib.Path(__file__).resolve().parent.parent
        / "tools"
        / "launch_covis_multicam_demo.ps1"
    ).read_text(encoding="utf-8")

    assert "Get-DirectShowVideoDeviceNames" in launcher
    assert "expected_device_name" in launcher
    assert "$camera.Source = [string]$matches[0]" in launcher
    assert "disconnected or unavailable" in launcher


@pytest.mark.parametrize(
    "values, message",
    [
        ([["cam1", "0", "auto"]], "camera count"),
        (
            [[f"cam{i}", str(i), "auto"] for i in range(6)],
            "camera count",
        ),
        (
            [["cam1", "0", "auto"], ["cam1", "1", "auto"]],
            "duplicate camera name",
        ),
        (
            [["cam1", "0", "auto"], ["cam2", "0", "auto"]],
            "duplicate camera source",
        ),
    ],
)
def test_camera_parser_rejects_ambiguous_configurations(values, message):
    with pytest.raises(ValueError, match=message):
        parse_camera_specs(values)


def test_semantic_cli_requires_weights_and_frozen_hash_together():
    args = _parser().parse_args(
        [
            "--camera",
            "cam1",
            "0",
            "dshow",
            "--camera",
            "cam2",
            "2",
            "msmf",
            "--weights",
            "yolov8n.pt",
        ]
    )

    with pytest.raises(ValueError, match="requires both"):
        _validate_args(args)


@pytest.mark.parametrize("count, expected", [(2, 1), (3, 3), (4, 6), (5, 10)])
def test_all_unordered_camera_pairs_are_generated(count, expected):
    pairs = camera_pairs(_specs(count))

    assert len(pairs) == expected
    assert len(set(pairs)) == expected


def test_visible_window_preserves_aspect_ratio_and_fits_usable_screen():
    width, height = fit_visible_window(1920, 1080, 1536, 864)

    assert width <= 1536 - 40
    assert height <= 864 - 110
    assert width / height == pytest.approx(16 / 9, rel=0.01)


def test_visible_window_does_not_enlarge_a_dashboard_that_already_fits():
    assert fit_visible_window(1280, 720, 1920, 1080) == (1280, 720)


class _LatestWorker:
    def __init__(self, packet):
        self.packet = packet
        self.error = None

    def latest(self):
        return self.packet


def test_wait_new_frames_returns_one_fresh_packet_from_every_source():
    workers = {
        f"cam{i}": _LatestWorker(_packet(_frame(i), sequence=i + 1))
        for i in range(1, 6)
    }

    packets = wait_new_frames(workers, {name: 0 for name in workers}, 0.1)

    assert set(packets) == set(workers)
    assert all(packet.sequence > 0 for packet in packets.values())


class _ClosableWorker:
    def __init__(self, name, calls):
        self.name = name
        self.calls = calls

    def request_stop(self):
        self.calls.append((self.name, "stop"))

    def join(self, _timeout):
        self.calls.append((self.name, "join"))
        return True


def test_five_source_shutdown_signals_every_camera_before_any_join():
    calls = []
    workers = {
        f"cam{i}": _ClosableWorker(f"cam{i}", calls) for i in range(1, 6)
    }

    result = _shutdown_workers(workers, 20.0)

    assert calls[:5] == [(f"cam{i}", "stop") for i in range(1, 6)]
    assert calls[5:] == [(f"cam{i}", "join") for i in range(1, 6)]
    assert all(item["closed"] for item in result.values())


class _CountingProvider:
    def __init__(self):
        self.calls = 0

    def __call__(self, _frame):
        self.calls += 1
        return _observation()


def test_detector_runs_once_per_camera_not_once_per_pair():
    packets = {f"cam{i + 1}": _packet(_frame(i)) for i in range(5)}
    provider = _CountingProvider()

    observations = observe_once_per_camera(packets, provider)

    assert observations is not None
    assert provider.calls == 5
    assert len(observations) == 5


def test_multicam_recorder_uses_a_distinct_safe_filename(tmp_path):
    recorder = CompositeVideoRecorder(
        tmp_path,
        "MJPG",
        3.0,
        object(),
        filename="multicam_dashboard.avi",
    )

    assert recorder.path == tmp_path / "video" / "multicam_dashboard.avi"
    with pytest.raises(ValueError, match="plain file name"):
        CompositeVideoRecorder(
            tmp_path, "MJPG", 3.0, object(), filename="../outside.avi"
        )


def test_four_cameras_produce_six_semantic_pair_assessments_from_cache():
    specs = _specs(4)
    packets = {spec.name: _packet(_frame(index)) for index, spec in enumerate(specs)}
    observations = {spec.name: _observation() for spec in specs}

    pairs = assess_all_pairs(
        specs,
        packets,
        observations,
        cv2_module=cv2,
        now_monotonic_ns=1_000_000_000,
        alignment_provider=_alignment,
    )

    assert len(pairs) == 6
    assert all(pair.displayed_decision == "AGREE" for pair in pairs)
    assert all(pair.overlap_available for pair in pairs)


def test_one_detector_failure_abstains_only_pairs_using_that_camera():
    specs = _specs(3)
    packets = {spec.name: _packet(_frame(index)) for index, spec in enumerate(specs)}
    observations = {
        "cam1": RuntimeError("synthetic inference failure"),
        "cam2": _observation(),
        "cam3": _observation(),
    }

    pairs = assess_all_pairs(
        specs,
        packets,
        observations,
        cv2_module=cv2,
        now_monotonic_ns=1_000_000_000,
        alignment_provider=_alignment,
    )

    by_pair = {(pair.camera_a, pair.camera_b): pair for pair in pairs}
    assert by_pair[("cam1", "cam2")].displayed_decision == "ABSTAIN"
    assert by_pair[("cam1", "cam3")].displayed_decision == "ABSTAIN"
    assert by_pair[("cam2", "cam3")].displayed_decision == "AGREE"


def test_zero_projected_intersection_is_displayed_as_abstain():
    specs = _specs(2)
    frame = _frame()
    packets = {"cam1": _packet(frame), "cam2": _packet(frame.copy())}
    observations = {"cam1": _observation(), "cam2": _observation()}

    def outside(_a, _b):
        return FeatureAlignmentResult(
            FeatureMatchResult(40, 50, 200, 210),
            np.array([[1, 0, 300], [0, 1, 0], [0, 0, 1]], dtype=float),
        )

    pair = assess_all_pairs(
        specs,
        packets,
        observations,
        cv2_module=cv2,
        now_monotonic_ns=1_000_000_000,
        alignment_provider=outside,
    )[0]

    assert pair.overlap_available is False
    assert pair.displayed_decision == "ABSTAIN"
    assert pair.displayed_reason == "no_projected_view_intersection"
    assert pair.assessment.decision == "ABSTAIN"
    assert pair.assessment.reason == "no_projected_view_intersection"


def test_feature_gate_failure_is_explicit_no_intersection_abstain():
    specs = _specs(2)
    packets = {"cam1": _packet(_frame(1)), "cam2": _packet(_frame(2))}

    def no_features(_a, _b):
        return FeatureAlignmentResult(FeatureMatchResult(0, 0, 10, 10), None)

    pair = assess_all_pairs(
        specs,
        packets,
        None,
        cv2_module=cv2,
        now_monotonic_ns=1_000_000_000,
        alignment_provider=no_features,
    )[0]

    assert pair.overlap_available is False
    assert pair.displayed_decision == "ABSTAIN"
    assert pair.displayed_reason == "feature_overlap_below_threshold"
    assert pair.assessment.decision == "ABSTAIN"


def test_matching_same_class_crops_are_shown_as_an_explicit_assumption_only():
    specs = _specs(2)
    frame = _frame(21)
    packets = {"cam1": _packet(frame), "cam2": _packet(frame.copy())}
    observations = {"cam1": _observation(), "cam2": _observation()}

    def no_features(_a, _b):
        return FeatureAlignmentResult(FeatureMatchResult(0, 0, 10, 10), None)

    pair = assess_all_pairs(
        specs,
        packets,
        observations,
        cv2_module=cv2,
        now_monotonic_ns=1_000_000_000,
        alignment_provider=no_features,
    )[0]

    assert pair.overlap_available is False
    assert pair.displayed_decision == "ABSTAIN"
    assert pair.appearance_assumption is not None
    assert pair.appearance_assumption.assumed_same_object is True
    assert pair.appearance_assumption.identity_proven is False
    assert pair.appearance_assumption.class_name == "car"
    event = event_record(1, specs, packets, observations, (pair,), "f" * 64)
    assumption = event["pairs"][0]["appearance_assumption"]
    assert assumption["assumed_same_object"] is True
    assert assumption["identity_proven"] is False

    dashboard = render_dashboard(
        specs,
        packets,
        observations,
        (pair,),
        width=1000,
        height=700,
        cv2_module=cv2,
    )
    assert dashboard.shape == (700, 1000, 3)
    assert np.count_nonzero(dashboard) > 0


def test_different_classes_never_create_an_appearance_assumption():
    specs = _specs(2)
    frame = _frame(22)
    packets = {"cam1": _packet(frame), "cam2": _packet(frame.copy())}
    observations = {"cam1": _observation(2), "cam2": _observation(3)}

    def no_features(_a, _b):
        return FeatureAlignmentResult(FeatureMatchResult(0, 0, 10, 10), None)

    pair = assess_all_pairs(
        specs,
        packets,
        observations,
        cv2_module=cv2,
        now_monotonic_ns=1_000_000_000,
        alignment_provider=no_features,
    )[0]

    assert pair.displayed_decision == "ABSTAIN"
    assert pair.appearance_assumption is None


def test_dashboard_uses_small_source_strip_and_all_large_pair_tiles():
    specs = _specs(4)
    packets = {spec.name: _packet(_frame(index)) for index, spec in enumerate(specs)}
    observations = {spec.name: _observation() for spec in specs}
    pairs = assess_all_pairs(
        specs,
        packets,
        observations,
        cv2_module=cv2,
        now_monotonic_ns=1_000_000_000,
        alignment_provider=_alignment,
    )

    dashboard = render_dashboard(
        specs,
        packets,
        observations,
        pairs,
        width=1280,
        height=800,
        cv2_module=cv2,
    )

    assert dashboard.shape == (800, 1280, 3)
    assert np.count_nonzero(dashboard) > 0
    # Six pair tiles are arranged 3x2 below a much shorter source strip.
    assert len(pairs) == 6


def test_dashboard_handles_all_pairs_without_intersection():
    specs = _specs(3)
    packets = {spec.name: _packet(_frame(index)) for index, spec in enumerate(specs)}

    def no_features(_a, _b):
        return FeatureAlignmentResult(FeatureMatchResult(0, 0, 5, 6), None)

    pairs = assess_all_pairs(
        specs,
        packets,
        None,
        cv2_module=cv2,
        now_monotonic_ns=1_000_000_000,
        alignment_provider=no_features,
    )
    dashboard = render_dashboard(
        specs,
        packets,
        None,
        pairs,
        width=1000,
        height=700,
        cv2_module=cv2,
    )

    assert all(pair.displayed_decision == "ABSTAIN" for pair in pairs)
    assert all(not pair.overlap_available for pair in pairs)
    assert dashboard.shape == (700, 1000, 3)


def test_event_records_every_pair_and_the_exact_displayed_abstention():
    specs = _specs(2)
    packets = {"cam1": _packet(_frame(1)), "cam2": _packet(_frame(2))}

    def no_features(_a, _b):
        return FeatureAlignmentResult(FeatureMatchResult(0, 0, 5, 6), None)

    pairs = assess_all_pairs(
        specs,
        packets,
        None,
        cv2_module=cv2,
        now_monotonic_ns=1_000_000_000,
        alignment_provider=no_features,
    )

    event = event_record(1, specs, packets, None, pairs, None)

    assert set(event["cameras"]) == {"cam1", "cam2"}
    assert len(event["pairs"]) == 1
    assert event["pairs"][0]["displayed_decision"] == "ABSTAIN"
    assert event["pairs"][0]["assessment"]["decision"] == "ABSTAIN"
