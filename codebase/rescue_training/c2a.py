"""Fail-closed C2A v2 intake and detector-refinement split preparation.

C2A's published train/validation/test folders split camera views rather than
necessarily splitting the underlying synthetic scene.  A scene is identified
by removing the final ``_0`` through ``_4`` camera-view suffix from an image
stem.  The publisher test set is therefore *not* treated as untouched when a
scene (or exact image bytes) also occurs in train or validation.

This module audits an already extracted C2A v2 tree and creates a deterministic
group-safe YOLO dataset outside Git.  It never reads the separate pose-hint
labels and never turns either pose or dataset origin into a detector/state
class.  The only detector class remains ``person_candidate``.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
from collections import defaultdict
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


C2A_DATASET_ID = "c2a-v2"
C2A_INVENTORY_RECORD_SCHEMA = "veriswarm.rescue.c2a_inventory_record.v1"
C2A_PREPARATION_REPORT_SCHEMA = "veriswarm.rescue.c2a_preparation.v1"
C2A_ARTIFACT_HASHES_SCHEMA = "veriswarm.rescue.c2a_artifact_hashes.v1"
OFFICIAL_SPLIT_COUNTS = MappingProxyType(
    {"train": 6129, "val": 2043, "test": 2043}
)
SPLITS = ("train", "val", "test")
SUPPORTED_IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png"})
POSE_HINT_DIRECTORY = "All labels with Pose information"
FROZEN_GROUP_SPLIT_SEED = 20260822
FROZEN_GROUP_SPLIT_RATIOS = MappingProxyType(
    {"train": 0.60, "val": 0.20, "test": 0.20}
)
_VIEW_SUFFIX_RE = re.compile(r"^(?P<group>.+)_(?P<view>[0-4])$")


class C2ADataError(ValueError):
    """C2A source or derived data failed a qualification boundary."""


@dataclass(frozen=True, slots=True)
class C2ASourceRecord:
    """One audited publisher image/label pair."""

    publisher_split: str
    stem: str
    scene_group_id: str
    scene_group_key: str
    view_index: int
    image_path: Path
    label_path: Path
    image_sha256: str
    label_sha256: str
    image_bytes: int
    label_bytes: int
    width: int
    height: int
    person_boxes: int


@dataclass(frozen=True, slots=True)
class C2AAudit:
    """Complete, immutable result of auditing the publisher tree."""

    extracted_root: Path
    dataset_root: Path
    records: tuple[C2ASourceRecord, ...]
    split_counts: Mapping[str, int]
    split_box_counts: Mapping[str, int]
    group_count: int
    publisher_group_overlaps: Mapping[str, tuple[str, ...]]
    publisher_image_hash_overlaps: Mapping[str, tuple[str, ...]]
    publisher_test_untouched: bool
    pose_hint_tree_present: bool
    excluded_pose_hint_files: int

    def publisher_qualification(self) -> dict[str, Any]:
        reasons: list[str] = []
        if self.publisher_group_overlaps:
            reasons.append("scene_group_overlap_across_publisher_splits")
        if self.publisher_image_hash_overlaps:
            reasons.append("exact_image_bytes_overlap_across_publisher_splits")
        return {
            "qualified": self.publisher_test_untouched,
            "test_untouched": self.publisher_test_untouched,
            "reasons": reasons,
            "scene_group_overlap_count": len(self.publisher_group_overlaps),
            "exact_image_hash_overlap_count": len(
                self.publisher_image_hash_overlaps
            ),
            "scene_group_overlaps": [
                {"scene_group_id": group, "publisher_splits": list(splits)}
                for group, splits in sorted(self.publisher_group_overlaps.items())
            ],
            "exact_image_hash_overlaps": [
                {"image_sha256": digest, "publisher_splits": list(splits)}
                for digest, splits in sorted(
                    self.publisher_image_hash_overlaps.items()
                )
            ],
        }


@dataclass(frozen=True, slots=True)
class C2APreparationResult:
    """Paths and hashes for one complete create-once derived dataset."""

    output_root: Path
    report_path: Path
    report_sha256: str
    inventory_path: Path
    inventory_sha256: str
    dataset_yaml_path: Path
    dataset_yaml_sha256: str
    artifact_hashes_path: Path
    artifact_hashes_sha256: str
    publisher_test_untouched: bool
    derived_split_counts: Mapping[str, int]


def _is_link_or_junction(path: Path) -> bool:
    """Treat Windows directory junctions as links at the data boundary."""

    is_junction = getattr(path, "is_junction", None)
    return path.is_symlink() or (callable(is_junction) and bool(is_junction()))


def decode_image_dimensions(path: str | os.PathLike[str]) -> tuple[int, int]:
    """Fully decode an image with Pillow and return positive dimensions.

    Pillow is installed by the frozen Ultralytics training environment.  The
    import is lazy so importing dataset contracts remains side-effect free.
    Header-only checks are deliberately insufficient for the qualification
    gate because a truncated image can still carry plausible dimensions.
    """

    source = Path(path)
    if _is_link_or_junction(source) or not source.is_file():
        raise C2ADataError(f"image must be one regular file: {source}")
    try:
        from PIL import Image, UnidentifiedImageError
    except ImportError as error:  # pragma: no cover - exercised in lean envs only.
        raise C2ADataError(
            "Pillow is required to prove C2A image decodability"
        ) from error
    try:
        with Image.open(source) as image:
            image.load()
            width, height = image.size
    except (OSError, ValueError, UnidentifiedImageError) as error:
        raise C2ADataError(f"image is not fully decodable: {source}: {error}") from error
    return _validate_dimensions((width, height), source)


def _validate_dimensions(value: Sequence[int], image_path: Path) -> tuple[int, int]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes, bytearray))
        or len(value) != 2
    ):
        raise C2ADataError(
            f"image decoder must return (width, height) for {image_path}"
        )
    width, height = value
    if (
        isinstance(width, bool)
        or isinstance(height, bool)
        or type(width) is not int
        or type(height) is not int
        or width <= 0
        or height <= 0
    ):
        raise C2ADataError(f"invalid image dimensions for {image_path}")
    return width, height


def _directory_entries(directory: Path, field: str) -> list[Path]:
    if _is_link_or_junction(directory) or not directory.is_dir():
        raise C2ADataError(f"{field} is missing or unsafe: {directory}")
    try:
        return sorted(
            directory.iterdir(), key=lambda item: (item.name.casefold(), item.name)
        )
    except OSError as error:
        raise C2ADataError(f"cannot list {field} {directory}: {error}") from error


def _require_named_directories(
    directory: Path,
    *,
    required: frozenset[str],
    optional: frozenset[str] = frozenset(),
    field: str,
) -> dict[str, Path]:
    entries = _directory_entries(directory, field)
    names = {entry.name for entry in entries}
    missing = required - names
    unknown = names - required - optional
    if missing or unknown:
        raise C2ADataError(
            f"{field} structure differs; missing={sorted(missing)}, "
            f"unknown={sorted(unknown)}"
        )
    result: dict[str, Path] = {}
    for entry in entries:
        if _is_link_or_junction(entry) or not entry.is_dir():
            raise C2ADataError(f"{field} entry must be a real directory: {entry}")
        result[entry.name] = entry
    return result


def _index_regular_files(
    directory: Path, *, suffixes: frozenset[str], field: str
) -> dict[str, Path]:
    indexed: dict[str, Path] = {}
    for entry in _directory_entries(directory, field):
        if _is_link_or_junction(entry) or not entry.is_file():
            raise C2ADataError(f"unexpected non-regular {field} entry: {entry}")
        if entry.suffix.lower() not in suffixes:
            raise C2ADataError(f"unexpected {field} extension: {entry}")
        key = entry.stem.casefold()
        if not key:
            raise C2ADataError(f"empty {field} filename stem: {entry}")
        if key in indexed:
            raise C2ADataError(
                f"duplicate case-insensitive {field} stem: "
                f"{indexed[key].name!r}, {entry.name!r}"
            )
        indexed[key] = entry
    return indexed


def _count_excluded_pose_files(directory: Path) -> int:
    """Prove the ignored pose tree contains no links or special files."""

    count = 0
    pending = [directory]
    while pending:
        current = pending.pop()
        for entry in _directory_entries(current, "excluded pose-hint tree"):
            if _is_link_or_junction(entry):
                raise C2ADataError(f"symlink in excluded pose-hint tree: {entry}")
            if entry.is_dir():
                pending.append(entry)
            elif entry.is_file():
                count += 1
            else:
                raise C2ADataError(
                    f"special file in excluded pose-hint tree: {entry}"
                )
    return count


def _parse_yolo_label(label_path: Path) -> int:
    try:
        text = label_path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as error:
        raise C2ADataError(f"cannot read YOLO label {label_path}: {error}") from error
    lines = text.splitlines()
    if not lines:
        raise C2ADataError(f"C2A person label must not be empty: {label_path}")
    for line_number, original in enumerate(lines, start=1):
        if not original.strip():
            raise C2ADataError(
                f"blank YOLO row at {label_path}:{line_number}"
            )
        fields = original.split()
        if len(fields) != 5:
            raise C2ADataError(
                f"YOLO row must have exactly five fields at "
                f"{label_path}:{line_number}"
            )
        if fields[0] != "0":
            raise C2ADataError(
                "C2A detector labels must contain only class 0 "
                f"person_candidate at {label_path}:{line_number}"
            )
        try:
            center_x, center_y, width, height = (
                float(field) for field in fields[1:]
            )
        except ValueError as error:
            raise C2ADataError(
                f"non-numeric YOLO box at {label_path}:{line_number}"
            ) from error
        values = (center_x, center_y, width, height)
        if not all(math.isfinite(value) for value in values):
            raise C2ADataError(
                f"non-finite YOLO box at {label_path}:{line_number}"
            )
        if not (0.0 <= center_x <= 1.0 and 0.0 <= center_y <= 1.0):
            raise C2ADataError(
                f"YOLO center is not normalized at {label_path}:{line_number}"
            )
        if not (0.0 < width <= 1.0 and 0.0 < height <= 1.0):
            raise C2ADataError(
                f"YOLO box size must be positive and normalized at "
                f"{label_path}:{line_number}"
            )
        tolerance = 1e-6
        if (
            center_x - width / 2.0 < -tolerance
            or center_x + width / 2.0 > 1.0 + tolerance
            or center_y - height / 2.0 < -tolerance
            or center_y + height / 2.0 > 1.0 + tolerance
        ):
            raise C2ADataError(
                f"YOLO box extends outside normalized image at "
                f"{label_path}:{line_number}"
            )
    return len(lines)


def _validated_expected_counts(expected_counts: Mapping[str, int]) -> dict[str, int]:
    if not isinstance(expected_counts, Mapping) or set(expected_counts) != set(SPLITS):
        raise C2ADataError("expected_counts must contain exactly train, val, and test")
    result: dict[str, int] = {}
    for split in SPLITS:
        count = expected_counts[split]
        if isinstance(count, bool) or type(count) is not int or count <= 0:
            raise C2ADataError(f"expected_counts[{split!r}] must be positive")
        result[split] = count
    return result


def audit_c2a_v2(
    extracted_root: str | os.PathLike[str],
    *,
    expected_counts: Mapping[str, int] = OFFICIAL_SPLIT_COUNTS,
    image_decoder: Callable[[Path], Sequence[int]] = decode_image_dimensions,
) -> C2AAudit:
    """Audit the complete official detector tree without writing artifacts.

    Leakage is evidence, not a reason to hide or rewrite publisher provenance.
    It is recorded in the returned audit and makes
    ``publisher_test_untouched`` false.  Structural, decoding, pairing, label,
    and hash ambiguity instead raises immediately.
    """

    counts = _validated_expected_counts(expected_counts)
    if not callable(image_decoder):
        raise C2ADataError("image_decoder must be callable")
    root_input = Path(extracted_root).expanduser()
    if not root_input.is_absolute():
        raise C2ADataError("extracted_root must be absolute")
    if _is_link_or_junction(root_input):
        raise C2ADataError(f"extracted_root must not be a symlink: {root_input}")
    root = root_input.resolve(strict=False)
    root_entries = _require_named_directories(
        root,
        required=frozenset({"new_dataset3"}),
        field="C2A extraction root",
    )
    dataset_root = root_entries["new_dataset3"]
    dataset_entries = _require_named_directories(
        dataset_root,
        required=frozenset(SPLITS),
        optional=frozenset({POSE_HINT_DIRECTORY}),
        field="C2A new_dataset3",
    )
    pose_path = dataset_entries.get(POSE_HINT_DIRECTORY)
    excluded_pose_files = (
        _count_excluded_pose_files(pose_path) if pose_path is not None else 0
    )

    records: list[C2ASourceRecord] = []
    seen_global_stems: dict[str, Path] = {}
    group_views: dict[str, set[int]] = defaultdict(set)
    group_names: dict[str, str] = {}
    split_box_counts: dict[str, int] = {}
    group_splits: dict[str, set[str]] = defaultdict(set)
    image_hash_splits: dict[str, set[str]] = defaultdict(set)

    for split in SPLITS:
        split_entries = _require_named_directories(
            dataset_entries[split],
            required=frozenset({"images", "labels"}),
            field=f"C2A {split} split",
        )
        images = _index_regular_files(
            split_entries["images"],
            suffixes=SUPPORTED_IMAGE_SUFFIXES,
            field=f"{split} image",
        )
        labels = _index_regular_files(
            split_entries["labels"],
            suffixes=frozenset({".txt"}),
            field=f"{split} label",
        )
        if len(images) != counts[split]:
            raise C2ADataError(
                f"{split} image count mismatch: expected {counts[split]}, "
                f"got {len(images)}"
            )
        if len(labels) != counts[split]:
            raise C2ADataError(
                f"{split} label count mismatch: expected {counts[split]}, "
                f"got {len(labels)}"
            )
        if images.keys() != labels.keys():
            missing = sorted(images.keys() - labels.keys())[:10]
            extra = sorted(labels.keys() - images.keys())[:10]
            raise C2ADataError(
                f"{split} image/label stem mismatch; missing={missing}, extra={extra}"
            )

        boxes_in_split = 0
        for stem_key in sorted(
            images, key=lambda key: (images[key].name.casefold(), images[key].name)
        ):
            image_path = images[stem_key]
            label_path = labels[stem_key]
            if stem_key in seen_global_stems:
                raise C2ADataError(
                    "duplicate case-insensitive image stem across publisher splits: "
                    f"{seen_global_stems[stem_key]}, {image_path}"
                )
            seen_global_stems[stem_key] = image_path
            match = _VIEW_SUFFIX_RE.fullmatch(image_path.stem)
            if match is None:
                raise C2ADataError(
                    "C2A image stem must end in one camera view suffix _0.._4: "
                    f"{image_path.name}"
                )
            scene_group = match.group("group")
            group_key = scene_group.casefold()
            view_index = int(match.group("view"))
            if view_index in group_views[group_key]:
                raise C2ADataError(
                    f"duplicate view _{view_index} for scene group {scene_group!r}"
                )
            if group_key in group_names and group_names[group_key] != scene_group:
                raise C2ADataError(
                    f"case-ambiguous scene group: {group_names[group_key]!r}, "
                    f"{scene_group!r}"
                )
            group_names[group_key] = scene_group
            group_views[group_key].add(view_index)
            try:
                decoded = image_decoder(image_path)
            except C2ADataError:
                raise
            except Exception as error:
                raise C2ADataError(
                    f"image decoder failed for {image_path}: {error}"
                ) from error
            width, height = _validate_dimensions(decoded, image_path)
            person_boxes = _parse_yolo_label(label_path)
            boxes_in_split += person_boxes
            try:
                image_digest = sha256_file(image_path)
                label_digest = sha256_file(label_path)
            except ArtifactIOError as error:
                raise C2ADataError(str(error)) from error
            group_splits[group_key].add(split)
            image_hash_splits[image_digest].add(split)
            records.append(
                C2ASourceRecord(
                    publisher_split=split,
                    stem=image_path.stem,
                    scene_group_id=scene_group,
                    scene_group_key=group_key,
                    view_index=view_index,
                    image_path=image_path.resolve(strict=False),
                    label_path=label_path.resolve(strict=False),
                    image_sha256=image_digest,
                    label_sha256=label_digest,
                    image_bytes=image_path.stat().st_size,
                    label_bytes=label_path.stat().st_size,
                    width=width,
                    height=height,
                    person_boxes=person_boxes,
                )
            )
        split_box_counts[split] = boxes_in_split

    incomplete_groups = {
        group_names[key]: sorted(set(range(5)) - views)
        for key, views in group_views.items()
        if views != set(range(5))
    }
    if incomplete_groups:
        preview = list(sorted(incomplete_groups.items()))[:10]
        raise C2ADataError(
            "each C2A scene group must contain exactly views _0.._4; "
            f"missing={preview}"
        )

    group_overlaps = {
        group_names[key]: tuple(sorted(splits, key=SPLITS.index))
        for key, splits in group_splits.items()
        if len(splits) > 1
    }
    image_hash_overlaps = {
        digest: tuple(sorted(splits, key=SPLITS.index))
        for digest, splits in image_hash_splits.items()
        if len(splits) > 1
    }
    publisher_test_untouched = not group_overlaps and not image_hash_overlaps
    ordered_records = tuple(
        sorted(
            records,
            key=lambda item: (
                SPLITS.index(item.publisher_split),
                item.image_path.name.casefold(),
                item.image_path.name,
            ),
        )
    )
    return C2AAudit(
        extracted_root=root,
        dataset_root=dataset_root.resolve(strict=False),
        records=ordered_records,
        split_counts=MappingProxyType(dict(counts)),
        split_box_counts=MappingProxyType(split_box_counts),
        group_count=len(group_views),
        publisher_group_overlaps=MappingProxyType(group_overlaps),
        publisher_image_hash_overlaps=MappingProxyType(image_hash_overlaps),
        publisher_test_untouched=publisher_test_untouched,
        pose_hint_tree_present=pose_path is not None,
        excluded_pose_hint_files=excluded_pose_files,
    )


def require_publisher_split_qualification(audit: C2AAudit) -> None:
    """Reject use of a leaking publisher test split as untouched evidence."""

    if not isinstance(audit, C2AAudit):
        raise C2ADataError("audit must be a C2AAudit")
    if not audit.publisher_test_untouched:
        qualification = audit.publisher_qualification()
        raise C2ADataError(
            "publisher test is not untouched: "
            + ", ".join(qualification["reasons"])
        )


def _group_allocation_counts(group_count: int) -> dict[str, int]:
    if type(group_count) is not int or group_count < len(SPLITS):
        raise C2ADataError("at least three scene groups are required for group-safe splits")
    # Reserve one group per split, then allocate the remainder by the frozen
    # ratios using largest remainders.  This guarantees a non-empty test gate.
    result = {split: 1 for split in SPLITS}
    remaining = group_count - len(SPLITS)
    exact = {
        split: remaining * FROZEN_GROUP_SPLIT_RATIOS[split] for split in SPLITS
    }
    floors = {split: math.floor(exact[split]) for split in SPLITS}
    for split in SPLITS:
        result[split] += floors[split]
    unassigned = remaining - sum(floors.values())
    ranked = sorted(
        SPLITS,
        key=lambda split: (-(exact[split] - floors[split]), SPLITS.index(split)),
    )
    for split in ranked[:unassigned]:
        result[split] += 1
    if sum(result.values()) != group_count or any(result[split] < 1 for split in SPLITS):
        raise C2ADataError("internal group allocation error")
    return result


def _derived_group_assignments(audit: C2AAudit) -> tuple[dict[str, str], dict[str, int]]:
    group_names = {
        record.scene_group_key: record.scene_group_id for record in audit.records
    }
    ordered = sorted(
        group_names,
        key=lambda key: (
            hashlib.sha256(
                f"{FROZEN_GROUP_SPLIT_SEED}\0{key}".encode("utf-8")
            ).hexdigest(),
            key,
        ),
    )
    allocation = _group_allocation_counts(len(ordered))
    assignments: dict[str, str] = {}
    offset = 0
    for split in SPLITS:
        end = offset + allocation[split]
        for group_key in ordered[offset:end]:
            assignments[group_key] = split
        offset = end
    if len(assignments) != len(group_names):
        raise C2ADataError("internal group assignment error")
    return assignments, allocation


def render_c2a_dataset_yaml(dataset_root: str | os.PathLike[str]) -> str:
    root = Path(dataset_root).resolve(strict=False).as_posix()
    return (
        f"path: {json.dumps(root, ensure_ascii=False)}\n"
        "train: images/train\n"
        "val: images/val\n"
        "test: images/test\n"
        "names:\n"
        "  0: person_candidate\n"
    )


def _write_exclusive(path: Path, data: bytes) -> None:
    try:
        with path.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError as error:
        raise C2ADataError(f"refusing to overwrite output artifact: {path}") from error
    except OSError as error:
        raise C2ADataError(f"cannot create output artifact {path}: {error}") from error


def _transfer(source: Path, destination: Path, mode: str) -> None:
    if os.path.lexists(destination):
        raise C2ADataError(f"refusing to overwrite derived file: {destination}")
    try:
        if mode == "hardlink":
            os.link(source, destination)
        elif mode == "copy":
            shutil.copy2(source, destination)
        else:
            raise C2ADataError("transfer_mode must be 'hardlink' or 'copy'")
    except C2ADataError:
        raise
    except OSError as error:
        raise C2ADataError(
            f"{mode} failed from {source} to {destination}: {error}"
        ) from error


def _inventory_line(payload: Mapping[str, Any]) -> bytes:
    try:
        return (
            json.dumps(
                payload,
                allow_nan=False,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise C2ADataError(f"inventory record is not strict JSON: {error}") from error


def prepare_c2a_group_safe_dataset(
    extracted_root: str | os.PathLike[str],
    output_root: str | os.PathLike[str],
    *,
    workspace_root: str | os.PathLike[str],
    repository_root: str | os.PathLike[str],
    transfer_mode: Literal["hardlink", "copy"] = "copy",
    expected_counts: Mapping[str, int] = OFFICIAL_SPLIT_COUNTS,
    image_decoder: Callable[[Path], Sequence[int]] = decode_image_dimensions,
) -> C2APreparationResult:
    """Audit C2A v2 and create a deterministic, group-safe detector dataset.

    The destination must not exist.  Files are transferred without modifying
    their bytes, using only the explicitly requested copy or hardlink mode.
    ``artifact_hashes.json`` is written last and is the completion marker.
    """

    if transfer_mode not in {"hardlink", "copy"}:
        raise C2ADataError("transfer_mode must be explicitly 'hardlink' or 'copy'")
    try:
        workspace = require_external_workspace(workspace_root, repository_root)
        destination = require_path_within_workspace(output_root, workspace)
    except ArtifactIOError as error:
        raise C2ADataError(str(error)) from error
    if os.path.lexists(destination):
        raise C2ADataError(f"refusing to overwrite output root: {destination}")

    source_input = Path(extracted_root).expanduser()
    if not source_input.is_absolute():
        raise C2ADataError("extracted_root must be absolute")
    if _is_link_or_junction(source_input):
        raise C2ADataError(f"extracted_root must not be a symlink: {source_input}")
    source = source_input.resolve(strict=False)
    repository = Path(repository_root).expanduser().resolve(strict=False)
    if source == repository or repository in source.parents or source in repository.parents:
        raise C2ADataError("C2A source data must remain outside the Git repository")
    if (
        source == destination
        or source in destination.parents
        or destination in source.parents
    ):
        raise C2ADataError("C2A source and derived output roots must not overlap")

    audit = audit_c2a_v2(
        source,
        expected_counts=expected_counts,
        image_decoder=image_decoder,
    )
    assignments, group_allocation = _derived_group_assignments(audit)

    # Exact duplicate image bytes assigned to different derived splits would
    # invalidate the new split even when publisher scene names differ.  Reject
    # that contradictory provenance before creating any output.
    hash_assignments: dict[str, set[str]] = defaultdict(set)
    for record in audit.records:
        hash_assignments[record.image_sha256].add(
            assignments[record.scene_group_key]
        )
    leaking_hashes = sorted(
        digest for digest, splits in hash_assignments.items() if len(splits) > 1
    )
    if leaking_hashes:
        raise C2ADataError(
            "exact image bytes span scene groups assigned to different derived splits: "
            f"{leaking_hashes[:10]}"
        )

    created_output = False
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.mkdir(exist_ok=False)
        created_output = True
        for split in SPLITS:
            (destination / "images" / split).mkdir(parents=True, exist_ok=False)
            (destination / "labels" / split).mkdir(parents=True, exist_ok=False)

        inventory_path = destination / "source_inventory.jsonl"
        derived_counts = {split: 0 for split in SPLITS}
        derived_boxes = {split: 0 for split in SPLITS}
        inventory_bytes = bytearray()
        for record in audit.records:
            derived_split = assignments[record.scene_group_key]
            image_output = destination / "images" / derived_split / record.image_path.name
            label_output = destination / "labels" / derived_split / record.label_path.name
            # Detect source mutation between audit and transfer.
            if sha256_file(record.image_path) != record.image_sha256:
                raise C2ADataError(f"source image changed during preparation: {record.image_path}")
            if sha256_file(record.label_path) != record.label_sha256:
                raise C2ADataError(f"source label changed during preparation: {record.label_path}")
            _transfer(record.image_path, image_output, transfer_mode)
            _transfer(record.label_path, label_output, transfer_mode)
            if sha256_file(image_output) != record.image_sha256:
                raise C2ADataError(f"derived image hash mismatch: {image_output}")
            if sha256_file(label_output) != record.label_sha256:
                raise C2ADataError(f"derived label hash mismatch: {label_output}")
            derived_counts[derived_split] += 1
            derived_boxes[derived_split] += record.person_boxes
            inventory_bytes.extend(
                _inventory_line(
                    {
                        "schema": C2A_INVENTORY_RECORD_SCHEMA,
                        "dataset_id": C2A_DATASET_ID,
                        "publisher_split": record.publisher_split,
                        "derived_split": derived_split,
                        "scene_group_id": record.scene_group_id,
                        "view_index": record.view_index,
                        "stem": record.stem,
                        "source_image_path": str(record.image_path),
                        "source_image_sha256": record.image_sha256,
                        "source_image_bytes": record.image_bytes,
                        "source_label_path": str(record.label_path),
                        "source_label_sha256": record.label_sha256,
                        "source_label_bytes": record.label_bytes,
                        "derived_image_path": str(image_output.resolve(strict=False)),
                        "derived_label_path": str(label_output.resolve(strict=False)),
                        "image_width": record.width,
                        "image_height": record.height,
                        "person_boxes": record.person_boxes,
                        "detector_class": "person_candidate",
                        "pose_hint_used": False,
                        "state_label_derived": False,
                    }
                )
            )
        _write_exclusive(inventory_path, bytes(inventory_bytes))

        class_map_path = destination / "class_map.json"
        class_map_bytes = b'{\n  "0": "person_candidate"\n}\n'
        _write_exclusive(class_map_path, class_map_bytes)
        dataset_yaml_path = destination / "dataset.yaml"
        dataset_yaml_bytes = render_c2a_dataset_yaml(destination).encode("utf-8")
        _write_exclusive(dataset_yaml_path, dataset_yaml_bytes)

        inventory_sha = sha256_file(inventory_path)
        yaml_sha = sha256_file(dataset_yaml_path)
        class_map_sha = sha256_file(class_map_path)
        derived_group_splits: dict[str, set[str]] = defaultdict(set)
        derived_hash_splits: dict[str, set[str]] = defaultdict(set)
        for record in audit.records:
            split = assignments[record.scene_group_key]
            derived_group_splits[record.scene_group_id].add(split)
            derived_hash_splits[record.image_sha256].add(split)
        if any(len(splits) != 1 for splits in derived_group_splits.values()):
            raise C2ADataError("internal cross-split scene-group leakage")
        if any(len(splits) != 1 for splits in derived_hash_splits.values()):
            raise C2ADataError("internal cross-split image-hash leakage")

        report_path = destination / "preparation_report.json"
        report_payload = {
            "schema": C2A_PREPARATION_REPORT_SCHEMA,
            "dataset_id": C2A_DATASET_ID,
            "source": {
                "extracted_root": str(audit.extracted_root),
                "dataset_root": str(audit.dataset_root),
                "expected_official_split_counts": dict(audit.split_counts),
                "observed_publisher_split_counts": dict(audit.split_counts),
                "publisher_split_box_counts": dict(audit.split_box_counts),
                "scene_groups": audit.group_count,
            },
            "publisher_split_qualification": audit.publisher_qualification(),
            "derived_split_policy": {
                "group_key": "filename stem with final _0.._4 view suffix removed",
                "group_rank": "sha256(seed + NUL + casefold(scene_group_id))",
                "allocation": "one_group_per_split_then_largest_remainder",
                "seed": FROZEN_GROUP_SPLIT_SEED,
                "ratios": dict(FROZEN_GROUP_SPLIT_RATIOS),
                "group_counts": group_allocation,
                "image_counts": derived_counts,
                "person_box_counts": derived_boxes,
                "group_overlap_count": 0,
                "exact_image_hash_overlap_count": 0,
                "test_reserved_for_final_evaluation": True,
                "test_used_for_selection": False,
            },
            "class_policy": {
                "classes": {"0": "person_candidate"},
                "pose_hint_directory": POSE_HINT_DIRECTORY,
                "pose_hint_tree_present": audit.pose_hint_tree_present,
                "excluded_pose_hint_files": audit.excluded_pose_hint_files,
                "pose_hints_used_as_detector_labels": False,
                "pose_hints_used_as_state_labels": False,
                "dataset_origin_used_as_state_label": False,
            },
            "transfer_mode": transfer_mode,
            "artifacts": {
                "inventory_path": str(inventory_path.resolve(strict=False)),
                "inventory_sha256": inventory_sha,
                "dataset_yaml_path": str(dataset_yaml_path.resolve(strict=False)),
                "dataset_yaml_sha256": yaml_sha,
                "class_map_path": str(class_map_path.resolve(strict=False)),
                "class_map_sha256": class_map_sha,
            },
        }
        atomic_create_json(report_path, report_payload)
        report_sha = sha256_file(report_path)

        artifact_hashes_path = destination / "artifact_hashes.json"
        hashes_payload = {
            "schema": C2A_ARTIFACT_HASHES_SCHEMA,
            "artifacts": [
                {
                    "path": str(inventory_path.resolve(strict=False)),
                    "sha256": inventory_sha,
                },
                {
                    "path": str(dataset_yaml_path.resolve(strict=False)),
                    "sha256": yaml_sha,
                },
                {
                    "path": str(class_map_path.resolve(strict=False)),
                    "sha256": class_map_sha,
                },
                {
                    "path": str(report_path.resolve(strict=False)),
                    "sha256": report_sha,
                },
            ],
        }
        atomic_create_json(artifact_hashes_path, hashes_payload)
        artifact_hashes_sha = sha256_file(artifact_hashes_path)
        return C2APreparationResult(
            output_root=destination,
            report_path=report_path,
            report_sha256=report_sha,
            inventory_path=inventory_path,
            inventory_sha256=inventory_sha,
            dataset_yaml_path=dataset_yaml_path,
            dataset_yaml_sha256=yaml_sha,
            artifact_hashes_path=artifact_hashes_path,
            artifact_hashes_sha256=artifact_hashes_sha,
            publisher_test_untouched=audit.publisher_test_untouched,
            derived_split_counts=MappingProxyType(derived_counts),
        )
    except C2ADataError:
        if created_output:
            shutil.rmtree(destination, ignore_errors=True)
        raise
    except (ArtifactIOError, FileExistsError, OSError) as error:
        if created_output:
            shutil.rmtree(destination, ignore_errors=True)
        raise C2ADataError(f"C2A preparation failed: {error}") from error


__all__ = [
    "C2A_ARTIFACT_HASHES_SCHEMA",
    "C2A_DATASET_ID",
    "C2A_INVENTORY_RECORD_SCHEMA",
    "C2A_PREPARATION_REPORT_SCHEMA",
    "FROZEN_GROUP_SPLIT_RATIOS",
    "FROZEN_GROUP_SPLIT_SEED",
    "OFFICIAL_SPLIT_COUNTS",
    "POSE_HINT_DIRECTORY",
    "C2AAudit",
    "C2ADataError",
    "C2APreparationResult",
    "C2ASourceRecord",
    "audit_c2a_v2",
    "decode_image_dimensions",
    "prepare_c2a_group_safe_dataset",
    "render_c2a_dataset_yaml",
    "require_publisher_split_qualification",
]
