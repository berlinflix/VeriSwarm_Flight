from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import rescue_training.state_weak_labeler as weak_labeler
from rescue_training.state_dataset import validate_state_dataset_manifest
from rescue_training.state_weak_labeler import (
    ContextCrop,
    HAZARD_CATEGORIES,
    POSTURE_CATEGORIES,
    WEAK_LABEL_CANDIDATE_SCHEMA,
    WeakLabelerError,
    decide_teacher_signals,
    generate_state_weak_labels,
)


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _distribution(
    categories: tuple[str, ...], top: str, probability: float = 0.90
) -> dict[str, float]:
    remainder = (1.0 - probability) / (len(categories) - 1)
    return {
        category: probability if category == top else remainder
        for category in categories
    }


SAFE_POSTURE = _distribution(POSTURE_CATEGORIES, "upright_walking")
SAFE_HAZARD = _distribution(HAZARD_CATEGORIES, "no_hazard")
DISASTER_POSTURE = _distribution(POSTURE_CATEGORIES, "fallen")
DISASTER_HAZARD = _distribution(HAZARD_CATEGORIES, "debris")


class _PostureTeacher:
    model_id = "posture-teacher-v1"
    model_version = "1.0.0"
    deterministic = True
    inference_configuration = {
        "device": "cuda:0",
        "precision": "fp32",
        "test_double": True,
    }

    def __init__(self, artifact: Path) -> None:
        self.model_artifact_path = artifact
        self.inputs: list[ContextCrop] = []

    def predict_posture(self, crop: ContextCrop) -> dict[str, float]:
        self.inputs.append(crop)
        if crop.png_bytes.startswith(b"safe"):
            return dict(SAFE_POSTURE)
        if crop.png_bytes.startswith(b"disaster"):
            return dict(DISASTER_POSTURE)
        return {
            "upright_walking": 0.40,
            "fallen": 0.40,
            "trapped": 0.05,
            "injured": 0.05,
            "partially_buried": 0.05,
            "other_or_uncertain": 0.05,
        }


class _HazardTeacher:
    model_id = "hazard-teacher-v1"
    model_version = "1.0.0"
    deterministic = True
    inference_configuration = {
        "device": "cuda:0",
        "precision": "fp32",
        "test_double": True,
    }

    def __init__(self, artifact: Path) -> None:
        self.model_artifact_path = artifact
        self.inputs: list[ContextCrop] = []

    def predict_hazard(self, crop: ContextCrop) -> dict[str, float]:
        self.inputs.append(crop)
        if crop.png_bytes.startswith(b"safe"):
            return dict(SAFE_HAZARD)
        if crop.png_bytes.startswith(b"disaster"):
            return dict(DISASTER_HAZARD)
        return {
            "no_hazard": 0.40,
            "flood": 0.40,
            "fire": 0.10,
            "debris": 0.05,
            "other_or_uncertain": 0.05,
        }


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text(
        "".join(
            json.dumps(record, allow_nan=False, sort_keys=True) + "\n"
            for record in records
        ),
        encoding="utf-8",
    )


