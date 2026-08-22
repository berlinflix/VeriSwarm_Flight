"""Fuse calibrated person detections from a JSON capture group."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from rescue.multiview import (
    CameraModel,
    MissingViewEvidence,
    MultiViewFusionError,
    PersonView,
    fuse_person_views,
)


INPUT_SCHEMA = "veriswarm.rescue.multiview.input.v1"
OUTPUT_SCHEMA = "veriswarm.rescue.multiview.result.v1"


def _load(path: Path) -> tuple[str, list[PersonView], list[MissingViewEvidence], dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema") != INPUT_SCHEMA:
        raise MultiViewFusionError(f"input schema must be {INPUT_SCHEMA}")
    candidate_id = payload.get("candidate_id")
    positive_data = payload.get("positive_views")
    missing_data = payload.get("missing_views", [])
    if not isinstance(positive_data, list) or not isinstance(missing_data, list):
        raise MultiViewFusionError("positive_views and missing_views must be lists")
    positives: list[PersonView] = []
    for index, item in enumerate(positive_data):
        if not isinstance(item, dict) or not isinstance(item.get("camera"), dict):
            raise MultiViewFusionError(f"positive_views[{index}] requires a camera object")
        values = dict(item)
        values["camera"] = CameraModel(**values["camera"])
        positives.append(PersonView(**values))
    missing: list[MissingViewEvidence] = []
    for index, item in enumerate(missing_data):
        if not isinstance(item, dict):
            raise MultiViewFusionError(f"missing_views[{index}] must be an object")
        missing.append(MissingViewEvidence(**item))
    options = payload.get("options", {})
    if not isinstance(options, dict):
        raise MultiViewFusionError("options must be an object")
    return candidate_id, positives, missing, options


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fuse positive rescue-person views without negative-vote suppression"
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    try:
        candidate_id, positives, missing, options = _load(args.input)
        fused = fuse_person_views(
            candidate_id,
            positives,
            missing_views=missing,
            **options,
        )
        result = {
            "schema": OUTPUT_SCHEMA,
            "candidate": fused.to_dict(),
            "observation_enrichments": {
                view.observation_id: fused.enrichment_for(view) for view in positives
            },
        }
        raw = json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n"
        if args.out is None:
            sys.stdout.write(raw)
        else:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(raw, encoding="utf-8")
        return 0
    except (OSError, TypeError, json.JSONDecodeError, MultiViewFusionError) as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
