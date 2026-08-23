"""Select reproducible, correctly retrieved University-1652 judge-demo images.

This is an evidence-preparation tool, not training.  It runs every drone image
in one known-location folder through the frozen model, keeps only top-1-correct
queries, ranks them by appearance margin, and writes a self-describing folder.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

try:
    from tools.university1652_console import enrich_hypotheses, load_locations
    from tools.university1652_retrieval import (
        _load_cache,
        build_encoder,
        encode_files,
        gallery_images,
        gallery_manifest_sha256,
        location_id,
        normalized_sha256,
        rank_top_k,
        sha256_file,
    )
except ModuleNotFoundError:
    from university1652_console import enrich_hypotheses, load_locations  # type: ignore[no-redef]
    from university1652_retrieval import (  # type: ignore[no-redef]
        _load_cache,
        build_encoder,
        encode_files,
        gallery_images,
        gallery_manifest_sha256,
        location_id,
        normalized_sha256,
        rank_top_k,
        sha256_file,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", required=True, type=Path)
    parser.add_argument("--expected-checkpoint-sha256", required=True)
    parser.add_argument("--gallery-dir", required=True, type=Path)
    parser.add_argument("--gallery-cache", required=True, type=Path)
    parser.add_argument("--locations", required=True, type=Path)
    parser.add_argument("--query-dir", required=True, type=Path)
    parser.add_argument("--expected-location-id", required=True)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--count", type=int, default=6)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--batch-size", type=int, default=8)
    args = parser.parse_args()

    output = args.out_dir.expanduser().resolve()
    if output.exists():
        raise SystemExit(f"refusing to overwrite existing demo folder: {output}")
    expected_id = args.expected_location_id.strip()
    if len(expected_id) != 4 or not expected_id.isdigit():
        raise SystemExit("--expected-location-id must contain four digits")
    if args.count <= 0:
        raise SystemExit("--count must be positive")

    checkpoint = args.checkpoint.expanduser().resolve(strict=True)
    model_hash = sha256_file(checkpoint)
    expected_hash = normalized_sha256(
        args.expected_checkpoint_sha256, "expected_checkpoint_sha256"
    )
    if model_hash != expected_hash:
        raise SystemExit(f"checkpoint hash mismatch: expected {expected_hash}, got {model_hash}")
    gallery_root = args.gallery_dir.expanduser().resolve(strict=True)
    gallery_paths = gallery_images(gallery_root)
    manifest_hash = gallery_manifest_sha256(gallery_paths, gallery_root)
    features, labels, display_paths = _load_cache(
        args.gallery_cache.expanduser().resolve(strict=True), model_hash, manifest_hash
    )
    queries = gallery_images(args.query_dir.expanduser().resolve(strict=True))
    encoder = build_encoder(checkpoint, args.device)
    query_features = encode_files(encoder, queries, args.device, args.batch_size)
    locations = load_locations(args.locations.expanduser().resolve(strict=True))

    correct: list[dict[str, object]] = []
    for path, feature in zip(queries, query_features, strict=True):
        hypotheses = rank_top_k(feature, features, labels, display_paths, 5)
        enriched = enrich_hypotheses(hypotheses, locations)
        top_score = float(enriched[0]["cosine_similarity"])
        second_score = float(enriched[1]["cosine_similarity"])
        if enriched[0]["location_id"] == expected_id:
            correct.append(
                {
                    "source": str(path),
                    "filename": path.name,
                    "top1_similarity": top_score,
                    "top1_margin": top_score - second_score,
                    "top_k": enriched,
                }
            )
    correct.sort(
        key=lambda item: (float(item["top1_margin"]), float(item["top1_similarity"])),
        reverse=True,
    )
    selected = correct[: args.count]
    if not selected:
        raise SystemExit("no top-1-correct query images were found; no demo folder created")

    output.mkdir(parents=True)
    for index, item in enumerate(selected, 1):
        source = Path(str(item["source"]))
        target_name = f"DEMO_{index:02d}_{expected_id}_{source.name}"
        shutil.copy2(source, output / target_name)
        item["demo_filename"] = target_name
        del item["source"]
    satellite = Path(display_paths[labels.index(expected_id)])
    satellite_name = f"REFERENCE_SATELLITE_{expected_id}{satellite.suffix.casefold()}"
    shutil.copy2(satellite, output / satellite_name)
    location = locations[expected_id]
    manifest = {
        "schema": "veriswarm.geolocation.judge_demo.v1",
        "purpose": "pre-scored University-1652 cross-view retrieval demonstration",
        "warning": "selection demonstrates known benchmark examples; it is not a field-accuracy estimate",
        "checkpoint_sha256": model_hash,
        "gallery_manifest_sha256": manifest_hash,
        "expected_location": {
            "location_id": expected_id,
            "name": location["name"],
            "latitude": location["latitude"],
            "longitude": location["longitude"],
        },
        "reference_satellite_image": satellite_name,
        "eligible_correct_count": len(correct),
        "selected": selected,
    }
    (output / "DEMO_MANIFEST.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (output / "README.txt").write_text(
        "VeriSwarm University-1652 judge demo\n\n"
        "Upload any DEMO_*.jpeg image to the Visual Geolocation console.\n"
        f"Expected top-1 ID: {expected_id}\n"
        f"Expected place: {location['name']}\n"
        f"Official coordinate: {location['latitude']}, {location['longitude']}\n\n"
        "The files were selected only after frozen-model evaluation. This is a known-example\n"
        "demonstration, not a claim that every arbitrary location is identified correctly.\n",
        encoding="utf-8",
    )
    print(
        f"selected {len(selected)} of {len(correct)} top-1-correct images into {output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
