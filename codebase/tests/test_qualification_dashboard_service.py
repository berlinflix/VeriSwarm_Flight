from __future__ import annotations

import json

import pytest

from tools.qualification_dashboard_service import (
    ServiceConfig,
    _write_once_json,
    enrich_evidence,
)


APPROVED = "a" * 64
TAMPERED = "b" * 64


def _evidence(*, case: str) -> dict:
    model_hash = APPROVED if case == "clean" else TAMPERED
    rejected = case == "model_swap"
    return {
        "pass": True,
        "actual": {
            "outcome": "REJECTED" if rejected else "ACCEPTED",
            "disputes": 2 if rejected else 0,
            "semantic_acks": 0 if rejected else 2,
        },
        "receipt": {"model_hash": model_hash},
        "authorization": {
            "allowed": False,
            "released": [0.0, 0.0, 0.0],
        },
    }


def test_model_swap_dashboard_proof_is_derived_from_actual_evidence():
    result = enrich_evidence(
        _evidence(case="model_swap"),
        contract={
            "approved_model_sha256": APPROVED,
            "tampered_model_sha256": TAMPERED,
        },
        case_name="model_swap",
        manifest_sha256="c" * 64,
    )
    proof = result["dashboard_proof"]
    assert proof["proof_valid"] is True
    assert proof["observed_model_sha256"] == TAMPERED
    assert proof["observed_is_approved"] is False
    assert proof["policy_reason"] == "model_hash_not_approved"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda row: row["receipt"].update(model_hash=APPROVED),
        lambda row: row["actual"].update(outcome="ACCEPTED"),
        lambda row: row["actual"].update(disputes=0),
        lambda row: row["authorization"].update(allowed=True),
        lambda row: row["authorization"].update(released=[1.0, 0.0, 0.0]),
        lambda row: row.update({"pass": False}),
    ],
)
def test_model_swap_dashboard_proof_fails_closed(mutation):
    evidence = _evidence(case="model_swap")
    mutation(evidence)
    result = enrich_evidence(
        evidence,
        contract={
            "approved_model_sha256": APPROVED,
            "tampered_model_sha256": TAMPERED,
        },
        case_name="model_swap",
        manifest_sha256="c" * 64,
    )
    assert result["dashboard_proof"]["proof_valid"] is False


def test_service_config_requires_optee_alpha_without_seed(tmp_path):
    public = tmp_path / "public"
    public.mkdir()
    for filename in ("clean-case.json", "model-swap-case.json", "contract.json"):
        (public / filename).write_text("{}\n", encoding="utf-8")
    manifest = tmp_path / "alpha.manifest.json"
    manifest.write_text(
        json.dumps({"nodes": {"alpha": {"backend": "optee"}}}) + "\n",
        encoding="utf-8",
    )
    config = ServiceConfig(manifest, public, tmp_path / "evidence", "x" * 32)
    config.validate()
    assert config.evidence_dir.is_dir()

    manifest.write_text(
        json.dumps({"nodes": {"alpha": {"backend": "optee", "seed": "secret"}}}) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="must not contain"):
        config.validate()


def test_dashboard_evidence_is_create_once(tmp_path):
    path = tmp_path / "run.dashboard.json"
    _write_once_json(path, {"proof": "first"})
    with pytest.raises(FileExistsError):
        _write_once_json(path, {"proof": "replacement"})
    assert json.loads(path.read_text(encoding="utf-8")) == {"proof": "first"}
