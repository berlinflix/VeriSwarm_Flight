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
    CycleTracker,
    DetectorObservation,
    FramePacket,
    LiveDemoError,
    _actual_backend,
    _backend_api,
    _draw_detections,
    _open_capture,
    _parser,
    _shutdown_workers,
    _source_value,
    assess_pair,
    projected_iou_evidence,
)


def _textured(seed: int = 7):
    rng = np.random.default_rng(seed)
    return rng.integers(20, 235, size=(120, 160, 3), dtype=np.uint8)


def _packet(frame, sequence: int, received_ns: int) -> FramePacket:
    return FramePacket(
        sequence=sequence,
        received_wall_ns=1_000_000_000 + sequence,
        received_monotonic_ns=received_ns,
        frame=frame,
    )


def _features(inliers: int = 40):
    return lambda _a, _b: FeatureMatchResult(
        inliers=inliers,
        good_matches=max(inliers, 40),
        keypoints_a=200,
        keypoints_b=210,
    )


def _identity_alignment(inliers: int = 40):
    return lambda _a, _b: FeatureAlignmentResult(
        match=FeatureMatchResult(
            inliers=inliers,
            good_matches=max(inliers, 40),
            keypoints_a=200,
            keypoints_b=210,
        ),
        homography_a_to_b=np.eye(3),
    )


def test_source_parser_preserves_paths_and_urls():
    assert _source_value("0") == 0
    assert _source_value("/dev/video2") == "/dev/video2"
    assert _source_value("http://10.0.0.4:4747/video") == (
        "http://10.0.0.4:4747/video"
    )
    with pytest.raises(ValueError, match="cannot be empty"):
        _source_value("   ")


class _FakeCapture:
    def __init__(self):
        self.open_calls = []

    def open(self, *args):
        self.open_calls.append(args)
        return True

    def getBackendName(self):
        return "DSHOW"


class _FakeCv2:
    CAP_ANY = 0
    CAP_DSHOW = 700


def test_explicit_windows_backend_uses_opencv_api_preference():
    capture = _FakeCapture()

    assert _open_capture(capture, 2, "dshow", _FakeCv2)
    assert capture.open_calls == [(2, _FakeCv2.CAP_DSHOW)]
    assert _actual_backend(capture) == "DSHOW"


def test_auto_backend_uses_single_argument_open_overload():
    capture = _FakeCapture()
    url = "http://10.0.0.4:4747/video"

    assert _open_capture(capture, url, "auto", _FakeCv2)
    assert capture.open_calls == [(url,)]


def test_missing_backend_fails_closed():
    with pytest.raises(LiveDemoError, match="does not expose backend"):
        _backend_api("ffmpeg", _FakeCv2)


def test_unknown_backend_fails_closed():
    with pytest.raises(LiveDemoError, match="unsupported capture backend"):
        _backend_api("invented", _FakeCv2)


class _FakeWorker:
    def __init__(self, name, calls):
        self.name = name
        self.calls = calls
        self.join_timeout = None

    def request_stop(self):
        self.calls.append((self.name, "stop"))

    def join(self, timeout):
        self.calls.append((self.name, "join"))
        self.join_timeout = timeout
        return True


def test_shutdown_signals_both_workers_before_joining_with_shared_budget():
    calls = []
    camera_a = _FakeWorker("a", calls)
    camera_b = _FakeWorker("b", calls)

    result = _shutdown_workers(
        {"camera_a_worker": camera_a, "camera_b_worker": camera_b},
        timeout=20.0,
    )

    assert calls == [("a", "stop"), ("b", "stop"), ("a", "join"), ("b", "join")]
    assert 0 < camera_a.join_timeout <= 20.0
    assert 0 < camera_b.join_timeout <= 20.0
    assert result["camera_a_worker"]["closed"] is True
    assert result["camera_b_worker"]["closed"] is True
    assert result["camera_a_worker"]["error"] is None


