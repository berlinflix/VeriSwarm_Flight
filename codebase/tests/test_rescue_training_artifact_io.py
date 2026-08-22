from __future__ import annotations

import hashlib
import json
from types import MappingProxyType

import pytest

from rescue_training.artifact_io import (
    ArtifactIOError,
    atomic_create_json,
    canonical_json_bytes,
    create_external_json,
    require_external_workspace,
    require_path_within_workspace,
    sha256_file,
)


def test_streaming_sha256_and_chunk_validation(tmp_path):
    source = tmp_path / "payload.bin"
    source.write_bytes(b"rescue-person-model")

    assert sha256_file(source, chunk_size=3) == hashlib.sha256(
        b"rescue-person-model"
    ).hexdigest()
    with pytest.raises(ArtifactIOError, match="positive integer"):
        sha256_file(source, chunk_size=0)


def test_external_workspace_rejects_repo_overlap_and_escape(tmp_path):
    repository = (tmp_path / "repo").resolve()
    repository.mkdir()
    workspace = (tmp_path / "external" / "model-work").resolve()

    assert require_external_workspace(workspace, repository) == workspace
    assert require_path_within_workspace(workspace / "manifests" / "a.json", workspace) == (
        workspace / "manifests" / "a.json"
    )
    with pytest.raises(ArtifactIOError, match="separate from repository"):
        require_external_workspace(repository / "artifacts", repository)
    with pytest.raises(ArtifactIOError, match="escapes external workspace"):
        require_path_within_workspace(tmp_path / "elsewhere.json", workspace)
    with pytest.raises(ArtifactIOError, match="below, not equal"):
        require_path_within_workspace(workspace, workspace)


def test_atomic_json_is_canonical_create_once_and_preserves_existing_bytes(tmp_path):
    target = (tmp_path / "manifests" / "intake.json").resolve()
    atomic_create_json(target, {"z": 1, "a": {"ok": True}})

    assert target.read_bytes() == (
        b'{\n  "a": {\n    "ok": true\n  },\n  "z": 1\n}\n'
    )
    original = target.read_bytes()
    with pytest.raises(ArtifactIOError, match="refusing to overwrite"):
        atomic_create_json(target, {"replacement": True})
    assert target.read_bytes() == original


def test_non_finite_json_fails_before_creating_artifact(tmp_path):
    target = (tmp_path / "bad.json").resolve()
    with pytest.raises(ArtifactIOError, match="strict JSON"):
        atomic_create_json(target, {"loss": float("nan")})
    assert not target.exists()


def test_combined_external_json_writer_enforces_boundary(tmp_path):
    repository = (tmp_path / "repo").resolve()
    repository.mkdir()
    workspace = (tmp_path / "workspace").resolve()
    target = workspace / "manifests" / "plan.json"

    create_external_json(
        target,
        {"schema": "example.v1"},
        workspace_root=workspace,
        repository_root=repository,
    )
    assert json.loads(target.read_text(encoding="utf-8")) == {"schema": "example.v1"}

    with pytest.raises(ArtifactIOError, match="escapes external workspace"):
        create_external_json(
            repository / "bad.json",
            {"bad": True},
            workspace_root=workspace,
            repository_root=repository,
        )


def test_canonical_json_requires_an_object():
    with pytest.raises(ArtifactIOError, match="must be an object"):
        canonical_json_bytes(["not", "an", "object"])  # type: ignore[arg-type]

    assert json.loads(
        canonical_json_bytes({"nested": MappingProxyType({"value": 1})})
    ) == {"nested": {"value": 1}}
    with pytest.raises(ArtifactIOError, match="keys must be strings"):
        canonical_json_bytes({"nested": {0: "ambiguous"}})
