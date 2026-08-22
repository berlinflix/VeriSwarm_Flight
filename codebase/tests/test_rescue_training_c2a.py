from __future__ import annotations

import json

import pytest

from rescue_training.artifact_io import sha256_file
from rescue_training.c2a import (
    C2A_ARTIFACT_HASHES_SCHEMA,
    C2A_INVENTORY_RECORD_SCHEMA,
    C2A_PREPARATION_REPORT_SCHEMA,
    C2ADataError,
    FROZEN_GROUP_SPLIT_SEED,
    audit_c2a_v2,
    prepare_c2a_group_safe_dataset,
    require_publisher_split_qualification,
)


TEST_COUNTS = {"train": 10, "val": 10, "test": 5}


def _decoded_dimensions(_path):
    return 1280, 720


def _make_source(tmp_path):
    extracted = (tmp_path / "source").resolve()
    dataset = extracted / "new_dataset3"
    for split in ("train", "val", "test"):
        (dataset / split / "images").mkdir(parents=True)
        (dataset / split / "labels").mkdir()
    pose_labels = dataset / "All labels with Pose information" / "labels"
    pose_labels.mkdir(parents=True)
    pose_labels.joinpath("scene000_0.txt").write_text(
        "0 0.5 0.5 0.25 0.25 4\n", encoding="utf-8"
    )

    # Five complete scene groups.  Publisher folders deliberately split every
    # scene's five camera views across train/val/test.
    publisher_for_view = {
        0: "train",
        1: "train",
        2: "val",
        3: "val",
        4: "test",
    }
    for group_index in range(5):
        for view_index in range(5):
            split = publisher_for_view[view_index]
            stem = f"scene{group_index:03d}_{view_index}"
            (dataset / split / "images" / f"{stem}.png").write_bytes(
                f"unique-image:{stem}".encode("ascii")
            )
            (dataset / split / "labels" / f"{stem}.txt").write_text(
                "0 0.500000 0.500000 0.250000 0.250000\n",
                encoding="utf-8",
            )
    return extracted


def _external_paths(tmp_path):
    repository = (tmp_path / "repo").resolve()
    repository.mkdir()
    workspace = (tmp_path / "workspace").resolve()
    output = workspace / "c2a-group-safe"
    return repository, workspace, output


def test_publisher_scene_leakage_blocks_untouched_test_qualification(tmp_path):
    source = _make_source(tmp_path)

    audit = audit_c2a_v2(
        source,
        expected_counts=TEST_COUNTS,
        image_decoder=_decoded_dimensions,
    )

    assert audit.split_counts == TEST_COUNTS
    assert audit.group_count == 5
    assert set(audit.publisher_group_overlaps) == {
        "scene000",
        "scene001",
        "scene002",
        "scene003",
        "scene004",
    }
    assert audit.publisher_test_untouched is False
    qualification = audit.publisher_qualification()
    assert qualification["qualified"] is False
    assert qualification["test_untouched"] is False
    assert "scene_group_overlap_across_publisher_splits" in qualification["reasons"]
    with pytest.raises(C2ADataError, match="publisher test is not untouched"):
        require_publisher_split_qualification(audit)


