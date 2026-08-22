"""Independent, fail-closed audit for YOLO object-detection datasets.

The tool never edits source data. It decodes every image, validates image/label
pairing and normalized boxes, records per-split class counts, detects
byte-identical images within and across splits, and writes a create-once JSON
report plus a deterministic, hash-bound stratified label montage.

Example, from ``codebase``::

    python -m tools.audit_yolo_dataset \
      --split train D:/data/images/train D:/data/labels/train \
      --split val D:/data/images/val D:/data/labels/val \
      --class-map D:/data/class_map.json \
      --output-dir D:/audits/disaster-response-v1
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

SCHEMA = "veriswarm.dataset_audit.v1"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}
SPLIT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


class DatasetAuditError(RuntimeError):
    """Invalid audit configuration or unsafe output state."""


@dataclass(frozen=True)
class SplitSpec:
    name: str
    images: Path
    labels: Path


@dataclass(frozen=True)
class YoloBox:
    class_id: int
    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True)
class Sample:
    split: str
    stem: str
    image_path: Path
    label_path: Path
    image_sha256: str
    label_sha256: str
    width: int
    height: int
    boxes: tuple[YoloBox, ...]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    payload = json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"
    with temporary.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def parse_split_specs(values: Sequence[Sequence[str]]) -> tuple[SplitSpec, ...]:
    if not values:
        raise DatasetAuditError("at least one --split is required")
    names: set[str] = set()
    specs: list[SplitSpec] = []
    for value in values:
        if len(value) != 3:
            raise DatasetAuditError("each --split requires NAME IMAGE_DIR LABEL_DIR")
        name, images_text, labels_text = value
        if not SPLIT_NAME_RE.fullmatch(name):
            raise DatasetAuditError(f"invalid split name: {name!r}")
        if name in names:
            raise DatasetAuditError(f"duplicate split name: {name}")
        images = Path(images_text).expanduser().resolve()
        labels = Path(labels_text).expanduser().resolve()
        if not images.is_dir():
            raise DatasetAuditError(f"image directory not found for {name}: {images}")
        if not labels.is_dir():
            raise DatasetAuditError(f"label directory not found for {name}: {labels}")
        names.add(name)
        specs.append(SplitSpec(name, images, labels))
    return tuple(specs)


def load_class_map(path: Path) -> dict[int, str]:
    """Load an explicit ``{"0": "person_candidate"}`` JSON class map."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DatasetAuditError(f"could not read class map: {exc}") from exc
    if not isinstance(value, dict) or not value:
        raise DatasetAuditError("class map must be a non-empty JSON object")
    result: dict[int, str] = {}
    for key, name in value.items():
        try:
            class_id = int(key)
        except (TypeError, ValueError) as exc:
            raise DatasetAuditError(f"class ID is not an integer: {key!r}") from exc
        if class_id < 0 or str(class_id) != str(key):
            raise DatasetAuditError(f"class ID must be a canonical non-negative integer: {key!r}")
        if not isinstance(name, str) or not name.strip():
            raise DatasetAuditError(f"class name is empty for ID {class_id}")
        if class_id in result:
            raise DatasetAuditError(f"duplicate class ID: {class_id}")
        result[class_id] = name.strip()
    expected = set(range(max(result) + 1))
    if set(result) != expected:
        raise DatasetAuditError("class IDs must be contiguous from zero")
    return result


def parse_yolo_label(path: Path, class_map: Mapping[int, str]) -> tuple[YoloBox, ...]:
    boxes: list[YoloBox] = []
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError) as exc:
        raise DatasetAuditError(f"cannot read label {path}: {exc}") from exc
    for line_number, raw in enumerate(lines, start=1):
        text = raw.strip()
        if not text:
            continue
        parts = text.split()
        if len(parts) != 5:
            raise DatasetAuditError(
                f"{path}:{line_number}: expected 5 YOLO detection fields, got {len(parts)}"
            )
        try:
            class_value = float(parts[0])
            coordinates = [float(item) for item in parts[1:]]
        except ValueError as exc:
            raise DatasetAuditError(f"{path}:{line_number}: non-numeric field") from exc
        if not all(math.isfinite(item) for item in (class_value, *coordinates)):
            raise DatasetAuditError(f"{path}:{line_number}: non-finite field")
        if not class_value.is_integer():
            raise DatasetAuditError(f"{path}:{line_number}: class ID is not an integer")
        class_id = int(class_value)
        if class_id not in class_map:
            raise DatasetAuditError(f"{path}:{line_number}: unsupported class ID {class_id}")
        x, y, width, height = coordinates
        if not (0 <= x <= 1 and 0 <= y <= 1 and 0 < width <= 1 and 0 < height <= 1):
            raise DatasetAuditError(f"{path}:{line_number}: invalid normalized box")
        if x - width / 2 < 0 or y - height / 2 < 0 or x + width / 2 > 1 or y + height / 2 > 1:
            raise DatasetAuditError(f"{path}:{line_number}: box extends outside image")
        boxes.append(YoloBox(class_id, x, y, width, height))
    return tuple(boxes)