def test_parser_defaults_to_msmf_safe_bounded_release_timeout():
    args = _parser().parse_args(["--camera-a", "0", "--camera-b", "2"])

    assert args.release_timeout == 20.0


def test_excessive_capture_skew_abstains_before_matching():
    frame = _textured()
    a = _packet(frame, 1, 1_000_000_000)
    b = _packet(frame, 1, 1_300_000_000)

    result = assess_pair(
        a,
        b,
        cv2_module=cv2,
        now_monotonic_ns=1_300_000_000,
        max_receive_skew_ms=100,
        feature_matcher=_features(),
    )

    assert result.decision == "ABSTAIN"
    assert result.reason == "host_receive_skew_exceeded"
    assert result.features is None


def test_blurred_camera_abstains_instead_of_claiming_disagreement():
    textured = _textured()
    blank = np.full_like(textured, 120)
    result = assess_pair(
        _packet(textured, 1, 1_000_000_000),
        _packet(blank, 1, 1_000_000_000),
        cv2_module=cv2,
        now_monotonic_ns=1_000_000_000,
        min_focus=10,
        feature_matcher=_features(),
    )

    assert result.decision == "ABSTAIN"
    assert result.reason == "camera_b_blur_or_low_features"


def test_feature_only_mode_never_claims_semantic_agreement():
    frame = _textured()
    result = assess_pair(
        _packet(frame, 1, 1_000_000_000),
        _packet(frame.copy(), 1, 1_000_000_000),
        cv2_module=cv2,
        now_monotonic_ns=1_000_000_000,
        feature_matcher=_features(),
    )

    assert result.decision == "COVISIBLE"
    assert result.reason == "feature_overlap_only_no_semantic_detector"
    assert result.claim_a is None
    assert result.claim_b is None


def test_low_feature_overlap_abstains_before_semantic_comparison():
    frame = _textured()
    result = assess_pair(
        _packet(frame, 1, 1_000_000_000),
        _packet(frame.copy(), 1, 1_000_000_000),
        cv2_module=cv2,
        now_monotonic_ns=1_000_000_000,
        m_min=15,
        feature_matcher=_features(inliers=14),
        claim_provider=lambda _frame: PerceptionClaim(
            measured=True, detections_present=False
        ),
    )

    assert result.decision == "ABSTAIN"
    assert result.reason == "feature_overlap_below_threshold"


def test_measured_claims_produce_agreement_and_dispute():
    frame = _textured()
    empty = PerceptionClaim(measured=True, detections_present=False)
    detected = PerceptionClaim(
        measured=True,
        detections_present=True,
        detection_count=1,
        class_ids=(2,),
        occupancy=0.2,
        max_confidence=0.8,
    )
    agreed = assess_pair(
        _packet(frame, 1, 1_000_000_000),
        _packet(frame.copy(), 1, 1_000_000_000),
        cv2_module=cv2,
        now_monotonic_ns=1_000_000_000,
        feature_matcher=_features(),
        claim_provider=lambda _frame: empty,
    )
    claims = iter((empty, detected))
    disputed = assess_pair(
        _packet(frame, 1, 1_000_000_000),
        _packet(frame.copy(), 1, 1_000_000_000),
        cv2_module=cv2,
        now_monotonic_ns=1_000_000_000,
        feature_matcher=_features(),
        claim_provider=lambda _frame: next(claims),
    )

    assert (agreed.decision, agreed.reason) == ("AGREE", "semantic_agreement")
    assert (disputed.decision, disputed.reason) == (
        "DISPUTE",
        "semantic_disagreement",
    )


def test_cycle_tracker_requires_explicit_valid_clean_attack_recovery():
    tracker = CycleTracker()

    assert not tracker.observe("clean", "ABSTAIN", "host_receive_skew_exceeded")
    assert tracker.observe("clean", "AGREE", "semantic_agreement")
    assert not tracker.observe("attack", "ABSTAIN", "camera_b_stale_frame")
    assert tracker.observe("attack", "DISPUTE", "semantic_disagreement")
    assert tracker.observe("recovery", "AGREE", "semantic_agreement")
    assert tracker.completed == 1
    assert tracker.phase == "clean"


