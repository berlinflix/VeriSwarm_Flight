from __future__ import annotations

import hashlib

import pytest

import rescue_training.deployment as deployment
import rescue_training.selection as selection_module
from rescue_training.artifact_io import canonical_json_bytes
from rescue_training.deployment import (
    CANDIDATE_QUALIFICATION_SCHEMA,
    CandidateQualification,
)
from rescue_training.selection import (
    VALIDATION_REPORT_SCHEMA,
    SelectionError,
    ValidationReport,
    freeze_validation_threshold,
    select_final_candidate,
)


HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
ENGINE_A = "d" * 64
ENGINE_B = "e" * 64
ENGINE_C = "f" * 64
PLAN_HASH = "9" * 64


@pytest.fixture(autouse=True)
def pure_selection_policy_fixture(monkeypatch):
    """Keep synthetic fixtures scoped to pure ranking-policy unit tests.

    Production selection always runs the saved evaluator evidence through the
    original verifier.  These legacy fixtures intentionally have no files and
    exist to exercise independent ranking/provenance policy.  Dedicated tests
    below restore the real verifier and prove they cannot cross the public
    qualification boundary.
    """

    original = selection_module._require_rederived_report
    monkeypatch.setattr(
        selection_module, "_require_rederived_report", lambda value: value
    )
    return original


def candidate_identity(candidate: str) -> tuple[str, list[int]]:
    if candidate.startswith("yolov8s"):
        architecture = "yolov8s.pt"
    else:
        architecture = "yolov8n.pt"
    size = 960 if candidate == "yolov8n-960" else 640
    return architecture, [1, 3, size, size]


