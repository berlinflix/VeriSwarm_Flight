from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from urllib.error import URLError

import pytest

from rescue.collector import RescueCollector
from rescue.outbox import RescueOutbox, RescueOutboxError
from tools.rescue_event_collector import ServiceConfig, _handler_factory
from tools.rescue_event_sender import main as sender_main


MISSION_ID = "OP-VARUNA-001"


def _event(
    seq: int = 1,
    *,
    event_id: str | None = None,
    state: str = "SEARCHING",
) -> dict:
    return {
        "schema": "veriswarm.rescue.event.v1",
        "mission_id": MISSION_ID,
        "event_id": event_id or f"alpha-state-{seq:04d}",
        "source": "alpha",
        "source_seq": seq,
        "observed_at_ms": 1_787_394_601_000 + seq,
        "kind": "vehicle_state",
        "payload": {
            "node": "alpha",
            "state": state,
            "position_ned": [float(seq), 2.0, -14.0],
            "battery_pct": 84.0,
        },
    }


@contextmanager
def _collector_server(tmp_path, *, token: str = ""):
    collector = RescueCollector(MISSION_ID, tmp_path / "collector.jsonl")
    config = ServiceConfig(
        bind="127.0.0.1",
        port=8770,
        mission_id=MISSION_ID,
        log_path=tmp_path / "collector.jsonl",
        token=token,
    )
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), _handler_factory(collector, config)
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield collector, f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_outbox_survives_restart_and_delivers_fifo(tmp_path):
    path = tmp_path / "producer.sqlite3"
    first = RescueOutbox(MISSION_ID, path)
    assert first.enqueue(_event(1)).accepted
    assert first.enqueue(_event(2)).accepted

    restored = RescueOutbox(MISSION_ID, path)
    assert restored.status()["pending"] == 2
    with _collector_server(tmp_path) as (collector, endpoint):
        result = restored.flush(endpoint, transient_retries=0)

    assert result.delivered == 2
    assert result.pending == 0
    assert result.stopped_reason is None
    state = collector.state()
    assert state["events_applied"] == 2
    alpha = next(vehicle for vehicle in state["vehicles"] if vehicle["node"] == "alpha")
    assert alpha["position_ned"] == [2.0, 2.0, -14.0]


def test_enqueue_is_idempotent_but_event_id_reuse_fails(tmp_path):
    outbox = RescueOutbox(MISSION_ID, tmp_path / "producer.sqlite3")
    event = _event()
    assert outbox.enqueue(event).accepted
    duplicate = outbox.enqueue(event)
    assert duplicate.duplicate and not duplicate.accepted

    changed = _event(state="HOLD")
    with pytest.raises(RescueOutboxError, match="event_id_reused"):
        outbox.enqueue(changed)


def test_link_failure_keeps_event_then_recovery_delivers(tmp_path, monkeypatch):
    outbox = RescueOutbox(MISSION_ID, tmp_path / "producer.sqlite3")
    outbox.enqueue(_event())

    with monkeypatch.context() as patch:
        patch.setattr(
            "rescue.outbox._post_event",
            lambda *args, **kwargs: (_ for _ in ()).throw(URLError("offline")),
        )
        failed = outbox.flush(
            "http://127.0.0.1:8770", transient_retries=1, retry_backoff_s=0
        )
    assert failed.delivered == 0
    assert failed.attempts == 2
    assert failed.pending == 1
    assert failed.stopped_reason == "network_error:URLError"

    with _collector_server(tmp_path) as (collector, endpoint):
        recovered = outbox.flush(endpoint, transient_retries=0)
    assert recovered.delivered == 1
    assert recovered.pending == 0
    assert collector.state()["events_applied"] == 1


def test_collector_duplicate_is_successfully_drained(tmp_path):
    event = _event()
    outbox = RescueOutbox(MISSION_ID, tmp_path / "producer.sqlite3")
    outbox.enqueue(event)
    with _collector_server(tmp_path) as (collector, endpoint):
        collector.collect(event)
        result = outbox.flush(endpoint, transient_retries=0)
    assert result.delivered == 1
    assert result.duplicates == 1
    assert result.pending == 0
    assert collector.state()["events_applied"] == 1


