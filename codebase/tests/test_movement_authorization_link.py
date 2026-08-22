from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from tools.movement_authorization_link import (
    AuthorizationHTTPServer,
    AuthorizationLinkError,
    AuthorizationPublisher,
    PublisherConfig,
    ReceiverConfig,
    ROSTER,
    SequenceStore,
    SnapshotReceiverState,
    _control_handler_factory,
    _receiver_handler_factory,
    build_snapshot,
    read_policy,
    update_policy,
    validate_snapshot,
)


MISSION_ID = "OP-VARUNA-001"


def _policy(path: Path, decision: str = "ALLOW") -> dict:
    return update_policy(
        path,
        node="all",
        decision=decision,
        reason=f"test_{decision.casefold()}",
    )


def _snapshot(tmp_path: Path, *, observed_at_ms: int | None = None) -> dict:
    policy = _policy(tmp_path / "policy.json")
    return build_snapshot(
        policy,
        mission_id=MISSION_ID,
        sequences=(1, 2, 3, 4, 5),
        observed_at_ms=observed_at_ms,
    )


def test_policy_initializes_all_five_fail_closed_and_updates_one_atomically(tmp_path: Path) -> None:
    path = tmp_path / "policy.json"
    policy = update_policy(
        path,
        node="all",
        decision="HOLD",
        reason="startup_fail_closed",
    )
    assert set(policy["decisions"]) == set(ROSTER)
    assert {entry["decision"] for entry in policy["decisions"].values()} == {"HOLD"}

    updated = update_policy(
        path,
        node="alpha",
        decision="QUARANTINE",
        reason="reviewer_quarantine",
    )
    assert updated["decisions"]["alpha"]["decision"] == "QUARANTINE"
    assert updated["decisions"]["bravo"]["decision"] == "HOLD"
    assert read_policy(path) == updated
    assert path.stat().st_mode & 0o077 == 0


def test_single_node_update_requires_an_initialized_five_vehicle_policy(tmp_path: Path) -> None:
    with pytest.raises(AuthorizationLinkError, match="initialize all five"):
        update_policy(
            tmp_path / "missing.json",
            node="alpha",
            decision="ALLOW",
            reason="reviewed_nominal_release",
        )


def test_snapshot_contains_five_canonical_fresh_events(tmp_path: Path) -> None:
    now = time.time_ns() // 1_000_000
    snapshot = _snapshot(tmp_path, observed_at_ms=now)
    accepted = validate_snapshot(
        snapshot,
        expected_mission_id=MISSION_ID,
        now_ms=now + 200,
    )
    assert set(accepted["authorizations"]) == set(ROSTER)
    assert [
        accepted["authorizations"][node]["source_seq"] for node in ROSTER
    ] == [1, 2, 3, 4, 5]
    assert all(
        accepted["authorizations"][node]["payload"]["node"] == node
        for node in ROSTER
    )


def test_snapshot_rejects_stale_future_missing_and_wrong_source(tmp_path: Path) -> None:
    now = time.time_ns() // 1_000_000
    stale = _snapshot(tmp_path, observed_at_ms=now - 1_501)
    with pytest.raises(AuthorizationLinkError, match="stale"):
        validate_snapshot(stale, expected_mission_id=MISSION_ID, now_ms=now)

    future = _snapshot(tmp_path, observed_at_ms=now + 251)
    with pytest.raises(AuthorizationLinkError, match="future"):
        validate_snapshot(future, expected_mission_id=MISSION_ID, now_ms=now)

    missing = _snapshot(tmp_path, observed_at_ms=now)
    del missing["authorizations"]["echo"]
    with pytest.raises(AuthorizationLinkError, match="exactly five"):
        validate_snapshot(missing, expected_mission_id=MISSION_ID, now_ms=now)

    wrong_source = _snapshot(tmp_path, observed_at_ms=now)
    wrong_source["authorizations"]["alpha"]["source"] = "unreviewed-source"
    with pytest.raises(AuthorizationLinkError, match="source is invalid"):
        validate_snapshot(wrong_source, expected_mission_id=MISSION_ID, now_ms=now)


def test_sequence_store_persists_before_reuse(tmp_path: Path) -> None:
    path = tmp_path / "sequence.json"
    assert SequenceStore(path).allocate(5) == (1, 2, 3, 4, 5)
    assert SequenceStore(path).allocate(5) == (6, 7, 8, 9, 10)


def test_sequence_store_fails_closed_when_state_is_corrupt(tmp_path: Path) -> None:
    path = tmp_path / "sequence.json"
    path.write_text('{"schema":"wrong","last_source_seq":5}\n', encoding="utf-8")
    with pytest.raises(AuthorizationLinkError, match="sequence state is invalid"):
        SequenceStore(path).allocate(5)


