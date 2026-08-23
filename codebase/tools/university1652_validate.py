"""Validate University-1652 retrieval over a labelled drone-query directory.

This is a benchmark and threshold-selection aid.  It never authorizes a position
correction.  Each query's parent directory is treated as its ground-truth location ID;
the report includes per-frame ranks and a temporal fused result for each location.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Sequence

import numpy as np

try:
    from tools.university1652_retrieval import (
        _load_cache,
        _save_cache,
        build_encoder,
        encode_files,
        gallery_images,
        gallery_manifest_sha256,
        location_id,
        normalized_sha256,
        rank_top_k,
        sha256_file,
    )
except ModuleNotFoundError:  # Allow execution from a standalone handoff directory.
    from university1652_retrieval import (  # type: ignore[no-redef]
        _load_cache,
        _save_cache,
        build_encoder,
        encode_files,
        gallery_images,
        gallery_manifest_sha256,
        location_id,
        normalized_sha256,
        rank_top_k,
        sha256_file,
    )


SCHEMA = "veriswarm.geolocation.university1652_validation.v1"


def ground_truth_rank(
    hypotheses: Sequence[dict[str, object]], ground_truth: str
) -> int | None:
    for hypothesis in hypotheses:
        if str(hypothesis["location_id"]) == ground_truth:
            return int(hypothesis["rank"])
    return None


def rank_metrics(ranks: Sequence[int | None]) -> dict[str, float | int]:
    if not ranks:
        raise ValueError("at least one query rank is required")
    return {
        "query_count": len(ranks),
        "top1_count": sum(rank == 1 for rank in ranks),
        "top5_count": sum(rank is not None and rank <= 5 for rank in ranks),
        "top1_rate": sum(rank == 1 for rank in ranks) / len(ranks),
        "top5_rate": sum(rank is not None and rank <= 5 for rank in ranks) / len(ranks),
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", required=True, type=Path)
    parser.add_argument("--expected-checkpoint-sha256", required=True)
    parser.add_argument("--gallery-dir", required=True, type=Path)
    parser.add_argument("--gallery-cache", required=True, type=Path)
    parser.add_argument("--query-dir", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--top-k", type=int, default=10)
    args = parser.parse_args()
    if args.batch_size <= 0:
        parser.error("--batch-size must be positive")
    if args.top_k < 5:
        parser.error("--top-k must be at least 5 for top-5 validation")
    return args


def main() -> int:
    args = _parse_args()
    checkpoint = args.checkpoint.expanduser().resolve()
    gallery_root = args.gallery_dir.expanduser().resolve()
    query_root = args.query_dir.expanduser().resolve()
    cache = args.gallery_cache.expanduser().resolve()
    output = args.out.expanduser().resolve()
    if not checkpoint.is_file():
        raise SystemExit(f"checkpoint not found: {checkpoint}")
    if output.exists():
        raise SystemExit(f"refusing to overwrite existing output: {output}")

    model_hash = sha256_file(checkpoint)
    expected_hash = normalized_sha256(
        args.expected_checkpoint_sha256, "expected_checkpoint_sha256"
    )
    if model_hash != expected_hash:
        raise SystemExit(
            f"checkpoint hash mismatch: expected {expected_hash}, got {model_hash}"
        )

    gallery_paths = gallery_images(gallery_root)
    gallery_labels = [location_id(path) for path in gallery_paths]
    gallery_manifest = gallery_manifest_sha256(gallery_paths, gallery_root)
    query_paths = gallery_images(query_root)
    query_labels = [location_id(path) for path in query_paths]

    encoder = build_encoder(checkpoint, args.device)
    if cache.exists():
        gallery_features, gallery_labels, display_paths = _load_cache(
            cache, model_hash, gallery_manifest
        )
    else:
        gallery_features = encode_files(
            encoder, gallery_paths, args.device, args.batch_size
        )
        _save_cache(
            cache,
            features=gallery_features,
            labels=gallery_labels,
            paths=gallery_paths,
            model_sha256=model_hash,
            manifest_sha256=gallery_manifest,
        )
        display_paths = [str(path) for path in gallery_paths]

    if args.top_k > len(gallery_labels):
        raise SystemExit("--top-k exceeds gallery size")
    query_features = encode_files(encoder, query_paths, args.device, args.batch_size)

    per_query: list[dict[str, object]] = []
    ranks: list[int | None] = []
    grouped_features: dict[str, list[np.ndarray]] = defaultdict(list)
    for path, truth, feature in zip(
        query_paths, query_labels, query_features, strict=True
    ):
        hypotheses = rank_top_k(
            feature,
            gallery_features,
            gallery_labels,
            display_paths,
            args.top_k,
        )
        rank = ground_truth_rank(hypotheses, truth)
        ranks.append(rank)
        grouped_features[truth].append(feature)
        per_query.append(
            {
                "query": str(path),
                "ground_truth": truth,
                "ground_truth_rank": rank,
                "top_k": hypotheses,
            }
        )

    fused: list[dict[str, object]] = []
    for truth, values in sorted(grouped_features.items()):
        fused_feature = np.mean(np.stack(values), axis=0)
        hypotheses = rank_top_k(
            fused_feature,
            gallery_features,
            gallery_labels,
            display_paths,
            args.top_k,
        )
        fused.append(
            {
                "ground_truth": truth,
                "frame_count": len(values),
                "ground_truth_rank": ground_truth_rank(hypotheses, truth),
                "top_k": hypotheses,
                "meaning": "offline temporal descriptor fusion; still requires VIO/IMU confirmation",
            }
        )

    report = {
        "schema": SCHEMA,
        "checkpoint": str(checkpoint),
        "checkpoint_sha256": model_hash,
        "gallery_root": str(gallery_root),
        "gallery_manifest_sha256": gallery_manifest,
        "gallery_size": len(gallery_labels),
        "query_root": str(query_root),
        "metrics": rank_metrics(ranks),
        "per_query": per_query,
        "temporal_fused": fused,
        "position_correction_authorized": False,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
    print(json.dumps({key: report[key] for key in ("schema", "metrics", "temporal_fused")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