def evidence_hash(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def inference_evidence(candidate: str, data_kind: str, input_shape: list[int]) -> dict:
    domain_root = f"/workspace/evaluation/{data_kind}"
    candidate_root = f"/workspace/evaluation/{candidate}/{data_kind}"
    return {
        "report_path": f"{candidate_root}/inference.json",
        "report_sha256": evidence_hash(candidate, data_kind, "report"),
        "ground_truth_records_path": f"{domain_root}/ground-truth.json",
        "ground_truth_records_sha256": evidence_hash(data_kind, "ground-truth"),
        "prediction_records_path": f"{candidate_root}/predictions.json",
        "prediction_records_sha256": evidence_hash(candidate, data_kind, "predictions"),
        "ordered_image_ids_sha256": evidence_hash(data_kind, "ordered-image-ids"),
        "preprocess": {
            "coordinate_space": "original_source_pixels",
            "resize": "ultralytics_letterbox",
            "input_shape_nchw": input_shape,
        },
    }


def validation_dict(
    candidate: str,
    data_kind: str,
    *,
    model_hash: str = HASH_A,
    split: str = "val",
    points: list[dict[str, float]] | None = None,
) -> dict:
    architecture, input_shape = candidate_identity(candidate)
    return {
        "schema": VALIDATION_REPORT_SCHEMA,
        "candidate": candidate,
        "candidate_stage": "smoke" if candidate.endswith("-smoke") else "full",
        "architecture": architecture,
        "input_shape_nchw": input_shape,
        "training_plan_sha256": PLAN_HASH,
        "split": split,
        "class_map": {"0": "person_candidate"},
        "data_kind": data_kind,
        "dataset_id": f"dataset-{data_kind}",
        "dataset_sha256": ("1" if data_kind == "real_aerial" else "2") * 64,
        "model_sha256": model_hash,
        "inference_evidence": inference_evidence(candidate, data_kind, input_shape),
        "small_person_definition": {
            "basis": "bbox_area_original_source_pixels",
            "max_area_px2": 1024,
            "inclusive": True,
        },
        "aggregate": {"mAP50": 0.80},
        "threshold_points": points
        or [
            {
                "threshold": 0.20,
                "precision": 0.70,
                "recall": 0.82,
                "small_person_recall": 0.68,
                "mAP50": 0.80,
            }
        ],
    }


def qualification(
    candidate: str,
    model_hash: str,
    engine_hash: str,
    *,
    qualified: bool = True,
    threshold: float = 0.20,
    issued: bool = True,
) -> CandidateQualification:
    architecture, input_shape = candidate_identity(candidate)
    result = CandidateQualification(
        schema=CANDIDATE_QUALIFICATION_SCHEMA,
        report_id=f"qualification-{candidate}",
        candidate=candidate,
        architecture=architecture,
        input_shape_nchw=tuple(input_shape),
        training_plan_sha256=PLAN_HASH,
        confidence_threshold=threshold,
        source_pt_sha256=model_hash,
        onnx_sha256="7" * 64,
        executed_engine_path=f"C:\\jetson\\models\\{candidate}.engine",
        executed_engine_sha256=engine_hash,
        training_completion_report_sha256="3" * 64,
        threshold_selection_report_sha256="4" * 64,
        onnx_report_sha256="5" * 64,
        engine_identity_report_sha256="6" * 64,
        validation_accuracy_equivalence_report_sha256="7" * 64,
        nano_benchmark_report_sha256="8" * 64,
        runtime={"hardware": "Jetson Orin Nano 8GB", "power_mode": "15W"},
        qualified=qualified,
    )
    if issued:
        # Unit-test stand-in for deployment's fully evidenced builder. A
        # separate test proves an ordinary public construction is rejected.
        object.__setattr__(
            result, "_issuance_token", deployment._QUALIFICATION_ISSUANCE_TOKEN
        )
    return result


def report(candidate: str, kind: str, model_hash: str, **kwargs) -> ValidationReport:
    return ValidationReport.from_dict(
        validation_dict(candidate, kind, model_hash=model_hash, **kwargs)
    )


def mandatory_reports() -> list[ValidationReport]:
    return [
        report("yolov8n-640", "real_aerial", HASH_A),
        report("yolov8n-640", "synthetic_disaster", HASH_A),
        report("yolov8n-960", "real_aerial", HASH_B),
        report("yolov8n-960", "synthetic_disaster", HASH_B),
    ]


def mandatory_qualifications() -> list[CandidateQualification]:
    return [
        qualification("yolov8n-640", HASH_A, ENGINE_A),
        qualification("yolov8n-960", HASH_B, ENGINE_B),
    ]


def test_threshold_selection_rejects_test_data() -> None:
    test_report = report(
        "yolov8n-640", "real_aerial", HASH_A, split="test"
    )
    with pytest.raises(SelectionError, match="validation only"):
        freeze_validation_threshold(test_report)
    with pytest.raises(SelectionError, match="test reports are forbidden"):
        select_final_candidate([test_report], mandatory_qualifications())


def test_real_and_synthetic_reports_are_both_required_and_not_averaged() -> None:
    reports = mandatory_reports()
    reports.pop(1)
    with pytest.raises(SelectionError, match="separate provenance"):
        select_final_candidate(reports, mandatory_qualifications())


def test_missing_mandatory_candidate_fails_closed() -> None:
    reports = mandatory_reports()[:2]
    with pytest.raises(SelectionError, match="mandatory full candidates"):
        select_final_candidate(reports, mandatory_qualifications()[:1])


def test_failed_actual_jetson_gate_excludes_more_accurate_candidate() -> None:
    strong = [{
        "threshold": 0.20,
        "precision": 0.95,
        "recall": 0.97,
        "small_person_recall": 0.93,
        "mAP50": 0.80,
    }]
    reports = mandatory_reports()
    reports[2] = report("yolov8n-960", "real_aerial", HASH_B, points=strong)
    reports[3] = report("yolov8n-960", "synthetic_disaster", HASH_B, points=strong)
    qualifications = [
        qualification("yolov8n-640", HASH_A, ENGINE_A),
        qualification("yolov8n-960", HASH_B, ENGINE_B, qualified=False),
    ]
    selected = select_final_candidate(reports, qualifications)
    assert selected.candidate == "yolov8n-640"
    assert selected.executed_engine_sha256 == ENGINE_A


def test_highest_validation_accuracy_wins_when_both_pass_jetson() -> None:
    strong = [{
        "threshold": 0.20,
        "precision": 0.90,
        "recall": 0.91,
        "small_person_recall": 0.84,
        "mAP50": 0.80,
    }]
    reports = mandatory_reports()
    reports[2] = report("yolov8n-960", "real_aerial", HASH_B, points=strong)
    reports[3] = report("yolov8n-960", "synthetic_disaster", HASH_B, points=strong)
    selected = select_final_candidate(
        reversed(reports), reversed(mandatory_qualifications())
    )
    assert selected.candidate == "yolov8n-960"
    assert selected.executed_engine_sha256 == ENGINE_B


def test_accuracy_beats_speed_and_ties_are_stable() -> None:
    # Jetson latency is intentionally absent from ranking. Equal metrics choose
    # lower threshold and then stable candidate name.
    tied_points = [
        {
            "threshold": 0.10,
            "precision": 0.75,
            "recall": 0.85,
            "small_person_recall": 0.70,
            "mAP50": 0.80,
        },
        {
            "threshold": 0.20,
            "precision": 0.75,
            "recall": 0.85,
            "small_person_recall": 0.70,
            "mAP50": 0.80,
        },
    ]
    reports = [
        report(candidate, kind, model_hash, points=tied_points)
        for candidate, model_hash in (
            ("yolov8n-640", HASH_A),
            ("yolov8n-960", HASH_B),
        )
        for kind in ("real_aerial", "synthetic_disaster")
    ]
    qualifications = [
        qualification("yolov8n-640", HASH_A, ENGINE_A, threshold=0.10),
        qualification("yolov8n-960", HASH_B, ENGINE_B, threshold=0.10),
    ]
    selected = select_final_candidate(reports, qualifications)
    assert selected.candidate == "yolov8n-640"
    assert selected.confidence_threshold == 0.10


def test_final_selection_is_stably_serializable_and_proves_mandatory_pool() -> None:
    reports = mandatory_reports()
    first = select_final_candidate(reports, mandatory_qualifications())
    second = select_final_candidate(
        reversed(reports), reversed(mandatory_qualifications())
    )
    assert first.to_dict() == second.to_dict()
    assert first.sha256 == second.sha256
    assert len(first.sha256) == 64
    considered = {
        item["candidate"] for item in first.to_dict()["considered_candidates"]
    }
    assert considered == {"yolov8n-640", "yolov8n-960"}
    for item in first.to_dict()["considered_candidates"]:
        assert "validation_accuracy_equivalence" in item["report_hashes"]
        assert "untouched_test_accuracy" not in item["report_hashes"]
    assert first.to_dict()["validation"]["real_aerial"]["data_kind"] == "real_aerial"
    assert (
        first.to_dict()["validation"]["synthetic_disaster"]["data_kind"]
        == "synthetic_disaster"
    )
    for domain in ("real_aerial", "synthetic_disaster"):
        evidence = first.to_dict()["validation"][domain]
        assert evidence["inference_evidence"]["preprocess"]["coordinate_space"] == (
            "original_source_pixels"
        )
        assert evidence["inference_evidence_sha256"] == hashlib.sha256(
            canonical_json_bytes(evidence["inference_evidence"])
        ).hexdigest()


def test_qualification_must_bind_deterministic_validation_threshold() -> None:
    qualifications = mandatory_qualifications()
    qualifications[0] = qualification(
        "yolov8n-640", HASH_A, ENGINE_A, threshold=0.30
    )
    with pytest.raises(SelectionError, match="deterministic validation-selected"):
        select_final_candidate(mandatory_reports(), qualifications)


@pytest.mark.parametrize("state_label", ["safe", "safe_walking", "disaster"])
def test_green_red_state_labels_are_rejected_from_detector_class_map(
    state_label: str,
) -> None:
    value = validation_dict("yolov8n-640", "real_aerial")
    value["class_map"] = {"0": "person_candidate", "1": state_label}
    with pytest.raises(SelectionError, match="separate classifier"):
        ValidationReport.from_dict(value)


def test_smoke_candidate_is_never_selectable() -> None:
    smoke = report("yolov8n-640-smoke", "real_aerial", HASH_A)
    with pytest.raises(SelectionError, match="smoke candidate"):
        freeze_validation_threshold(smoke)


def test_accuracy_gates_include_small_person_recall_and_map50() -> None:
    value = validation_dict("yolov8n-640", "real_aerial")
    value["aggregate"]["mAP50"] = 0.69
    value["threshold_points"][0]["mAP50"] = 0.69
    parsed = ValidationReport.from_dict(value)
    with pytest.raises(SelectionError, match="no threshold passing"):
        freeze_validation_threshold(parsed)

    value = validation_dict("yolov8n-640", "real_aerial")
    value["threshold_points"][0]["small_person_recall"] = 0.59
    parsed = ValidationReport.from_dict(value)
    with pytest.raises(SelectionError, match="no threshold passing"):
        freeze_validation_threshold(parsed)


def test_raw_self_attested_qualification_mapping_is_rejected() -> None:
    raw = qualification("yolov8n-640", HASH_A, ENGINE_A).to_dict()
    with pytest.raises(SelectionError, match="sealed deployment-validated"):
        select_final_candidate(mandatory_reports(), [raw])


def test_directly_constructed_candidate_qualification_is_rejected() -> None:
    unsealed = qualification(
        "yolov8n-640", HASH_A, ENGINE_A, issued=False
    )
    with pytest.raises(SelectionError, match="sealed deployment-validated"):
        select_final_candidate(mandatory_reports(), [unsealed])


def test_public_selection_rejects_caller_fabricated_metrics_and_nonexistent_paths(
    monkeypatch, pure_selection_policy_fixture
) -> None:
    monkeypatch.setattr(
        selection_module,
        "_require_rederived_report",
        pure_selection_policy_fixture,
    )
    fabricated = mandatory_reports()
    with pytest.raises(SelectionError, match="not sealed evaluator evidence"):
        select_final_candidate(fabricated, mandatory_qualifications())


def test_public_threshold_freeze_rejects_structurally_valid_unsealed_report(
    monkeypatch, pure_selection_policy_fixture
) -> None:
    monkeypatch.setattr(
        selection_module,
        "_require_rederived_report",
        pure_selection_policy_fixture,
    )
    fabricated = report("yolov8n-640", "real_aerial", HASH_A)
    with pytest.raises(SelectionError, match="caller-constructed"):
        freeze_validation_threshold(fabricated)


def test_strict_json_rejects_duplicate_fields_and_unknown_fields() -> None:
    text = '{"schema":"x","schema":"y"}'
    with pytest.raises(SelectionError, match="duplicate JSON"):
        ValidationReport.from_json_text(text)
    value = validation_dict("yolov8n-640", "real_aerial")
    value["unexpected"] = True
    with pytest.raises(SelectionError, match="unknown"):
        ValidationReport.from_dict(value)


def test_mismatched_model_hashes_fail_closed() -> None:
    reports = mandatory_reports()
    reports[1] = report("yolov8n-640", "synthetic_disaster", HASH_C)
    with pytest.raises(SelectionError, match="different model hashes"):
        select_final_candidate(reports, mandatory_qualifications())


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("architecture", "yolov8s.pt"),
        ("input_shape_nchw", [1, 3, 960, 960]),
    ],
)
def test_candidate_name_is_bound_to_architecture_and_shape(
    field: str, replacement: object
) -> None:
    value = validation_dict("yolov8n-640", "real_aerial")
    value[field] = replacement
    with pytest.raises(SelectionError, match="must bind architecture"):
        ValidationReport.from_dict(value)


