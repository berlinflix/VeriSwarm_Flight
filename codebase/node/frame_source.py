"""
Where a node's camera frames come from.

One interface, several backends, so the mission loop never knows or cares whether
it is reading a simulator, a webcam, or a folder of JPEGs recorded last week.
That indirection is what keeps the highest-risk part of the project off the
critical path: if AirSim never installs, `FileSource` replays captured frames and
every other piece of the demo is unaffected.

    source = FileSource("sim/demo_frames/alpha")
    frame = source.read()          # BGR ndarray, or None when exhausted
    pose = source.pose()           # protocol.geometry.Pose, or None
    depth = source.depth()         # metric depth ndarray, or None

`AirSimSource` lives in the simulator bridge, not here, so this module stays
importable on a machine with no simulator installed. OpenCV is imported lazily
for the same reason: `node.mission` imports this, and a peer that replays raw
`.npy` frames should not need cv2 on the path.

Every backend returns `None` rather than raising when it has nothing to give. A
missing frame is an ordinary condition mid-flight — a dropped USB packet, a
simulator hiccup — and a mission loop that crashes on one is worse than one that
skips a round.
"""

from __future__ import annotations

import hashlib
import itertools
import json
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

from protocol.geometry import Pose

#: Extensions `FileSource` will pick up, in the order cv2 handles best.
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".bmp")


def frame_bytes(frame, *, color_space: str = "BGR") -> bytes:
    """
    Canonical bytes of a frame, for the receipt's `input_hash`.

    The envelope binds raw pixels *and* their interpretation. Hashing only
    ``tobytes()`` makes differently shaped arrays, dtypes, and colour spaces
    collide whenever their buffers happen to match. JPEG is still avoided
    because encoder output varies across versions and settings.
    """
    if not isinstance(color_space, str) or not color_space:
        raise ValueError("color_space must be a non-empty string")
    shape = tuple(int(v) for v in frame.shape)
    if len(shape) not in (2, 3) or any(v <= 0 for v in shape):
        raise ValueError("frame must be a non-empty HxW or HxWxC array")
    header = json.dumps(
        {
            "color_space": color_space,
            "dtype": str(frame.dtype),
            "shape": shape,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    return b"VSFRAME1" + len(header).to_bytes(4, "big") + header + frame.tobytes(order="C")


def frame_hash(frame, *, color_space: str = "BGR") -> str:
    """SHA-256 of canonical pixels plus shape, dtype, and colour space."""
    return hashlib.sha256(frame_bytes(frame, color_space=color_space)).hexdigest()


class FrameSource:
    """
    Interface every backend implements.

    Not an ABC on purpose: `AirSimSource` is constructed in a module that imports
    the `airsim` package, and forcing it to inherit from here would drag this
    module into that import graph.
    """

    def read(self):
        """Latest BGR frame as an (H, W, 3) ndarray, or None."""
        raise NotImplementedError

    def pose(self) -> Optional[Pose]:
        """Current pose, or None when the backend has no pose to give."""
        return None

    def depth(self):
        """Metric depth as an (H, W) ndarray, or None if unavailable."""
        return None

    def close(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        self.close()


class FileSource(FrameSource):
    """
    Replay frames from a directory. The universal fallback.

    This is what everyone develops against and what the demo falls back to when
    the simulator will not start. It is also what makes a run reproducible: the
    same folder produces the same frames, so the same receipts, so the same
    consensus decisions.

    Frames are sorted by name, so capture them zero-padded (`f_001.jpg`) or the
    ordering will be lexicographic nonsense.

    `loop=True` cycles forever, which is what a 2 Hz mission loop wants from a
    200-frame capture. `poses` optionally supplies a Pose per frame; a single
    Pose is held for all of them, which is right for a fixed camera node.
    """

    def __init__(
        self,
        directory: str | Path,
        *,
        loop: bool = True,
        poses: Optional[Pose | Sequence[Pose]] = None,
        depth_directory: Optional[str | Path] = None,
    ):
        self.directory = Path(directory)
        if not self.directory.is_dir():
            raise FileNotFoundError(f"frame directory not found: {self.directory}")
        self.paths: List[Path] = sorted(
            p for p in self.directory.iterdir()
            if p.suffix.lower() in IMAGE_SUFFIXES
        )
        if not self.paths:
            raise FileNotFoundError(
                f"no frames in {self.directory} "
                f"(looked for {', '.join(IMAGE_SUFFIXES)})"
            )
        self.loop = loop
        self._index = 0
        self._poses = poses
        self._depth_dir = Path(depth_directory) if depth_directory else None
        self._last_index = 0

    def __len__(self) -> int:
        return len(self.paths)

    def read(self):
        import cv2  # lazy

        if self._index >= len(self.paths):
            if not self.loop:
                return None
            self._index = 0
        path = self.paths[self._index]
        self._last_index = self._index
        self._index += 1
        frame = cv2.imread(str(path))
        if frame is None:
            # A corrupt file mid-capture should skip, not kill the mission.
            return self.read() if self._index < len(self.paths) else None
        return frame

    def pose(self) -> Optional[Pose]:
        if self._poses is None:
            return None
        if isinstance(self._poses, Pose):
            return self._poses
        if not self._poses:
            return None
        return self._poses[self._last_index % len(self._poses)]

    def depth(self):
        """Load `<name>.npy` from the depth directory, matched by frame name."""
        if self._depth_dir is None:
            return None
        import numpy as np

        stem = self.paths[self._last_index].stem
        candidate = self._depth_dir / f"{stem}.npy"
        if not candidate.exists():
            return None
        try:
            return np.load(candidate)
        except (OSError, ValueError):
            return None


class WebcamSource(FrameSource):
    """
    A USB camera. This is the physical node on the table.

    Opened lazily on the first `read()` so constructing a node does not seize the
    camera — several processes are started in sequence during setup, and a device
    grabbed early is a device unavailable to the one that actually needs it.

    `pose` is fixed and supplied by the caller: a tabletop camera does not move,
    but it still has to appear in the co-visibility geometry like any other node.
    """

    def __init__(
        self,
        index: int = 0,
        *,
        width: int = 640,
        height: int = 360,
        fixed_pose: Optional[Pose] = None,
    ):
        self.index = index
        self.width = width
        self.height = height
        self._fixed_pose = fixed_pose
        self._capture = None

    def _ensure_open(self):
        import cv2  # lazy

        if self._capture is None:
            capture = cv2.VideoCapture(self.index)
            if not capture.isOpened():
                raise RuntimeError(
                    f"could not open camera {self.index}. On Linux check "
                    f"/dev/video*; another process may already hold it."
                )
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
            self._capture = capture
        return self._capture

    def read(self):
        ok, frame = self._ensure_open().read()
        return frame if ok else None

    def pose(self) -> Optional[Pose]:
        return self._fixed_pose

    def close(self) -> None:
        if self._capture is not None:
            self._capture.release()
            self._capture = None


class StaticSource(FrameSource):
    """
    Repeat a single in-memory frame. For tests and for holding a scene still
    while an attack is armed, without touching the filesystem.
    """

    def __init__(self, frame, *, fixed_pose: Optional[Pose] = None, depth=None):
        self._frame = frame
        self._pose = fixed_pose
        self._depth = depth

    def read(self):
        return self._frame

    def pose(self) -> Optional[Pose]:
        return self._pose

    def depth(self):
        return self._depth


def cycle_sources(sources: Iterable[FrameSource]):
    """Round-robin several sources — one process driving several camera nodes."""
    return itertools.cycle(list(sources))
