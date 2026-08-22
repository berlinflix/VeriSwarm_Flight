"""
The event bus: one append-only stream of everything the swarm decides.

Why this exists
---------------
The protocol has always worked and never been watchable. `eval.run_all` prints
CSVs after the fact; a live run prints nothing. In a judged demo that is fatal,
because a defence nobody can see is indistinguishable from no defence at all.
This module is the seam that makes the running protocol legible.

Transport is a **JSONL file**, not a socket. Every producer appends one line per
event; every consumer tails the file. That choice is deliberate:

* it works with the network unplugged, which is a demo beat in its own right;
* a crashed console loses nothing — the log is still on disk and replays;
* the same file is the post-mortem artefact, so "what did the swarm decide" and
  "what did the judge see" are literally the same bytes;
* no extra dependency, no port to fight the venue firewall over.

A WebSocket layer can sit on top later if a remote viewer is ever needed. It is
not needed for a console running on the same LAN, and every additional moving
part is another thing to fail on stage.

Contract
--------
`docs/EVENT_SCHEMA.md` is frozen: producers may add event *types*, but must not
rename or repurpose an existing field. Consumers are written against the schema
and must ignore unknown types and unknown fields, so the two sides can move
independently.

Telemetry must never take down a voting node. Every emit path here swallows its
own exceptions: a full disk or a locked file degrades the demo, it does not
change a consensus decision.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional

#: Default location of the live stream. Gitignored — it is runtime output.
DEFAULT_EVENT_LOG = Path("results/live_events.jsonl")

#: Event types the schema defines. Producers should use these constants rather
#: than string literals so a typo fails at import instead of silently emitting an
#: event no console will ever render.
ROUND = "round"
RECEIPT = "receipt"
COVISIBILITY = "covisibility"
VOTE = "vote"
VERDICT = "verdict"
SAFE_ACTION = "safe_action"
REPUTATION = "reputation"
ISOLATION = "isolation"
POSE = "pose"
FRAME = "frame"
DEPTH = "depth"
ATTACK = "attack"
RESCUE_EVENT = "rescue_event"
LOG = "log"

EVENT_TYPES = frozenset({
    ROUND, RECEIPT, COVISIBILITY, VOTE, VERDICT, SAFE_ACTION,
    REPUTATION, ISOLATION, POSE, FRAME, DEPTH, ATTACK, RESCUE_EVENT, LOG,
})


@contextmanager
def _exclusive_file_lock(path: Path):
    """Cross-process advisory lock used to allocate sequence numbers safely."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as lock_file:
        if os.name == "nt":
            import msvcrt

            lock_file.seek(0)
            if lock_file.tell() == 0 and path.stat().st_size == 0:
                lock_file.write(b"0")
                lock_file.flush()
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def now_ms() -> int:
    """Wall-clock milliseconds. Every event carries one as `t`."""
    return time.time_ns() // 1_000_000


def _last_complete_event(path: Path) -> Optional[dict]:
    """Read the newest valid JSONL record by scanning backward from EOF."""
    if not path.is_file() or path.stat().st_size == 0:
        return None
    with path.open("rb") as fh:
        position = fh.seek(0, os.SEEK_END)
        buffer = b""
        while position > 0:
            take = min(4096, position)
            position -= take
            fh.seek(position)
            buffer = fh.read(take) + buffer
            lines = buffer.split(b"\n")
            # The first item may be partial until position reaches zero. All
            # later items are complete records; walk newest to oldest.
            candidates = lines if position == 0 else lines[1:]
            for raw in reversed(candidates):
                if not raw.strip():
                    continue
                try:
                    event = json.loads(raw)
                    return event if isinstance(event, dict) else None
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
            buffer = lines[0]
    return None


