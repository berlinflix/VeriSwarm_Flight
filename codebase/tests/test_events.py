"""
The event contract and the frame sources.

These pin the two properties the console depends on and cannot check for itself:
events survive a torn write, and `seq` never silently skips. A console that
renders an incomplete picture as a complete one is worse than one that crashes,
because the thing on screen is a security claim.
"""

from __future__ import annotations

import json
import threading

import pytest

from node import events as ev
from node.events import EventLog, follow, read_events
from node.frame_source import FileSource, StaticSource, frame_bytes, frame_hash
from protocol.geometry import Pose

np = pytest.importorskip("numpy")


# ---------------------------------------------------------------------------
# EventLog
# ---------------------------------------------------------------------------


def test_every_event_carries_seq_type_and_time(tmp_path):
    log = EventLog(tmp_path / "e.jsonl")
    event = log.emit(ev.LOG, message="hello")

    assert event["seq"] == 1
    assert event["type"] == "log"
    assert isinstance(event["t"], int)


def test_seq_is_monotonic(tmp_path):
    log = EventLog(tmp_path / "e.jsonl")
    for _ in range(5):
        log.log("x")
    assert [e["seq"] for e in read_events(tmp_path / "e.jsonl")] == [1, 2, 3, 4, 5]


def test_emit_never_raises_on_an_unwritable_path(tmp_path):
    """
    Telemetry must not take down a voting node. A full disk degrades the demo;
    it must not change a consensus decision.
    """
    log = EventLog(tmp_path / "nested" / "e.jsonl")
    log.path = tmp_path  # a directory — writing to it will fail
    assert log.emit(ev.LOG, message="still returns") is not None


def test_subscribers_receive_events_and_cannot_break_the_emitter(tmp_path):
    seen = []
    log = EventLog(tmp_path / "e.jsonl")
    log.subscribe(seen.append)
    log.subscribe(lambda e: (_ for _ in ()).throw(RuntimeError("bad consumer")))
    log.subscribe(seen.append)

    log.log("x")
    assert len(seen) == 2, "a throwing subscriber must not starve the others"


