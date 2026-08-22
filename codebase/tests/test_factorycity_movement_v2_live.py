from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import cosysairsim
import pytest

from rescue.movement_security import load_movement_contract
from tools.movement_authorization_link import ROSTER, build_snapshot
from sim.cosys.factorycity.movement_v2 import (
    CellLedger,
    DurableMovementEvents,
    GatedCommandDispatcher,
    MovementV2Error,
    load_sensor_movement_extension,
)
from sim.cosys.factorycity.tools.run_factorycity_movement_v2 import (
    AuthorizationFileProvider,
    LiveCoSimCommandAdapter,
    _gate_separation,
    _outbox_factory,
    _preflight_command,
)


ROOT = Path(__file__).parents[1]
FACTORYCITY = ROOT / "sim" / "cosys" / "factorycity"
CONTRACT_PATH = FACTORYCITY / "factorycity_joint_movement_contract.development.json"
EXTENSION_PATH = FACTORYCITY / "factorycity_sensor_movement_extension.v2.development.json"
CELL_EXTENSION_PATH = ROOT / "config" / "rescue_cell_movement_event_extension.v1.json"
NOMINAL_RUNNER = FACTORYCITY / "tools" / "run_factorycity_ab_swarm.py"
LIVE_RUNNER = FACTORYCITY / "tools" / "run_factorycity_movement_v2.py"


class _Future:
    def __init__(self, value=None):
        self.value = value

    def join(self):
        return self.value


class _Client:
    def __init__(self):
        self.calls = []
        self.local = [0.0, 0.0, -10.0]
        self.world = [0.0, 0.0, -10.0]

    @staticmethod
    def _vector(values):
        return SimpleNamespace(x_val=values[0], y_val=values[1], z_val=values[2])

    def getMultirotorState(self, *, vehicle_name):
        return SimpleNamespace(
            kinematics_estimated=SimpleNamespace(position=self._vector(self.local)),
            landed_state=cosysairsim.LandedState.Flying,
        )

    def simGetObjectPose(self, name, *, ned):
        assert ned is True
        return SimpleNamespace(position=self._vector(self.world))

    def moveByVelocityZAsync(
        self,
        vx,
        vy,
        target_z,
        duration,
        *,
        drivetrain,
        yaw_mode,
        vehicle_name,
    ):
        self.calls.append(("velocity_z", vehicle_name, vx, vy, target_z, duration))
        return _Future("velocity-finished")

    def hoverAsync(self, *, vehicle_name):
        self.calls.append(("hover", vehicle_name))
        return _Future("hover-finished")

    def landAsync(self, *, timeout_sec, vehicle_name):
        self.calls.append(("land", vehicle_name, timeout_sec))
        return _Future("land-finished")


def _loaded():
    contract = load_movement_contract(CONTRACT_PATH)
    extension = load_sensor_movement_extension(
        EXTENSION_PATH,
        base_contract_path=CONTRACT_PATH,
        cell_extension_path=CELL_EXTENSION_PATH,
    )
    return contract, extension


def _authorization(path: Path, decision: str, *, age_ms: int = 0) -> None:
    observed = time.time_ns() // 1_000_000 - age_ms
    path.write_text(
        json.dumps(
            {
                "schema": "veriswarm.rescue.event.v1",
                "mission_id": "OP-VARUNA-001",
                "event_id": f"auth-{decision}-{observed}",
                "source": "abhijan-security",
                "source_seq": 1,
                "observed_at_ms": observed,
                "kind": "authorization",
                "payload": {
                    "node": "alpha",
                    "decision": decision,
                    "reason": f"test_{decision.casefold()}",
                },
            }
        ),
        encoding="utf-8",
    )


def _adapter(tmp_path: Path, decision: str = "ALLOW"):
    contract, extension = _loaded()
    authorization = tmp_path / "authorization.json"
    _authorization(authorization, decision)
    client = _Client()
    adapter = LiveCoSimCommandAdapter(
        client=client,
        contract=contract,
        extension=extension,
        authorization_provider=AuthorizationFileProvider(authorization),
        dispatcher=GatedCommandDispatcher(contract),
        route_dx=-92.82463950706873,
        route_dy=20.434910575448075,
        route_distance=95.04735277662299,
        cruise_z_ned_m=-10.0,
        yaw_mode=cosysairsim.YawMode(is_rate=False, yaw_or_rate=0.0),
    )
    return adapter, client, authorization


