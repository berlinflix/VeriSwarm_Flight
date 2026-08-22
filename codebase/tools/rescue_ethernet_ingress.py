"""Source-IP-restricted Ethernet ingress for Pratik's rescue-event producers.

The canonical rescue collector remains bound to loopback.  This small adapter exposes
only ``POST /events`` on Abhijan's wired address, accepts posts only from the configured
Pratik peer address, and forwards the unchanged JSON body to the loopback collector.
It intentionally has no bearer-token mode: its trust boundary is one explicitly bound
Ethernet address plus one exact source-IP allowlist entry.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import IPv4Address, IPv6Address, ip_address
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


MAX_REQUEST_BYTES = 1 << 20
MAX_RESPONSE_BYTES = 1 << 20


def _normalized_ip(value: str) -> IPv4Address | IPv6Address:
    address = ip_address(value.split("%", 1)[0])
    if isinstance(address, IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


@dataclass(frozen=True)
class IngressConfig:
    bind: str
    port: int
    peer_ip: str
    collector_url: str
    timeout_s: float = 3.0

    def validate(self) -> None:
        if not 1 <= self.port <= 65535:
            raise ValueError("port must be inside 1..65535")
        if self.timeout_s <= 0 or self.timeout_s > 30:
            raise ValueError("timeout must be inside (0, 30]")
        bind = _normalized_ip(self.bind)
        peer = _normalized_ip(self.peer_ip)
        if bind.is_loopback or bind.is_unspecified or bind.is_multicast:
            raise ValueError("ingress must bind one exact non-loopback Ethernet address")
        if peer.is_loopback or peer.is_unspecified or peer.is_multicast:
            raise ValueError("peer must be one exact non-loopback Ethernet address")
        if bind.version != peer.version:
            raise ValueError("bind and peer IP versions must match")
        if bind == peer:
            raise ValueError("bind and peer addresses must differ")

        parsed = urlparse(self.collector_url)
        if parsed.scheme != "http" or not parsed.hostname:
            raise ValueError("collector URL must be loopback HTTP")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("collector URL must not contain credentials, query or fragment")
        if parsed.path not in {"", "/"}:
            raise ValueError("collector URL must not contain a path")
        if not _normalized_ip(parsed.hostname).is_loopback:
            raise ValueError("collector URL must remain loopback-only")

    @property
    def events_url(self) -> str:
        return f"{self.collector_url.rstrip('/')}/events"


def _remote_matches(remote_ip: str, expected_ip: str) -> bool:
    try:
        return _normalized_ip(remote_ip) == _normalized_ip(expected_ip)
    except ValueError:
        return False


def _forward_event(config: IngressConfig, raw: bytes) -> tuple[int, bytes]:
    request = Request(
        config.events_url,
        data=raw,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "VeriSwarmPratikEthernetIngress/1",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=config.timeout_s) as response:  # noqa: S310
            return response.status, response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as error:
        return error.code, error.read(MAX_RESPONSE_BYTES + 1)


def _handler_factory(config: IngressConfig) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "VeriSwarmPratikIngress/1"

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            print(f"pratik-ingress {self.client_address[0]} {format % args}")

        def _send_json(self, status: int, payload: dict[str, Any]) -> None:
            raw = (json.dumps(payload, sort_keys=True, allow_nan=False) + "\n").encode()
            self._send_raw(status, raw)

        def _send_raw(self, status: int, raw: bytes) -> None:
            if len(raw) > MAX_RESPONSE_BYTES:
                self._send_json(502, {"ok": False, "error": "collector_response_too_large"})
                return
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)

        def _is_peer(self) -> bool:
            return _remote_matches(self.client_address[0], config.peer_ip)

        def _is_local_mac(self) -> bool:
            return _remote_matches(self.client_address[0], config.bind)

        def do_GET(self) -> None:  # noqa: N802
            if urlparse(self.path).path != "/health":
                self._send_json(404, {"ok": False, "error": "not_found"})
                return
            if not (self._is_peer() or self._is_local_mac()):
                self._send_json(403, {"ok": False, "error": "source_ip_not_allowed"})
                return
            self._send_json(200, {
                "ok": True,
                "schema": "veriswarm.pratik.ethernet-ingress.v1",
                "peer_ip": config.peer_ip,
                "events_path": "/events",
            })

        def do_POST(self) -> None:  # noqa: N802
            if urlparse(self.path).path != "/events":
                self._send_json(404, {"ok": False, "error": "not_found"})
                return
            if not self._is_peer():
                self._send_json(403, {"ok": False, "error": "source_ip_not_allowed"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 1 or length > MAX_REQUEST_BYTES:
                    raise ValueError("invalid_request_size")
                raw = self.rfile.read(length)
                candidate = json.loads(raw)
                if not isinstance(candidate, dict):
                    raise ValueError("event_must_be_json_object")
                status, response = _forward_event(config, raw)
            except json.JSONDecodeError:
                self._send_json(400, {"ok": False, "error": "invalid_json"})
                return
            except ValueError as error:
                self._send_json(400, {"ok": False, "error": str(error)})
                return
            except (OSError, TimeoutError, URLError):
                self._send_json(503, {"ok": False, "error": "loopback_collector_unavailable"})
                return
            self._send_raw(status, response)

    return Handler


class _IngressServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Accept rescue events only from Pratik's exact wired source IP"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    serve = subparsers.add_parser("serve", help="run the restricted Ethernet ingress")
    serve.add_argument("--bind", required=True)
    serve.add_argument("--port", type=int, default=8771)
    serve.add_argument("--peer-ip", required=True)
    serve.add_argument("--collector-url", default="http://127.0.0.1:8770")
    serve.add_argument("--timeout", type=float, default=3.0)
    args = parser.parse_args(argv)

    config = IngressConfig(
        bind=args.bind,
        port=args.port,
        peer_ip=args.peer_ip,
        collector_url=args.collector_url,
        timeout_s=args.timeout,
    )
    try:
        config.validate()
    except ValueError as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 2

    server = _IngressServer((config.bind, config.port), _handler_factory(config))
    print(
        f"READY Pratik Ethernet ingress on {config.bind}:{config.port} "
        f"peer={config.peer_ip} collector={config.collector_url}",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