def test_production_transport_is_frozen_to_exact_ethernet_peers(tmp_path: Path) -> None:
    ReceiverConfig(
        bind="192.168.50.11",
        port=8772,
        peer_ip="192.168.50.14",
        output=tmp_path / "snapshot.json",
        mission_id=MISSION_ID,
    ).validate()
    PublisherConfig(
        policy=tmp_path / "policy.json",
        state=tmp_path / "state.json",
        target_url="http://192.168.50.11:8772/authorization-snapshot",
        mission_id=MISSION_ID,
    ).validate()

    with pytest.raises(AuthorizationLinkError, match="frozen Pratik"):
        ReceiverConfig(
            bind="192.168.50.12",
            port=8772,
            peer_ip="192.168.50.14",
            output=tmp_path / "snapshot.json",
            mission_id=MISSION_ID,
        ).validate()
    with pytest.raises(AuthorizationLinkError, match="frozen Pratik receiver"):
        PublisherConfig(
            policy=tmp_path / "policy.json",
            state=tmp_path / "state.json",
            target_url="http://192.168.50.12:8772/authorization-snapshot",
            mission_id=MISSION_ID,
        ).validate()


def test_windows_receiver_atomically_accepts_and_rejects_replay(tmp_path: Path) -> None:
    output = tmp_path / "authorization_snapshot.json"
    config = ReceiverConfig(
        bind="127.0.0.1",
        port=0,
        peer_ip="127.0.0.1",
        output=output,
        mission_id=MISSION_ID,
    )
    state = SnapshotReceiverState(config)
    server = AuthorizationHTTPServer(
        ("127.0.0.1", 0), _receiver_handler_factory(config, state)
    )
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        snapshot = _snapshot(tmp_path)
        raw = json.dumps(snapshot).encode()
        request = Request(
            f"http://127.0.0.1:{server.server_port}/authorization-snapshot",
            data=raw,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=2) as response:  # noqa: S310
            accepted = json.loads(response.read())
        assert response.status == 202
        assert accepted["last_source_seq"] == 5
        assert json.loads(output.read_text())["authorizations"]["echo"]["payload"]["node"] == "echo"

        with pytest.raises(HTTPError) as replay:
            urlopen(request, timeout=2)  # noqa: S310
        assert replay.value.code == 409
        assert "replayed or out of order" in replay.value.read().decode()
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def test_mac_publisher_delivers_to_receiver_and_control_updates_policy(tmp_path: Path) -> None:
    output = tmp_path / "authorization_snapshot.json"
    receiver_config = ReceiverConfig(
        bind="127.0.0.1",
        port=0,
        peer_ip="127.0.0.1",
        output=output,
        mission_id=MISSION_ID,
    )
    receiver_state = SnapshotReceiverState(receiver_config)
    receiver = AuthorizationHTTPServer(
        ("127.0.0.1", 0), _receiver_handler_factory(receiver_config, receiver_state)
    )
    receiver_worker = threading.Thread(target=receiver.serve_forever, daemon=True)
    receiver_worker.start()

    policy_path = tmp_path / "policy.json"
    _policy(policy_path, "HOLD")
    publisher = AuthorizationPublisher(PublisherConfig(
        policy=policy_path,
        state=tmp_path / "publisher-state.json",
        target_url=f"http://127.0.0.1:{receiver.server_port}/authorization-snapshot",
        mission_id=MISSION_ID,
    ))
    control = AuthorizationHTTPServer(
        ("127.0.0.1", 0), _control_handler_factory(publisher)
    )
    control_worker = threading.Thread(target=control.serve_forever, daemon=True)
    control_worker.start()
    try:
        result = publisher.publish_once()
        assert result["accepted"] is True
        assert publisher.status()["last_success_at_ms"] is not None

        body = json.dumps({
            "node": "alpha",
            "decision": "QUARANTINE",
            "reason": "reviewer_quarantine",
        }).encode()
        request = Request(
            f"http://127.0.0.1:{control.server_port}/policy",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=2) as response:  # noqa: S310
            state = json.loads(response.read())
        assert state["policy"]["decisions"]["alpha"]["decision"] == "QUARANTINE"
        assert state["policy"]["decisions"]["bravo"]["decision"] == "HOLD"
    finally:
        control.shutdown()
        control.server_close()
        receiver.shutdown()
        receiver.server_close()
        control_worker.join(timeout=2)
        receiver_worker.join(timeout=2)