def test_compared_candidates_must_use_identical_provenance_datasets() -> None:
    reports = mandatory_reports()
    altered = validation_dict(
        "yolov8n-960", "real_aerial", model_hash=HASH_B
    )
    altered["dataset_id"] = "different-real-validation"
    reports[2] = ValidationReport.from_dict(altered)
    with pytest.raises(SelectionError, match="identical real_aerial"):
        select_final_candidate(reports, mandatory_qualifications())


def test_compared_candidates_must_use_identical_ground_truth_evidence() -> None:
    reports = mandatory_reports()
    altered = validation_dict(
        "yolov8n-960", "real_aerial", model_hash=HASH_B
    )
    altered["inference_evidence"]["ground_truth_records_sha256"] = "0" * 64
    reports[2] = ValidationReport.from_dict(altered)
    with pytest.raises(SelectionError, match="ground-truth/ordered-image evidence"):
        select_final_candidate(reports, mandatory_qualifications())


def test_validation_report_requires_strict_bound_inference_evidence() -> None:
    value = validation_dict("yolov8n-640", "real_aerial")
    value["inference_evidence"]["report_path"] = "relative/inference.json"
    with pytest.raises(SelectionError, match="absolute path"):
        ValidationReport.from_dict(value)

    value = validation_dict("yolov8n-640", "real_aerial")
    value["inference_evidence"]["preprocess"]["input_shape_nchw"] = [1, 3, 960, 960]
    with pytest.raises(SelectionError, match="must match the candidate input shape"):
        ValidationReport.from_dict(value)

    value = validation_dict("yolov8n-640", "real_aerial")
    del value["inference_evidence"]["prediction_records_sha256"]
    with pytest.raises(SelectionError, match="fields mismatch"):
        ValidationReport.from_dict(value)

    value = validation_dict("yolov8n-640", "real_aerial")
    value["small_person_definition"]["basis"] = (
        "bbox_area_pixels_at_evaluation_resolution"
    )
    with pytest.raises(SelectionError, match="original source pixels"):
        ValidationReport.from_dict(value)


