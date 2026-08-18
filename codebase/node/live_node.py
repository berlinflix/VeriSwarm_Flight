"""Non-blocking live perception state for attestation peer servers."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from perception.claim import PerceptionClaim, PerceptionResult
from protocol.geometry import Pose

from .frame_source import FrameSource, frame_hash


@dataclass(frozen=True)
class PerceptionSnapshot:
    frame: object
    frame_hash: str
    action: tuple[float, float, float]
    claim: PerceptionClaim
    pose: Optional[Pose]
    depth: object
    captured_ns: int


class PerceptionWorker:
    """Continuously update a bounded latest-value snapshot.

    gRPC handlers read the latest complete snapshot and never run a detector on
    their request thread. Stale or failed perception returns ``None`` so the
    peer abstains rather than ACKing evidence it does not have.
    """

    def __init__(
        self,
        source: FrameSource,
        inference: Callable[[object], PerceptionResult],
        *,
        rate_hz: float = 10.0,
        max_age_s: float = 0.25,
    ):
        if rate_hz <= 0.0 or max_age_s <= 0.0:
            raise ValueError("rate_hz and max_age_s must be positive")
        self.source = source
        self.inference = inference
        self.period_s = 1.0 / rate_hz
        self.max_age_ns = int(max_age_s * 1e9)
        self._snapshot: Optional[PerceptionSnapshot] = None
        self._error: Optional[str] = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @property
    def last_error(self) -> Optional[str]:
        with self._lock:
            return self._error

    def sample_once(self) -> Optional[PerceptionSnapshot]:
        try:
            frame = self.source.read()
            if frame is None:
                raise RuntimeError("frame_unavailable")
            captured_ns = time.time_ns()
            result = self.inference(frame)
            if not isinstance(result, PerceptionResult):
                raise ValueError("inference must return PerceptionResult with a measured claim")
            action = result.action
            snapshot = PerceptionSnapshot(
                frame=frame,
                frame_hash=frame_hash(frame),
                action=action,  # type: ignore[arg-type]
                claim=result.claim,
                pose=self.source.pose(),
                depth=self.source.depth(),
                captured_ns=captured_ns,
            )
            with self._lock:
                self._snapshot = snapshot
                self._error = None
            return snapshot
        except Exception as exc:
            with self._lock:
                self._snapshot = None
                self._error = f"{type(exc).__name__}:{exc}"
            return None

    def snapshot(self, *, now_ns: Optional[int] = None) -> Optional[PerceptionSnapshot]:
        with self._lock:
            snapshot = self._snapshot
        now = time.time_ns() if now_ns is None else now_ns
        age_ns = 0 if snapshot is None else now - snapshot.captured_ns
        if snapshot is None or age_ns < 0 or age_ns > self.max_age_ns:
            return None
        return snapshot

    def observation(self):
        snapshot = self.snapshot()
        return None if snapshot is None else snapshot.action

    def pose(self) -> Optional[Pose]:
        snapshot = self.snapshot()
        return None if snapshot is None else snapshot.pose

    def _run(self) -> None:
        deadline = time.monotonic()
        while not self._stop.is_set():
            self.sample_once()
            deadline += self.period_s
            self._stop.wait(max(0.0, deadline - time.monotonic()))

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="veriswarm-perception",
            daemon=True,
        )
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, 2.0 * self.period_s))
        self.source.close()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.close()
