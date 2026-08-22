"""Fail-closed movement-v2 authorization link for the isolated demo Ethernet.

Abhijan's Mac owns the desired simulation authorization policy and continuously
publishes fresh, canonical rescue ``authorization`` events for all five vehicles.
Pratik's Windows receiver accepts snapshots only from the Mac's exact wired IP and
atomically replaces the file consumed by ``run_factorycity_movement_v2.py``.

This is deliberately separate from the Jetson/model-hash qualification plane.  The
transport is source-IP restricted but not cryptographically authenticated, so it is
approved only for the physically isolated hackathon LAN.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import IPv4Address, IPv6Address, ip_address
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from rescue.schema import RescueEventError, validate_rescue_event


ROSTER = ("alpha", "bravo", "charlie", "delta", "echo")
DECISIONS = frozenset({"ALLOW", "HOLD", "QUARANTINE"})
POLICY_SCHEMA = "veriswarm.factorycity.authorization_policy.v1"
SNAPSHOT_SCHEMA = "veriswarm.factorycity.authorization_snapshot.v1"
PUBLISHER_STATE_SCHEMA = "veriswarm.factorycity.authorization_publisher_state.v1"
SERVICE_SCHEMA = "veriswarm.factorycity.authorization_link.v1"
SOURCE = "abhijan-movement"
MAX_REQUEST_BYTES = 64 * 1024
MAX_RESPONSE_BYTES = 64 * 1024


class AuthorizationLinkError(ValueError):
    """The requested policy, snapshot or transport operation is unsafe."""


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


def _normalized_ip(value: str) -> IPv4Address | IPv6Address:
    address = ip_address(value.split("%", 1)[0])
    if isinstance(address, IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def _remote_matches(remote_ip: str, expected_ip: str) -> bool:
    try:
        return _normalized_ip(remote_ip) == _normalized_ip(expected_ip)
    except ValueError:
        return False


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        try:
            os.fchmod(descriptor, 0o600)
        except OSError:
            pass
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        try:
            directory = os.open(path.parent, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _validate_reason(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise AuthorizationLinkError("reason must be non-empty and at most 256 characters")
    return value.strip()


def validate_policy(candidate: object) -> dict[str, Any]:
    if not isinstance(candidate, Mapping) or candidate.get("schema") != POLICY_SCHEMA:
        raise AuthorizationLinkError("authorization policy schema is invalid")
    decisions = candidate.get("decisions")
    if not isinstance(decisions, Mapping) or set(decisions) != set(ROSTER):
        raise AuthorizationLinkError("authorization policy must contain exactly five vehicles")
    normalized: dict[str, dict[str, str]] = {}
    for node in ROSTER:
        entry = decisions.get(node)
        if not isinstance(entry, Mapping) or entry.get("decision") not in DECISIONS:
            raise AuthorizationLinkError(f"authorization policy decision is invalid for {node}")
        normalized[node] = {
            "decision": str(entry["decision"]),
            "reason": _validate_reason(entry.get("reason")),
        }
    return {"schema": POLICY_SCHEMA, "decisions": normalized}


def read_policy(path: Path) -> dict[str, Any]:
    try:
        return validate_policy(json.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError as error:
        raise AuthorizationLinkError(f"authorization policy not found: {path}") from error
    except json.JSONDecodeError as error:
        raise AuthorizationLinkError("authorization policy is invalid JSON") from error


def update_policy(
    path: Path,
    *,
    node: str,
    decision: str,
    reason: str,
) -> dict[str, Any]:
    decision = decision.upper()
    if decision not in DECISIONS:
        raise AuthorizationLinkError("decision must be ALLOW, HOLD or QUARANTINE")
    if node not in {*ROSTER, "all"}:
        raise AuthorizationLinkError("node must be alpha, bravo, charlie, delta, echo or all")
    reason = _validate_reason(reason)
    if path.exists():
        policy = read_policy(path)
    elif node == "all":
        policy = {
            "schema": POLICY_SCHEMA,
            "decisions": {
                vehicle: {"decision": "HOLD", "reason": "startup_fail_closed"}
                for vehicle in ROSTER
            },
        }
    else:
        raise AuthorizationLinkError("initialize all five vehicles before changing one node")
    targets = ROSTER if node == "all" else (node,)
    for vehicle in targets:
        policy["decisions"][vehicle] = {"decision": decision, "reason": reason}
    normalized = validate_policy(policy)
    _atomic_write_json(path, normalized)
    return normalized


class SequenceStore:
    """Persist the last allocated source sequence before a network attempt."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()

    def _last(self) -> int:
        try:
            candidate = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return 0
        except (json.JSONDecodeError, OSError) as error:
            raise AuthorizationLinkError(
                "publisher sequence state is unreadable; restore or remove it deliberately"
            ) from error
        if (
            not isinstance(candidate, Mapping)
            or candidate.get("schema") != PUBLISHER_STATE_SCHEMA
            or not isinstance(candidate.get("last_source_seq"), int)
            or isinstance(candidate.get("last_source_seq"), bool)
            or candidate["last_source_seq"] < 0
        ):
            raise AuthorizationLinkError(
                "publisher sequence state is invalid; restore or remove it deliberately"
            )
        return int(candidate["last_source_seq"])

    def allocate(self, count: int) -> tuple[int, ...]:
        if count < 1:
            raise AuthorizationLinkError("sequence allocation count must be positive")
        with self._lock:
            first = self._last() + 1
            result = tuple(range(first, first + count))
            _atomic_write_json(
                self.path,
                {
                    "schema": PUBLISHER_STATE_SCHEMA,
                    "last_source_seq": result[-1],
                },
            )
            return result