def test_inference_report_cannot_be_reused_across_candidate_or_domain_claims() -> None:
    reports = mandatory_reports()
    altered = validation_dict(
        "yolov8n-960", "synthetic_disaster", model_hash=HASH_B
    )
    altered["inference_evidence"]["report_sha256"] = (
        reports[0].inference_evidence.report_sha256
    )
    reports[3] = ValidationReport.from_dict(altered)
    with pytest.raises(SelectionError, match="distinct inference evidence"):
        select_final_candidate(reports, mandatory_qualifications())


def test_compared_candidates_must_bind_one_training_plan_hash() -> None:
    reports = mandatory_reports()
    altered = validation_dict(
        "yolov8n-960", "synthetic_disaster", model_hash=HASH_B
    )
    altered["training_plan_sha256"] = "8" * 64
    reports[3] = ValidationReport.from_dict(altered)
    with pytest.raises(SelectionError, match="one training plan"):
        select_final_candidate(reports, mandatory_qualifications())


def test_real_and_synthetic_dataset_hashes_must_differ() -> None:
    reports = mandatory_reports()
    for index in (1, 3):
        value = validation_dict(
            reports[index].candidate,
            "synthetic_disaster",
            model_hash=reports[index].model_sha256,
        )
        value["dataset_sha256"] = "1" * 64
        reports[index] = ValidationReport.from_dict(value)
    with pytest.raises(SelectionError, match="dataset hashes must differ"):
        select_final_candidate(reports, mandatory_qualifications())


