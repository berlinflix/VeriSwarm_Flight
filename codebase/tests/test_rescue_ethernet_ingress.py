from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen

import pytest

from tools.rescue_ethernet_ingress import (
    IngressConfig,
    _IngressServer,
    _handler_factory,
    _remote_matches,
)


def test_ingress_requires_exact_peer_and_loopback_collector() -> None:
    valid = IngressConfig(
        bind="192.168.50.14",
        port=8771,
        peer_ip="192.168.50.11",
        collector_url="http://127.0.0.1:8770",
    )
    valid.validate()
    assert valid.events_url == "http://127.0.0.1:8770/events"

    for unsafe in (
        IngressConfig("0.0.0.0", 8771, "192.168.50.11", "http://127.0.0.1:8770"),
        IngressConfig("127.0.0.1", 8771, "192.168.50.11", "http://127.0.0.1:8770"),
        IngressConfig("192.168.50.14", 8771, "192.168.50.14", "http://127.0.0.1:8770"),
        IngressConfig("192.168.50.14", 8771, "192.168.50.11", "http://192.168.50.14:8770"),
    ):
        with pytest.raises(ValueError):
            unsafe.validate()


def test_remote_matching_is_exact_and_handles_ipv4_mapped_addresses() -> None:
    assert _remote_matches("192.168.50.11", "192.168.50.11")
    assert _remote_matches("::ffff:192.168.50.11", "192.168.50.11")
    assert not _remote_matches("192.168.50.12", "192.168.50.11")
    assert not _remote_matches("not-an-ip", "192.168.50.11")


def test_ingress_forwards_event_and_returns_collector_acceptance() -> None:
    received: list[dict] = []

    class CollectorHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            return

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers["Content-Length"])
            received.append(json.loads(self.rfile.read(length)))
            raw = b'{"accepted":true,"duplicate":false,"ok":true}\n'
            self.send_response(202)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    collector = ThreadingHTTPServer(("127.0.0.1", 0), CollectorHandler)
    collector_thread = threading.Thread(target=collector.serve_forever, daemon=True)
    collector_thread.start()

    # The production validator forbids loopback ingress. The loopback values below are
    # deliberately limited to this process-level forwarding test.
    config = IngressConfig(
        bind="127.0.0.1",
        port=0,
        peer_ip="127.0.0.1",
        collector_url=f"http://127.0.0.1:{collector.server_port}",
    )
    ingress = _IngressServer(("127.0.0.1", 0), _handler_factory(config))
    ingress_thread = threading.Thread(target=ingress.serve_forever, daemon=True)
    ingress_thread.start()
    try:
        event = {
            "schema": "veriswarm.rescue.event.v1",
            "mission_id": "OP-VARUNA-001",
            "event_id": "alpha-state-1",
            "source": "alpha.telemetry",
            "source_seq": 1,
            "observed_at_ms": 1787418000000,
            "kind": "vehicle_state",
            "payload": {"node": "alpha", "state": "SEARCHING", "position_ned": [0, 0, -10]},
        }
        raw = json.dumps(event).encode()
        request = Request(
            f"http://127.0.0.1:{ingress.server_port}/events",
            data=raw,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=2) as response:  # noqa: S310
            body = json.loads(response.read())
        assert response.status == 202
        assert body == {"accepted": True, "duplicate": False, "ok": True}
        assert received == [event]
    finally:
        ingress.shutdown()
        ingress.server_close()
        collector.shutdown()
        collector.server_close()
        ingress_thread.join(timeout=2)
        collector_thread.join(timeout=2)