def test_cycle_tracker_requires_boxes_in_both_clean_and_recovery_views():
    tracker = CycleTracker()

    assert not tracker.observe(
        "clean", "AGREE", "semantic_agreement", both_detected=False
    )
    assert tracker.phase == "clean"
    assert tracker.observe(
        "clean", "AGREE", "semantic_agreement", both_detected=True
    )
    assert tracker.observe("attack", "DISPUTE", "semantic_disagreement")
    assert not tracker.observe(
        "recovery", "AGREE", "semantic_agreement", both_detected=False
    )
    assert tracker.phase == "recovery"
    assert tracker.observe(
        "recovery", "AGREE", "semantic_agreement", both_detected=True
    )
    assert tracker.completed == 1


def test_feature_overlap_abstention_can_be_recorded_as_attack_evidence():
    tracker = CycleTracker()
    assert tracker.observe("clean", "AGREE", "semantic_agreement")
    assert tracker.observe(
        "attack", "ABSTAIN", "feature_overlap_below_threshold"
    )
    assert tracker.phase == "recovery"


def test_projected_iou_identity_alignment_is_one():
    frame = _textured()
    detection = Detection(cls=2, x=0.5, y=0.5, w=0.4, h=0.25, conf=0.9)

    evidence = projected_iou_evidence(
        frame,
        frame.copy(),
        np.eye(3),
        (detection,),
        (detection,),
        cv2,
    )

    assert evidence.reason == "ok"
    assert evidence.view_overlap_iou == pytest.approx(1.0)
    assert evidence.same_class_best_box_iou == pytest.approx(1.0)
    assert evidence.same_class_candidate_pairs == 1


def test_assessment_reports_projected_iou_in_common_plane():
    frame = _textured()
    detection = Detection(cls=2, x=0.5, y=0.5, w=0.4, h=0.25, conf=0.9)
    observation = DetectorObservation(
        detections=(detection,),
        claim=PerceptionClaim.from_detections((detection,)),
    )

    result = assess_pair(
        _packet(frame, 1, 1_000_000_000),
        _packet(frame.copy(), 1, 1_000_000_000),
        cv2_module=cv2,
        now_monotonic_ns=1_000_000_000,
        alignment_provider=_identity_alignment(),
        claim_provider=lambda _frame: observation,
    )

    assert result.decision == "AGREE"
    assert result.spatial is not None
    assert result.spatial.view_overlap_iou == pytest.approx(1.0)
    assert result.spatial.same_class_best_box_iou == pytest.approx(1.0)


def test_live_overlay_draws_exact_detector_box():
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    detection = Detection(cls=2, x=0.5, y=0.5, w=0.4, h=0.2, conf=0.91)

    _draw_detections(frame, (detection,), ((2, "car"),), cv2)

    # Normalized box maps to (30, 40)-(70, 60); a real coloured border must exist.
    assert tuple(int(value) for value in frame[40, 30]) != (0, 0, 0)
    assert np.count_nonzero(frame) > 0


def test_low_overlap_still_retains_live_yolo_boxes_but_abstains():
    frame = _textured()
    detection = Detection(cls=2, x=0.5, y=0.5, w=0.4, h=0.2, conf=0.91)
    observation = DetectorObservation(
        detections=(detection,),
        claim=PerceptionClaim.from_detections((detection,)),
        class_names=((2, "car"),),
    )

    result = assess_pair(
        _packet(frame, 1, 1_000_000_000),
        _packet(frame.copy(), 1, 1_000_000_000),
        cv2_module=cv2,
        now_monotonic_ns=1_000_000_000,
        m_min=15,
        feature_matcher=_features(inliers=14),
        claim_provider=lambda _frame: observation,
    )

    assert result.decision == "ABSTAIN"
    assert result.reason == "feature_overlap_below_threshold"
    assert result.detections_a == (detection,)
    assert result.detections_b == (detection,)
    assert result.class_names == ((2, "car"),)
