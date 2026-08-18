"""Narrow HTTP bridge from the C2 dashboard to the qualification runner.

Run this service on the Jetson, beside Alpha's OP-TEE-scoped manifest.  The
browser never receives the service token or any private manifest.  Only the
reviewed ``clean`` and ``model_swap`` cases can be selected; request payloads
cannot provide paths, commands, model hashes, or expected verdicts.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse


SERVICE_SCHEMA = "veriswarm.qualification_dashboard.v1"
CASE_FILES = {
    "clean": "clean-case.json",
    "model_swap": "model-swap-case.json",
}
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
MAX_REQUEST_BYTES = 4096


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON object required: {path}")
    return payload


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_once_json(path: Path, payload: dict[str, Any]) -> None:
    raw = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise


@dataclass(frozen=True)
class ServiceConfig:
    manifest_path: Path
    public_dir: Path
    evidence_dir: Path
    token: str

    def validate(self) -> None:
        if len(self.token) < 32:
            raise ValueError("qualification token must contain at least 32 characters")
        manifest = _read_json(self.manifest_path)
        alpha = manifest.get("nodes", {}).get("alpha", {})
        if alpha.get("backend") != "optee":
            raise ValueError("dashboard qualification requires Alpha backend=optee")
        if "seed" in alpha:
            raise ValueError("Alpha OP-TEE manifest must not contain a software seed")
        for filename in (*CASE_FILES.values(), "contract.json"):
            if not (self.public_dir / filename).is_file():
                raise FileNotFoundError(self.public_dir / filename)
        self.evidence_dir.mkdir(parents=True, exist_ok=True)


def enrich_evidence(
    evidence: dict[str, Any],
    *,
    contract: dict[str, Any],
    case_name: str,
    manifest_sha256: str,
) -> dict[str, Any]:
    """Attach a read-only provenance proof derived from actual runner output."""
    receipt = evidence.get("receipt", {})
    actual = evidence.get("actual", {})
    authorization = evidence.get("authorization", {})
    approved = str(contract.get("approved_model_sha256", ""))
    tampered = str(contract.get("tampered_model_sha256", ""))
    observed = str(receipt.get("model_hash", ""))
    released = authorization.get("released")

    is_model_swap = case_name == "model_swap"
    model_identity_valid = (
        len(approved) == 64
        and len(observed) == 64
        and (
            (not is_model_swap and observed == approved)
            or (is_model_swap and observed == tampered and observed != approved)
        )
    )
    rejection_valid = (
        not is_model_swap
        or (
            actual.get("outcome") == "REJECTED"
            and int(actual.get("disputes", 0)) >= 1
            and authorization.get("allowed") is False
            and released == [0.0, 0.0, 0.0]
        )
    )
    proof_valid = bool(evidence.get("pass")) and model_identity_valid and rejection_valid

    return {
        **evidence,
        "dashboard_proof": {
            "schema": SERVICE_SCHEMA,
            "case": case_name,
            "approved_model_sha256": approved,
            "tampered_model_sha256": tampered if is_model_swap else None,
            "observed_model_sha256": observed,
            "observed_is_approved": observed == approved,
            "manifest_sha256": manifest_sha256,
            "signer_backend": "optee",
            "policy_reason": (
                "model_hash_not_approved" if is_model_swap else "model_hash_approved"
            ),
            "proof_valid": proof_valid,
        },
    }


def _handler_factory(
    config: ServiceConfig,
    run_case: Callable[..., dict[str, Any]],
) -> type[BaseHTTPRequestHandler]:
    run_lock = threading.Lock()
    contract = _read_json(config.public_dir / "contract.json")
    manifest_sha256 = _sha256_file(config.manifest_path)

    class Handler(BaseHTTPRequestHandler):
        server_version = "VeriSwarmQualification/1"

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            print(f"qualification-api {self.address_string()} {format % args}")

        def _send(self, status: int, payload: dict[str, Any]) -> None:
            raw = (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)

        def _authorized(self) -> bool:
            supplied = self.headers.get("X-VeriSwarm-Token", "")
            return hmac.compare_digest(supplied, config.token)

        def _require_auth(self) -> bool:
            if self._authorized():
                return True
            self._send(401, {"ok": False, "error": "unauthorized"})
            return False

        def do_GET(self) -> None:  # noqa: N802
            if not self._require_auth():
                return
            if urlparse(self.path).path != "/health":
                self._send(404, {"ok": False, "error": "not_found"})
                return
            self._send(
                200,
                {
                    "ok": True,
                    "schema": SERVICE_SCHEMA,
                    "service": "qualification_dashboard",
                    "signerBackend": "optee",
                    "cases": sorted(CASE_FILES),
                    "manifestSha256": manifest_sha256,
                    "publicManifestSha256": contract.get("public_manifest_sha256"),
                },
            )

        def do_POST(self) -> None:  # noqa: N802
            if not self._require_auth():
                return
            if urlparse(self.path).path != "/runs":
                self._send(404, {"ok": False, "error": "not_found"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 1 or length > MAX_REQUEST_BYTES:
                    raise ValueError("invalid_request_size")
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError("request_object_required")
                case_name = body.get("case")
                if case_name not in CASE_FILES:
                    raise ValueError("unsupported_case")
                run_id = body.get("runId") or f"dashboard-{case_name}-{time.time_ns()}"
                if not isinstance(run_id, str) or not RUN_ID_RE.fullmatch(run_id):
                    raise ValueError("invalid_run_id")
            except (ValueError, json.JSONDecodeError) as error:
                self._send(400, {"ok": False, "error": str(error)})
                return

            if not run_lock.acquire(blocking=False):
                self._send(409, {"ok": False, "error": "qualification_busy"})
                return
            try:
                case_path = config.public_dir / CASE_FILES[case_name]
                output_path = config.evidence_dir / f"{run_id}.json"
                evidence = run_case(
                    manifest_path=config.manifest_path,
                    case_path=case_path,
                    out_path=output_path,
                    run_id=run_id,
                )
                enriched = enrich_evidence(
                    evidence,
                    contract=contract,
                    case_name=case_name,
                    manifest_sha256=manifest_sha256,
                )
                dashboard_path = config.evidence_dir / f"{run_id}.dashboard.json"
                _write_once_json(dashboard_path, enriched)
                self._send(200 if enriched["dashboard_proof"]["proof_valid"] else 422, {
                    "ok": enriched["dashboard_proof"]["proof_valid"],
                    "evidence": enriched,
                    "dashboardEvidenceFile": dashboard_path.name,
                })
            except FileExistsError:
                self._send(409, {"ok": False, "error": "evidence_exists"})
            except SystemExit as error:
                self._send(503, {"ok": False, "error": str(error)})
            except Exception as error:  # fail closed; detail is retained locally
                self._send(500, {"ok": False, "error": f"qualification_failed:{error}"})
            finally:
                run_lock.release()

    return Handler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve dashboard qualification cases")
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--manifest", required=True, help="Alpha-scoped OP-TEE manifest")
    parser.add_argument("--public-dir", required=True, help="qualification bundle public directory")
    parser.add_argument("--evidence-dir", required=True, help="create-once protocol evidence directory")
    args = parser.parse_args(argv)

    token = os.environ.get("VERISWARM_QUALIFIER_TOKEN", "")
    config = ServiceConfig(
        manifest_path=Path(args.manifest).expanduser().resolve(strict=True),
        public_dir=Path(args.public_dir).expanduser().resolve(strict=True),
        evidence_dir=Path(args.evidence_dir).expanduser().resolve(),
        token=token,
    )
    config.validate()

    from tools.qualification_protocol_demo import run_case

    server = ThreadingHTTPServer((args.bind, args.port), _handler_factory(config, run_case))
    print(
        f"READY qualification dashboard service on {args.bind}:{args.port} "
        f"manifest={_sha256_file(config.manifest_path)[:16]}",
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
