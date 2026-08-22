from __future__ import annotations

import json
import os
import struct
import zipfile
from dataclasses import replace
from types import MappingProxyType

import pytest
import rescue_training.dataset_intake as dataset_intake

from rescue_training.dataset_intake import (
    FROZEN_DATASET_IDENTITIES,
    extract_registered_zip,
    register_dataset_archive,
)

from rescue_training.visdrone import (
    VISDRONE_REPORT_SCHEMA,
    VisDroneConversionError,
    convert_visdrone_det,
    read_image_dimensions,
    validate_visdrone_conversion_report,
)


NOW = "2026-08-22T12:34:56Z"


@pytest.fixture(autouse=True)
def _allow_small_visdrone_archives(monkeypatch):
    identities = dict(FROZEN_DATASET_IDENTITIES)
    for split in ("train", "val"):
        key = f"visdrone-det-2019-{split}"
        identities[key] = replace(
            identities[key],
            known_expected_bytes=None,
            known_expected_sha256=None,
            known_identity_trust=None,
        )
    monkeypatch.setattr(
        dataset_intake, "FROZEN_DATASET_IDENTITIES", MappingProxyType(identities)
    )


def _registered_split(repository, workspace, source, split, records):
    prefix = f"VisDrone2019-DET-{split}"
    archive = source / f"{prefix}.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name, annotation in records:
            bundle.writestr(f"{prefix}/images/{name}.jpg", b"test-image")
            bundle.writestr(
                f"{prefix}/annotations/{name}.txt", annotation.encode("utf-8")
            )
    registration = register_dataset_archive(
        archive.resolve(),
        workspace / "manifests" / f"{split}-registration.json",
        workspace / "manifests" / f"{split}-response.json",
        dataset_key=f"visdrone-det-2019-{split}",
        workspace_root=workspace,
        repository_root=repository,
        origin_kind="local_user_provided",
        supplied_by="unit-test",
        dataset_use_authorization_status="authorization_recorded",
        terms_recorded_by="unit-test",
        response_metadata={"transport": "unit-test"},
        retrieved_at_utc=NOW,
    )
    extraction = extract_registered_zip(
        registration.evidence_path,
        workspace / "raw" / split,
        workspace / "manifests" / f"{split}-inventory.json",
        workspace / "manifests" / f"{split}-extraction.json",
        workspace_root=workspace,
        repository_root=repository,
        expected_image_count=len(records),
        required_top_level_prefixes=(f"{prefix}/images", f"{prefix}/annotations"),
        extracted_at_utc=NOW,
    )
    return extraction.manifest_path, extraction.extracted_root / prefix


def _layout(tmp_path):
    repository = (tmp_path / "repo").resolve()
    repository.mkdir()
    workspace = (tmp_path / "workspace").resolve()
    source = (tmp_path / "source").resolve()
    source.mkdir()
    train_manifest, train = _registered_split(
        repository,
        workspace,
        source,
        "train",
        [
            (
                "a",
                "-10,-5,30,20,1,1,0,0\n"
                "0,0,5,5,1,3,0,0\n",
            ),
            ("b", "1,2,3,4,1,11,0,0\n"),
        ],
    )
    val_manifest, val = _registered_split(
        repository,
        workspace,
        source,
        "val",
        [("c", "90,40,20,20,1,2,1,2\n")],
    )
    return repository, workspace, train_manifest, val_manifest, train, val


def _dimensions(_path):
    return 100, 50