def test_authentication_failure_never_discards_event(tmp_path):
    token = "T" * 32
    outbox = RescueOutbox(MISSION_ID, tmp_path / "producer.sqlite3")
    outbox.enqueue(_event())
    with _collector_server(tmp_path, token=token) as (collector, endpoint):
        blocked = outbox.flush(endpoint, transient_retries=0)
        assert blocked.pending == 1
        assert blocked.dead_lettered == 0
        assert blocked.stopped_reason == "http_401:unauthorized"

        delivered = outbox.flush(endpoint, token=token, transient_retries=0)
    assert delivered.delivered == 1
    assert delivered.pending == 0
    assert collector.state()["events_applied"] == 1


def test_sequence_conflict_moves_event_to_retained_dead_letter(tmp_path):
    outbox = RescueOutbox(MISSION_ID, tmp_path / "producer.sqlite3")
    outbox.enqueue(_event(1))
    with _collector_server(tmp_path) as (collector, endpoint):
        collector.collect(_event(2))
        result = outbox.flush(endpoint, transient_retries=0)

    assert result.delivered == 0
    assert result.dead_lettered == 1
    assert result.pending == 0
    status = outbox.status()
    assert status["dead_lettered"] == 1
    assert status["dead_letters"][0]["status_code"] == 409
    assert "source_sequence_not_monotonic" in status["dead_letters"][0]["reason"]


def test_local_content_corruption_is_quarantined_before_network(tmp_path, monkeypatch):
    path = tmp_path / "producer.sqlite3"
    outbox = RescueOutbox(MISSION_ID, path)
    outbox.enqueue(_event())
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE outbox SET canonical_json = canonical_json || ' '")

    called = False

    def should_not_send(*args, **kwargs):
        nonlocal called
        called = True
        return 202, {"ok": True, "accepted": True, "duplicate": False}

    monkeypatch.setattr("rescue.outbox._post_event", should_not_send)
    result = outbox.flush("http://127.0.0.1:8770", transient_retries=0)
    assert not called
    assert result.dead_lettered == 1
    assert result.pending == 0
    assert outbox.status()["dead_letters"][0]["reason"] == "outbox_content_hash_mismatch"


def test_transient_http_failure_uses_bounded_retry(tmp_path, monkeypatch):
    outbox = RescueOutbox(MISSION_ID, tmp_path / "producer.sqlite3")
    outbox.enqueue(_event())
    responses = iter([
        (503, {"ok": False, "error": "busy"}),
        (202, {"ok": True, "accepted": True, "duplicate": False}),
    ])
    monkeypatch.setattr("rescue.outbox._post_event", lambda *args, **kwargs: next(responses))

    result = outbox.flush(
        "http://127.0.0.1:8770", transient_retries=1, retry_backoff_s=0
    )
    assert result.delivered == 1
    assert result.attempts == 2
    assert result.pending == 0


def test_wrong_mission_endpoint_response_remains_pending(tmp_path):
    outbox = RescueOutbox(MISSION_ID, tmp_path / "producer.sqlite3")
    outbox.enqueue(_event())
    other_collector = RescueCollector("OTHER-MISSION", tmp_path / "other.jsonl")
    config = ServiceConfig(
        bind="127.0.0.1",
        port=8770,
        mission_id="OTHER-MISSION",
        log_path=tmp_path / "other.jsonl",
    )
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), _handler_factory(other_collector, config)
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = outbox.flush(
            f"http://127.0.0.1:{server.server_port}", transient_retries=0
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert result.pending == 1
    assert result.dead_lettered == 0
    assert result.stopped_reason.startswith("http_400:")


def test_sender_cli_enqueues_reports_and_flushes(tmp_path, capsys):
    source = tmp_path / "event.json"
    source.write_text(json.dumps(_event()), encoding="utf-8")
    outbox_path = tmp_path / "producer.sqlite3"
    common = ["--mission-id", MISSION_ID, "--outbox", str(outbox_path)]

    assert sender_main([*common, "enqueue", "--input", str(source)]) == 0
    enqueue_output = json.loads(capsys.readouterr().out)
    assert enqueue_output["accepted"] == 1
    assert enqueue_output["pending"] == 1

    assert sender_main([*common, "status"]) == 0
    status_output = json.loads(capsys.readouterr().out)
    assert status_output["pending_events"][0]["event_id"] == "alpha-state-0001"

    with _collector_server(tmp_path) as (collector, endpoint):
        assert sender_main([
            *common,
            "flush",
            "--endpoint",
            endpoint,
            "--transient-retries",
            "0",
        ]) == 0
    raw_output = capsys.readouterr().out
    flush_output = json.loads(raw_output[raw_output.index("{"):])
    assert flush_output["pending"] == 0
    assert collector.state()["events_applied"] == 1
