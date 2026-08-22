"""Local HTTP/JSONL bridge for the VeriSwarm rescue data plane."""

from __future__ import annotations

import argparse
import hmac
import json
import os
import sys
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import ip_address
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from rescue.collector import RescueCollector, RescueCollectorError
from rescue.schema import RescueEventError


MAX_REQUEST_BYTES = 1 << 20
TOKEN_ENV = "VERISWARM_RESCUE_TOKEN"


@dataclass(frozen=True)
class ServiceConfig:
    bind: str
    port: int
    mission_id: str
    log_path: Path
    token: str = ""

    def validate(self) -> None:
        if not 1 <= self.port <= 65535:
            raise ValueError("port must be inside 1..65535")
        address = ip_address(self.bind)
        if not address.is_loopback and len(self.token) < 32:
            raise ValueError(
                f"non-loopback bind requires a 32+ character token in {TOKEN_ENV}"
            )


def _handler_factory(
    collector: RescueCollector,
    config: ServiceConfig,
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "VeriSwarmRescue/1"

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            print(f"rescue-api {self.address_string()} {format % args}")

        def _send(self, status: int, payload: dict[str, Any]) -> None:
            raw = (json.dumps(payload, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)

        def _authorized(self) -> bool:
            if not config.token:
                return True
            bearer = self.headers.get("Authorization", "")
            supplied = bearer[7:] if bearer.startswith("Bearer ") else ""
            supplied = self.headers.get("X-VeriSwarm-Token", supplied)
            return hmac.compare_digest(supplied, config.token)

        def _require_auth(self) -> bool:
            if self._authorized():
                return True
            self._send(401, {"ok": False, "error": "unauthorized"})
            return False

        def do_GET(self) -> None:  # noqa: N802
            if not self._require_auth():
                return
            path = urlparse(self.path).path
            if path == "/health":
                state = collector.state()
                self._send(200, {
                    "ok": True,
                    "schema": "veriswarm.rescue.service.v1",
                    "mission_id": config.mission_id,
                    "events_applied": state["events_applied"],
                })
            elif path == "/state":
                self._send(200, {"ok": True, "state": collector.state()})
            elif path == "/report":
                self._send(200, {"ok": True, "report": collector.report()})
            else:
                self._send(404, {"ok": False, "error": "not_found"})

        def do_POST(self) -> None:  # noqa: N802
            if not self._require_auth():
                return
            if urlparse(self.path).path != "/events":
                self._send(404, {"ok": False, "error": "not_found"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 1 or length > MAX_REQUEST_BYTES:
                    raise RescueEventError("invalid_request_size")
                body = json.loads(self.rfile.read(length))
                result = collector.collect(body)
            except json.JSONDecodeError:
                self._send(400, {"ok": False, "error": "invalid_json"})
                return
            except RescueEventError as error:
                self._send(400, {"ok": False, "error": str(error)})
                return
            except RescueCollectorError as error:
                self._send(409, {"ok": False, "error": str(error)})
                return
            self._send(
                200 if result.duplicate else 202,
                {
                    "ok": True,
                    "accepted": result.accepted,
                    "duplicate": result.duplicate,
                    "warning": result.warning,
                    "log_seq": result.event.get("seq") if result.event else None,
                },
            )

    return Handler


def _write_json(path: Path | None, payload: dict[str, Any]) -> None:
    raw = json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if path is None:
        sys.stdout.write(raw)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(raw, encoding="utf-8")


def _ingest_file(collector: RescueCollector, input_path: Path) -> int:
    accepted = duplicates = 0
    with input_path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                result = collector.collect(json.loads(line))
            except (json.JSONDecodeError, RescueEventError, RescueCollectorError) as error:
                print(f"FAIL line={line_number} error={error}", file=sys.stderr)
                return 2
            accepted += int(not result.duplicate)
            duplicates += int(result.duplicate)
            if result.warning:
                print(f"WARN line={line_number} {result.warning}", file=sys.stderr)
    print(f"PASS accepted={accepted} duplicates={duplicates} log={collector.log_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Collect and project rescue mission events")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve = subparsers.add_parser("serve", help="run the local HTTP collector")
    serve.add_argument("--bind", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8770)
    serve.add_argument("--mission-id", required=True)
    serve.add_argument("--log", type=Path, default=Path("results/rescue_events.jsonl"))

    ingest = subparsers.add_parser("ingest", help="ingest an existing producer JSONL file")
    ingest.add_argument("--mission-id", required=True)
    ingest.add_argument("--log", type=Path, default=Path("results/rescue_events.jsonl"))
    ingest.add_argument("--input", type=Path, required=True)

    report = subparsers.add_parser("report", help="project a report from the current log")
    report.add_argument("--mission-id", required=True)
    report.add_argument("--log", type=Path, default=Path("results/rescue_events.jsonl"))
    report.add_argument("--out", type=Path)

    state = subparsers.add_parser("state", help="print the dashboard state projection")
    state.add_argument("--mission-id", required=True)
    state.add_argument("--log", type=Path, default=Path("results/rescue_events.jsonl"))

    args = parser.parse_args(argv)
    collector = RescueCollector(args.mission_id, args.log)
    if args.command == "ingest":
        return _ingest_file(collector, args.input)
    if args.command == "report":
        _write_json(args.out, collector.report())
        return 0
    if args.command == "state":
        _write_json(None, collector.state())
        return 0

    config = ServiceConfig(
        bind=args.bind,
        port=args.port,
        mission_id=args.mission_id,
        log_path=args.log,
        token=os.environ.get(TOKEN_ENV, ""),
    )
    config.validate()
    server = ThreadingHTTPServer((config.bind, config.port), _handler_factory(collector, config))
    print(
        f"READY rescue collector on {config.bind}:{config.port} "
        f"mission={config.mission_id} log={config.log_path}",
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
