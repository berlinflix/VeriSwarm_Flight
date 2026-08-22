"""CLI for durable rescue-event enqueue and delivery."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from rescue.outbox import RescueOutbox, RescueOutboxError
from rescue.schema import RescueEventError


TOKEN_ENV = "VERISWARM_RESCUE_TOKEN"


def _events_from_file(path: Path) -> list[dict[str, Any]]:
    raw = path.read_text(encoding="utf-8")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        events = []
        for line_number, line in enumerate(raw.splitlines(), 1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as error:
                raise RescueOutboxError(
                    f"invalid JSON at {path}:{line_number}: {error.msg}"
                ) from error
            if not isinstance(event, dict):
                raise RescueOutboxError(f"event at {path}:{line_number} is not an object")
            events.append(event)
        return events
    if isinstance(payload, dict):
        return [payload]
    if isinstance(payload, list) and all(isinstance(item, dict) for item in payload):
        return payload
    raise RescueOutboxError("input must be one event, an event list or JSONL objects")


def _print(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Persist and deliver rescue producer events without losing link outages"
    )
    parser.add_argument("--mission-id", required=True)
    parser.add_argument("--outbox", type=Path, required=True)
    subparsers = parser.add_subparsers(dest="command", required=True)

    enqueue = subparsers.add_parser("enqueue", help="validate and persist event JSON/JSONL")
    enqueue.add_argument("--input", type=Path, required=True)

    flush = subparsers.add_parser("flush", help="deliver pending events to a collector")
    flush.add_argument("--endpoint", required=True)
    flush.add_argument("--timeout", type=float, default=3.0)
    flush.add_argument("--max-events", type=int, default=100)
    flush.add_argument("--transient-retries", type=int, default=2)
    flush.add_argument("--retry-backoff", type=float, default=0.25)

    status = subparsers.add_parser("status", help="inspect pending/dead-letter counts")
    status.add_argument("--limit", type=int, default=20)

    args = parser.parse_args(argv)
    try:
        outbox = RescueOutbox(args.mission_id, args.outbox)
        if args.command == "enqueue":
            accepted = duplicates = 0
            for event in _events_from_file(args.input):
                result = outbox.enqueue(event)
                accepted += int(result.accepted)
                duplicates += int(result.duplicate)
            current = outbox.status(limit=1)
            _print({
                "ok": True,
                "accepted": accepted,
                "duplicates": duplicates,
                "pending": current["pending"],
                "dead_lettered": current["dead_lettered"],
            })
            return 0
        if args.command == "status":
            _print(outbox.status(limit=args.limit))
            return 0

        result = outbox.flush(
            args.endpoint,
            token=os.environ.get(TOKEN_ENV, ""),
            timeout_s=args.timeout,
            max_events=args.max_events,
            transient_retries=args.transient_retries,
            retry_backoff_s=args.retry_backoff,
        )
        payload = {"ok": result.pending == 0, **result.to_dict()}
        _print(payload)
        return 0 if result.pending == 0 else 3
    except (OSError, RescueEventError, RescueOutboxError) as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
