"""Idempotent collector backed by the existing append-only VeriSwarm event log."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from node.events import RESCUE_EVENT, EventLog, read_events, verify_event_chain

from .projection import MissionProjection
from .schema import RescueEventError, validate_rescue_event


class RescueCollectorError(RuntimeError):
    """The event was valid JSON but cannot be safely accepted."""


@dataclass(frozen=True)
class CollectResult:
    accepted: bool
    duplicate: bool
    event: dict[str, Any] | None
    warning: str | None = None


def _canonical(event: Mapping[str, Any]) -> str:
    return json.dumps(event, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _from_log_event(event: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema": event["schema"],
        "mission_id": event["mission_id"],
        "event_id": event["event_id"],
        "source": event["source"],
        "source_seq": event["source_seq"],
        "observed_at_ms": event["observed_at_ms"],
        "kind": event["kind"],
        "payload": event["payload"],
    }


class RescueCollector:
    """Validate, de-duplicate and append producer events.

    Duplicate delivery of the same ``event_id`` and bytes is a successful no-op,
    which makes offline replay idempotent. Reusing an ID with different content or
    moving a producer sequence backwards is rejected.
    """

    def __init__(self, mission_id: str, log_path: str | Path):
        self.mission_id = mission_id
        self.log_path = Path(log_path)
        self.log = EventLog(self.log_path)
        self.projection = MissionProjection(mission_id)
        self._lock = threading.Lock()
        self._by_event_id: dict[str, str] = {}
        self._last_source_seq: dict[str, int] = {}
        self._restore()

    def _restore(self) -> None:
        chain_ok, chain_reason = verify_event_chain(self.log_path)
        if not chain_ok:
            raise RescueCollectorError(f"event_log_chain_invalid:{chain_reason}")
        for persisted in read_events(self.log_path):
            if persisted.get("type") != RESCUE_EVENT:
                continue
            if persisted.get("mission_id") != self.mission_id:
                continue
            try:
                event = validate_rescue_event(
                    _from_log_event(persisted), expected_mission_id=self.mission_id
                )
            except (KeyError, RescueEventError) as error:
                raise RescueCollectorError(f"persisted_rescue_event_invalid:{error}") from error
            self._by_event_id[event["event_id"]] = _canonical(event)
            self._last_source_seq[event["source"]] = max(
                event["source_seq"], self._last_source_seq.get(event["source"], 0)
            )
            self.projection.apply(persisted)

    def collect(self, candidate: Mapping[str, Any]) -> CollectResult:
        event = validate_rescue_event(candidate, expected_mission_id=self.mission_id)
        canonical = _canonical(event)
        with self._lock:
            existing = self._by_event_id.get(event["event_id"])
            if existing is not None:
                if existing != canonical:
                    raise RescueCollectorError("event_id_reused_with_different_content")
                return CollectResult(accepted=True, duplicate=True, event=None)

            last_seq = self._last_source_seq.get(event["source"], 0)
            if event["source_seq"] <= last_seq:
                raise RescueCollectorError(
                    f"source_sequence_not_monotonic:last={last_seq},got={event['source_seq']}"
                )
            warning = None
            if last_seq and event["source_seq"] > last_seq + 1:
                warning = f"source_sequence_gap:expected={last_seq + 1},got={event['source_seq']}"

            persisted = self.log.emit(
                RESCUE_EVENT,
                schema=event["schema"],
                mission_id=event["mission_id"],
                event_id=event["event_id"],
                source=event["source"],
                source_seq=event["source_seq"],
                observed_at_ms=event["observed_at_ms"],
                kind=event["kind"],
                payload=event["payload"],
            )
            if persisted.get("type") != RESCUE_EVENT:
                raise RescueCollectorError("event_persistence_failed")

            self._by_event_id[event["event_id"]] = canonical
            self._last_source_seq[event["source"]] = event["source_seq"]
            self.projection.apply(persisted)
            return CollectResult(
                accepted=True,
                duplicate=False,
                event=persisted,
                warning=warning,
            )

    def state(self) -> dict[str, Any]:
        with self._lock:
            return self.projection.snapshot()

    def report(self) -> dict[str, Any]:
        with self._lock:
            return self.projection.report()
