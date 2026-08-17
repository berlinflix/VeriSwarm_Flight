from eval.run_all import _merged_manifest


def test_partial_eval_manifest_preserves_unselected_evidence(tmp_path, monkeypatch):
    from eval import harness

    monkeypatch.setattr(harness, "RESULTS_DIR", tmp_path)
    (tmp_path / "manifest.csv").write_text(
        "run,feeds,csv,status,n_rows\n"
        "R7,Table 4.8,isolation.csv,filled,1\n"
        "R9,old,overhead.csv,filled,5\n"
    )

    refreshed = [{
        "run": "R9",
        "feeds": "Table 4.10",
        "csv": "overhead.csv",
        "status": "filled",
        "n_rows": 5,
    }]
    merged = _merged_manifest(refreshed, ["R9"], partial_run=True)

    assert [row["run"] for row in merged] == ["R7", "R9"]
    assert merged[1]["feeds"] == "Table 4.10"


def test_full_eval_manifest_replaces_prior_evidence():
    rows = [{"run": "R1"}]
    assert _merged_manifest(rows, ["R1"], partial_run=False) is rows