def test_concurrent_emits_produce_intact_lines(tmp_path):
    """
    Nodes emit from gRPC worker threads and the mission loop at once. A reader
    tailing the file must never see half an event.
    """
    path = tmp_path / "e.jsonl"
    log = EventLog(path)

    def spam():
        for _ in range(50):
            log.emit(ev.LOG, message="x" * 200)

    threads = [threading.Thread(target=spam) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    lines = path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 200
    for line in lines:
        json.loads(line)  # raises if torn
    assert sorted(json.loads(x)["seq"] for x in lines) == list(range(1, 201))


def test_reader_skips_a_torn_trailing_line(tmp_path):
    path = tmp_path / "e.jsonl"
    log = EventLog(path)
    log.log("complete")
    with path.open("a", encoding="utf-8") as fh:
        fh.write('{"seq":2,"type":"lo')  # writer caught mid-flush

    events = read_events(path)
    assert len(events) == 1
    assert events[0]["message"] == "complete"


def test_read_events_on_a_missing_file_is_empty(tmp_path):
    assert read_events(tmp_path / "absent.jsonl") == []


# ---------------------------------------------------------------------------
# Typed helpers keep the schema field names
# ---------------------------------------------------------------------------


def test_round_helpers_do_not_shadow_the_builtin(tmp_path):
    """
    A parameter named `round` shadows the builtin inside the method body, so any
    rounding call in the same function raises TypeError. These helpers take
    `round_index` and emit the wire field `round`.
    """
    log = EventLog(tmp_path / "e.jsonl")
    assert log.round_start(3)["round"] == 3
    assert log.isolation("alpha", round_index=6, rho=0.6, n_active_after=4)["rho"] == 0.6


def test_verdict_carries_the_tallying_node_and_semantic_count(tmp_path):
    log = EventLog(tmp_path / "e.jsonl")
    event = log.verdict("bravo", "alpha", "REJECTED", acks=1, disputes=2,
                        semantic_acks=2, consensus_ms=12.4)

    assert event["node"] == "bravo", "who tallied, not who originated"
    assert event["semantic_acks"] == 2


def test_covisibility_helper_accepts_a_real_diagnostic(tmp_path):
    from protocol.peer_consensus import CoVisDiagnostic

    diag = CoVisDiagnostic(False, "low_parallax", 0.94, None, 0.1, 15,
                           parallax_deg=1.2, phi_min=23.0)
    event = EventLog(tmp_path / "e.jsonl").covisibility("bravo", "alpha", diag)

    assert event["method"] == "low_parallax"
    assert event["parallax_deg"] == 1.2
    assert "redundant viewpoint" in event["detail"]


def test_depth_helper_accepts_a_real_check(tmp_path):
    from perception.depth_check import check_free_space

    check = check_free_space((1.0, 0.0, 0.0), measured_range_m=7.9)
    event = EventLog(tmp_path / "e.jsonl").depth("alpha", check)

    assert event["contradicted"] is True
    assert event["nearest_m"] == 7.9


# ---------------------------------------------------------------------------
# follow()
# ---------------------------------------------------------------------------


def test_follow_yields_appended_events(tmp_path):
    path = tmp_path / "e.jsonl"
    log = EventLog(path)
    log.log("first")
    log.log("second")

    stop = threading.Event()
    stream = follow(path, poll_s=0.01, stop=stop)
    got = [next(stream), next(stream)]
    stop.set()

    assert [e["message"] for e in got] == ["first", "second"]


def test_follow_waits_for_a_file_that_does_not_exist_yet(tmp_path):
    """A console started before the swarm must wait, not crash."""
    path = tmp_path / "later.jsonl"
    stop = threading.Event()
    received = []

    def consume():
        for event in follow(path, poll_s=0.01, stop=stop):
            received.append(event)
            return

    thread = threading.Thread(target=consume, daemon=True)
    thread.start()
    EventLog(path).log("arrived")
    thread.join(timeout=5.0)
    stop.set()

    assert [e["message"] for e in received] == ["arrived"]


# ---------------------------------------------------------------------------
# Frame sources
# ---------------------------------------------------------------------------


def _write_frames(tmp_path, n=3):
    import cv2

    directory = tmp_path / "frames"
    directory.mkdir()
    for i in range(n):
        img = np.full((16, 24, 3), i * 40, dtype=np.uint8)
        cv2.imwrite(str(directory / f"f_{i:03d}.jpg"), img)
    return directory


def test_file_source_reads_in_sorted_order(tmp_path):
    pytest.importorskip("cv2")
    directory = _write_frames(tmp_path)
    source = FileSource(directory, loop=False)

    frames = [source.read() for _ in range(3)]
    assert all(f is not None for f in frames)
    assert source.read() is None, "should exhaust when loop=False"


def test_file_source_loops(tmp_path):
    pytest.importorskip("cv2")
    source = FileSource(_write_frames(tmp_path), loop=True)
    assert [source.read() is not None for _ in range(7)] == [True] * 7


def test_file_source_rejects_an_empty_directory(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(FileNotFoundError, match="no frames"):
        FileSource(empty)


def test_file_source_rejects_a_missing_directory(tmp_path):
    with pytest.raises(FileNotFoundError, match="not found"):
        FileSource(tmp_path / "nope")


def test_file_source_holds_a_fixed_pose(tmp_path):
    pytest.importorskip("cv2")
    pose = Pose(0.0, 0.0, 14.0)
    source = FileSource(_write_frames(tmp_path), poses=pose)
    source.read()
    assert source.pose() is pose


def test_file_source_loads_matching_depth(tmp_path):
    pytest.importorskip("cv2")
    directory = _write_frames(tmp_path)
    depth_dir = tmp_path / "depth"
    depth_dir.mkdir()
    np.save(depth_dir / "f_000.npy", np.full((16, 24), 7.5))

    source = FileSource(directory, depth_directory=depth_dir)
    source.read()
    depth = source.depth()

    assert depth is not None and depth.mean() == pytest.approx(7.5)


def test_missing_depth_returns_none_not_an_error(tmp_path):
    pytest.importorskip("cv2")
    source = FileSource(_write_frames(tmp_path), depth_directory=tmp_path / "absent")
    source.read()
    assert source.depth() is None


def test_static_source_repeats_one_frame():
    frame = np.zeros((8, 8, 3), dtype=np.uint8)
    source = StaticSource(frame, fixed_pose=Pose(0, 0, 10))
    assert source.read() is frame
    assert source.read() is frame
    assert source.pose().z == 10


# ---------------------------------------------------------------------------
# Frame hashing — what binds a receipt to an image
# ---------------------------------------------------------------------------


def test_frame_hash_is_stable_and_content_addressed():
    a = np.zeros((8, 8, 3), dtype=np.uint8)
    b = np.zeros((8, 8, 3), dtype=np.uint8)
    c = a.copy()
    c[0, 0] = 255

    assert frame_hash(a) == frame_hash(b)
    assert frame_hash(a) != frame_hash(c)
    assert len(frame_hash(a)) == 64


def test_frame_bytes_uses_the_raw_buffer():
    """
    Not a re-encode: JPEG compression varies across library versions and quality
    settings, so two nodes hashing the same frame could disagree. The raw buffer
    is exactly what the detector consumed.
    """
    frame = np.arange(48, dtype=np.uint8).reshape(4, 4, 3)
    assert frame_bytes(frame) == frame.tobytes()