def _fixture(tmp_path: Path) -> SimpleNamespace:
    repository = tmp_path / "repository"
    workspace = tmp_path / "workspace"
    repository.mkdir()
    workspace.mkdir()
    archives = workspace / "archives"
    media = workspace / "media"
    models = workspace / "models"
    archives.mkdir()
    media.mkdir()
    models.mkdir()

    posture_artifact = models / "posture.onnx"
    hazard_artifact = models / "hazard.onnx"
    detector_artifact = models / "person.pt"
    posture_artifact.write_bytes(b"posture-teacher-model")
    hazard_artifact.write_bytes(b"hazard-teacher-model")
    detector_artifact.write_bytes(b"person-detector-model")

    sources: list[dict] = []
    for dataset_id in ("c2a-v2", "adilshamim8-people-detection-v1"):
        archive = archives / f"{dataset_id}.zip"
        archive.write_bytes(f"archive:{dataset_id}".encode())
        sources.append(
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
    source_ids = ("c2a-v2", "adilshamim8-people-detection-v1")
    for split_index, split in enumerate(("train", "val", "test")):
        for source_index, dataset_id in enumerate(source_ids):
            for label in ("disaster", "safe"):
                sample_id = f"{split}-{source_index}-{label}"
                source = media / f"{sample_id}.frame"
                source.write_bytes(f"{label}:{sample_id}".encode())
                records.append(
                    {
                        "schema": WEAK_LABEL_CANDIDATE_SCHEMA,
                        "sample_id": sample_id,
                        "split": split,
                        "source_dataset_id": dataset_id,
                        "source_family_id": f"camera-family-{source_index}",
                        "source_group_id": (
                            f"scene-{split_index}-{source_index}-{label}"
                        ),
                        "source_media_path": str(source.resolve()),
                        "source_media_sha256": _hash(source),
                        "frame_index": None,
                        "detector_label": "person_candidate",
                        "detector_confidence": 0.91,
                        "detector_model_id": "sar-rgb-person-v1",
                        "detector_model_path": str(detector_artifact.resolve()),
                        "detector_model_sha256": _hash(detector_artifact),
                        "person_bbox_xyxy_abs": [2, 2, 12, 14],
                    }
                )

    ambiguous_source = media / "ambiguous.frame"
    ambiguous_source.write_bytes(b"ambiguous:visual-content")
    records.append(
        {
            "schema": WEAK_LABEL_CANDIDATE_SCHEMA,
            "sample_id": "train-ambiguous-extra",
            "split": "train",
            "source_dataset_id": "c2a-v2",
            "source_family_id": "camera-family-0",
            "source_group_id": "scene-ambiguous-extra",
            "source_media_path": str(ambiguous_source.resolve()),
            "source_media_sha256": _hash(ambiguous_source),
            "frame_index": None,
            "detector_label": "person_candidate",
            "detector_confidence": 0.88,
            "detector_model_id": "sar-rgb-person-v1",
            "detector_model_path": str(detector_artifact.resolve()),
            "detector_model_sha256": _hash(detector_artifact),
            "person_bbox_xyxy_abs": [1, 1, 10, 12],
        }
    )
    candidate_path = workspace / "person-candidates.jsonl"
    _write_jsonl(candidate_path, records)
    return SimpleNamespace(
        repository=repository,
        workspace=workspace,
        candidate_path=candidate_path,
        records=records,
        sources=sources,
        posture_artifact=posture_artifact,
        hazard_artifact=hazard_artifact,
    )


@pytest.fixture
def origin_blind_cropper(monkeypatch: pytest.MonkeyPatch) -> None:
    def crop(candidate: dict) -> tuple[ContextCrop, list[int]]:
        payload = Path(candidate["source_media_path"]).read_bytes()
        return (
            ContextCrop(
                png_bytes=payload,
                width=224,
                height=224,
                sha256=hashlib.sha256(payload).hexdigest(),
            ),
            [0, 0, 16, 16],
        )

    monkeypatch.setattr(weak_labeler, "_make_context_crop", crop)


def _generate(
    fixture: SimpleNamespace,
    output_name: str,
) -> tuple[dict, _PostureTeacher, _HazardTeacher]:
    posture = _PostureTeacher(fixture.posture_artifact)
    hazard = _HazardTeacher(fixture.hazard_artifact)
    report = generate_state_weak_labels(
        candidate_records_path=fixture.candidate_path,
        expected_candidate_records_sha256=_hash(fixture.candidate_path),
        source_datasets=fixture.sources,
        posture_teacher=posture,
        hazard_teacher=hazard,
        output_root=fixture.workspace / output_name,
        workspace_root=fixture.workspace,
        repository_root=fixture.repository,
        now=lambda: "2026-08-22T12:00:00+00:00",
    )
    return report, posture, hazard


def _decisions(report: dict) -> list[dict]:
    path = Path(report["decision_evidence"]["path"])
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_frozen_consensus_maps_safe_and_disaster() -> None:
    safe = decide_teacher_signals(SAFE_POSTURE, SAFE_HAZARD)
    assert safe.state_label == "safe_walking"
    assert safe.state_reason == "normal_walking_visual_cue"
    assert safe.runtime_box_color == "green"
    assert safe.confidence == pytest.approx(0.90)

    disaster = decide_teacher_signals(DISASTER_POSTURE, DISASTER_HAZARD)
    assert disaster.state_label == "disaster_stressed"
    assert disaster.state_reason == "fallen"
    assert disaster.runtime_box_color == "red"
    assert disaster.confidence == pytest.approx(0.90)


def test_ambiguous_or_disagreeing_teachers_abstain_red_unverified() -> None:
    ambiguous = decide_teacher_signals(
        {
            "upright_walking": 0.40,
            "fallen": 0.40,
            "trapped": 0.05,
            "injured": 0.05,
            "partially_buried": 0.05,
            "other_or_uncertain": 0.05,
        },
        SAFE_HAZARD,
    )
    assert ambiguous.abstained
    assert ambiguous.training_action == "exclude"
    assert ambiguous.runtime_box_color == "red"
    assert ambiguous.runtime_status == "UNVERIFIED"

    disagreement = decide_teacher_signals(SAFE_POSTURE, DISASTER_HAZARD)
    assert disagreement.abstained
    assert disagreement.state_reason == "teacher_disagreement"
    assert disagreement.runtime_status == "UNVERIFIED"


def test_generation_is_v2_compatible_records_full_evidence_and_excludes_abstain(
    tmp_path: Path,
    origin_blind_cropper: None,
) -> None:
    fixture = _fixture(tmp_path)
    report, posture, hazard = _generate(fixture, "weak-label-run")

    assert report["passed"] is True
    assert report["human_review_performed"] is False
    assert report["dataset_origin_is_label"] is False
    assert report["decision_evidence"]["retained_count"] == 12
    assert report["decision_evidence"]["abstained_count"] == 1
    assert report["source_family_coverage"] == {
        "disaster_stressed": ["camera-family-0", "camera-family-1"],
        "safe_walking": ["camera-family-0", "camera-family-1"],
    }
    manifest = Path(report["dataset_manifest"]["path"])
    state_report = validate_state_dataset_manifest(manifest)
    assert state_report["record_count"] == 12
    assert state_report["counts"]["test"] == {
        "disaster_stressed": 2,
        "safe_walking": 2,
    }

    decisions = _decisions(report)
    abstained = next(item for item in decisions if item["decision"]["state_label"] is None)
    assert abstained["decision"]["training_action"] == "exclude"
    assert abstained["decision"]["runtime_presentation"] == {
        "box_color": "red",
        "status": "UNVERIFIED",
    }
    assert Path(abstained["context_crop"]["path"]).is_file()
    retained = next(
        item for item in decisions if item["decision"]["state_label"] == "safe_walking"
    )
    assert retained["teachers"]["posture_mobility"]["artifact_sha256"] == _hash(
        fixture.posture_artifact
    )
    assert retained["teachers"]["scene_hazard_context"]["artifact_sha256"] == _hash(
        fixture.hazard_artifact
    )
    assert set(retained["teachers"]["posture_mobility"]["probabilities"]) == set(
        POSTURE_CATEGORIES
    )
    assert retained["context_crop"]["sha256"] == _hash(
        Path(retained["context_crop"]["path"])
    )
    assert retained["policy"]["sha256"] == report["policy"]["sha256"]
    assert retained["labeled_at_utc"] == "2026-08-22T12:00:00+00:00"

    # The protocol boundary exposes visual bytes and dimensions only.
    assert posture.inputs and hazard.inputs
    for teacher_input in [*posture.inputs, *hazard.inputs]:
        assert not hasattr(teacher_input, "source_dataset_id")
        assert not hasattr(teacher_input, "source_family_id")
        assert not hasattr(teacher_input, "source_media_path")
        assert not hasattr(teacher_input, "sample_id")


def test_changing_dataset_origin_cannot_change_any_visual_decision(
    tmp_path: Path,
    origin_blind_cropper: None,
) -> None:
    fixture = _fixture(tmp_path)
    first, _, _ = _generate(fixture, "origin-a")
    first_decisions = {
        item["sample_id"]: (
            item["decision"],
            item["decision_id"],
            item["teachers"]["posture_mobility"]["probabilities"],
            item["teachers"]["scene_hazard_context"]["probabilities"],
        )
        for item in _decisions(first)
    }

    swapped = []
    for record in fixture.records:
        changed = dict(record)
        changed["source_dataset_id"] = (
            "adilshamim8-people-detection-v1"
            if record["source_dataset_id"] == "c2a-v2"
            else "c2a-v2"
        )
        swapped.append(changed)
    swapped_path = fixture.workspace / "person-candidates-origin-swapped.jsonl"
    _write_jsonl(swapped_path, swapped)
    fixture.candidate_path = swapped_path
    second, _, _ = _generate(fixture, "origin-b")
    second_decisions = {
        item["sample_id"]: (
            item["decision"],
            item["decision_id"],
            item["teachers"]["posture_mobility"]["probabilities"],
            item["teachers"]["scene_hazard_context"]["probabilities"],
        )
        for item in _decisions(second)
    }
    assert second_decisions == first_decisions


def test_rejects_same_hashed_artifact_for_both_teachers(
    tmp_path: Path,
    origin_blind_cropper: None,
) -> None:
    fixture = _fixture(tmp_path)
    posture = _PostureTeacher(fixture.posture_artifact)
    hazard = _HazardTeacher(fixture.posture_artifact)
    with pytest.raises(WeakLabelerError, match="distinct SHA-256"):
        generate_state_weak_labels(
            candidate_records_path=fixture.candidate_path,
            expected_candidate_records_sha256=_hash(fixture.candidate_path),
            source_datasets=fixture.sources,
            posture_teacher=posture,
            hazard_teacher=hazard,
            output_root=fixture.workspace / "same-teacher-artifact",
            workspace_root=fixture.workspace,
            repository_root=fixture.repository,
        )


def test_rejects_teacher_that_changes_probabilities_for_the_same_crop(
    tmp_path: Path,
    origin_blind_cropper: None,
) -> None:
    fixture = _fixture(tmp_path)

    class NondeterministicPosture(_PostureTeacher):
        def predict_posture(self, crop: ContextCrop) -> dict[str, float]:
            self.inputs.append(crop)
            return (
                dict(SAFE_POSTURE)
                if len(self.inputs) % 2
                else dict(DISASTER_POSTURE)
            )

    posture = NondeterministicPosture(fixture.posture_artifact)
    hazard = _HazardTeacher(fixture.hazard_artifact)
    with pytest.raises(WeakLabelerError, match="nondeterministic"):
        generate_state_weak_labels(
            candidate_records_path=fixture.candidate_path,
            expected_candidate_records_sha256=_hash(fixture.candidate_path),
            source_datasets=fixture.sources,
            posture_teacher=posture,
            hazard_teacher=hazard,
            output_root=fixture.workspace / "nondeterministic-teacher",
            workspace_root=fixture.workspace,
            repository_root=fixture.repository,
        )


def test_rejects_cross_split_group_before_labeling(
    tmp_path: Path,
    origin_blind_cropper: None,
) -> None:
    fixture = _fixture(tmp_path)
    fixture.records[8]["source_group_id"] = fixture.records[0]["source_group_id"]
    _write_jsonl(fixture.candidate_path, fixture.records)
    with pytest.raises(WeakLabelerError, match="cross-split leakage"):
        _generate(fixture, "group-leak")


def test_rejects_single_source_family_per_class(
    tmp_path: Path,
    origin_blind_cropper: None,
) -> None:
    fixture = _fixture(tmp_path)
    for record in fixture.records:
        record["source_family_id"] = "one-family"
    _write_jsonl(fixture.candidate_path, fixture.records)
    with pytest.raises(WeakLabelerError, match="span at least 2"):
        _generate(fixture, "one-family")


def test_generation_is_create_once(
    tmp_path: Path,
    origin_blind_cropper: None,
) -> None:
    fixture = _fixture(tmp_path)
    _generate(fixture, "create-once")
    with pytest.raises(WeakLabelerError, match="refusing to overwrite output root"):
        _generate(fixture, "create-once")


def test_rejects_probability_vectors_that_are_not_complete() -> None:
    incomplete = dict(SAFE_POSTURE)
    incomplete.pop("fallen")
    with pytest.raises(WeakLabelerError, match="exactly the frozen categories"):
        decide_teacher_signals(incomplete, SAFE_HAZARD)