def build_snapshot(
    policy: Mapping[str, Any],
    *,
    mission_id: str,
    sequences: tuple[int, ...],
    observed_at_ms: int | None = None,
) -> dict[str, Any]:
    normalized = validate_policy(policy)
    if len(sequences) != len(ROSTER) or len(set(sequences)) != len(ROSTER):
        raise AuthorizationLinkError("snapshot requires five unique source sequences")
    if any(not isinstance(value, int) or isinstance(value, bool) or value < 1 for value in sequences):
        raise AuthorizationLinkError("snapshot source sequence is invalid")
    observed = _now_ms() if observed_at_ms is None else observed_at_ms
    if not isinstance(observed, int) or isinstance(observed, bool) or observed < 0:
        raise AuthorizationLinkError("snapshot timestamp is invalid")
    authorizations: dict[str, dict[str, Any]] = {}
    for node, sequence in zip(ROSTER, sequences, strict=True):
        entry = normalized["decisions"][node]
        event = {
            "schema": "veriswarm.rescue.event.v1",
            "mission_id": mission_id,
            "event_id": f"abhijan-movement-{node}-{sequence}-{observed}",
            "source": SOURCE,
            "source_seq": sequence,
            "observed_at_ms": observed,
            "kind": "authorization",
            "payload": {
                "node": node,
                "decision": entry["decision"],
                "reason": entry["reason"],
            },
        }
        try:
            authorizations[node] = validate_rescue_event(
                event, expected_mission_id=mission_id
            )
        except RescueEventError as error:
            raise AuthorizationLinkError(f"canonical authorization is invalid: {error}") from error
    return {
        "schema": SNAPSHOT_SCHEMA,
        "mission_id": mission_id,
        "generated_at_ms": observed,
        "authorizations": authorizations,
    }


