"""Offline University-1652 drone-to-satellite retrieval for Jetson.

This is a coarse visual relocalization sensor, not a flight controller or a GPS
replacement.  It embeds one drone query, ranks a frozen satellite gallery and emits
top-k hypotheses.  A match is never accepted unless explicit similarity and margin
thresholds are supplied; downstream VIO/IMU consistency remains mandatory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np


SCHEMA = "veriswarm.geolocation.university1652_retrieval.v1"
IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".bmp", ".webp"})


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalized_sha256(value: str, field: str) -> str:
    result = value.strip().lower()
    if len(result) != 64 or any(character not in "0123456789abcdef" for character in result):
        raise ValueError(f"{field} must be a 64-character hexadecimal SHA-256")
    return result


def gallery_images(root: Path) -> list[Path]:
    if not root.is_dir():
        raise ValueError(f"gallery directory not found: {root}")
    result = sorted(
        path.resolve()
        for path in root.rglob("*")
        if path.is_file() and path.suffix.casefold() in IMAGE_SUFFIXES
    )
    if not result:
        raise ValueError(f"gallery contains no supported images: {root}")
    return result


def location_id(path: Path) -> str:
    value = path.parent.name.strip()
    if not value:
        raise ValueError(f"gallery image has no parent location ID: {path}")
    return value


def gallery_manifest_sha256(paths: Sequence[Path], root: Path) -> str:
    digest = hashlib.sha256()
    resolved_root = root.resolve()
    for path in paths:
        resolved = path.resolve()
        try:
            relative = resolved.relative_to(resolved_root).as_posix()
        except ValueError as error:
            raise ValueError(f"gallery file escapes gallery root: {resolved}") from error
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(sha256_file(resolved).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def l2_normalize(rows: np.ndarray) -> np.ndarray:
    values = np.asarray(rows, dtype=np.float32)
    if values.ndim != 2 or values.shape[0] == 0 or values.shape[1] == 0:
        raise ValueError("features must be a non-empty rank-2 array")
    norms = np.linalg.norm(values, axis=1, keepdims=True)
    if not np.all(np.isfinite(norms)) or np.any(norms <= 0.0):
        raise ValueError("features contain a non-finite or zero-norm row")
    return values / norms


def rank_top_k(
    query: np.ndarray,
    gallery: np.ndarray,
    labels: Sequence[str],
    paths: Sequence[str],
    top_k: int,
) -> list[dict[str, object]]:
    query_values = l2_normalize(np.asarray(query, dtype=np.float32).reshape(1, -1))[0]
    gallery_values = l2_normalize(gallery)
    if len(labels) != gallery_values.shape[0] or len(paths) != gallery_values.shape[0]:
        raise ValueError("gallery features, labels and paths must have equal lengths")
    if top_k <= 0 or top_k > gallery_values.shape[0]:
        raise ValueError("top_k is outside the gallery size")
    scores = gallery_values @ query_values
    order = np.argsort(-scores, kind="stable")[:top_k]
    return [
        {
            "rank": rank,
            "location_id": str(labels[index]),
            "gallery_path": str(paths[index]),
            "cosine_similarity": float(scores[index]),
        }
        for rank, index in enumerate(order, start=1)
    ]


def acceptance_decision(
    hypotheses: Sequence[dict[str, object]],
    *,
    minimum_similarity: float | None,
    minimum_margin: float | None,
) -> tuple[bool, str, float | None]:
    if not hypotheses:
        return False, "no_hypotheses", None
    if minimum_similarity is None or minimum_margin is None:
        return False, "thresholds_not_configured", None
    if not 0.0 <= minimum_similarity <= 1.0 or not 0.0 <= minimum_margin <= 2.0:
        raise ValueError("similarity and margin thresholds are outside valid ranges")
    first = float(hypotheses[0]["cosine_similarity"])
    second = float(hypotheses[1]["cosine_similarity"]) if len(hypotheses) > 1 else -1.0
    margin = first - second
    if first < minimum_similarity:
        return False, "similarity_below_threshold", margin
    if margin < minimum_margin:
        return False, "top1_margin_below_threshold", margin
    return True, "appearance_gate_passed_vio_confirmation_required", margin


def _load_checkpoint(path: Path) -> dict[str, object]:
    import torch  # type: ignore[import-not-found]

    try:
        value = torch.load(path, map_location="cpu", weights_only=True)
    except TypeError:  # Compatibility with older JetPack PyTorch builds.
        value = torch.load(path, map_location="cpu")
    if isinstance(value, dict) and "state_dict" in value and isinstance(value["state_dict"], dict):
        value = value["state_dict"]
    if not isinstance(value, dict) or not value:
        raise ValueError("checkpoint is not a non-empty state dictionary")
    result: dict[str, object] = {}
    for raw_key, tensor in value.items():
        key = str(raw_key)
        if key.startswith("module."):
            key = key[len("module.") :]
        result[key] = tensor
    return result


def build_encoder(checkpoint: Path, device: str):
    """Rebuild only the shared satellite/drone branch of the legacy network."""

    import torch  # type: ignore[import-not-found]
    import torch.nn as nn  # type: ignore[import-not-found]
    from torchvision import models  # type: ignore[import-not-found]

    class Encoder(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            # weights=None is deliberate: deployment must remain offline and the complete
            # learned backbone is restored from net_119.pth.
            self.backbone = models.resnet50(weights=None)
            self.backbone.layer4[0].downsample[0].stride = (1, 1)
            self.backbone.layer4[0].conv2.stride = (1, 1)
            self.projection = nn.Sequential(
                nn.Linear(2048, 512),
                nn.BatchNorm1d(512),
                nn.Dropout(p=0.75),
            )

        def forward(self, image):
            model = self.backbone
            value = model.conv1(image)
            value = model.bn1(value)
            value = model.relu(value)
            value = model.maxpool(value)
            value = model.layer1(value)
            value = model.layer2(value)
            value = model.layer3(value)
            value = model.layer4(value)
            value = nn.functional.adaptive_avg_pool2d(value, (1, 1)).flatten(1)
            return self.projection(value)

    state = _load_checkpoint(checkpoint)
    backbone_prefix = "model_1.model."
    projection_prefix = "classifier.add_block."
    backbone_state = {
        key[len(backbone_prefix) :]: tensor
        for key, tensor in state.items()
        if key.startswith(backbone_prefix)
    }
    projection_state = {
        key[len(projection_prefix) :]: tensor
        for key, tensor in state.items()
        if key.startswith(projection_prefix)
    }
    if not backbone_state or not projection_state:
        raise ValueError("checkpoint lacks the expected University-1652 model_1/projection keys")

    encoder = Encoder()
    backbone_result = encoder.backbone.load_state_dict(backbone_state, strict=True)
    projection_result = encoder.projection.load_state_dict(projection_state, strict=True)
    if backbone_result.missing_keys or backbone_result.unexpected_keys:
        raise ValueError(f"backbone state mismatch: {backbone_result}")
    if projection_result.missing_keys or projection_result.unexpected_keys:
        raise ValueError(f"projection state mismatch: {projection_result}")
    encoder.eval().to(torch.device(device))
    return encoder


def encode_pil_images(encoder, images: Sequence[object], device: str) -> np.ndarray:
    """Encode already-open PIL images using the frozen evaluation transform."""

    import torch  # type: ignore[import-not-found]
    from torchvision import transforms  # type: ignore[import-not-found]

    if not images:
        raise ValueError("at least one image is required")
    preprocess = transforms.Compose(
        [
            transforms.Resize((256, 256), interpolation=transforms.InterpolationMode.BICUBIC),
            transforms.ToTensor(),
            transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
        ]
    )
    device_value = torch.device(device)
    items = [preprocess(image.convert("RGB")) for image in images]
    tensors = torch.stack(items).to(device_value)
    with torch.inference_mode():
        features = encoder(tensors) + encoder(torch.flip(tensors, dims=(3,)))
        features = torch.nn.functional.normalize(features, p=2, dim=1)
    return l2_normalize(features.detach().cpu().numpy().astype(np.float32, copy=False))


def encode_files(encoder, paths: Sequence[Path], device: str, batch_size: int) -> np.ndarray:
    from PIL import Image  # type: ignore[import-not-found]

    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    batches: list[np.ndarray] = []
    for start in range(0, len(paths), batch_size):
        opened = []
        try:
            for path in paths[start : start + batch_size]:
                opened.append(Image.open(path))
            batches.append(encode_pil_images(encoder, opened, device))
        finally:
            for image in opened:
                image.close()
    return l2_normalize(np.concatenate(batches, axis=0))


def _save_cache(
    path: Path,
    *,
    features: np.ndarray,
    labels: Sequence[str],
    paths: Sequence[Path],
    model_sha256: str,
    manifest_sha256: str,
) -> None:
    if path.exists():
        raise ValueError(f"refusing to overwrite existing gallery cache: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        path,
        features=np.asarray(features, dtype=np.float16),
        labels=np.asarray(labels),
        paths=np.asarray([str(item) for item in paths]),
        model_sha256=np.asarray(model_sha256),
        gallery_manifest_sha256=np.asarray(manifest_sha256),
    )


def _load_cache(path: Path, model_sha256: str, manifest_sha256: str):
    try:
        with np.load(path, allow_pickle=False) as value:
            cached_model = str(value["model_sha256"].item())
            cached_manifest = str(value["gallery_manifest_sha256"].item())
            if cached_model != model_sha256 or cached_manifest != manifest_sha256:
                raise ValueError("gallery cache identity does not match model/gallery bytes")
            return (
                l2_normalize(value["features"].astype(np.float32)),
                [str(item) for item in value["labels"].tolist()],
                [str(item) for item in value["paths"].tolist()],
            )
    except (OSError, KeyError) as error:
        raise ValueError(f"invalid gallery cache: {error}") from error


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", required=True, type=Path)
    parser.add_argument("--expected-checkpoint-sha256")
    parser.add_argument("--gallery-dir", required=True, type=Path)
    parser.add_argument("--gallery-cache", required=True, type=Path)
    parser.add_argument("--query", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--minimum-similarity", type=float)
    parser.add_argument("--minimum-margin", type=float)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    checkpoint = args.checkpoint.expanduser().resolve()
    query = args.query.expanduser().resolve()
    gallery_root = args.gallery_dir.expanduser().resolve()
    cache = args.gallery_cache.expanduser().resolve()
    output = args.out.expanduser().resolve()
    if not checkpoint.is_file() or not query.is_file():
        raise SystemExit("checkpoint or query image does not exist")
    if output.exists():
        raise SystemExit(f"refusing to overwrite existing output: {output}")

    model_hash = sha256_file(checkpoint)
    if args.expected_checkpoint_sha256:
        expected = normalized_sha256(
            args.expected_checkpoint_sha256, "expected_checkpoint_sha256"
        )
        if model_hash != expected:
            raise SystemExit(
                f"checkpoint hash mismatch: expected {expected}, got {model_hash}"
            )

    paths = gallery_images(gallery_root)
    labels = [location_id(path) for path in paths]
    manifest_hash = gallery_manifest_sha256(paths, gallery_root)
    encoder = build_encoder(checkpoint, args.device)
    if cache.exists():
        gallery_features, cached_labels, cached_paths = _load_cache(
            cache, model_hash, manifest_hash
        )
        labels = cached_labels
        display_paths = cached_paths
    else:
        gallery_features = encode_files(
            encoder, paths, args.device, args.batch_size
        )
        _save_cache(
            cache,
            features=gallery_features,
            labels=labels,
            paths=paths,
            model_sha256=model_hash,
            manifest_sha256=manifest_hash,
        )
        display_paths = [str(path) for path in paths]

    query_feature = encode_files(encoder, [query], args.device, 1)[0]
    hypotheses = rank_top_k(
        query_feature, gallery_features, labels, display_paths, args.top_k
    )
    accepted, reason, margin = acceptance_decision(
        hypotheses,
        minimum_similarity=args.minimum_similarity,
        minimum_margin=args.minimum_margin,
    )
    result = {
        "schema": SCHEMA,
        "accepted": accepted,
        "reason": reason,
        "appearance_margin": margin,
        "vio_confirmation_required": True,
        "checkpoint": str(checkpoint),
        "checkpoint_sha256": model_hash,
        "gallery_root": str(gallery_root),
        "gallery_manifest_sha256": manifest_hash,
        "gallery_size": len(labels),
        "gallery_cache": str(cache),
        "query": str(query),
        "top_k": hypotheses,
        "thresholds": {
            "minimum_similarity": args.minimum_similarity,
            "minimum_margin": args.minimum_margin,
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
