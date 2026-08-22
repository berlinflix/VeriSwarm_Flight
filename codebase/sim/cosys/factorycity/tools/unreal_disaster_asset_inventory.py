"""Inventory disaster-relevant Unreal assets without modifying the project."""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

import unreal


OUTPUT_ENV = "VERISWARM_DISASTER_ASSET_INVENTORY_OUTPUT"
SEARCH_TERMS = (
    "barrier",
    "barrel",
    "broken",
    "car",
    "concrete",
    "debris",
    "fire",
    "human",
    "mannequin",
    "person",
    "pipe",
    "rubble",
    "smoke",
    "traffic",
    "trash",
    "vehicle",
    "water",
)
ALLOWED_CLASSES = {
    "Material",
    "MaterialInstanceConstant",
    "NiagaraSystem",
    "ParticleSystem",
    "SkeletalMesh",
    "StaticMesh",
}


def _required_environment(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"missing required environment variable {name}")
    return value


def _main() -> None:
    output = Path(_required_environment(OUTPUT_ENV))
    registry = unreal.AssetRegistryHelpers.get_asset_registry()
    assets = registry.get_all_assets()
    candidates: list[dict[str, object]] = []
    for asset in assets:
        class_name = str(asset.asset_class_path.asset_name)
        if class_name not in ALLOWED_CLASSES:
            continue
        asset_name = str(asset.asset_name)
        package_name = str(asset.package_name)
        haystack = f"{asset_name} {package_name}".casefold()
        matches = [term for term in SEARCH_TERMS if term in haystack]
        if not matches:
            continue
        candidates.append(
            {
                "asset_name": asset_name,
                "asset_class": class_name,
                "package_name": package_name,
                "object_path": f"{package_name}.{asset_name}",
                "matched_terms": matches,
            }
        )

    result = {
        "schema": "veriswarm.factorycity.disaster_asset_inventory.v1",
        "captured_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "search_terms": SEARCH_TERMS,
        "candidate_count": len(candidates),
        "candidates": sorted(
            candidates,
            key=lambda item: (item["asset_class"], item["package_name"]),
        ),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    unreal.log(f"Disaster asset inventory written to {output}")


_main()