def validate_snapshot(
    candidate: object,
    *,
    expected_mission_id: str,
    now_ms: int | None = None,
    max_age_ms: int | None = 1_500,
    max_future_ms: int = 250,
) -> dict[str, Any]:
    if not isinstance(candidate, Mapping) or candidate.get("schema") != SNAPSHOT_SCHEMA:
        raise AuthorizationLinkError("authorization snapshot schema is invalid")
    if candidate.get("mission_id") != expected_mission_id:
        raise AuthorizationLinkError("authorization snapshot mission_id is invalid")
    authorizations = candidate.get("authorizations")
    if not isinstance(authorizations, Mapping) or set(authorizations) != set(ROSTER):
        raise AuthorizationLinkError("authorization snapshot must contain exactly five vehicles")
    current = _now_ms() if now_ms is None else now_ms
    normalized: dict[str, dict[str, Any]] = {}
    sequences: list[int] = []
    for node in ROSTER:
        try:
            event = validate_rescue_event(
                authorizations[node], expected_mission_id=expected_mission_id
            )
        except RescueEventError as error:
            raise AuthorizationLinkError(f"authorization event is invalid for {node}: {error}") from error
        if event["kind"] != "authorization" or event["payload"]["node"] != node:
            raise AuthorizationLinkError(f"authorization event does not own {node}")
        if event["source"] != SOURCE:
            raise AuthorizationLinkError(f"authorization source is invalid for {node}")
        age = current - event["observed_at_ms"]
        if max_age_ms is not None and age > max_age_ms:
            raise AuthorizationLinkError(f"authorization event is stale for {node}")
        if age < -max_future_ms:
            raise AuthorizationLinkError(f"authorization event is from the future for {node}")
        sequences.append(event["source_seq"])
        normalized[node] = event
    if len(set(sequences)) != len(ROSTER) or sequences != sorted(sequences):
        raise AuthorizationLinkError("authorization source sequences must be unique and ordered")
    return {
        "schema": SNAPSHOT_SCHEMA,
        "mission_id": expected_mission_id,
        "generated_at_ms": max(event["observed_at_ms"] for event in normalized.values()),
        "authorizations": normalized,
    }


@dataclass(frozen=True)
class ReceiverConfig:
    bind: str
    port: int
    peer_ip: str
    output: Path
    mission_id: str
    max_age_ms: int = 1_500

    def validate(self) -> None:
        if not 1 <= self.port <= 65535:
            raise AuthorizationLinkError("port must be inside 1..65535")
        if not 250 <= self.max_age_ms < 2_000:
            raise AuthorizationLinkError("receiver max age must be inside 250..1999 ms")
        bind = _normalized_ip(self.bind)
        peer = _normalized_ip(self.peer_ip)
        if bind.is_loopback or bind.is_unspecified or bind.is_multicast:
            raise AuthorizationLinkError("receiver must bind one exact Ethernet address")
        if peer.is_loopback or peer.is_unspecified or peer.is_multicast:
            raise AuthorizationLinkError("receiver peer must be one exact Ethernet address")
        if bind.version != peer.version or bind == peer:
            raise AuthorizationLinkError("receiver bind and peer addresses are invalid")
        if str(bind) != "192.168.50.11" or str(peer) != "192.168.50.14" or self.port != 8772:
            raise AuthorizationLinkError(
                "receiver must use frozen Pratik .11:8772 and Abhijan peer .14"
            )


class SnapshotReceiverState:
    def __init__(
        self,
        config: ReceiverConfig,
        *,
        clock_ms: Callable[[], int] = _now_ms,
        sleeper: Callable[[float], None] = time.sleep,
    ):
        self.config = config
        self._clock_ms = clock_ms
        self._sleeper = sleeper
        self._lock = threading.Lock()
        self.last_source_seq = 0
        self.last_received_at_ms: int | None = None
        try:
            existing = validate_snapshot(
                json.loads(config.output.read_text(encoding="utf-8")),
                expected_mission_id=config.mission_id,
                max_age_ms=None,
            )
            self.last_source_seq = max(
                event["source_seq"] for event in existing["authorizations"].values()
            )
        except (OSError, json.JSONDecodeError, AuthorizationLinkError):
            pass

    def accept(self, candidate: object) -> dict[str, Any]:
        with self._lock:
            snapshot = validate_snapshot(
                candidate,
                expected_mission_id=self.config.mission_id,
                now_ms=self._clock_ms(),
                max_age_ms=self.config.max_age_ms,
            )
            newest_sequence = max(
                event["source_seq"]
                for event in snapshot["authorizations"].values()
            )
            if newest_sequence <= self.last_source_seq:
                raise AuthorizationLinkError("authorization snapshot is replayed or out of order")

            # The isolated Ethernet receiver accepts at most 250 ms of positive clock
            # skew. The command gate accepts no future-dated lease. Delay projection
            # until the Windows clock reaches the canonical Mac timestamp, then
            # revalidate with the stricter zero-future boundary.
            wait_ms = snapshot["generated_at_ms"] - self._clock_ms()
            if wait_ms > 0:
                self._sleeper(wait_ms / 1000.0)
            snapshot = validate_snapshot(
                snapshot,
                expected_mission_id=self.config.mission_id,
                now_ms=self._clock_ms(),
                max_age_ms=self.config.max_age_ms,
                max_future_ms=0,
            )
            _atomic_write_json(self.config.output, snapshot)
            self.last_source_seq = newest_sequence
            self.last_received_at_ms = self._clock_ms()
        return snapshot