def _indexed_files(root: Path, extensions: set[str]) -> tuple[dict[str, Path], list[str]]:
    indexed: dict[str, Path] = {}
    errors: list[str] = []
    if root.is_symlink() or not root.is_dir():
        return indexed, [f"unsafe or missing data root: {root}"]
    try:
        entries = sorted(root.rglob("*"))
    except OSError as exc:
        return indexed, [f"cannot enumerate data root {root}: {exc}"]
    for path in entries:
        if path.is_symlink():
            errors.append(f"unsafe symbolic link: {path}")
            continue
        if path.is_dir():
            continue
        if not path.is_file():
            errors.append(f"unsafe non-file entry: {path}")
            continue
        if path.suffix.lower() not in extensions:
            errors.append(f"unexpected file extension: {path}")
            continue
        relative_stem = path.relative_to(root).with_suffix("").as_posix()
        if relative_stem in indexed:
            errors.append(
                f"duplicate relative stem {relative_stem!r}: {indexed[relative_stem]} and {path}"
            )
        else:
            indexed[relative_stem] = path
    return indexed, errors


def audit_dataset(
    specs: Sequence[SplitSpec],
    class_map: Mapping[int, str],
    cv2_module: Any,
) -> tuple[dict[str, Any], tuple[Sample, ...]]:
    """Audit all splits without mutating source files."""
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    samples: list[Sample] = []
    split_summary: dict[str, Any] = {}
    class_counts: Counter[int] = Counter()

    for spec in specs:
        images, index_errors = _indexed_files(spec.images, IMAGE_EXTENSIONS)
        labels, label_index_errors = _indexed_files(spec.labels, {".txt"})
        for message in (*index_errors, *label_index_errors):
            errors.append({"split": spec.name, "sample": "", "reason": message})
        missing_labels = sorted(set(images) - set(labels))
        orphan_labels = sorted(set(labels) - set(images))
        for stem in missing_labels:
            errors.append({"split": spec.name, "sample": stem, "reason": "missing_label"})
        for stem in orphan_labels:
            errors.append({"split": spec.name, "sample": stem, "reason": "orphan_label"})

        valid_count = 0
        empty_count = 0
        split_class_counts: Counter[int] = Counter()
        for stem in sorted(set(images) & set(labels)):
            image_path = images[stem]
            label_path = labels[stem]
            image = cv2_module.imread(str(image_path), cv2_module.IMREAD_COLOR)
            if image is None or getattr(image, "ndim", 0) != 3:
                errors.append({"split": spec.name, "sample": stem, "reason": "unreadable_image"})
                continue
            try:
                boxes = parse_yolo_label(label_path, class_map)
            except DatasetAuditError as exc:
                errors.append({"split": spec.name, "sample": stem, "reason": str(exc)})
                continue
            height, width = image.shape[:2]
            if width <= 0 or height <= 0:
                errors.append({"split": spec.name, "sample": stem, "reason": "invalid_image_dimensions"})
                continue
            digest = _sha256(image_path)
            samples.append(
                Sample(
                    spec.name,
                    stem,
                    image_path,
                    label_path,
                    digest,
                    _sha256(label_path),
                    width,
                    height,
                    boxes,
                )
            )
            valid_count += 1
            if not boxes:
                empty_count += 1
            class_counts.update(box.class_id for box in boxes)
            split_class_counts.update(box.class_id for box in boxes)
        split_samples = sorted(
            (sample for sample in samples if sample.split == spec.name),
            key=lambda sample: sample.stem,
        )
        sample_set_digest = hashlib.sha256()
        for sample in split_samples:
            for value in (sample.stem, sample.image_sha256, sample.label_sha256):
                sample_set_digest.update(value.encode("utf-8"))
                sample_set_digest.update(b"\x00")
        split_summary[spec.name] = {
            "image_root": str(spec.images),
            "label_root": str(spec.labels),
            "images": len(images),
            "labels": len(labels),
            "paired": len(set(images) & set(labels)),
            "valid_samples": valid_count,
            "boxes": sum(split_class_counts.values()),
            "class_box_counts": {
                str(key): split_class_counts.get(key, 0)
                for key in sorted(class_map)
            },
            "empty_label_samples": empty_count,
            "missing_labels": len(missing_labels),
            "orphan_labels": len(orphan_labels),
            "sample_set_sha256": sample_set_digest.hexdigest(),
        }

    by_hash: dict[str, list[Sample]] = defaultdict(list)
    for sample in samples:
        by_hash[sample.image_sha256].append(sample)
    duplicate_groups: list[dict[str, Any]] = []
    leakage_groups: list[dict[str, Any]] = []
    for digest, group in sorted(by_hash.items()):
        if len(group) < 2:
            continue
        record = {
            "sha256": digest,
            "samples": [f"{item.split}:{item.stem}" for item in group],
            "splits": sorted({item.split for item in group}),
        }
        duplicate_groups.append(record)
        if len(record["splits"]) > 1:
            leakage_groups.append(record)
            errors.append(
                {
                    "split": ",".join(record["splits"]),
                    "sample": ",".join(record["samples"]),
                    "reason": "cross_split_duplicate_image",
                }
            )
        else:
            warnings.append(
                {
                    "split": record["splits"][0],
                    "sample": ",".join(record["samples"]),
                    "reason": "within_split_duplicate_image",
                }
            )

    report = {
        "schema": SCHEMA,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "passed": not errors,
        "class_map": {str(key): value for key, value in sorted(class_map.items())},
        "splits": split_summary,
        "totals": {
            "valid_samples": len(samples),
            "boxes": sum(len(sample.boxes) for sample in samples),
            "class_box_counts": {
                str(key): class_counts.get(key, 0) for key in sorted(class_map)
            },
            "duplicate_groups": len(duplicate_groups),
            "cross_split_leakage_groups": len(leakage_groups),
            "errors": len(errors),
            "warnings": len(warnings),
        },
        "duplicates": duplicate_groups,
        "cross_split_leakage": leakage_groups,
        "errors": errors,
        "warnings": warnings,
    }
    return report, tuple(samples)


