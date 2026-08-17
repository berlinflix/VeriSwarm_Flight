from __future__ import annotations

import pytest

from node.frame_source import StaticSource
from node.live_node import PerceptionWorker
from protocol.geometry import Pose

np = pytest.importorskip("numpy")


def test_worker_publishes_one_complete_snapshot():
    source = StaticSource(
        np.zeros((8, 8, 3), dtype=np.uint8),
        fixed_pose=Pose(1.0, 2.0, 14.0),
        depth=7.0,
    )
    worker = PerceptionWorker(source, lambda _frame: (0.1, 0.0, 0.0))
    snapshot = worker.sample_once()
    assert snapshot is not None
    assert worker.observation() == (0.1, 0.0, 0.0)
    assert worker.pose().z == 14.0
    assert snapshot.depth == 7.0


def test_stale_snapshot_causes_abstention():
    source = StaticSource(np.zeros((8, 8, 3), dtype=np.uint8))
    worker = PerceptionWorker(
        source, lambda _frame: (0.0, 0.0, 0.0), max_age_s=0.01
    )
    snapshot = worker.sample_once()
    assert worker.snapshot(now_ns=snapshot.captured_ns + worker.max_age_ns + 1) is None


def test_perception_failure_clears_previous_snapshot():
    source = StaticSource(np.zeros((8, 8, 3), dtype=np.uint8))
    worker = PerceptionWorker(source, lambda _frame: (0.0, 0.0, 0.0))
    assert worker.sample_once() is not None
    worker.inference = lambda _frame: (_ for _ in ()).throw(RuntimeError("boom"))
    assert worker.sample_once() is None
    assert worker.observation() is None
    assert "boom" in worker.last_error


def test_invalid_action_never_becomes_a_snapshot():
    source = StaticSource(np.zeros((8, 8, 3), dtype=np.uint8))
    worker = PerceptionWorker(source, lambda _frame: (float("nan"), 0.0, 0.0))
    assert worker.sample_once() is None
    assert worker.snapshot() is None
    assert "invalid normalized action" in worker.last_error


def test_future_dated_snapshot_is_not_accepted():
    source = StaticSource(np.zeros((8, 8, 3), dtype=np.uint8))
    worker = PerceptionWorker(source, lambda _frame: (0.0, 0.0, 0.0))
    snapshot = worker.sample_once()
    assert snapshot is not None
    assert worker.snapshot(now_ns=snapshot.captured_ns - 1) is None