def _receiver_handler_factory(
    config: ReceiverConfig, state: SnapshotReceiverState
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "VeriSwarmAuthorizationReceiver/1"

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            print(f"authorization-receiver {self.client_address[0]} {format % args}")

        def _send(self, status: int, payload: Mapping[str, Any]) -> None:
            raw = (json.dumps(payload, sort_keys=True, allow_nan=False) + "\n").encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)

        def _is_peer(self) -> bool:
            return _remote_matches(self.client_address[0], config.peer_ip)

        def _is_local(self) -> bool:
            return _remote_matches(self.client_address[0], config.bind)

        def do_GET(self) -> None:  # noqa: N802
            if urlparse(self.path).path != "/health":
                self._send(404, {"ok": False, "error": "not_found"})
                return
            if not (self._is_peer() or self._is_local()):
                self._send(403, {"ok": False, "error": "source_ip_not_allowed"})
                return
            self._send(200, {
                "ok": True,
                "schema": SERVICE_SCHEMA,
                "role": "windows_atomic_receiver",
                "last_source_seq": state.last_source_seq,
                "last_received_at_ms": state.last_received_at_ms,
                "output": str(config.output),
            })

        def do_POST(self) -> None:  # noqa: N802
            if urlparse(self.path).path != "/authorization-snapshot":
                self._send(404, {"ok": False, "error": "not_found"})
                return
            if not self._is_peer():
                self._send(403, {"ok": False, "error": "source_ip_not_allowed"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 1 or length > MAX_REQUEST_BYTES:
                    raise AuthorizationLinkError("invalid_request_size")
                candidate = json.loads(self.rfile.read(length))
                snapshot = state.accept(candidate)
            except json.JSONDecodeError:
                self._send(400, {"ok": False, "error": "invalid_json"})
                return
            except AuthorizationLinkError as error:
                self._send(409, {"ok": False, "error": str(error)})
                return
            self._send(202, {
                "ok": True,
                "accepted": True,
                "last_source_seq": state.last_source_seq,
                "generated_at_ms": snapshot["generated_at_ms"],
            })

    return Handler


class AuthorizationHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


@dataclass(frozen=True)
class PublisherConfig:
    policy: Path
    state: Path
    target_url: str
    mission_id: str
    interval_ms: int = 500
    timeout_s: float = 1.0

    def validate(self) -> None:
        if not 100 <= self.interval_ms <= 1_000:
            raise AuthorizationLinkError("publish interval must be inside 100..1000 ms")
        if self.timeout_s <= 0 or self.timeout_s > 5:
            raise AuthorizationLinkError("publish timeout must be inside (0, 5]")
        parsed = urlparse(self.target_url)
        if (
            parsed.scheme != "http"
            or not parsed.hostname
            or parsed.path != "/authorization-snapshot"
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise AuthorizationLinkError("target URL must be exact HTTP /authorization-snapshot")
        target = _normalized_ip(parsed.hostname)
        if target.is_loopback or target.is_unspecified or target.is_multicast:
            raise AuthorizationLinkError("target URL must use Pratik's exact Ethernet address")
        if str(target) != "192.168.50.11" or (parsed.port or 80) != 8772:
            raise AuthorizationLinkError("target must be frozen Pratik receiver 192.168.50.11:8772")


class AuthorizationPublisher:
    def __init__(self, config: PublisherConfig):
        self.config = config
        self.sequences = SequenceStore(config.state)
        self._lock = threading.Lock()
        self.last_attempt_at_ms: int | None = None
        self.last_success_at_ms: int | None = None
        self.last_error: str | None = None
        self.last_source_seq = 0

    def _policy_or_hold(self) -> dict[str, Any]:
        try:
            return read_policy(self.config.policy)
        except AuthorizationLinkError:
            return {
                "schema": POLICY_SCHEMA,
                "decisions": {
                    node: {
                        "decision": "HOLD",
                        "reason": "authorization_policy_unavailable",
                    }
                    for node in ROSTER
                },
            }

    def snapshot(self, *, now_ms: int | None = None) -> dict[str, Any]:
        sequences = self.sequences.allocate(len(ROSTER))
        return build_snapshot(
            self._policy_or_hold(),
            mission_id=self.config.mission_id,
            sequences=sequences,
            observed_at_ms=now_ms,
        )

    def publish_once(self) -> dict[str, Any]:
        snapshot = self.snapshot()
        raw = json.dumps(snapshot, sort_keys=True, allow_nan=False).encode()
        request = Request(
            self.config.target_url,
            data=raw,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "VeriSwarmAbhijanAuthorizationPublisher/1",
            },
            method="POST",
        )
        with self._lock:
            self.last_attempt_at_ms = _now_ms()
            self.last_source_seq = max(
                event["source_seq"] for event in snapshot["authorizations"].values()
            )
        try:
            with urlopen(request, timeout=self.config.timeout_s) as response:  # noqa: S310
                response_raw = response.read(MAX_RESPONSE_BYTES + 1)
                if len(response_raw) > MAX_RESPONSE_BYTES:
                    raise AuthorizationLinkError("authorization receiver response is too large")
                payload = json.loads(response_raw)
                if response.status != 202 or not isinstance(payload, Mapping) or not payload.get("ok"):
                    raise AuthorizationLinkError("authorization receiver rejected snapshot")
        except HTTPError as error:
            detail = error.read(MAX_RESPONSE_BYTES).decode(errors="replace")
            raise AuthorizationLinkError(f"authorization receiver HTTP {error.code}: {detail}") from error
        except (OSError, TimeoutError, URLError, json.JSONDecodeError) as error:
            raise AuthorizationLinkError("authorization receiver unavailable") from error
        with self._lock:
            self.last_success_at_ms = _now_ms()
            self.last_error = None
        return dict(payload)

    def run(self, stop: threading.Event) -> None:
        while not stop.is_set():
            started = time.monotonic()
            try:
                self.publish_once()
            except AuthorizationLinkError as error:
                with self._lock:
                    self.last_error = str(error)
            elapsed = time.monotonic() - started
            stop.wait(max(0.0, self.config.interval_ms / 1000 - elapsed))

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "last_attempt_at_ms": self.last_attempt_at_ms,
                "last_success_at_ms": self.last_success_at_ms,
                "last_error": self.last_error,
                "last_source_seq": self.last_source_seq,
            }


def _control_handler_factory(
    publisher: AuthorizationPublisher,
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "VeriSwarmAuthorizationControl/1"

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            print(f"authorization-control {self.client_address[0]} {format % args}")

        def _send(self, status: int, payload: Mapping[str, Any]) -> None:
            raw = (json.dumps(payload, sort_keys=True, allow_nan=False) + "\n").encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)

        def _state(self) -> dict[str, Any]:
            try:
                policy = read_policy(publisher.config.policy)
                policy_error = None
            except AuthorizationLinkError as error:
                policy = publisher._policy_or_hold()
                policy_error = str(error)
            return {
                "ok": True,
                "schema": SERVICE_SCHEMA,
                "role": "mac_policy_publisher",
                "mission_id": publisher.config.mission_id,
                "policy": policy,
                "policy_error": policy_error,
                "publisher": publisher.status(),
            }

        def do_GET(self) -> None:  # noqa: N802
            if urlparse(self.path).path not in {"/health", "/policy"}:
                self._send(404, {"ok": False, "error": "not_found"})
                return
            if not _normalized_ip(self.client_address[0]).is_loopback:
                self._send(403, {"ok": False, "error": "loopback_only"})
                return
            self._send(200, self._state())

        def do_POST(self) -> None:  # noqa: N802
            if urlparse(self.path).path != "/policy":
                self._send(404, {"ok": False, "error": "not_found"})
                return
            if not _normalized_ip(self.client_address[0]).is_loopback:
                self._send(403, {"ok": False, "error": "loopback_only"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 1 or length > MAX_REQUEST_BYTES:
                    raise AuthorizationLinkError("invalid_request_size")
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, Mapping):
                    raise AuthorizationLinkError("policy update must be a JSON object")
                update_policy(
                    publisher.config.policy,
                    node=str(body.get("node", "")),
                    decision=str(body.get("decision", "")),
                    reason=body.get("reason"),
                )
            except json.JSONDecodeError:
                self._send(400, {"ok": False, "error": "invalid_json"})
                return
            except AuthorizationLinkError as error:
                self._send(400, {"ok": False, "error": str(error)})
                return
            self._send(200, self._state())

    return Handler


def _serve_receiver(config: ReceiverConfig) -> int:
    config.validate()
    state = SnapshotReceiverState(config)
    server = AuthorizationHTTPServer(
        (config.bind, config.port), _receiver_handler_factory(config, state)
    )
    print(
        f"READY authorization receiver on {config.bind}:{config.port} "
        f"peer={config.peer_ip} output={config.output}",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
    return 0


def _serve_control(config: PublisherConfig, bind: str, port: int) -> int:
    config.validate()
    if not _normalized_ip(bind).is_loopback:
        raise AuthorizationLinkError("authorization control service must remain loopback-only")
    if not 1 <= port <= 65535:
        raise AuthorizationLinkError("control port must be inside 1..65535")
    publisher = AuthorizationPublisher(config)
    stop = threading.Event()
    server = AuthorizationHTTPServer((bind, port), _control_handler_factory(publisher))
    worker = threading.Thread(target=publisher.run, args=(stop,), daemon=True)
    worker.start()
    print(
        f"READY authorization publisher on {bind}:{port} target={config.target_url} "
        f"interval_ms={config.interval_ms}",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Maintain five fresh movement-v2 authorization leases over Ethernet"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    policy = subparsers.add_parser("set-policy", help="atomically update desired decisions")
    policy.add_argument("--file", type=Path, required=True)
    policy.add_argument("--node", default="all")
    policy.add_argument("--decision", required=True)
    policy.add_argument("--reason", required=True)

    receiver = subparsers.add_parser("receive", help="run Pratik's atomic Windows receiver")
    receiver.add_argument("--bind", required=True)
    receiver.add_argument("--port", type=int, default=8772)
    receiver.add_argument("--peer-ip", required=True)
    receiver.add_argument("--output", type=Path, required=True)
    receiver.add_argument("--mission-id", default="OP-VARUNA-001")
    receiver.add_argument("--max-age-ms", type=int, default=1_500)

    serve = subparsers.add_parser("serve", help="run Mac publisher plus loopback control API")
    serve.add_argument("--policy", type=Path, required=True)
    serve.add_argument("--state", type=Path, required=True)
    serve.add_argument("--target-url", required=True)
    serve.add_argument("--mission-id", default="OP-VARUNA-001")
    serve.add_argument("--interval-ms", type=int, default=500)
    serve.add_argument("--timeout", type=float, default=1.0)
    serve.add_argument("--bind", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8773)
    args = parser.parse_args(argv)

    try:
        if args.command == "set-policy":
            updated = update_policy(
                args.file,
                node=args.node.casefold(),
                decision=args.decision,
                reason=args.reason,
            )
            summary = ", ".join(
                f"{node}={entry['decision']}"
                for node, entry in updated["decisions"].items()
            )
            print(f"PASS policy={args.file} {summary}")
            return 0
        if args.command == "receive":
            return _serve_receiver(ReceiverConfig(
                bind=args.bind,
                port=args.port,
                peer_ip=args.peer_ip,
                output=args.output,
                mission_id=args.mission_id,
                max_age_ms=args.max_age_ms,
            ))
        return _serve_control(
            PublisherConfig(
                policy=args.policy,
                state=args.state,
                target_url=args.target_url,
                mission_id=args.mission_id,
                interval_ms=args.interval_ms,
                timeout_s=args.timeout,
            ),
            args.bind,
            args.port,
        )
    except (AuthorizationLinkError, ValueError) as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