def select_montage_samples(samples: Sequence[Sample], limit: int) -> tuple[Sample, ...]:
    """Round-robin deterministic buckets by split and represented class."""
    buckets: dict[tuple[str, str], deque[Sample]] = defaultdict(deque)
    for sample in sorted(samples, key=lambda item: (item.split, item.stem)):
        keys = sorted({str(box.class_id) for box in sample.boxes}) or ["negative"]
        buckets[(sample.split, "+".join(keys))].append(sample)
    selected: list[Sample] = []
    keys = sorted(buckets)
    while keys and len(selected) < limit:
        next_keys: list[tuple[str, str]] = []
        for key in keys:
            if len(selected) >= limit:
                break
            bucket = buckets[key]
            if bucket:
                candidate = bucket.popleft()
                if candidate not in selected:
                    selected.append(candidate)
            if bucket:
                next_keys.append(key)
        keys = next_keys
    return tuple(selected)


def montage_membership(samples: Sequence[Sample]) -> list[dict[str, str]]:
    """Return the exact deterministic sample identities rendered in a montage."""

    return [
        {
            "split": sample.split,
            "stem": sample.stem,
            "image_sha256": sample.image_sha256,
            "label_sha256": sample.label_sha256,
        }
        for sample in samples
    ]


def montage_membership_sha256(members: Sequence[Mapping[str, str]]) -> str:
    """Hash montage membership independently of JPEG encoder metadata."""

    payload = json.dumps(
        list(members),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def render_montage(
    samples: Sequence[Sample],
    class_map: Mapping[int, str],
    output_path: Path,
    cv2_module: Any,
    *,
    tile_width: int = 320,
    tile_height: int = 220,
    columns: int = 5,
) -> None:
    import numpy as np

    if not samples:
        raise DatasetAuditError("cannot render a montage without valid samples")
    rows = math.ceil(len(samples) / columns)
    canvas = np.zeros((rows * tile_height, columns * tile_width, 3), dtype=np.uint8)
    colours = ((40, 220, 40), (255, 160, 40), (40, 180, 255), (220, 80, 220))
    for index, sample in enumerate(samples):
        image = cv2_module.imread(str(sample.image_path), cv2_module.IMREAD_COLOR)
        if image is None:
            raise DatasetAuditError(f"image became unreadable during montage: {sample.image_path}")
        header = 30
        scale = min(tile_width / image.shape[1], (tile_height - header) / image.shape[0])
        resized_width = max(1, round(image.shape[1] * scale))
        resized_height = max(1, round(image.shape[0] * scale))
        resized = cv2_module.resize(image, (resized_width, resized_height))
        tile = np.zeros((tile_height, tile_width, 3), dtype=np.uint8)
        x_offset = (tile_width - resized_width) // 2
        y_offset = header + (tile_height - header - resized_height) // 2
        tile[y_offset : y_offset + resized_height, x_offset : x_offset + resized_width] = resized
        for box in sample.boxes:
            x0 = round((box.x - box.width / 2) * resized_width) + x_offset
            x1 = round((box.x + box.width / 2) * resized_width) + x_offset
            y0 = round((box.y - box.height / 2) * resized_height) + y_offset
            y1 = round((box.y + box.height / 2) * resized_height) + y_offset
            colour = colours[box.class_id % len(colours)]
            cv2_module.rectangle(tile, (x0, y0), (x1, y1), colour, 2)
            cv2_module.putText(
                tile,
                class_map[box.class_id],
                (x0, max(header + 12, y0 - 3)),
                cv2_module.FONT_HERSHEY_SIMPLEX,
                0.42,
                colour,
                1,
                cv2_module.LINE_AA,
            )
        cv2_module.putText(
            tile,
            f"{sample.split}:{sample.stem}"[:46],
            (5, 19),
            cv2_module.FONT_HERSHEY_SIMPLEX,
            0.43,
            (230, 230, 230),
            1,
            cv2_module.LINE_AA,
        )
        row, column = divmod(index, columns)
        canvas[
            row * tile_height : (row + 1) * tile_height,
            column * tile_width : (column + 1) * tile_width,
        ] = tile
    if not cv2_module.imwrite(str(output_path), canvas):
        raise DatasetAuditError(f"failed to write montage: {output_path}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--split",
        action="append",
        nargs=3,
        metavar=("NAME", "IMAGE_DIR", "LABEL_DIR"),
        required=True,
    )
    parser.add_argument("--class-map", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--montage-size", type=int, default=100)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.montage_size <= 0 or args.montage_size > 1000:
        print("ERROR: montage-size must be between 1 and 1000")
        return 2
    try:
        specs = parse_split_specs(args.split)
        class_map = load_class_map(args.class_map.resolve())
        output_dir = args.output_dir.resolve()
        output_dir.mkdir(parents=True, exist_ok=False)
        import cv2

        report, samples = audit_dataset(specs, class_map, cv2)
        selected = select_montage_samples(samples, min(args.montage_size, len(samples)))
        members = montage_membership(selected)
        if selected:
            render_montage(selected, class_map, output_dir / "label_montage.jpg", cv2)
        report["montage"] = {
            "requested_samples": args.montage_size,
            "rendered_samples": len(selected),
            "samples": members,
            "sample_set_sha256": montage_membership_sha256(members),
            "path": "label_montage.jpg" if selected else None,
            "sha256": _sha256(output_dir / "label_montage.jpg") if selected else None,
        }
        _atomic_json(output_dir / "audit_report.json", report)
    except (DatasetAuditError, FileExistsError) as exc:
        print(f"ERROR: {exc}")
        return 2
    print(
        f"AUDIT pass={report['passed']} samples={report['totals']['valid_samples']} "
        f"errors={report['totals']['errors']} warnings={report['totals']['warnings']} "
        f"output={output_dir}"
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