def test_converter_preserves_splits_maps_people_clips_and_writes_empty_labels(tmp_path):
    repository, workspace, train_manifest, val_manifest, train, val = _layout(tmp_path)
    output = workspace / "derived-person"

    report = convert_visdrone_det(
        {"train": train_manifest, "val": val_manifest},
        output,
        workspace_root=workspace,
        repository_root=repository,
        transfer_mode="copy",
        expected_counts={"train": 2, "val": 1},
        image_size_reader=_dimensions,
    )

    assert (output / "images" / "train" / "a.jpg").read_bytes() == b"test-image"
    assert (output / "labels" / "train" / "a.txt").read_text(encoding="utf-8") == (
        "0 0.1000000000 0.1500000000 0.2000000000 0.3000000000\n"
    )
    assert (output / "labels" / "train" / "b.txt").read_bytes() == b""
    assert (output / "labels" / "val" / "c.txt").read_text(encoding="utf-8") == (
        "0 0.9500000000 0.9000000000 0.1000000000 0.2000000000\n"
    )
    assert report.splits["train"].mapped_person_boxes == 1
    assert report.splits["train"].ignored_boxes == 2
    assert report.splits["train"].clipped_person_boxes == 1
    assert report.splits["train"].empty_person_labels == 1
    assert report.splits["val"].mapped_person_boxes == 1

    written = json.loads(
        (output / "conversion_report.json").read_text(encoding="utf-8")
    )
    assert written["class_map"] == {"0": "person_candidate"}
    assert written["schema"] == VISDRONE_REPORT_SCHEMA
    assert written["source_category_map"] == {"1": "pedestrian", "2": "people"}
    assert written["split_policy"] == "official_train_val_preserved"
    assert written["totals"]["images"] == 3
    assert (output / "class_map.json").read_text(encoding="utf-8") == (
        '{\n  "0": "person_candidate"\n}\n'
    )
    assert (output / "dataset.yaml").read_text(encoding="utf-8") == (
        f'path: "{output.resolve().as_posix()}"\n'
        "train: images/train\n"
        "val: images/val\n"
        "names:\n"
        "  0: person_candidate\n"
    )
    assert len(written["dataset_yaml_sha256"]) == 64
    assert len(written["class_map_sha256"]) == 64
    assert written["splits"]["train"]["source_lineage"]["manifest"][
        "path"
    ] == str(train_manifest)
    assert written["splits"]["val"]["source_lineage"]["archive"][
        "sha256"
    ] != written["splits"]["train"]["source_lineage"]["archive"]["sha256"]
    verified = validate_visdrone_conversion_report(
        output / "conversion_report.json",
        workspace_root=workspace,
        repository_root=repository,
        expected_counts={"train": 2, "val": 1},
        image_size_reader=_dimensions,
    )
    assert verified["conversion_report"]["schema"] == VISDRONE_REPORT_SCHEMA
    assert verified["dataset_yaml"]["path"] == str(output / "dataset.yaml")


def test_hardlink_mode_is_explicit_and_does_not_fallback(tmp_path, monkeypatch):
    repository, workspace, train_manifest, val_manifest, train, val = _layout(tmp_path)
    output = workspace / "hardlinked"
    monkeypatch.setattr(os, "link", lambda *_args: (_ for _ in ()).throw(OSError("no links")))
    with pytest.raises(VisDroneConversionError, match="hardlink failed"):
        convert_visdrone_det(
            {"train": train_manifest, "val": val_manifest},
            output,
            workspace_root=workspace,
            repository_root=repository,
            transfer_mode="hardlink",
            expected_counts={"train": 2, "val": 1},
            image_size_reader=_dimensions,
        )
    assert not output.exists()


def test_existing_output_is_never_overwritten(tmp_path):
    repository, workspace, train_manifest, val_manifest, train, val = _layout(tmp_path)
    output = workspace / "existing"
    output.mkdir(parents=True)
    sentinel = output / "keep.txt"
    sentinel.write_text("owned", encoding="utf-8")

    with pytest.raises(VisDroneConversionError, match="refusing to overwrite"):
        convert_visdrone_det(
            {"train": train_manifest, "val": val_manifest},
            output,
            workspace_root=workspace,
            repository_root=repository,
            transfer_mode="copy",
            expected_counts={"train": 2, "val": 1},
            image_size_reader=_dimensions,
        )
    assert sentinel.read_text(encoding="utf-8") == "owned"


def test_count_and_pairing_mismatches_fail_without_partial_output(tmp_path):
    repository, workspace, train_manifest, val_manifest, train, val = _layout(tmp_path)
    output = workspace / "count-failure"
    with pytest.raises(VisDroneConversionError, match="exactly one 3-image"):
        convert_visdrone_det(
            {"train": train_manifest, "val": val_manifest},
            output,
            workspace_root=workspace,
            repository_root=repository,
            transfer_mode="copy",
            expected_counts={"train": 3, "val": 1},
            image_size_reader=_dimensions,
        )
    assert not output.exists()

    (train / "annotations" / "b.txt").unlink()
    output = workspace / "pairing-failure"
    with pytest.raises(VisDroneConversionError, match="extraction lineage is invalid"):
        convert_visdrone_det(
            {"train": train_manifest, "val": val_manifest},
            output,
            workspace_root=workspace,
            repository_root=repository,
            transfer_mode="copy",
            expected_counts={"train": 2, "val": 1},
            image_size_reader=_dimensions,
        )
    assert not output.exists()


