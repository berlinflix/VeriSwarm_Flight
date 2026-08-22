from __future__ import annotations

import http.client
import json
import threading
from http.server import ThreadingHTTPServer

import pytest

from rescue.collector import RescueCollector
from rescue.schema import RESCUE_SCHEMA
from tools.rescue_event_collector import ServiceConfig, _handler_factory


def _event() -> dict:
    return {
        "schema": RESCUE_SCHEMA,
        "mission_id": "OP-VARUNA-001",
        "event_id": "c2-mission-start-1",
        "source": "c2",
        "source_seq": 1,
        "observed_at_ms": 1_787_394_600_000,
        "kind": "mission_started",
        "payload": {"scenario_id": "varuna-v1", "coordinate_frame": "NED"},
    }


def _request(server, method, path, payload=None, headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    body = None if payload is None else json.dumps(payload)
    request_headers = dict(headers or {})
    if body is not None:
        request_headers["Content-Type"] = "application/json"
    connection.request(method, path, body=body, headers=request_headers)
    response = connection.getresponse()
    data = json.loads(response.read())
    connection.close()
    return response.status, data


def _start_server(tmp_path, *, token=""):
    collector = RescueCollector("OP-VARUNA-001", tmp_path / "rescue.jsonl")
    config = ServiceConfig("127.0.0.1", 8770, "OP-VARUNA-001", collector.log_path, token)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler_factory(collector, config))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def test_non_loopback_bind_requires_token(tmp_path):
    with pytest.raises(ValueError, match=r"requires a 32\+ character token"):
        ServiceConfig(
            "192.168.50.13", 8770, "OP-VARUNA-001", tmp_path / "events.jsonl", ""
        ).validate()


def test_http_collect_state_and_idempotent_replay(tmp_path):
    server, thread = _start_server(tmp_path)
    try:
        status, accepted = _request(server, "POST", "/events", _event())
        assert status == 202 and accepted["accepted"] and not accepted["duplicate"]

        status, duplicate = _request(server, "POST", "/events", _event())
        assert status == 200 and duplicate["duplicate"]

        status, state = _request(server, "GET", "/state")
        assert status == 200
        assert state["state"]["mission_status"] == "ACTIVE"
        assert state["state"]["events_applied"] == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_http_token_and_validation_fail_closed(tmp_path):
    token = "x" * 32
    server, thread = _start_server(tmp_path, token=token)
    try:
        status, response = _request(server, "GET", "/health")
        assert status == 401 and response["error"] == "unauthorized"

        status, response = _request(
            server,
            "POST",
            "/events",
            {**_event(), "schema": "wrong"},
            {"Authorization": f"Bearer {token}"},
        )
        assert status == 400 and "schema must be" in response["error"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
