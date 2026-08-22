from __future__ import annotations

import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

from tools.audit_yolo_dataset import (  # noqa: E402
    DatasetAuditError,
    SplitSpec,
    audit_dataset,
    load_class_map,
    montage_membership,
    montage_membership_sha256,
    parse_yolo_label,
    render_montage,
    select_montage_samples,
)


def _split(tmp_path, name):
    images = tmp_path / "images" / name
    labels = tmp_path / "labels" / name
    images.mkdir(parents=True)
    labels.mkdir(parents=True)
    return SplitSpec(name, images, labels)


def _sample(spec, stem, label="0 0.5 0.5 0.4 0.4\n", seed=1):
    rng = np.random.default_rng(seed)
    image = rng.integers(20, 230, size=(80, 120, 3), dtype=np.uint8)
    assert cv2.imwrite(str(spec.images / f"{stem}.jpg"), image)
    (spec.labels / f"{stem}.txt").write_text(label, encoding="utf-8")


def test_class_map_requires_contiguous_nonempty_classes(tmp_path):
    path = tmp_path / "classes.json"
    path.write_text('{"0":"person_candidate","1":"fire"}', encoding="utf-8")

    assert load_class_map(path) == {0: "person_candidate", 1: "fire"}

    path.write_text('{"1":"fire"}', encoding="utf-8")
    with pytest.raises(DatasetAuditError, match="contiguous"):
        load_class_map(path)


@pytest.mark.parametrize(
    "label, message",
    [
        ("2 0.5 0.5 0.2 0.2\n", "unsupported class"),
        ("0 nan 0.5 0.2 0.2\n", "non-finite"),
        ("0 0.5 0.5 0 0.2\n", "invalid normalized"),
        ("0 0.95 0.5 0.2 0.2\n", "outside image"),
        ("0 0.5 0.5 0.2\n", "expected 5"),
    ],
)
def test_invalid_yolo_boxes_fail_closed(tmp_path, label, message):
    path = tmp_path / "bad.txt"
    path.write_text(label, encoding="utf-8")

    with pytest.raises(DatasetAuditError, match=message):
        parse_yolo_label(path, {0: "person_candidate", 1: "fire"})


def test_audit_accepts_valid_positive_and_negative_samples(tmp_path):
    train = _split(tmp_path, "train")
    _sample(train, "positive")
    _sample(train, "negative", label="", seed=2)

    report, samples = audit_dataset((train,), {0: "person_candidate"}, cv2)

    assert report["passed"] is True
    assert report["totals"]["valid_samples"] == 2
    assert report["totals"]["boxes"] == 1
    assert report["splits"]["train"]["boxes"] == 1
    assert report["splits"]["train"]["class_box_counts"] == {"0": 1}
    assert report["splits"]["train"]["empty_label_samples"] == 1
    assert len(samples) == 2


def test_missing_and_orphan_labels_are_reported(tmp_path):
    train = _split(tmp_path, "train")
    image = np.full((40, 60, 3), 100, dtype=np.uint8)
    assert cv2.imwrite(str(train.images / "missing.jpg"), image)
    (train.labels / "orphan.txt").write_text("", encoding="utf-8")

    report, _ = audit_dataset((train,), {0: "person_candidate"}, cv2)

    assert report["passed"] is False
    reasons = {item["reason"] for item in report["errors"]}
    assert reasons == {"missing_label", "orphan_label"}


def test_cross_split_duplicate_bytes_are_hard_leakage_failure(tmp_path):
    train = _split(tmp_path, "train")
    val = _split(tmp_path, "val")
    _sample(train, "same", seed=7)
    (val.images / "copy.jpg").write_bytes((train.images / "same.jpg").read_bytes())
    (val.labels / "copy.txt").write_text("0 0.5 0.5 0.4 0.4\n", encoding="utf-8")

    report, _ = audit_dataset((train, val), {0: "person_candidate"}, cv2)

    assert report["passed"] is False
    assert report["totals"]["cross_split_leakage_groups"] == 1
    assert report["cross_split_leakage"][0]["splits"] == ["train", "val"]


def test_within_split_duplicate_is_visible_warning(tmp_path):
    train = _split(tmp_path, "train")
    _sample(train, "one", seed=9)
    (train.images / "two.jpg").write_bytes((train.images / "one.jpg").read_bytes())
    (train.labels / "two.txt").write_text("0 0.5 0.5 0.4 0.4\n", encoding="utf-8")

    report, _ = audit_dataset((train,), {0: "person_candidate"}, cv2)

    assert report["passed"] is True
    assert report["totals"]["warnings"] == 1
    assert report["warnings"][0]["reason"] == "within_split_duplicate_image"


def test_stratified_montage_is_deterministic_and_renders(tmp_path):
    train = _split(tmp_path, "train")
    val = _split(tmp_path, "val")
    _sample(train, "person", seed=1)
    _sample(train, "negative", label="", seed=2)
    _sample(val, "fire", label="1 0.5 0.5 0.3 0.3\n", seed=3)
    report, samples = audit_dataset(
        (train, val), {0: "person_candidate", 1: "fire"}, cv2
    )
    assert report["passed"] is True

    first = select_montage_samples(samples, 100)
    second = select_montage_samples(samples, 100)
    assert [(item.split, item.stem) for item in first] == [
        (item.split, item.stem) for item in second
    ]
    first_members = montage_membership(first)
    second_members = montage_membership(second)
    assert first_members == second_members
    assert montage_membership_sha256(first_members) == montage_membership_sha256(
        second_members
    )
    output = tmp_path / "montage.jpg"
    render_montage(first, {0: "person_candidate", 1: "fire"}, output, cv2)

    rendered = cv2.imread(str(output))
    assert rendered is not None
    assert rendered.shape[1] == 5 * 320
    assert np.count_nonzero(rendered) > 0


def test_unexpected_or_symlinked_dataset_entries_fail_closed(tmp_path):
    train = _split(tmp_path, "train")
    _sample(train, "person")
    (train.images / "unexpected.bin").write_bytes(b"not an image")

    report, _ = audit_dataset((train,), {0: "person_candidate"}, cv2)

    assert report["passed"] is False
    assert any(
        "unexpected file extension" in item["reason"] for item in report["errors"]
    )


def test_report_is_json_serializable_without_nonfinite_values(tmp_path):
    train = _split(tmp_path, "train")
    _sample(train, "person")
    report, _ = audit_dataset((train,), {0: "person_candidate"}, cv2)

    payload = json.dumps(report, allow_nan=False)

    assert "veriswarm.dataset_audit.v1" in payload
