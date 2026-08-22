from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from rescue_training.state_dataset import (
    STATE_DATASET_SCHEMA,
    STATE_MODEL_ID,
    STATE_RECORD_SCHEMA,
    StateDatasetError,
    validate_state_dataset_manifest,
)


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_fixture(tmp_path: Path) -> tuple[Path, list[dict]]:
    archives = tmp_path / "archives"
    crops = tmp_path / "crops"
    media = tmp_path / "media"
    archives.mkdir()
    media.mkdir()
    labeler_artifact = tmp_path / "automated-state-labeler.bin"
    labeler_artifact.write_bytes(b"deterministic-visual-labeler-v1")
    label_policy = tmp_path / "automated-state-label-policy.json"
    label_policy.write_text(
        json.dumps(
            {
                "classes": ["disaster_stressed", "safe_walking"],
                "dataset_origin_allowed": False,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    weak_supervision = {
        "schema": "veriswarm.rescue.person_state_weak_supervision.v1",
        "evidence_status": "weak_supervision_unverified",
        "label_source": "automated_weak_supervision",
        "labeler_type": "automated",
        "labeler_id": "visual-cue-labeler",
        "labeler_version": "1.0.0",
        "labeler_artifact_path": str(labeler_artifact.resolve()),
        "labeler_artifact_sha256": _hash(labeler_artifact),
        "policy_path": str(label_policy.resolve()),
        "policy_sha256": _hash(label_policy),
        "forbidden_inputs": [
            "source_dataset_id",
            "source_archive_identity",
            "dataset_origin",
        ],
    }
    source_specs = []
    for dataset_id in ("c2a-v2", "adilshamim8-people-detection-v1"):
        archive = archives / f"{dataset_id}.zip"
        archive.write_bytes(f"archive:{dataset_id}".encode())
        source_specs.append(
            {
                "dataset_id": dataset_id,
                "archive_path": str(archive.resolve()),
                "archive_sha256": _hash(archive),
                "dataset_use_authorization_status": "authorization_recorded",
                "terms_recorded_by": "automated-dataset-intake-v1",
                "role": "candidate_media_only",
            }
        )

    records: list[dict] = []
    for split_index, split in enumerate(("train", "val", "test")):
        for source_index, dataset_id in enumerate(
            ("c2a-v2", "adilshamim8-people-detection-v1")
        ):
            for label_index, label in enumerate(
                ("disaster_stressed", "safe_walking")
            ):
                item_id = f"{split}-{dataset_id}-{label}"
                source = media / f"{item_id}.jpg"
                source.write_bytes(f"source:{item_id}".encode())
                crop_dir = crops / split / label
                crop_dir.mkdir(parents=True, exist_ok=True)
                crop = crop_dir / f"{item_id}.jpg"
                crop.write_bytes(f"crop:{item_id}".encode())
                records.append(
                    {
                        "schema": STATE_RECORD_SCHEMA,
                        "sample_id": item_id,
                        "split": split,
                        "source_dataset_id": dataset_id,
                        "source_group_id": (
                            f"scene-{split_index}-{source_index}-{label_index}"
                        ),
                        "source_media_path": str(source.resolve()),
                        "source_media_sha256": _hash(source),
                        "frame_index": None,
                        "person_bbox_xyxy_abs": [1, 2, 20, 30],
                        "crop_path": str(crop.resolve()),
                        "crop_sha256": _hash(crop),
                        "state_label": label,
                        "state_reason": (
                            "lying"
                            if label == "disaster_stressed"
                            else "normal_walking_visual_cue"
                        ),
                        "source_pose_hint": (
                            "lying" if label == "disaster_stressed" else None
                        ),
                        "label_source": "automated_weak_supervision",
                        "labeler_id": weak_supervision["labeler_id"],
                        "labeler_version": weak_supervision["labeler_version"],
                        "labeler_artifact_sha256": weak_supervision[
                            "labeler_artifact_sha256"
                        ],
                        "label_policy_sha256": weak_supervision["policy_sha256"],
                        "label_confidence": 0.75,
                        "labeled_at_utc": "2026-08-22T12:00:00+00:00",
                        "context_notes": "fixture",
                    }
                )
    records_path = tmp_path / "records.jsonl"
    records_path.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )
    manifest = {
        "schema": STATE_DATASET_SCHEMA,
        "model_id": STATE_MODEL_ID,
        "classes": {"0": "disaster_stressed", "1": "safe_walking"},
        "dataset_origin_is_label": False,
        "source_datasets": source_specs,
        "records_path": str(records_path.resolve()),
        "records_sha256": _hash(records_path),
        "crop_root": str(crops.resolve()),
        "crop_policy": {
            "context_margin_ratio": 0.35,
            "output_size_hw": [224, 224],
            "padding": "constant_black",
            "interpolation": "bilinear",
        },
        "split_policy": {
            "group_key": "source_group_id",
            "splits": ["train", "val", "test"],
            "test_used_for_selection": False,
        },
        "weak_supervision": weak_supervision,
        "counts": {
            split: {"disaster_stressed": 2, "safe_walking": 2}
            for split in ("train", "val", "test")
        },
        "created_at_utc": "2026-08-22T12:01:00+00:00",
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path, records


def _rewrite_records(manifest_path: Path, records: list[dict]) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    records_path = Path(manifest["records_path"])
    records_path.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )
    manifest["records_sha256"] = _hash(records_path)
    manifest["counts"] = {
        split: {
            label: sum(
                record["split"] == split and record["state_label"] == label
                for record in records
            )
            for label in ("disaster_stressed", "safe_walking")
        }
        for split in ("train", "val", "test")
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")


def test_accepts_complete_weakly_supervised_group_safe_dataset(tmp_path: Path) -> None:
    manifest, _ = _write_fixture(tmp_path)
    report = validate_state_dataset_manifest(manifest)
    assert report["record_count"] == 12
    assert report["weak_supervision_records"] == 12
    assert report["state_evidence_status"] == "weak_supervision_unverified"
    assert report["label_confidence_range"] == {"minimum": 0.75, "maximum": 0.75}
    assert report["dataset_origin_is_label"] is False
    assert report["counts"]["test"] == {
        "disaster_stressed": 2,
        "safe_walking": 2,
    }
    assert report["source_class_counts"]["c2a-v2"]["val"] == {
        "disaster_stressed": 1,
        "safe_walking": 1,
    }


def test_rejects_dataset_origin_as_label(tmp_path: Path) -> None:
    manifest, records = _write_fixture(tmp_path)
    records[0]["label_source"] = "dataset_origin"
    _rewrite_records(manifest, records)
    with pytest.raises(StateDatasetError, match="dataset origin cannot"):
        validate_state_dataset_manifest(manifest)


def test_rejects_missing_dataset_use_authorization_record(tmp_path: Path) -> None:
    manifest, _ = _write_fixture(tmp_path)
    value = json.loads(manifest.read_text(encoding="utf-8"))
    value["source_datasets"][0]["dataset_use_authorization_status"] = "pending"
    manifest.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(StateDatasetError, match="recorded use authorization"):
        validate_state_dataset_manifest(manifest)


def test_rejects_missing_automated_terms_recorder_identity(tmp_path: Path) -> None:
    manifest, _ = _write_fixture(tmp_path)
    value = json.loads(manifest.read_text(encoding="utf-8"))
    value["source_datasets"][0]["terms_recorded_by"] = ""
    manifest.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(StateDatasetError, match="terms_recorded_by"):
        validate_state_dataset_manifest(manifest)


def test_rejects_record_labeler_provenance_mismatch(tmp_path: Path) -> None:
    manifest, records = _write_fixture(tmp_path)
    records[0]["labeler_version"] = "different-version"
    _rewrite_records(manifest, records)
    with pytest.raises(StateDatasetError, match="provenance differs"):
        validate_state_dataset_manifest(manifest)


def test_rejects_cross_split_source_group_leakage(tmp_path: Path) -> None:
    manifest, records = _write_fixture(tmp_path)
    records[4]["source_group_id"] = records[0]["source_group_id"]
    _rewrite_records(manifest, records)
    with pytest.raises(StateDatasetError, match="source_group_id"):
        validate_state_dataset_manifest(manifest)


def test_rejects_pose_shortcut_for_safe_label(tmp_path: Path) -> None:
    manifest, records = _write_fixture(tmp_path)
    safe = next(record for record in records if record["state_label"] == "safe_walking")
    safe["state_reason"] = "upright"
    _rewrite_records(manifest, records)
    with pytest.raises(StateDatasetError, match="normal_walking_visual_cue"):
        validate_state_dataset_manifest(manifest)


def test_rejects_tampered_weak_supervision_artifact(tmp_path: Path) -> None:
    manifest, _ = _write_fixture(tmp_path)
    value = json.loads(manifest.read_text(encoding="utf-8"))
    Path(value["weak_supervision"]["labeler_artifact_path"]).write_bytes(b"tampered")
    with pytest.raises(StateDatasetError, match="labeler_artifact SHA-256 mismatch"):
        validate_state_dataset_manifest(manifest)


def test_rejects_tampered_weak_supervision_policy(tmp_path: Path) -> None:
    manifest, _ = _write_fixture(tmp_path)
    value = json.loads(manifest.read_text(encoding="utf-8"))
    Path(value["weak_supervision"]["policy_path"]).write_text(
        "{}", encoding="utf-8"
    )
    with pytest.raises(StateDatasetError, match="policy SHA-256 mismatch"):
        validate_state_dataset_manifest(manifest)


def test_rejects_missing_record_confidence(tmp_path: Path) -> None:
    manifest, records = _write_fixture(tmp_path)
    del records[0]["label_confidence"]
    _rewrite_records(manifest, records)
    with pytest.raises(StateDatasetError, match="fields differ"):
        validate_state_dataset_manifest(manifest)


def test_rejects_invalid_record_confidence(tmp_path: Path) -> None:
    manifest, records = _write_fixture(tmp_path)
    records[0]["label_confidence"] = 0.0
    _rewrite_records(manifest, records)
    with pytest.raises(StateDatasetError, match=r"must lie in \(0, 1\]"):
        validate_state_dataset_manifest(manifest)


def test_rejects_dataset_source_as_a_perfect_state_shortcut(tmp_path: Path) -> None:
    manifest, records = _write_fixture(tmp_path)
    origin_confounded = [
        record
        for record in records
        if (
            record["source_dataset_id"] == "c2a-v2"
            and record["state_label"] == "disaster_stressed"
        )
        or (
            record["source_dataset_id"] == "adilshamim8-people-detection-v1"
            and record["state_label"] == "safe_walking"
        )
    ]
    _rewrite_records(manifest, origin_confounded)
    with pytest.raises(StateDatasetError, match="source/class confounding"):
        validate_state_dataset_manifest(manifest)


def test_rejects_tampered_crop(tmp_path: Path) -> None:
    manifest, records = _write_fixture(tmp_path)
    Path(records[0]["crop_path"]).write_bytes(b"tampered")
    with pytest.raises(StateDatasetError, match="crop SHA-256 mismatch"):
        validate_state_dataset_manifest(manifest)


def test_rejects_duplicate_manifest_key(tmp_path: Path) -> None:
    manifest, _ = _write_fixture(tmp_path)
    text = manifest.read_text(encoding="utf-8")
    manifest.write_text(text[:-1] + ',"schema":"duplicate"}', encoding="utf-8")
    with pytest.raises(StateDatasetError, match="duplicate JSON key"):
        validate_state_dataset_manifest(manifest)
