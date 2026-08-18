"""Deterministic perception-to-consensus-to-command mission loop.

This is the integration seam used by file replay, SITL, and later hardware. It
does not know whether frames and poses came from Gazebo, AirSim, a recording, or
a camera. It also never talks to motors directly: the supplied ``command_sink``
receives only the action released by :class:`SafetySupervisor`.
"""

from __future__ import annotations

import math
import numbers
import time
from dataclasses import dataclass
from typing import Callable, Optional

from perception.claim import PerceptionResult
from perception.depth_check import check_free_space, nearest_range
from perception.safety_supervisor import Authorization, HOLD_ACTION, SafetySupervisor

from .events import EventLog
from .frame_source import FrameSource, frame_bytes, frame_hash


@dataclass(frozen=True)
class HealthState:
    perception_healthy: bool
    state_estimate_healthy: bool
    geofence_clear: bool
    autopilot_guard_healthy: bool
    all_axis_clearance_confirmed: bool = False
    current_speed_mps: float = 0.0
    pose_uncertainty_m: float = 0.0

    def __post_init__(self) -> None:
        flags = (
            self.perception_healthy,
            self.state_estimate_healthy,
            self.geofence_clear,
            self.autopilot_guard_healthy,
            self.all_axis_clearance_confirmed,
        )
        if not all(type(flag) is bool for flag in flags):
            raise ValueError("health flags must be actual booleans")
        if not math.isfinite(self.current_speed_mps) or self.current_speed_mps < 0.0:
            raise ValueError("current_speed_mps must be finite and non-negative")
        if not math.isfinite(self.pose_uncertainty_m) or self.pose_uncertainty_m < 0.0:
            raise ValueError("pose_uncertainty_m must be finite and non-negative")


@dataclass(frozen=True)
class MissionStep:
    round_index: int
    requested_action: tuple[float, float, float]
    authorization: Authorization
    round_outcome: object | None


class MissionRunner:
    """Run one fail-closed mission step at a time for deterministic testing."""

    def __init__(
        self,
        *,
        node_id: str,
        source: FrameSource,
        inference: Callable[[object], PerceptionResult],
        originator,
        model_hash: str,
        health_provider: Callable[[], HealthState],
        command_sink: Callable[[tuple[float, float, float], Authorization], None],
        events: EventLog,
        supervisor: Optional[SafetySupervisor] = None,
    ):
        self.node_id = node_id
        self.source = source
        self.inference = inference
        self.originator = originator
        self.model_hash = model_hash
        self.health_provider = health_provider
        self.command_sink = command_sink
        self.events = events
        self.supervisor = supervisor or SafetySupervisor()
        self.round_index = 0

    def _hold(self, reason: str) -> MissionStep:
        authorization = Authorization(False, HOLD_ACTION, reason)
        self.command_sink(HOLD_ACTION, authorization)
        self.events.safe_action(
            self.node_id,
            "HOLD",
            "NO_DECISION",
            semantic_acks=0,
            reason=reason,
        )
        return MissionStep(self.round_index, HOLD_ACTION, authorization, None)

    def step(self) -> MissionStep:
        self.round_index += 1
        self.events.round_start(self.round_index)
        try:
            frame = self.source.read()
        except Exception as exc:
            self.events.log(f"frame_source_failure:{type(exc).__name__}", level="error")
            result = self._hold("frame_source_failure")
            self.events.round_end(self.round_index, authorized=False)
            return result
        if frame is None:
            result = self._hold("frame_unavailable")
            self.events.round_end(self.round_index, authorized=False)
            return result

        try:
            health = self.health_provider()
            perception = self.inference(frame)
            if not isinstance(perception, PerceptionResult):
                raise ValueError("inference must return PerceptionResult")
            requested = perception.action
        except Exception as exc:
            self.events.log(f"perception_failure:{type(exc).__name__}", level="error")
            result = self._hold("perception_failure")
            self.events.round_end(self.round_index, authorized=False)
            return result

        if len(requested) != 3 or not all(
            math.isfinite(v) and -1.0 <= v <= 1.0 for v in requested
        ):
            result = self._hold("invalid_action")
            self.events.round_end(self.round_index, authorized=False)
            return result

        try:
            depth = self.source.depth()
            if depth is None:
                measured_range = None
            elif isinstance(depth, numbers.Real):
                measured_range = float(depth)
            else:
                measured_range = nearest_range(depth)
            clearance = check_free_space(
                requested,
                measured_range,
                current_speed_mps=health.current_speed_mps,
            )
        except Exception as exc:
            self.events.log(f"depth_failure:{type(exc).__name__}", level="error")
            result = self._hold("depth_failure")
            self.events.round_end(self.round_index, authorized=False)
            return result
        self.events.depth(self.node_id, clearance, round=self.round_index)

        try:
            pose = self.source.pose()
            outcome = self.originator.originate(
                requested,
                self.model_hash,
                input_bytes=frame_bytes(frame),
                pose=pose,
                pose_uncertainty_m=health.pose_uncertainty_m,
                perception=perception.claim,
            )
        except Exception as exc:
            self.events.log(f"attestation_failure:{type(exc).__name__}", level="error")
            result = self._hold("attestation_failure")
            self.events.round_end(self.round_index, authorized=False)
            return result
        consensus = outcome.consensus
        authorization = self.supervisor.authorize(
            requested,
            consensus,
            forward_clearance=clearance,
            perception_healthy=health.perception_healthy,
            state_estimate_healthy=health.state_estimate_healthy and pose is not None,
            geofence_clear=health.geofence_clear,
            autopilot_guard_healthy=health.autopilot_guard_healthy,
            all_axis_clearance_confirmed=health.all_axis_clearance_confirmed,
            evidence_receipt=outcome.receipt,
            now_ns=outcome.completed_at_ns,
        )

        self.events.receipt(
            self.node_id,
            requested,
            self.model_hash,
            frame_hash(frame),
            backend=self.originator.manifest["nodes"][self.node_id].get(
                "backend", "unknown"
            ),
            sign_ms=outcome.sign_ms,
            round_index=self.round_index,
        )
        self.events.verdict(
            self.node_id,
            self.node_id,
            consensus.outcome.value,
            acks=consensus.ack_count,
            disputes=consensus.dispute_count,
            semantic_acks=consensus.semantic_ack_count,
            consensus_ms=outcome.consensus_ms,
            round=self.round_index,
        )
        self.events.safe_action(
            self.node_id,
            "EXECUTE" if authorization.allowed else "HOLD",
            consensus.outcome.value,
            semantic_acks=consensus.semantic_ack_count,
            reason=authorization.reason,
            requested=list(requested),
            released=list(authorization.action),
            round=self.round_index,
        )
        self.command_sink(authorization.action, authorization)
        self.events.round_end(self.round_index, authorized=authorization.allowed)
        return MissionStep(
            self.round_index,
            requested,  # type: ignore[arg-type]
            authorization,
            outcome,
        )

    def run(self, rounds: int, period_s: float = 0.5) -> list[MissionStep]:
        if rounds < 1 or period_s < 0.0:
            raise ValueError("rounds must be >= 1 and period_s non-negative")
        results = []
        deadline = time.monotonic()
        for _ in range(rounds):
            results.append(self.step())
            deadline += period_s
            remaining = deadline - time.monotonic()
            if remaining > 0.0:
                time.sleep(remaining)
        return results