def test_authorization_provider_accepts_canonical_event_and_snapshot(tmp_path: Path) -> None:
    path = tmp_path / "authorization.json"
    _authorization(path, "ALLOW")
    provider = AuthorizationFileProvider(path)
    allow = provider.latest("alpha")
    assert allow is not None
    assert allow["decision"] == "ALLOW"
    assert isinstance(allow["observed_at_ms"], int)
    assert provider.latest("bravo") is None

    event = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(
        json.dumps(
            {
                "schema": "veriswarm.factorycity.authorization_snapshot.v1",
                "authorizations": {"alpha": event},
            }
        ),
        encoding="utf-8",
    )
    assert provider.latest("alpha")["reason"] == "test_allow"
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    snapshot["schema"] = "unreviewed.authorization.snapshot"
    path.write_text(json.dumps(snapshot), encoding="utf-8")
    assert provider.latest("alpha") is None
    path.write_text("not-json", encoding="utf-8")
    assert provider.latest("alpha") is None


def test_abhijan_five_lease_snapshot_is_consumed_by_live_runner(tmp_path: Path) -> None:
    policy = {
        "schema": "veriswarm.factorycity.authorization_policy.v1",
        "decisions": {
            node: {"decision": "ALLOW", "reason": "reviewer_nominal_release"}
            for node in ROSTER
        },
    }
    snapshot = build_snapshot(
        policy,
        mission_id="OP-VARUNA-001",
        sequences=tuple(range(1, len(ROSTER) + 1)),
        observed_at_ms=time.time_ns() // 1_000_000,
    )
    path = tmp_path / "authorization_snapshot.json"
    path.write_text(json.dumps(snapshot), encoding="utf-8")
    provider = AuthorizationFileProvider(path)
    for node in ROSTER:
        lease = provider.latest(node)
        assert lease is not None
        assert lease["node"] == node
        assert lease["decision"] == "ALLOW"


def test_live_adapter_releases_actual_velocity_and_directional_commands(tmp_path: Path) -> None:
    adapter, client, _ = _adapter(tmp_path)
    decision, future = adapter.execute(
        node="alpha",
        action="CONTINUE_ROUTE",
        duration_seconds=0.2,
        minimum_separation_m=2.0,
    )
    assert decision.action == "RELEASE"
    assert future.join() == "velocity-finished"
    assert client.calls[-1][0] == "velocity_z"
    assert client.calls[-1][2] < 0.0
    assert client.calls[-1][3] > 0.0

    adapter.execute(
        node="alpha",
        action="DEFLECT_LEFT",
        duration_seconds=0.2,
        minimum_separation_m=2.0,
    )
    left = client.calls[-1]
    adapter.execute(
        node="alpha",
        action="DEFLECT_RIGHT",
        duration_seconds=0.2,
        minimum_separation_m=2.0,
    )
    right = client.calls[-1]
    assert left[2] == -right[2]
    assert left[3] == -right[3]

    adapter.execute(
        node="alpha",
        action="DEFLECT_UP",
        duration_seconds=0.2,
        minimum_separation_m=2.0,
    )
    assert client.calls[-1][4] < -10.0


def test_single_active_vehicle_uses_configured_gate_separation() -> None:
    contract, _ = _loaded()
    configured = contract["command_limits"]["minimum_pairwise_separation_m"]
    assert _gate_separation(contract, {"alpha": (1.0, 2.0, -10.0)}) == configured
    assert _gate_separation(
        contract,
        {"alpha": (0.0, 0.0, -10.0), "bravo": (3.0, 4.0, -10.0)},
    ) == 5.0


def test_preflight_gate_payload_uses_supplied_measured_separation() -> None:
    contract, _ = _loaded()
    command = _preflight_command(contract, _Client(), "alpha", -10.0, 2.75)
    assert command["minimum_pairwise_separation_m"] == 2.75


