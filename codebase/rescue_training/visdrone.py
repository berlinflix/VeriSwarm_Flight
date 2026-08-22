"""Deterministic official VisDrone-DET to one-class YOLO conversion.

Only VisDrone categories 1 (``pedestrian``) and 2 (``people``) map to
``person_candidate`` class 0.  The caller supplies the official train and val
roots separately, so no split is invented or reshuffled.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import struct
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

from .artifact_io import (
    ArtifactIOError,
    atomic_create_json,
    require_external_workspace,
    require_path_within_workspace,
    sha256_file,
)
from .dataset_intake import (
    DatasetIntakeError,
    ValidatedExtractionEvidence,
    validate_dataset_extraction_manifest,
)


VISDRONE_REPORT_SCHEMA = "veriswarm.rescue.visdrone_conversion.v2"
VISDRONE_LINEAGE_VALIDATION_SCHEMA = (
    "veriswarm.rescue.visdrone_lineage_validation.v1"
)
OFFICIAL_SPLIT_COUNTS = MappingProxyType({"train": 6471, "val": 548})
OFFICIAL_SPLIT_DATASET_KEYS = MappingProxyType(
    {
        "train": "visdrone-det-2019-train",
        "val": "visdrone-det-2019-val",
    }
)
PERSON_SOURCE_CATEGORIES = frozenset({1, 2})
SUPPORTED_IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png"})


class VisDroneConversionError(ValueError):
    """The source corpus cannot be converted without ambiguity or data loss."""


@dataclass(frozen=True, slots=True)
class SplitConversionReport:
    split: str
    source_root: str
    source_dataset_root_relative_path: str
    source_lineage: Mapping[str, Any]
    images: int
    annotations: int
    source_rows: int
    mapped_person_boxes: int
    ignored_boxes: int
    clipped_person_boxes: int
    empty_person_labels: int
    label_set_sha256: str
    derived_sample_set_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "split": self.split,
            "source_root": self.source_root,
            "source_dataset_root_relative_path": self.source_dataset_root_relative_path,
            "source_lineage": dict(self.source_lineage),
            "images": self.images,
            "annotations": self.annotations,
            "source_rows": self.source_rows,
            "mapped_person_boxes": self.mapped_person_boxes,
            "ignored_boxes": self.ignored_boxes,
            "clipped_person_boxes": self.clipped_person_boxes,
            "empty_person_labels": self.empty_person_labels,
            "label_set_sha256": self.label_set_sha256,
            "derived_sample_set_sha256": self.derived_sample_set_sha256,
        }


@dataclass(frozen=True, slots=True)
class VisDroneConversionReport:
    schema: str
    output_root: str
    transfer_mode: Literal["hardlink", "copy"]
    class_map: Mapping[int, str]
    source_category_map: Mapping[int, str]
    splits: Mapping[str, SplitConversionReport]
    dataset_yaml_sha256: str
    class_map_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "output_root": self.output_root,
            "transfer_mode": self.transfer_mode,
            "class_map": {str(key): value for key, value in self.class_map.items()},
            "source_category_map": {
                str(key): value for key, value in self.source_category_map.items()
            },
            "split_policy": "official_train_val_preserved",
            "dataset_yaml_sha256": self.dataset_yaml_sha256,
            "class_map_sha256": self.class_map_sha256,
            "splits": {
                name: report.to_dict() for name, report in self.splits.items()
            },
            "totals": {
                "images": sum(item.images for item in self.splits.values()),
                "annotations": sum(item.annotations for item in self.splits.values()),
                "mapped_person_boxes": sum(
                    item.mapped_person_boxes for item in self.splits.values()
                ),
                "empty_person_labels": sum(
                    item.empty_person_labels for item in self.splits.values()
                ),
            },
        }


def render_dataset_yaml(dataset_root: str | os.PathLike[str]) -> str:
    """Render the only dataset YAML accepted by the training runner."""

    root = Path(dataset_root).resolve(strict=False).as_posix()
    return (
        f"path: {json.dumps(root, ensure_ascii=False)}\n"
        "train: images/train\n"
        "val: images/val\n"
        "names:\n"
        "  0: person_candidate\n"
    )


def _write_exclusive_bytes(path: Path, data: bytes) -> None:
    try:
        with path.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError as error:
        raise VisDroneConversionError(f"refusing to overwrite output file: {path}") from error
    except OSError as error:
        raise VisDroneConversionError(f"cannot write output file {path}: {error}") from error


def read_image_dimensions(path: str | os.PathLike[str]) -> tuple[int, int]:
    """Read PNG/JPEG dimensions using only the Python standard library."""

    source = Path(path)
    if source.is_symlink() or not source.is_file():
        raise VisDroneConversionError(f"image must be one regular file: {source}")
    try:
        with source.open("rb") as stream:
            signature = stream.read(24)
            if signature.startswith(b"\x89PNG\r\n\x1a\n"):
                if len(signature) < 24 or signature[12:16] != b"IHDR":
                    raise VisDroneConversionError(f"invalid PNG header: {source}")
                width, height = struct.unpack(">II", signature[16:24])
                return _validated_dimensions(width, height, source)

            if not signature.startswith(b"\xff\xd8"):
                raise VisDroneConversionError(
                    f"unsupported image encoding (expected JPEG/PNG): {source}"
                )
            stream.seek(2)
            start_of_frame = {
                0xC0,
                0xC1,
                0xC2,
                0xC3,
                0xC5,
                0xC6,
                0xC7,
                0xC9,
                0xCA,
                0xCB,
                0xCD,
                0xCE,
                0xCF,
            }
            while True:
                prefix = stream.read(1)
                if not prefix:
                    break
                if prefix != b"\xff":
                    continue
                marker_byte = stream.read(1)
                while marker_byte == b"\xff":
                    marker_byte = stream.read(1)
                if not marker_byte:
                    break
                marker = marker_byte[0]
                if marker in {0x00, 0x01} or 0xD0 <= marker <= 0xD9:
                    continue
                length_bytes = stream.read(2)
                if len(length_bytes) != 2:
                    break
                segment_length = struct.unpack(">H", length_bytes)[0]
                if segment_length < 2:
                    raise VisDroneConversionError(
                        f"invalid JPEG segment length: {source}"
                    )
                if marker in start_of_frame:
                    frame_header = stream.read(5)
                    if segment_length < 7 or len(frame_header) != 5:
                        raise VisDroneConversionError(
                            f"truncated JPEG frame header: {source}"
                        )
                    height, width = struct.unpack(">HH", frame_header[1:5])
                    return _validated_dimensions(width, height, source)
                if marker == 0xDA:  # Start-of-scan before a valid SOF is malformed.
                    break
                stream.seek(segment_length - 2, os.SEEK_CUR)
    except VisDroneConversionError:
        raise
    except OSError as error:
        raise VisDroneConversionError(f"cannot read image {source}: {error}") from error
    raise VisDroneConversionError(f"JPEG dimensions were not found: {source}")


def _validated_dimensions(width: Any, height: Any, source: Path) -> tuple[int, int]:
    if type(width) is not int or type(height) is not int or width <= 0 or height <= 0:
        raise VisDroneConversionError(f"invalid image dimensions for {source}")
    return width, height


def _index_regular_files(
    directory: Path, *, suffixes: frozenset[str], kind: str
) -> dict[str, Path]:
    if directory.is_symlink() or not directory.is_dir():
        raise VisDroneConversionError(f"{kind} directory is missing or unsafe: {directory}")
    try:
        entries = sorted(directory.iterdir(), key=lambda item: (item.name.casefold(), item.name))
    except OSError as error:
        raise VisDroneConversionError(f"cannot list {directory}: {error}") from error

    indexed: dict[str, Path] = {}
    for entry in entries:
        if entry.is_symlink() or not entry.is_file():
            raise VisDroneConversionError(f"unexpected non-regular {kind} entry: {entry}")
        if entry.suffix.lower() not in suffixes:
            raise VisDroneConversionError(f"unexpected {kind} file extension: {entry}")
        key = entry.stem.casefold()
        if not key:
            raise VisDroneConversionError(f"empty {kind} filename stem: {entry}")
        if key in indexed:
            raise VisDroneConversionError(
                f"duplicate case-insensitive {kind} stem: {indexed[key].name!r}, {entry.name!r}"
            )
        indexed[key] = entry
    return indexed


def _validated_reader_result(
    result: Sequence[int], image_path: Path
) -> tuple[int, int]:
    if (
        not isinstance(result, Sequence)
        or isinstance(result, (str, bytes, bytearray))
        or len(result) != 2
    ):
        raise VisDroneConversionError(
            f"dimension reader must return (width, height) for {image_path}"
        )
    width, height = result
    return _validated_dimensions(width, height, image_path)


def _parse_annotation(
    annotation_path: Path, *, image_width: int, image_height: int
) -> tuple[list[str], int, int, int]:
    """Return YOLO lines, source-row count, ignored count, and clipped count."""

    try:
        text = annotation_path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as error:
        raise VisDroneConversionError(
            f"cannot read annotation {annotation_path}: {error}"
        ) from error

    output: list[str] = []
    source_rows = 0
    ignored_boxes = 0
    clipped_boxes = 0
    for line_number, original_line in enumerate(text.splitlines(), start=1):
        line = original_line.strip()
        if not line:
            raise VisDroneConversionError(
                f"blank annotation row at {annotation_path}:{line_number}"
            )
        fields = [field.strip() for field in line.split(",")]
        # Official VisDrone rows sometimes carry one trailing comma, which
        # yields an empty ninth field.  Drop exactly that empty trailing field
        # so the published corpus parses; any other field count still fails.
        if len(fields) == 9 and fields[8] == "":
            del fields[8]
        if len(fields) != 8:
            raise VisDroneConversionError(
                f"expected 8 VisDrone fields at {annotation_path}:{line_number}"
            )
        try:
            left, top, box_width, box_height, score, category, truncation, occlusion = (
                int(field, 10) for field in fields
            )
        except ValueError as error:
            raise VisDroneConversionError(
                f"non-integer VisDrone row at {annotation_path}:{line_number}"
            ) from error
        source_rows += 1
        if score not in {0, 1}:
            raise VisDroneConversionError(
                f"invalid score at {annotation_path}:{line_number}"
            )
        if category < 0 or category > 11:
            raise VisDroneConversionError(
                f"invalid category at {annotation_path}:{line_number}"
            )
        if truncation not in {0, 1, 2} or occlusion not in {0, 1, 2}:
            raise VisDroneConversionError(
                f"invalid truncation/occlusion at {annotation_path}:{line_number}"
            )
        if category not in PERSON_SOURCE_CATEGORIES:
            ignored_boxes += 1
            continue
        # Official VisDrone ships a small number of zero-area rows in discarded
        # categories (ignored regions and vehicles).  Geometry is therefore
        # enforced only for rows this converter actually maps, so a degenerate
        # non-person row cannot abort the whole official corpus.  Mapped person
        # rows keep this check plus the stricter visible-area check below.
        if box_width <= 0 or box_height <= 0:
            raise VisDroneConversionError(
                f"non-positive box at {annotation_path}:{line_number}"
            )
        if score != 1:
            raise VisDroneConversionError(
                f"mapped person row must have score 1 at {annotation_path}:{line_number}"
            )

        raw_x2 = left + box_width
        raw_y2 = top + box_height
        x1 = max(0, min(image_width, left))
        y1 = max(0, min(image_height, top))
        x2 = max(0, min(image_width, raw_x2))
        y2 = max(0, min(image_height, raw_y2))
        if x2 <= x1 or y2 <= y1:
            raise VisDroneConversionError(
                f"mapped person box has no visible area at {annotation_path}:{line_number}"
            )
        if (x1, y1, x2, y2) != (left, top, raw_x2, raw_y2):
            clipped_boxes += 1

        center_x = ((x1 + x2) / 2.0) / image_width
        center_y = ((y1 + y2) / 2.0) / image_height
        normalized_width = (x2 - x1) / image_width
        normalized_height = (y2 - y1) / image_height
        values = (center_x, center_y, normalized_width, normalized_height)
        if not all(0.0 <= value <= 1.0 for value in values):
            raise VisDroneConversionError(
                f"internal normalized-box error at {annotation_path}:{line_number}"
            )
        output.append("0 " + " ".join(f"{value:.10f}" for value in values))
    return output, source_rows, ignored_boxes, clipped_boxes


def _transfer_image(source: Path, destination: Path, mode: str) -> None:
    if os.path.lexists(destination):
        raise VisDroneConversionError(f"refusing to overwrite output image: {destination}")
    try:
        if mode == "hardlink":
            os.link(source, destination)
        elif mode == "copy":
            shutil.copy2(source, destination)
        else:  # The public entry point validates this, retained for local safety.
            raise VisDroneConversionError("transfer_mode must be 'hardlink' or 'copy'")
    except VisDroneConversionError:
        raise
    except OSError as error:
        raise VisDroneConversionError(
            f"{mode} failed from {source} to {destination}: {error}"
        ) from error


def _convert_split(
    split: str,
    source_root: Path,
    output_root: Path,
    *,
    source_dataset_root_relative_path: str,
    source_lineage: Mapping[str, Any],
    expected_count: int,
    transfer_mode: str,
    image_size_reader: Callable[[Path], Sequence[int]],
) -> SplitConversionReport:
    image_index = _index_regular_files(
        source_root / "images", suffixes=SUPPORTED_IMAGE_SUFFIXES, kind="image"
    )
    annotation_index = _index_regular_files(
        source_root / "annotations", suffixes=frozenset({".txt"}), kind="annotation"
    )
    if len(image_index) != expected_count:
        raise VisDroneConversionError(
            f"{split} image count mismatch: expected {expected_count}, got {len(image_index)}"
        )
    if len(annotation_index) != expected_count:
        raise VisDroneConversionError(
            f"{split} annotation count mismatch: expected {expected_count}, "
            f"got {len(annotation_index)}"
        )
    if image_index.keys() != annotation_index.keys():
        missing = sorted(image_index.keys() - annotation_index.keys())[:10]
        extra = sorted(annotation_index.keys() - image_index.keys())[:10]
        raise VisDroneConversionError(
            f"{split} image/annotation stem mismatch; missing={missing}, extra={extra}"
        )

    images_output = output_root / "images" / split
    labels_output = output_root / "labels" / split
    images_output.mkdir(parents=True, exist_ok=False)
    labels_output.mkdir(parents=True, exist_ok=False)

    source_rows = 0
    mapped_boxes = 0
    ignored_boxes = 0
    clipped_boxes = 0
    empty_labels = 0
    label_digest = hashlib.sha256()
    sample_digest = hashlib.sha256()
    ordered_stems = sorted(
        image_index,
        key=lambda key: (image_index[key].name.casefold(), image_index[key].name),
    )
    for stem_key in ordered_stems:
        image_path = image_index[stem_key]
        annotation_path = annotation_index[stem_key]
        try:
            dimensions = image_size_reader(image_path)
        except VisDroneConversionError:
            raise
        except Exception as error:
            raise VisDroneConversionError(
                f"dimension reader failed for {image_path}: {error}"
            ) from error
        image_width, image_height = _validated_reader_result(dimensions, image_path)
        lines, row_count, ignored_count, clipped_count = _parse_annotation(
            annotation_path,
            image_width=image_width,
            image_height=image_height,
        )
        source_rows += row_count
        mapped_boxes += len(lines)
        ignored_boxes += ignored_count
        clipped_boxes += clipped_count
        if not lines:
            empty_labels += 1

        _transfer_image(image_path, images_output / image_path.name, transfer_mode)
        output_image = images_output / image_path.name
        source_image_sha = sha256_file(image_path)
        if sha256_file(output_image) != source_image_sha:
            raise VisDroneConversionError(
                f"transferred image hash mismatch: {output_image}"
            )
        label_name = f"{image_path.stem}.txt"
        label_path = labels_output / label_name
        label_bytes = (("\n".join(lines) + "\n") if lines else "").encode("utf-8")
        try:
            with label_path.open("xb") as stream:
                stream.write(label_bytes)
        except FileExistsError as error:
            raise VisDroneConversionError(
                f"refusing to overwrite output label: {label_path}"
            ) from error
        except OSError as error:
            raise VisDroneConversionError(f"cannot write label {label_path}: {error}") from error
        label_digest.update(label_name.encode("utf-8"))
        label_digest.update(b"\x00")
        label_digest.update(label_bytes)
        label_digest.update(b"\x00")
        label_sha = hashlib.sha256(label_bytes).hexdigest()
        sample_digest.update(image_path.name.encode("utf-8"))
        sample_digest.update(b"\x00")
        sample_digest.update(source_image_sha.encode("ascii"))
        sample_digest.update(b"\x00")
        sample_digest.update(label_sha.encode("ascii"))
        sample_digest.update(b"\x00")

    return SplitConversionReport(
        split=split,
        source_root=str(source_root),
        source_dataset_root_relative_path=source_dataset_root_relative_path,
        source_lineage=source_lineage,
        images=len(image_index),
        annotations=len(annotation_index),
        source_rows=source_rows,
        mapped_person_boxes=mapped_boxes,
        ignored_boxes=ignored_boxes,
        clipped_person_boxes=clipped_boxes,
        empty_person_labels=empty_labels,
        label_set_sha256=label_digest.hexdigest(),
        derived_sample_set_sha256=sample_digest.hexdigest(),
    )


def _source_root_from_extraction(
    evidence: ValidatedExtractionEvidence,
    *,
    split: str,
    expected_count: int,
) -> tuple[Path, str]:
    """Locate the one VisDrone dataset root inside a verified archive tree."""

    candidates: list[Path] = []
    try:
        for root, directory_names, _file_names in os.walk(
            evidence.extracted_root, followlinks=False
        ):
            root_path = Path(root)
            if root_path.is_symlink():
                raise VisDroneConversionError(
                    f"{split} extracted tree contains a symbolic link: {root_path}"
                )
            names = set(directory_names)
            if "images" in names and "annotations" in names:
                candidate = root_path
                try:
                    images = _index_regular_files(
                        candidate / "images",
                        suffixes=SUPPORTED_IMAGE_SUFFIXES,
                        kind="image",
                    )
                    annotations = _index_regular_files(
                        candidate / "annotations",
                        suffixes=frozenset({".txt"}),
                        kind="annotation",
                    )
                except VisDroneConversionError:
                    continue
                if len(images) == expected_count and len(annotations) == expected_count:
                    candidates.append(candidate.resolve(strict=False))
    except OSError as error:
        raise VisDroneConversionError(
            f"cannot inspect {split} verified extraction: {error}"
        ) from error
    unique = sorted(set(candidates), key=lambda path: path.as_posix().encode("utf-8"))
    if len(unique) != 1:
        raise VisDroneConversionError(
            f"{split} extraction must contain exactly one {expected_count}-image "
            f"VisDrone images/annotations root; found {len(unique)}"
        )
    source_root = unique[0]
    relative = source_root.relative_to(evidence.extracted_root).as_posix()
    if relative == ".":
        relative = ""
    return source_root, relative


def convert_visdrone_det(
    split_extraction_manifests: Mapping[str, str | os.PathLike[str]],
    output_root: str | os.PathLike[str],
    *,
    workspace_root: str | os.PathLike[str],
    repository_root: str | os.PathLike[str],
    transfer_mode: Literal["hardlink", "copy"] = "copy",
    expected_counts: Mapping[str, int] = OFFICIAL_SPLIT_COUNTS,
    image_size_reader: Callable[[Path], Sequence[int]] = read_image_dimensions,
) -> VisDroneConversionReport:
    """Convert only registered/extracted official train/val archives.

    ``output_root`` must not exist.  On success the last created file is
    ``conversion_report.json``; its presence marks a complete conversion.  A
    failed conversion removes only the output directory created by this call.
    No hardlink-to-copy fallback occurs because transfer provenance must be
    explicit.
    """

    if (
        not isinstance(split_extraction_manifests, Mapping)
        or set(split_extraction_manifests) != {"train", "val"}
    ):
        raise VisDroneConversionError(
            "split_extraction_manifests must contain exactly train and val"
        )
    if not isinstance(expected_counts, Mapping) or set(expected_counts) != {"train", "val"}:
        raise VisDroneConversionError("expected_counts must contain exactly train and val")
    counts: dict[str, int] = {}
    for split in ("train", "val"):
        count = expected_counts[split]
        if type(count) is not int or count <= 0:
            raise VisDroneConversionError(f"expected_counts[{split!r}] must be positive")
        counts[split] = count
    if transfer_mode not in {"hardlink", "copy"}:
        raise VisDroneConversionError("transfer_mode must be explicitly 'hardlink' or 'copy'")
    if not callable(image_size_reader):
        raise VisDroneConversionError("image_size_reader must be callable")

    try:
        workspace = require_external_workspace(workspace_root, repository_root)
        destination = require_path_within_workspace(output_root, workspace)
    except ArtifactIOError as error:
        raise VisDroneConversionError(str(error)) from error
    if os.path.lexists(destination):
        raise VisDroneConversionError(f"refusing to overwrite output root: {destination}")

    sources: dict[str, Path] = {}
    source_relatives: dict[str, str] = {}
    source_evidence: dict[str, ValidatedExtractionEvidence] = {}
    for split in ("train", "val"):
        try:
            evidence = validate_dataset_extraction_manifest(
                split_extraction_manifests[split],
                workspace_root=workspace,
                repository_root=repository_root,
                expected_dataset_key=OFFICIAL_SPLIT_DATASET_KEYS[split],
            )
        except DatasetIntakeError as error:
            raise VisDroneConversionError(
                f"{split} extraction lineage is invalid: {error}"
            ) from error
        source, relative = _source_root_from_extraction(
            evidence, split=split, expected_count=counts[split]
        )
        if (
            source == destination
            or source in destination.parents
            or destination in source.parents
        ):
            raise VisDroneConversionError(
                f"{split} source and output roots must not overlap"
            )
        sources[split] = source
        source_relatives[split] = relative
        source_evidence[split] = evidence
    if sources["train"] == sources["val"]:
        raise VisDroneConversionError("train and val source roots must be different")
    if (
        source_evidence["train"].manifest_path
        == source_evidence["val"].manifest_path
        or source_evidence["train"].archive_sha256
        == source_evidence["val"].archive_sha256
    ):
        raise VisDroneConversionError(
            "train and val must come from distinct registered official archives"
        )

    created_output = False
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.mkdir(exist_ok=False)
        created_output = True
        split_reports: dict[str, SplitConversionReport] = {}
        for split in ("train", "val"):
            split_reports[split] = _convert_split(
                split,
                sources[split],
                destination,
                source_dataset_root_relative_path=source_relatives[split],
                source_lineage=source_evidence[split].to_dict(),
                expected_count=counts[split],
                transfer_mode=transfer_mode,
                image_size_reader=image_size_reader,
            )
        for split in ("train", "val"):
            try:
                post = validate_dataset_extraction_manifest(
                    source_evidence[split].manifest_path,
                    workspace_root=workspace,
                    repository_root=repository_root,
                    expected_dataset_key=OFFICIAL_SPLIT_DATASET_KEYS[split],
                )
            except DatasetIntakeError as error:
                raise VisDroneConversionError(
                    f"{split} extraction lineage changed during conversion: {error}"
                ) from error
            if post.to_dict() != source_evidence[split].to_dict():
                raise VisDroneConversionError(
                    f"{split} extraction lineage changed during conversion"
                )
        class_map_path = destination / "class_map.json"
        class_map_bytes = b'{\n  "0": "person_candidate"\n}\n'
        _write_exclusive_bytes(class_map_path, class_map_bytes)
        dataset_yaml_path = destination / "dataset.yaml"
        dataset_yaml_bytes = render_dataset_yaml(destination).encode("utf-8")
        _write_exclusive_bytes(dataset_yaml_path, dataset_yaml_bytes)
        report = VisDroneConversionReport(
            schema=VISDRONE_REPORT_SCHEMA,
            output_root=str(destination),
            transfer_mode=transfer_mode,
            class_map=MappingProxyType({0: "person_candidate"}),
            source_category_map=MappingProxyType({1: "pedestrian", 2: "people"}),
            splits=MappingProxyType(split_reports),
            dataset_yaml_sha256=hashlib.sha256(dataset_yaml_bytes).hexdigest(),
            class_map_sha256=hashlib.sha256(class_map_bytes).hexdigest(),
        )
        atomic_create_json(destination / "conversion_report.json", report.to_dict())
        return report
    except VisDroneConversionError:
        if created_output:
            shutil.rmtree(destination, ignore_errors=True)
        raise
    except (ArtifactIOError, FileExistsError, OSError) as error:
        if created_output:
            shutil.rmtree(destination, ignore_errors=True)
        raise VisDroneConversionError(f"conversion failed: {error}") from error


def _strict_report_object(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise VisDroneConversionError(
            f"conversion report must be one regular non-link file: {path}"
        )

    def reject_constant(value: str) -> None:
        raise VisDroneConversionError(
            f"non-finite JSON constant is forbidden: {value}"
        )

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise VisDroneConversionError(
                    f"duplicate conversion-report key is forbidden: {key!r}"
                )
            result[key] = value
        return result

    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=reject_constant,
            object_pairs_hook=unique,
        )
    except VisDroneConversionError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise VisDroneConversionError(
            f"cannot parse conversion report {path}: {error}"
        ) from error
    if type(value) is not dict:
        raise VisDroneConversionError("conversion report must contain one JSON object")
    return value


def _exact_report_fields(value: Any, expected: set[str], field: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != expected:
        actual = set(value) if type(value) is dict else set()
        raise VisDroneConversionError(
            f"{field} fields differ from the frozen schema; "
            f"missing={sorted(expected - actual)}, unknown={sorted(actual - expected)}"
        )
    return value


def _report_sha256(value: Any, field: str) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or value != value.lower()
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise VisDroneConversionError(f"{field} must be one lowercase SHA-256")
    return value


def _validate_converted_split(
    *,
    split: str,
    source_root: Path,
    output_root: Path,
    expected_count: int,
    image_size_reader: Callable[[Path], Sequence[int]],
) -> dict[str, Any]:
    source_images = _index_regular_files(
        source_root / "images", suffixes=SUPPORTED_IMAGE_SUFFIXES, kind="source image"
    )
    source_annotations = _index_regular_files(
        source_root / "annotations",
        suffixes=frozenset({".txt"}),
        kind="source annotation",
    )
    output_images = _index_regular_files(
        output_root / "images" / split,
        suffixes=SUPPORTED_IMAGE_SUFFIXES,
        kind="derived image",
    )
    output_labels = _index_regular_files(
        output_root / "labels" / split,
        suffixes=frozenset({".txt"}),
        kind="derived label",
    )
    for name, index in (
        ("source images", source_images),
        ("source annotations", source_annotations),
        ("derived images", output_images),
        ("derived labels", output_labels),
    ):
        if len(index) != expected_count:
            raise VisDroneConversionError(
                f"{split} {name} count mismatch: expected {expected_count}, got {len(index)}"
            )
    if not (
        source_images.keys()
        == source_annotations.keys()
        == output_images.keys()
        == output_labels.keys()
    ):
        raise VisDroneConversionError(
            f"{split} source and derived image/annotation/label stems differ"
        )

    source_rows = 0
    mapped_boxes = 0
    ignored_boxes = 0
    clipped_boxes = 0
    empty_labels = 0
    label_digest = hashlib.sha256()
    sample_digest = hashlib.sha256()
    ordered_stems = sorted(
        source_images,
        key=lambda key: (
            source_images[key].name.casefold(),
            source_images[key].name,
        ),
    )
    for stem in ordered_stems:
        source_image = source_images[stem]
        output_image = output_images[stem]
        if output_image.name != source_image.name:
            raise VisDroneConversionError(
                f"{split} derived image filename changed: {source_image.name!r}"
            )
        source_image_sha = sha256_file(source_image)
        if sha256_file(output_image) != source_image_sha:
            raise VisDroneConversionError(
                f"{split} derived image bytes differ from registered source: {source_image.name}"
            )
        try:
            dimensions = image_size_reader(source_image)
        except VisDroneConversionError:
            raise
        except Exception as error:
            raise VisDroneConversionError(
                f"dimension reader failed for {source_image}: {error}"
            ) from error
        width, height = _validated_reader_result(dimensions, source_image)
        lines, row_count, ignored_count, clipped_count = _parse_annotation(
            source_annotations[stem], image_width=width, image_height=height
        )
        expected_label = (("\n".join(lines) + "\n") if lines else "").encode("utf-8")
        try:
            actual_label = output_labels[stem].read_bytes()
        except OSError as error:
            raise VisDroneConversionError(
                f"cannot read derived label {output_labels[stem]}: {error}"
            ) from error
        if actual_label != expected_label:
            raise VisDroneConversionError(
                f"{split} derived label differs from source annotation: {output_labels[stem].name}"
            )
        source_rows += row_count
        mapped_boxes += len(lines)
        ignored_boxes += ignored_count
        clipped_boxes += clipped_count
        if not lines:
            empty_labels += 1
        label_digest.update(output_labels[stem].name.encode("utf-8"))
        label_digest.update(b"\x00")
        label_digest.update(actual_label)
        label_digest.update(b"\x00")
        sample_digest.update(source_image.name.encode("utf-8"))
        sample_digest.update(b"\x00")
        sample_digest.update(source_image_sha.encode("ascii"))
        sample_digest.update(b"\x00")
        sample_digest.update(hashlib.sha256(actual_label).hexdigest().encode("ascii"))
        sample_digest.update(b"\x00")
    return {
        "images": len(source_images),
        "annotations": len(source_annotations),
        "source_rows": source_rows,
        "mapped_person_boxes": mapped_boxes,
        "ignored_boxes": ignored_boxes,
        "clipped_person_boxes": clipped_boxes,
        "empty_person_labels": empty_labels,
        "label_set_sha256": label_digest.hexdigest(),
        "derived_sample_set_sha256": sample_digest.hexdigest(),
    }


def validate_visdrone_conversion_report(
    report_path: str | os.PathLike[str],
    *,
    workspace_root: str | os.PathLike[str],
    repository_root: str | os.PathLike[str],
    expected_counts: Mapping[str, int] = OFFICIAL_SPLIT_COUNTS,
    image_size_reader: Callable[[Path], Sequence[int]] = read_image_dimensions,
) -> dict[str, Any]:
    """Recursively prove derived YOLO bytes came from frozen VisDrone ZIPs."""

    if not isinstance(expected_counts, Mapping) or set(expected_counts) != {"train", "val"}:
        raise VisDroneConversionError("expected_counts must contain exactly train and val")
    counts: dict[str, int] = {}
    for split in ("train", "val"):
        value = expected_counts[split]
        if type(value) is not int or value <= 0:
            raise VisDroneConversionError(f"expected_counts[{split!r}] must be positive")
        counts[split] = value
    if not callable(image_size_reader):
        raise VisDroneConversionError("image_size_reader must be callable")
    try:
        workspace = require_external_workspace(workspace_root, repository_root)
        report = require_path_within_workspace(report_path, workspace)
        report_hash_before = sha256_file(report)
    except ArtifactIOError as error:
        raise VisDroneConversionError(str(error)) from error
    payload = _strict_report_object(report)
    _exact_report_fields(
        payload,
        {
            "schema",
            "output_root",
            "transfer_mode",
            "class_map",
            "source_category_map",
            "split_policy",
            "dataset_yaml_sha256",
            "class_map_sha256",
            "splits",
            "totals",
        },
        "conversion report",
    )
    if payload["schema"] != VISDRONE_REPORT_SCHEMA:
        raise VisDroneConversionError("unsupported VisDrone conversion schema")
    output_value = payload["output_root"]
    if type(output_value) is not str or not Path(output_value).is_absolute():
        raise VisDroneConversionError("conversion output_root must be absolute")
    try:
        output_root = require_path_within_workspace(output_value, workspace)
    except ArtifactIOError as error:
        raise VisDroneConversionError(str(error)) from error
    if output_root.is_symlink() or not output_root.is_dir():
        raise VisDroneConversionError("conversion output_root is missing or unsafe")
    if report != output_root / "conversion_report.json":
        raise VisDroneConversionError("conversion report is outside its output root")
    if payload["transfer_mode"] not in {"copy", "hardlink"}:
        raise VisDroneConversionError("conversion transfer mode is unsupported")
    if payload["class_map"] != {"0": "person_candidate"}:
        raise VisDroneConversionError("conversion class_map is not frozen")
    if payload["source_category_map"] != {"1": "pedestrian", "2": "people"}:
        raise VisDroneConversionError("conversion source category map is not frozen")
    if payload["split_policy"] != "official_train_val_preserved":
        raise VisDroneConversionError("conversion split policy is not frozen")

    class_map_path = output_root / "class_map.json"
    dataset_yaml_path = output_root / "dataset.yaml"
    expected_class_map = b'{\n  "0": "person_candidate"\n}\n'
    expected_yaml = render_dataset_yaml(output_root).encode("utf-8")
    try:
        if class_map_path.read_bytes() != expected_class_map:
            raise VisDroneConversionError("derived class_map.json is not canonical")
        if dataset_yaml_path.read_bytes() != expected_yaml:
            raise VisDroneConversionError("derived dataset.yaml is not canonical")
    except OSError as error:
        raise VisDroneConversionError(f"cannot read derived metadata: {error}") from error
    class_map_sha = sha256_file(class_map_path)
    yaml_sha = sha256_file(dataset_yaml_path)
    if _report_sha256(payload["class_map_sha256"], "class_map_sha256") != class_map_sha:
        raise VisDroneConversionError("derived class-map hash mismatch")
    if _report_sha256(payload["dataset_yaml_sha256"], "dataset_yaml_sha256") != yaml_sha:
        raise VisDroneConversionError("derived dataset-YAML hash mismatch")

    claimed_splits = _exact_report_fields(
        payload["splits"], {"train", "val"}, "conversion splits"
    )
    verified_lineage: dict[str, Any] = {}
    verified_split_hashes: dict[str, str] = {}
    normalized_split_reports: dict[str, dict[str, Any]] = {}
    seen_manifests: set[Path] = set()
    seen_archive_hashes: set[str] = set()
    for split in ("train", "val"):
        claimed = _exact_report_fields(
            claimed_splits[split],
            {
                "split",
                "source_root",
                "source_dataset_root_relative_path",
                "source_lineage",
                "images",
                "annotations",
                "source_rows",
                "mapped_person_boxes",
                "ignored_boxes",
                "clipped_person_boxes",
                "empty_person_labels",
                "label_set_sha256",
                "derived_sample_set_sha256",
            },
            f"conversion splits.{split}",
        )
        lineage_claim = claimed["source_lineage"]
        if type(lineage_claim) is not dict:
            raise VisDroneConversionError(f"{split} source_lineage must be an object")
        manifest_claim = lineage_claim.get("manifest")
        if type(manifest_claim) is not dict or type(manifest_claim.get("path")) is not str:
            raise VisDroneConversionError(f"{split} extraction manifest path is missing")
        try:
            evidence = validate_dataset_extraction_manifest(
                manifest_claim["path"],
                workspace_root=workspace,
                repository_root=repository_root,
                expected_dataset_key=OFFICIAL_SPLIT_DATASET_KEYS[split],
            )
        except DatasetIntakeError as error:
            raise VisDroneConversionError(
                f"{split} source lineage is invalid: {error}"
            ) from error
        expected_lineage = evidence.to_dict()
        if lineage_claim != expected_lineage:
            raise VisDroneConversionError(
                f"{split} source lineage differs from recursively verified evidence"
            )
        if evidence.manifest_path in seen_manifests or evidence.archive_sha256 in seen_archive_hashes:
            raise VisDroneConversionError(
                "train and val conversion lineage must use distinct archives"
            )
        seen_manifests.add(evidence.manifest_path)
        seen_archive_hashes.add(evidence.archive_sha256)
        source_root, relative = _source_root_from_extraction(
            evidence, split=split, expected_count=counts[split]
        )
        if (
            claimed["split"] != split
            or claimed["source_root"] != str(source_root)
            or claimed["source_dataset_root_relative_path"] != relative
        ):
            raise VisDroneConversionError(
                f"{split} report names a source outside its verified extraction"
            )
        computed = _validate_converted_split(
            split=split,
            source_root=source_root,
            output_root=output_root,
            expected_count=counts[split],
            image_size_reader=image_size_reader,
        )
        try:
            post_evidence = validate_dataset_extraction_manifest(
                evidence.manifest_path,
                workspace_root=workspace,
                repository_root=repository_root,
                expected_dataset_key=OFFICIAL_SPLIT_DATASET_KEYS[split],
            )
        except DatasetIntakeError as error:
            raise VisDroneConversionError(
                f"{split} source lineage changed during validation: {error}"
            ) from error
        if post_evidence.to_dict() != expected_lineage:
            raise VisDroneConversionError(
                f"{split} source lineage changed during validation"
            )
        expected_split = {
            "split": split,
            "source_root": str(source_root),
            "source_dataset_root_relative_path": relative,
            "source_lineage": expected_lineage,
            **computed,
        }
        if claimed != expected_split:
            raise VisDroneConversionError(
                f"{split} conversion report differs from rederived source/output bytes"
            )
        normalized_split_reports[split] = expected_split
        verified_lineage[split] = expected_lineage
        verified_split_hashes[split] = computed["derived_sample_set_sha256"]

    totals = _exact_report_fields(
        payload["totals"],
        {"images", "annotations", "mapped_person_boxes", "empty_person_labels"},
        "conversion totals",
    )
    expected_totals = {
        "images": sum(item["images"] for item in normalized_split_reports.values()),
        "annotations": sum(
            item["annotations"] for item in normalized_split_reports.values()
        ),
        "mapped_person_boxes": sum(
            item["mapped_person_boxes"] for item in normalized_split_reports.values()
        ),
        "empty_person_labels": sum(
            item["empty_person_labels"] for item in normalized_split_reports.values()
        ),
    }
    if totals != expected_totals:
        raise VisDroneConversionError("conversion totals differ from rederived splits")
    try:
        report_hash_after = sha256_file(report)
    except ArtifactIOError as error:
        raise VisDroneConversionError(str(error)) from error
    if report_hash_after != report_hash_before:
        raise VisDroneConversionError("conversion report changed during validation")
    return {
        "schema": VISDRONE_LINEAGE_VALIDATION_SCHEMA,
        "conversion_report": {
            "path": str(report),
            "sha256": report_hash_after,
            "schema": VISDRONE_REPORT_SCHEMA,
        },
        "output_root": str(output_root),
        "dataset_yaml": {"path": str(dataset_yaml_path), "sha256": yaml_sha},
        "class_map": {"0": "person_candidate"},
        "source_lineage": verified_lineage,
        "derived_sample_set_sha256": verified_split_hashes,
    }
