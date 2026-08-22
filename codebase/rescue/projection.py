"""Deterministic projection from rescue events to responder-facing mission state."""

from __future__ import annotations

import math
from copy import deepcopy
from typing import Any

from .schema import HAZARD_CLASSES, PERSON_CLASS


PRIORITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "REVIEW": 2, "INFO": 3}


def _distance(a: list[float], b: list[float]) -> float:
    return math.sqrt(sum((left - right) ** 2 for left, right in zip(a, b)))


class MissionProjection:
    """Replayable state used by both the HTTP API and final report generator."""

    def __init__(self, mission_id: str, *, person_dedup_radius_m: float = 5.0):
        self.mission_id = mission_id
        self.person_dedup_radius_m = float(person_dedup_radius_m)
        self.scenario_id: str | None = None
        self.coordinate_frame: str | None = None
        self.mission_status = "NOT_STARTED"
        self.vehicles: dict[str, dict[str, Any]] = {}
        self.assignments: dict[str, dict[str, Any]] = {}
        self.coverage: dict[str, dict[str, Any]] = {}
        self.people: dict[str, dict[str, Any]] = {}
        self.hazards: dict[str, dict[str, Any]] = {}
        self.alerts: dict[str, dict[str, Any]] = {}
        self.reassignments: list[dict[str, Any]] = []
        self.events_applied = 0

    def apply(self, event: dict[str, Any]) -> None:
        """Apply one persisted ``rescue_event`` record."""
        if event.get("type") != "rescue_event" or event.get("mission_id") != self.mission_id:
            return
        kind = event["kind"]
        payload = event["payload"]
        handler = getattr(self, f"_apply_{kind}")
        handler(event, payload)
        self.events_applied += 1

    def _apply_mission_started(self, event: dict, payload: dict) -> None:
        self.scenario_id = payload["scenario_id"]
        self.coordinate_frame = payload["coordinate_frame"]
        self.mission_status = "ACTIVE"

    def _vehicle(self, node: str) -> dict[str, Any]:
        return self.vehicles.setdefault(
            node,
            {"node": node, "state": "UNKNOWN", "link_state": "UNKNOWN"},
        )

    def _apply_assignment(self, event: dict, payload: dict) -> None:
        assignment = {
            "node": payload["node"],
            "sector_id": payload["sector_id"],
            "cells_total": payload["cells_total"],
            "event_id": event["event_id"],
        }
        self.assignments[payload["node"]] = assignment
        self._vehicle(payload["node"])["sector_id"] = payload["sector_id"]

    def _apply_coverage(self, event: dict, payload: dict) -> None:
        self.coverage[payload["sector_id"]] = {
            **payload,
            "event_id": event["event_id"],
            "observed_at_ms": event["observed_at_ms"],
        }

    def _apply_vehicle_state(self, event: dict, payload: dict) -> None:
        vehicle = self._vehicle(payload["node"])
        vehicle.update(payload)
        vehicle["observed_at_ms"] = event["observed_at_ms"]

    def _person_marker_for(self, payload: dict) -> str | None:
        position = payload.get("position_ned")
        if position is None:
            return None
        for marker_id, marker in self.people.items():
            existing = marker.get("position_ned")
            if existing is not None and _distance(position, existing) <= self.person_dedup_radius_m:
                return marker_id
        return None

    def _apply_observation(self, event: dict, payload: dict) -> None:
        if payload["class_id"] == PERSON_CLASS:
            marker_id = self._person_marker_for(payload) or f"person-{len(self.people) + 1:03d}"
            marker = self.people.setdefault(
                marker_id,
                {
                    "marker_id": marker_id,
                    "class_id": PERSON_CLASS,
                    "confidence": 0.0,
                    "sources": [],
                    "observation_ids": [],
                    "modalities": [],
                    "requires_responder_confirmation": True,
                },
            )
            marker["confidence"] = max(marker["confidence"], payload["confidence"])
            marker["last_observed_at_ms"] = event["observed_at_ms"]
            marker["position_ned"] = payload.get("position_ned", marker.get("position_ned"))
            marker["uncertainty_m"] = payload.get("uncertainty_m", marker.get("uncertainty_m"))
            for field, value in (
                ("sources", payload["node"]),
                ("observation_ids", payload["observation_id"]),
                ("modalities", payload["modality"]),
            ):
                if value not in marker[field]:
                    marker[field].append(value)
                    marker[field].sort()
            priority = "HIGH" if marker["confidence"] >= 0.8 or len(marker["sources"]) >= 2 else "REVIEW"
            self.alerts[f"alert:{marker_id}"] = {
                "alert_id": f"alert:{marker_id}",
                "priority": priority,
                "kind": "PERSON_CANDIDATE",
                "target_id": marker_id,
                "message": "Person candidate requires responder review",
                "source_count": len(marker["sources"]),
                "updated_at_ms": event["observed_at_ms"],
            }
        elif payload["class_id"] in HAZARD_CLASSES:
            # Detector-originated hazards remain observations until promoted by a
            # hazard event. Keep the raw item visible without inventing geometry.
            observation_id = payload["observation_id"]
            self.hazards.setdefault(
                f"observation:{observation_id}",
                {
                    "hazard_id": f"observation:{observation_id}",
                    "class_id": payload["class_id"],
                    "confidence": payload["confidence"],
                    "source": payload["node"],
                    "position_ned": payload.get("position_ned"),
                    "status": "OBSERVATION_ONLY",
                },
            )

    def _apply_hazard(self, event: dict, payload: dict) -> None:
        hazard = {
            **payload,
            "status": "MAPPED_HAZARD",
            "observed_at_ms": event["observed_at_ms"],
        }
        self.hazards[payload["hazard_id"]] = hazard
        priority = "CRITICAL" if payload["class_id"] in {"fire", "smoke"} else "HIGH"
        self.alerts[f"alert:{payload['hazard_id']}"] = {
            "alert_id": f"alert:{payload['hazard_id']}",
            "priority": priority,
            "kind": "HAZARD",
            "target_id": payload["hazard_id"],
            "message": f"Mapped hazard: {payload['class_id']}",
            "updated_at_ms": event["observed_at_ms"],
        }

    def _apply_authorization(self, event: dict, payload: dict) -> None:
        vehicle = self._vehicle(payload["node"])
        vehicle["authorization"] = payload["decision"]
        vehicle["authorization_reason"] = payload["reason"]
        if payload["decision"] in {"HOLD", "QUARANTINE"}:
            vehicle["state"] = "QUARANTINED" if payload["decision"] == "QUARANTINE" else "HOLD"
            self.alerts[f"alert:authorization:{payload['node']}"] = {
                "alert_id": f"alert:authorization:{payload['node']}",
                "priority": "CRITICAL" if payload["decision"] == "QUARANTINE" else "HIGH",
                "kind": "AUTHORIZATION",
                "target_id": payload["node"],
                "message": f"{payload['node']} {payload['decision']}: {payload['reason']}",
                "updated_at_ms": event["observed_at_ms"],
            }

    def _apply_task_reassigned(self, event: dict, payload: dict) -> None:
        self.reassignments.append({**payload, "event_id": event["event_id"]})

    def _apply_link_state(self, event: dict, payload: dict) -> None:
        vehicle = self._vehicle(payload["node"])
        vehicle["link_state"] = payload["state"]
        vehicle["link_observed_at_ms"] = event["observed_at_ms"]

    def _apply_mission_completed(self, event: dict, payload: dict) -> None:
        self.mission_status = payload["status"]

    def _coverage_summary(self) -> dict[str, Any]:
        visited = sum(item["visited_cells"] for item in self.coverage.values())
        total = sum(item["total_cells"] for item in self.coverage.values())
        return {
            "visited_cells": visited,
            "total_cells": total,
            "percent": round(100.0 * visited / total, 2) if total else 0.0,
            "sectors_reporting": len(self.coverage),
        }

    def snapshot(self) -> dict[str, Any]:
        alerts = sorted(
            self.alerts.values(),
            key=lambda item: (
                PRIORITY_ORDER[item["priority"]],
                -int(item["updated_at_ms"]),
                item["alert_id"],
            ),
        )
        return deepcopy({
            "schema": "veriswarm.rescue.state.v1",
            "mission_id": self.mission_id,
            "scenario_id": self.scenario_id,
            "coordinate_frame": self.coordinate_frame,
            "mission_status": self.mission_status,
            "events_applied": self.events_applied,
            "coverage": self._coverage_summary(),
            "vehicles": sorted(self.vehicles.values(), key=lambda item: item["node"]),
            "assignments": sorted(self.assignments.values(), key=lambda item: item["node"]),
            "people": sorted(self.people.values(), key=lambda item: item["marker_id"]),
            "hazards": sorted(self.hazards.values(), key=lambda item: item["hazard_id"]),
            "alerts": alerts,
            "reassignments": list(self.reassignments),
        })

    def report(self) -> dict[str, Any]:
        state = self.snapshot()
        state["schema"] = "veriswarm.rescue.report.v1"
        state["summary"] = {
            "person_candidates": len(state["people"]),
            "mapped_hazards": sum(
                1 for hazard in state["hazards"] if hazard["status"] == "MAPPED_HAZARD"
            ),
            "critical_alerts": sum(
                1 for alert in state["alerts"] if alert["priority"] == "CRITICAL"
            ),
            "vehicles_reporting": len(state["vehicles"]),
        }
        state["limitations"] = [
            "Person candidates require responder confirmation.",
            "Image-space observations without position_ned are not geolocated.",
            "Observation confidence is model output, not calibrated rescue probability.",
        ]
        return state
