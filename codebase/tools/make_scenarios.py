"""Generate degraded-quorum scenario cases from the frozen public cases.

The bundle's `public/` directory holds the two panel cases, each carrying the
outcome it predicts under a **full** quorum. Demonstrating peer loss needs the
same receipt inputs judged against a *different* prediction, so this tool derives
variant case files that differ from the originals in exactly one respect: the
`expected` block.

Why derive rather than hand-write
---------------------------------
Every field that reaches the wire -- model hash, runtime hash, action, perception
claim, mission id/epoch -- is copied verbatim from the signed public case. Only
the prediction changes. A hand-written variant could drift from the original and
silently demonstrate something other than the case it claims to be.

Why not a `--expect-outcome` command-line flag
----------------------------------------------
An operator able to type the expected verdict at run time is an operator able to
make any result "pass". Keeping predictions in files means a reviewer can diff
the scenario against the public case and see precisely what was claimed before
the run happened. Outputs land in `scenarios/`, never in `public/`, so the
bundle's identity and `PUBLIC_SHA256SUMS` are unchanged.

Scenarios produced
------------------
``degraded``  exactly one peer reachable::

    clean       ack=1 < ack_threshold=2          -> NO_QUORUM  -> HOLD
    model_swap  dispute=1 >= dispute_threshold=1 -> REJECTED   -> HOLD

``isolated``  no peer reachable::

    clean       ack=0, dispute=0                 -> NO_QUORUM  -> HOLD
    model_swap  ack=0, dispute=0                 -> NO_QUORUM  -> HOLD

The asymmetry in ``degraded`` is the point worth demonstrating: one honest
witness is enough to *refuse* a command, but authorising one still requires the
full quorum. A degraded swarm becomes more conservative, never more permissive.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

#: scenario -> case -> (expected block, human note)
SCENARIOS: dict[str, dict[str, tuple[dict, str]]] = {
    "degraded": {
        "clean": (
            {"outcome": "NO_QUORUM", "acks": 1, "disputes": 0, "missing": 1,
             "semantic_acks": 1, "decision": "HOLD"},
            "one peer offline: a single ACK cannot reach ack_threshold=2, so the "
            "command is NOT authorised even though the surviving peer agreed",
        ),
        "model_swap": (
            {"outcome": "REJECTED", "disputes": 1, "missing": 1,
             "decision": "HOLD"},
            "one peer offline: a single DISPUTE still meets dispute_threshold=1, "
            "so the unapproved model is rejected by one honest witness alone",
        ),
    },
    "isolated": {
        "clean": (
            {"outcome": "NO_QUORUM", "acks": 0, "disputes": 0, "missing": 2,
             "semantic_acks": 0, "decision": "HOLD"},
            "no peers reachable: nothing is verified, so nothing is authorised",
        ),
        "model_swap": (
            {"outcome": "NO_QUORUM", "acks": 0, "disputes": 0, "missing": 2,
             "decision": "HOLD"},
            "no peers reachable: the outcome is NO_QUORUM, not REJECTED -- the "
            "swarm does not claim to have caught what it never verified",
        ),
    },
}

SOURCE_CASES = {"clean": "clean-case.json", "model_swap": "model-swap-case.json"}


def build(public_dir: Path, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for scenario, cases in SCENARIOS.items():
        for case_key, (expected, note) in cases.items():
            source = json.loads((public_dir / SOURCE_CASES[case_key]).read_text())
            variant = dict(source)  # verbatim inputs
            variant["case_id"] = f"{source['case_id']}-{scenario.upper()}"
            variant["expected"] = expected
            variant["derived_from"] = SOURCE_CASES[case_key]
            variant["scenario"] = scenario
            variant["note"] = note
            path = out_dir / f"{case_key.replace('_', '-')}-{scenario}.json"
            path.write_text(json.dumps(variant, indent=2, sort_keys=True) + "\n")
            written.append(path)
    return written


def _main() -> None:
    parser = argparse.ArgumentParser(description="Derive degraded-quorum scenario cases")
    parser.add_argument("--public-dir", required=True, help="bundle public/ directory")
    parser.add_argument("--out", required=True, help="scenarios/ output directory")
    args = parser.parse_args()

    public_dir = Path(args.public_dir).expanduser().resolve(strict=True)
    written = build(public_dir, Path(args.out).expanduser().resolve())
    for path in written:
        data = json.loads(path.read_text())
        print(f"  {path.name:<28} {data['case_id']:<24} -> {data['expected']['outcome']}")
    print(f"\n{len(written)} scenario cases written (public/ untouched)")


if __name__ == "__main__":
    _main()