@pytest.mark.parametrize(
    "bad_row",
    [
        "1,2,3,4,1,1,0\n",
        "1,2,nope,4,1,1,0,0\n",
        "1,2,0,4,1,1,0,0\n",
        "1,2,3,4,2,1,0,0\n",
        "1,2,3,4,1,12,0,0\n",
        "1,2,3,4,1,1,3,0\n",
        "200,200,3,4,1,1,0,0\n",
    ],
)
def test_invalid_annotation_rows_fail_closed_and_are_cleaned(tmp_path, bad_row):
    repository, workspace, train_manifest, val_manifest, train, val = _layout(tmp_path)
    (train / "annotations" / "a.txt").write_text(bad_row, encoding="utf-8")
    output = workspace / "invalid-row"
    with pytest.raises(VisDroneConversionError):
        convert_visdrone_det(
            {"train": train_manifest, "val": val_manifest},
            output,
            workspace_root=workspace,
            repository_root=repository,
            transfer_mode="copy",
            expected_counts={"train": 2, "val": 1},
            image_size_reader=_dimensions,
        )
    assert not output.exists()


def test_arbitrary_visdrone_roots_cannot_masquerade_as_registered_archives(tmp_path):
    repository, workspace, _train_manifest, _val_manifest, train, val = _layout(tmp_path)
    with pytest.raises(VisDroneConversionError, match="extraction lineage is invalid"):
        convert_visdrone_det(
            {"train": train, "val": val},
            workspace / "must-not-exist",
            workspace_root=workspace,
            repository_root=repository,
            expected_counts={"train": 2, "val": 1},
            image_size_reader=_dimensions,
        )


def test_conversion_validator_rejects_tampered_output_and_lineage_claims(tmp_path):
    repository, workspace, train_manifest, val_manifest, _train, _val = _layout(tmp_path)
    output = workspace / "derived-person"
    convert_visdrone_det(
        {"train": train_manifest, "val": val_manifest},
        output,
        workspace_root=workspace,
        repository_root=repository,
        expected_counts={"train": 2, "val": 1},
        image_size_reader=_dimensions,
    )
    report_path = output / "conversion_report.json"
    (output / "labels" / "train" / "a.txt").write_text(
        "0 0.5 0.5 0.1 0.1\n", encoding="utf-8"
    )
    with pytest.raises(VisDroneConversionError, match="derived label differs"):
        validate_visdrone_conversion_report(
            report_path,
            workspace_root=workspace,
            repository_root=repository,
            expected_counts={"train": 2, "val": 1},
            image_size_reader=_dimensions,
        )

    # Restore the derived label, then prove a caller cannot rewrite a lineage
    # claim without the recursively validated registration/extraction agreeing.
    (output / "labels" / "train" / "a.txt").write_text(
        "0 0.1000000000 0.1500000000 0.2000000000 0.3000000000\n",
        encoding="utf-8",
    )
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    payload["splits"]["train"]["source_lineage"]["manifest"]["sha256"] = "0" * 64
    report_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(VisDroneConversionError, match="differs from recursively"):
        validate_visdrone_conversion_report(
            report_path,
            workspace_root=workspace,
            repository_root=repository,
            expected_counts={"train": 2, "val": 1},
            image_size_reader=_dimensions,
        )


def test_default_dimension_reader_handles_png_and_rejects_unknown_bytes(tmp_path):
    png = tmp_path / "image.png"
    png.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", 13)
        + b"IHDR"
        + struct.pack(">II", 320, 240)
    )
    assert read_image_dimensions(png) == (320, 240)

    unknown = tmp_path / "unknown.jpg"
    unknown.write_bytes(b"not-an-image")
    with pytest.raises(VisDroneConversionError, match="unsupported image encoding"):
        read_image_dimensions(unknown)