def test_hold_and_stale_authorization_release_only_hover(tmp_path: Path) -> None:
    adapter, client, authorization = _adapter(tmp_path, "HOLD")
    decision, value = adapter.execute(
        node="alpha",
        action="CONTINUE_ROUTE",
        duration_seconds=0.2,
        minimum_separation_m=2.0,
    )
    assert decision.action == "HOVER"
    assert value == "hover-finished"
    assert client.calls == [("hover", "alpha")]

    _authorization(authorization, "ALLOW", age_ms=2001)
    decision, _ = adapter.execute(
        node="alpha",
        action="CONTINUE_ROUTE",
        duration_seconds=0.2,
        minimum_separation_m=2.0,
    )
    assert decision.action == "HOVER"
    assert all(call[0] == "hover" for call in client.calls)


def test_quarantine_executes_hover_then_land_and_never_nominal_motion(tmp_path: Path) -> None:
    adapter, client, _ = _adapter(tmp_path, "QUARANTINE")
    decision, future = adapter.execute(
        node="alpha",
        action="CONTINUE_ROUTE",
        duration_seconds=0.2,
        minimum_separation_m=2.0,
    )
    assert decision.action == "ABORT_HOVER_LAND"
    assert future.join() == "land-finished"
    assert [call[0] for call in client.calls] == ["hover", "land"]


def test_quarantine_reloads_lease_between_hover_and_land(tmp_path: Path) -> None:
    adapter, client, authorization = _adapter(tmp_path, "QUARANTINE")
    original_hover = client.hoverAsync

    def change_lease_after_hover(*, vehicle_name):
        result = original_hover(vehicle_name=vehicle_name)
        _authorization(authorization, "HOLD")
        return result

    client.hoverAsync = change_lease_after_hover
    with pytest.raises(MovementV2Error, match="quarantine_changed_before_safety_land"):
        adapter.execute(
            node="alpha",
            action="CONTINUE_ROUTE",
            duration_seconds=0.2,
            minimum_separation_m=2.0,
        )
    assert [call[0] for call in client.calls] == ["hover"]


def test_outbox_factory_uses_one_file_per_exact_producer(tmp_path: Path) -> None:
    factory = _outbox_factory(mission_id="OP-VARUNA-001", output_directory=tmp_path)
    mission = factory("mission.controller")
    alpha = factory("alpha.telemetry")
    assert mission.path.name == "mission-controller-rescue-outbox.sqlite3"
    assert alpha.path.name == "alpha-rescue-outbox.sqlite3"
    assert mission.path != alpha.path


def test_durable_sequences_survive_producer_restart(tmp_path: Path) -> None:
    contract, extension = _loaded()
    factory = _outbox_factory(
        mission_id=contract["mission_id"], output_directory=tmp_path
    )

    def producer() -> DurableMovementEvents:
        return DurableMovementEvents(
            contract=contract,
            ledger=CellLedger(contract),
            outbox_factory=factory,
            enqueue_deadline_ms=extension["timing_boundaries_ms"][
                "movement_transition_durable_enqueue_deadline"
            ],
        )

    first = producer().assignment(node="alpha", observed_at_ms=time.time_ns() // 1_000_000)
    second = producer().assignment(node="alpha", observed_at_ms=time.time_ns() // 1_000_000)
    assert first["source_seq"] == 1
    assert second["source_seq"] == 2
    assert first["event_id"] != second["event_id"]


def test_live_runner_is_additive_and_uses_no_evaluator_truth() -> None:
    nominal = NOMINAL_RUNNER.read_text(encoding="utf-8")
    live = LIVE_RUNNER.read_text(encoding="utf-8")
    assert "run_factorycity_movement_v2" not in nominal
    assert "SensorDrivenMovementSupervisor(" in live
    assert "CellLedger(" in live
    assert "DurableMovementEvents(" in live
    assert "GatedCommandDispatcher(" in live
    assert "simGetImages(" in live
    assert "moveByVelocityZAsync(" in live
    assert '["evaluation_truth"]' not in live
    assert "evaluation_only_disaster_obstacles_unreal_cm" not in live