class EventLog:
    """
    Append-only writer for the live event stream.

    Thread-safe: nodes emit from their gRPC worker threads and from the mission
    loop concurrently. Each event is serialised and written under one lock in a
    single `write` call followed by a flush, so a tailing reader never sees half
    an event.

    `seq` is a monotonic counter stamped on every event. A consumer that sees a
    gap knows it dropped something rather than silently rendering an incomplete
    picture — which matters when the thing on screen is a security claim.
    """

    def __init__(
        self,
        path: str | Path = DEFAULT_EVENT_LOG,
        *,
        truncate: bool = False,
        mirror: Optional[Callable[[dict], None]] = None,
    ):
        self.path = Path(path)
        self._lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with _exclusive_file_lock(self._lock_path):
            if truncate and self.path.exists():
                self.path.unlink()
            last = _last_complete_event(self.path)
            self._next_seq = int(last.get("seq", 0)) + 1 if last else 1
        self._subscribers: List[Callable[[dict], None]] = []
        if mirror is not None:
            self._subscribers.append(mirror)

    def subscribe(self, fn: Callable[[dict], None]) -> None:
        """Receive every event in-process, in addition to the file."""
        with self._lock:
            self._subscribers.append(fn)

    def emit(self, type: str, **fields: Any) -> dict:
        """
        Append one event. Returns the event dict (useful in tests).

        Never raises. A telemetry failure must not propagate into the consensus
        path that called it.
        """
        with self._lock:
            try:
                with _exclusive_file_lock(self._lock_path):
                    last = _last_complete_event(self.path)
                    last_seq = int(last.get("seq", 0)) if last else 0
                    seq = max(self._next_seq, last_seq + 1)
                    previous_hash = str(last.get("event_hash", "")) if last else ""
                    # Reserved fields always win. A caller cannot forge ordering
                    # or event type by smuggling them through **fields.
                    payload: Dict[str, Any] = {
                        k: v for k, v in fields.items()
                        if k not in {"seq", "t", "type", "prev_hash", "event_hash"}
                    }
                    payload.update({
                        "seq": seq,
                        "t": now_ms(),
                        "type": type,
                        "prev_hash": previous_hash,
                    })
                    canonical = json.dumps(
                        payload,
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=True,
                        allow_nan=False,
                        default=str,
                    ).encode("utf-8")
                    event = dict(payload)
                    event["event_hash"] = hashlib.sha256(canonical).hexdigest()
                    line = json.dumps(
                        event,
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=True,
                        allow_nan=False,
                    )
                    with self.path.open("a", encoding="utf-8") as fh:
                        fh.write(line + "\n")
                        fh.flush()
                        os.fsync(fh.fileno())
                    self._next_seq = seq + 1
            except (OSError, TypeError, ValueError):
                # Keep telemetry out of the decision path. Return a diagnostic
                # object to in-process subscribers even when persistence fails.
                event = {
                    "seq": self._next_seq,
                    "t": now_ms(),
                    "type": LOG,
                    "level": "error",
                    "message": "event_persistence_failed",
                }
            subscribers = list(self._subscribers)

        for fn in subscribers:
            try:
                fn(event)
            except Exception:
                pass
        return event

    # -- typed helpers -------------------------------------------------------
    #
    # Thin wrappers that pin the field names from the schema. Producers call
    # these rather than emit(type, ...) with hand-written keys, so a rename shows
    # up here once instead of drifting across the codebase.

    # The wire field is `round`, but the Python parameters are `round_index` --
    # a parameter named `round` shadows the builtin inside the method body, which
    # turns any rounding call in the same function into a TypeError.

    def round_start(self, round_index: int, **extra) -> dict:
        return self.emit(ROUND, round=round_index, phase="start", **extra)

    def round_end(self, round_index: int, **extra) -> dict:
        return self.emit(ROUND, round=round_index, phase="end", **extra)

    def receipt(self, node: str, action, model_hash: str, frame_hash: str,
                backend: str, sign_ms: float, round_index: Optional[int] = None,
                **extra) -> dict:
        return self.emit(
            RECEIPT, node=node, round=round_index, action=list(action),
            model_hash=model_hash, frame_hash=frame_hash,
            backend=backend, sign_ms=round_ms(sign_ms), **extra,
        )

    def covisibility(self, node: str, target: str, diag, **extra) -> dict:
        """Emit a `protocol.peer_consensus.CoVisDiagnostic`."""
        return self.emit(
            COVISIBILITY, node=node, target=target,
            covisible=diag.covisible, method=diag.method,
            iou=diag.iou, parallax_deg=diag.parallax_deg,
            orb_inliers=diag.orb_inliers,
            o_min=diag.o_min, phi_min=diag.phi_min,
            detail=diag.describe(), **extra,
        )

    def vote(self, node: str, target: str, decision: str, reason: str,
             delta: Optional[float] = None, **extra) -> dict:
        return self.emit(VOTE, node=node, target=target, decision=decision,
                         reason=reason, delta=delta, **extra)

    def verdict(self, node: str, target: str, outcome: str, acks: int,
                disputes: int, semantic_acks: int,
                consensus_ms: Optional[float] = None, **extra) -> dict:
        """`node` is who tallied — every node tallies independently."""
        return self.emit(
            VERDICT, node=node, target=target, outcome=outcome,
            acks=acks, disputes=disputes, semantic_acks=semantic_acks,
            consensus_ms=round_ms(consensus_ms), **extra,
        )

    def safe_action(self, node: str, action: str, outcome: str,
                    semantic_acks: int, **extra) -> dict:
        return self.emit(SAFE_ACTION, node=node, action=action,
                         outcome=outcome, semantic_acks=semantic_acks, **extra)

    def reputation(self, node: str, value: float, **extra) -> dict:
        return self.emit(REPUTATION, node=node, value=round(value, 4), **extra)

    def isolation(self, node: str, round_index: int, rho: float,
                  n_active_after: int, **extra) -> dict:
        return self.emit(ISOLATION, node=node, round=round_index,
                         rho=round(rho, 4),
                         n_active_after=n_active_after, **extra)

    def pose(self, node: str, xyz, yaw: float = 0.0, pitch: float = 0.0,
             **extra) -> dict:
        return self.emit(POSE, node=node, xyz=[round(float(v), 3) for v in xyz],
                         yaw=round(yaw, 4), pitch=round(pitch, 4), **extra)

    def frame(self, node: str, jpeg_b64: str, w: int, h: int, **extra) -> dict:
        return self.emit(FRAME, node=node, jpeg_b64=jpeg_b64, w=w, h=h, **extra)

    def depth(self, node: str, check, **extra) -> dict:
        """Emit a `perception.depth_check.FreeSpaceCheck`."""
        return self.emit(
            DEPTH, node=node, contradicted=check.contradicted,
            nearest_m=check.measured_range_m,
            required_m=round(check.required_clear_m, 2),
            commanded_forward=round(check.commanded_forward, 3),
            safe_to_proceed=getattr(check, "safe_to_proceed", False),
            reason=check.reason, detail=check.describe(), **extra,
        )

    def attack(self, name: str, armed: bool, targets=(), **extra) -> dict:
        return self.emit(ATTACK, name=name, armed=armed,
                         targets=list(targets), **extra)

    def log(self, message: str, level: str = "info", **extra) -> dict:
        return self.emit(LOG, message=message, level=level, **extra)