def test_group_safe_derivation_has_no_group_or_exact_hash_overlap(tmp_path):
    source = _make_source(tmp_path)
    repository, workspace, output = _external_paths(tmp_path)

    result = prepare_c2a_group_safe_dataset(
        source,
        output,
        workspace_root=workspace,
        repository_root=repository,
        transfer_mode="copy",
        expected_counts=TEST_COUNTS,
        image_decoder=_decoded_dimensions,
    )

    assert result.publisher_test_untouched is False
    assert result.derived_split_counts == {"train": 10, "val": 10, "test": 5}
    assert result.report_sha256 == sha256_file(result.report_path)
    assert result.inventory_sha256 == sha256_file(result.inventory_path)
    assert result.dataset_yaml_sha256 == sha256_file(result.dataset_yaml_path)
    assert result.artifact_hashes_sha256 == sha256_file(
        result.artifact_hashes_path
    )

    records = [
        json.loads(line)
        for line in result.inventory_path.read_text(encoding="utf-8").splitlines()
    ]
    assert len(records) == 25
    assert all(record["schema"] == C2A_INVENTORY_RECORD_SCHEMA for record in records)
    assert all(record["detector_class"] == "person_candidate" for record in records)
    assert all(record["pose_hint_used"] is False for record in records)
    assert all(record["state_label_derived"] is False for record in records)

    group_splits: dict[str, set[str]] = {}
    image_hash_splits: dict[str, set[str]] = {}
    for record in records:
        group_splits.setdefault(record["scene_group_id"], set()).add(
            record["derived_split"]
        )
        image_hash_splits.setdefault(record["source_image_sha256"], set()).add(
            record["derived_split"]
        )
        assert sha256_file(record["derived_image_path"]) == record[
            "source_image_sha256"
        ]
        assert sha256_file(record["derived_label_path"]) == record[
            "source_label_sha256"
        ]
    assert all(len(splits) == 1 for splits in group_splits.values())
    assert all(len(splits) == 1 for splits in image_hash_splits.values())

    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert report["schema"] == C2A_PREPARATION_REPORT_SCHEMA
    assert report["publisher_split_qualification"]["qualified"] is False
    assert report["derived_split_policy"]["seed"] == FROZEN_GROUP_SPLIT_SEED
    assert report["derived_split_policy"]["ratios"] == {
        "train": 0.6,
        "val": 0.2,
        "test": 0.2,
    }
    assert report["derived_split_policy"]["group_overlap_count"] == 0
    assert report["derived_split_policy"]["exact_image_hash_overlap_count"] == 0
    assert report["derived_split_policy"]["test_used_for_selection"] is False
    assert report["class_policy"]["pose_hint_tree_present"] is True
    assert report["class_policy"]["excluded_pose_hint_files"] == 1
    assert report["class_policy"]["pose_hints_used_as_detector_labels"] is False
    assert report["class_policy"]["pose_hints_used_as_state_labels"] is False
    assert report["class_policy"]["dataset_origin_used_as_state_label"] is False
    assert report["artifacts"]["inventory_sha256"] == result.inventory_sha256
    assert report["artifacts"]["dataset_yaml_sha256"] == result.dataset_yaml_sha256

    hashes = json.loads(result.artifact_hashes_path.read_text(encoding="utf-8"))
    assert hashes["schema"] == C2A_ARTIFACT_HASHES_SCHEMA
    by_name = {entry["path"].replace("\\", "/").rsplit("/", 1)[-1]: entry["sha256"] for entry in hashes["artifacts"]}
    assert by_name == {
        "class_map.json": report["artifacts"]["class_map_sha256"],
        "dataset.yaml": result.dataset_yaml_sha256,
        "preparation_report.json": result.report_sha256,
        "source_inventory.jsonl": result.inventory_sha256,
    }

    expected_yaml = (
        f'path: "{output.resolve().as_posix()}"\n'
        "train: images/train\n"
        "val: images/val\n"
        "test: images/test\n"
        "names:\n"
        "  0: person_candidate\n"
    )
    assert result.dataset_yaml_path.read_text(encoding="utf-8") == expected_yaml
    assert not any("Pose" in record["source_label_path"] for record in records)


def test_cross_publisher_split_exact_image_bytes_are_detected(tmp_path):
    source = _make_source(tmp_path)
    dataset = source / "new_dataset3"
    train_image = dataset / "train" / "images" / "scene000_0.png"
    val_image = dataset / "val" / "images" / "scene000_2.png"
    val_image.write_bytes(train_image.read_bytes())

    audit = audit_c2a_v2(
        source,
        expected_counts=TEST_COUNTS,
        image_decoder=_decoded_dimensions,
    )

    digest = sha256_file(train_image)
    assert audit.publisher_image_hash_overlaps[digest] == ("train", "val")
    assert audit.publisher_test_untouched is False
    assert "exact_image_bytes_overlap_across_publisher_splits" in (
        audit.publisher_qualification()["reasons"]
    )


@pytest.mark.parametrize(
    "bad_label",
    [
        "1 0.5 0.5 0.25 0.25\n",
        "0 nan 0.5 0.25 0.25\n",
        "0 0.5 0.5 0 0.25\n",
        "0 0.95 0.5 0.25 0.25\n",
        "0 0.5 0.5 0.25\n",
        "\n",
    ],
)
def test_non_one_class_or_invalid_normalized_labels_fail_closed(tmp_path, bad_label):
    source = _make_source(tmp_path)
    label = source / "new_dataset3" / "train" / "labels" / "scene000_0.txt"
    label.write_text(bad_label, encoding="utf-8")

    with pytest.raises(C2ADataError):
        audit_c2a_v2(
            source,
            expected_counts=TEST_COUNTS,
            image_decoder=_decoded_dimensions,
        )


def test_output_is_create_once_and_failure_does_not_overwrite(tmp_path):
    source = _make_source(tmp_path)
    repository, workspace, output = _external_paths(tmp_path)
    output.mkdir(parents=True)
    sentinel = output / "owned.txt"
    sentinel.write_text("keep", encoding="utf-8")

    with pytest.raises(C2ADataError, match="refusing to overwrite output root"):
        prepare_c2a_group_safe_dataset(
            source,
            output,
            workspace_root=workspace,
            repository_root=repository,
            transfer_mode="copy",
            expected_counts=TEST_COUNTS,
            image_decoder=_decoded_dimensions,
        )
    assert sentinel.read_text(encoding="utf-8") == "keep"