def test_synthetic_metrics_rank_before_threshold_tie_break() -> None:
    # Both candidates tie on real metrics. n960 has stronger synthetic metrics
    # at a higher threshold, so it must win before threshold is considered.
    reports = mandatory_reports()
    lower_real = [{
        "threshold": 0.10,
        "precision": 0.75,
        "recall": 0.85,
        "small_person_recall": 0.70,
        "mAP50": 0.80,
    }]
    higher_real = [dict(lower_real[0], threshold=0.20)]
    lower_synth = [dict(lower_real[0], recall=0.80)]
    higher_synth = [dict(higher_real[0], recall=0.90)]
    reports[0] = report("yolov8n-640", "real_aerial", HASH_A, points=lower_real)
    reports[1] = report(
        "yolov8n-640", "synthetic_disaster", HASH_A, points=lower_synth
    )
    reports[2] = report("yolov8n-960", "real_aerial", HASH_B, points=higher_real)
    reports[3] = report(
        "yolov8n-960", "synthetic_disaster", HASH_B, points=higher_synth
    )
    qualifications = [
        qualification("yolov8n-640", HASH_A, ENGINE_A, threshold=0.10),
        qualification("yolov8n-960", HASH_B, ENGINE_B, threshold=0.20),
    ]
    selected = select_final_candidate(reports, qualifications)
    assert selected.candidate == "yolov8n-960"