def round_ms(value: Optional[float]) -> Optional[float]:
    """Round a millisecond timing for display; passes None through."""
    return None if value is None else round(float(value), 3)


# ---------------------------------------------------------------------------
# Consumer side
# ---------------------------------------------------------------------------


def read_events(path: str | Path = DEFAULT_EVENT_LOG) -> List[dict]:
    """Read the whole log. Malformed lines are skipped, not raised on."""
    p = Path(path)
    if not p.exists():
        return []
    events: List[dict] = []
    with p.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # a torn final line from a writer mid-flush
    return events


def verify_event_chain(path: str | Path = DEFAULT_EVENT_LOG) -> tuple[bool, str]:
    """Verify sequence continuity and the append hash chain.

    This detects corruption, deletion, reordering, and ordinary tampering. A
    separately signed final hash is still required to resist an attacker who can
    rewrite the complete log and recompute every hash.
    """
    previous_hash = ""
    expected_seq = 1
    for event in read_events(path):
        if event.get("seq") != expected_seq:
            return False, f"sequence_gap:expected={expected_seq},got={event.get('seq')}"
        if event.get("prev_hash", "") != previous_hash:
            return False, f"previous_hash_mismatch:seq={expected_seq}"
        claimed = event.get("event_hash")
        if not isinstance(claimed, str):
            return False, f"missing_event_hash:seq={expected_seq}"
        payload = {k: v for k, v in event.items() if k != "event_hash"}
        canonical = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
        actual = hashlib.sha256(canonical).hexdigest()
        if actual != claimed:
            return False, f"event_hash_mismatch:seq={expected_seq}"
        previous_hash = claimed
        expected_seq += 1
    return True, "ok"


def follow(
    path: str | Path = DEFAULT_EVENT_LOG,
    *,
    from_start: bool = True,
    poll_s: float = 0.1,
    stop: Optional[threading.Event] = None,
) -> Iterator[dict]:
    """
    Yield events as they are appended, `tail -f` style.

    Waits for the file to appear rather than failing, so a console can be started
    before the swarm — which is the order an operator will actually use, and
    crashing on a missing file would send them hunting for a bug that isn't one.

    Skips malformed lines: a reader may catch a writer mid-flush, and the next
    poll will see the completed line.
    """
    p = Path(path)
    pos = 0
    if not from_start and p.exists():
        pos = p.stat().st_size

    while stop is None or not stop.is_set():
        if not p.exists():
            time.sleep(poll_s)
            continue
        if p.stat().st_size < pos:
            pos = 0  # file was rotated/truncated between polls
        with p.open("r", encoding="utf-8") as fh:
            fh.seek(pos)
            for line in fh:
                if not line.endswith("\n"):
                    break  # partial write; re-read it next poll
                pos += len(line.encode("utf-8"))
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
        time.sleep(poll_s)
