from __future__ import annotations

import hashlib
import json
import stat
import struct
import zipfile
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType

import pytest

import rescue_training.dataset_intake as dataset_intake
from rescue_training.dataset_intake import (
    DATASET_ARCHIVE_REGISTRATION_SCHEMA,
    DATASET_EXTRACTION_INVENTORY_SCHEMA,
    DATASET_EXTRACTION_MANIFEST_SCHEMA,
    DATASET_RESPONSE_METADATA_SCHEMA,
    FROZEN_DATASET_IDENTITIES,
    DatasetIntakeError,
    extract_registered_zip,
    register_dataset_archive,
    validate_dataset_extraction_manifest,
)


NOW = "2026-08-22T12:34:56Z"


@pytest.fixture(autouse=True)
def _allow_small_c2a_archives_in_unit_tests(monkeypatch):
    identities = dict(FROZEN_DATASET_IDENTITIES)
    identities["c2a-v2"] = replace(
        identities["c2a-v2"],
        known_expected_bytes=None,
        known_expected_sha256=None,
        known_identity_trust=None,
    )
    monkeypatch.setattr(
        dataset_intake,
        "FROZEN_DATASET_IDENTITIES",
        MappingProxyType(identities),
    )


def _layout(tmp_path: Path):
    repository = (tmp_path / "repo").resolve()
    repository.mkdir()
    workspace = (tmp_path / "external-workspace").resolve()
    source = (tmp_path / "user-source").resolve()
    source.mkdir()
    return repository, workspace, source


def _write_zip(path: Path, members: list[tuple[str | zipfile.ZipInfo, bytes]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw_name_replacements: list[tuple[bytes, bytes]] = []
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name, payload in members:
            bundle.writestr(name, payload)
            if isinstance(name, str) and "\\" in name:
                raw_name_replacements.append(
                    (name.replace("\\", "/").encode("utf-8"), name.encode("utf-8"))
                )
    if raw_name_replacements:
        # Python deliberately canonicalizes backslashes while writing.  Patch
        # the equal-length local/central filename bytes to model an untrusted
        # archive produced by another ZIP implementation.
        raw = path.read_bytes()
        for normalized, original in raw_name_replacements:
            raw = raw.replace(normalized, original)
        path.write_bytes(raw)
    return path.resolve()


def _register(
    repository: Path,
    workspace: Path,
    archive: Path,
    *,
    stem: str = "c2a",
    authorization_status: str = "authorization_recorded",
):
    return register_dataset_archive(
        archive,
        workspace / "manifests" / f"{stem}-registration.json",
        workspace / "manifests" / f"{stem}-response.json",
        dataset_key="c2a-v2",
        workspace_root=workspace,
        repository_root=repository,
        origin_kind="local_user_provided",
        supplied_by="Samik",
        dataset_use_authorization_status=authorization_status,
        terms_recorded_by="automated-intake-v1",
        terms_reference="user-provided archive authorization recorded 2026-08-22",
        response_metadata={
            "transport": "local_user_provided",
            "original_filename": archive.name,
        },
        retrieved_at_utc=NOW,
    )


def _extract(
    repository: Path,
    workspace: Path,
    registration_path: Path,
    *,
    stem: str = "c2a",
    **kwargs,
):
    return extract_registered_zip(
        registration_path,
        workspace / "datasets" / stem,
        workspace / "manifests" / f"{stem}-inventory.json",
        workspace / "manifests" / f"{stem}-extraction.json",
        workspace_root=workspace,
        repository_root=repository,
        extracted_at_utc=NOW,
        **kwargs,
    )


def test_frozen_identities_bind_known_c2a_and_leave_unknown_safe_archive_open():
    identities = FROZEN_DATASET_IDENTITIES
    assert set(identities) == {
        "visdrone-det-2019-train",
        "visdrone-det-2019-val",
        "c2a-v2",
        "adilshamim8-people-detection-v1",
    }
    assert identities["visdrone-det-2019-train"].known_expected_bytes == 1_549_875_511
    assert identities["visdrone-det-2019-val"].known_expected_bytes == 81_638_851
    assert identities["visdrone-det-2019-train"].known_expected_sha256 == (
        "86a77eba93137bfc16e4993860de9245b0675c0dba0d3ab98fb458699e256f84"
    )
    assert identities["visdrone-det-2019-val"].known_expected_sha256 == (
        "abeea063037e5d20398837deb11084e652402a34ddf4f207bdf541a6f2a35ef9"
    )
    for split in ("train", "val"):
        assert identities[f"visdrone-det-2019-{split}"].known_identity_trust == (
            "tofu_official_https_url_exact_bytes_sha256_zip_integrity"
        )
    assert identities["c2a-v2"].known_expected_bytes == 4_903_081_990
    assert identities["c2a-v2"].known_expected_sha256 == (
        "cc21b41d7fcd555134117f95f52eab7fd39a96cd694e8edc8728c540e9eae653"
    )
    assert identities["adilshamim8-people-detection-v1"].known_expected_bytes is None
    assert identities["adilshamim8-people-detection-v1"].known_expected_sha256 is None
    assert identities["adilshamim8-people-detection-v1"].canonical_source_url.endswith(
        "/versions/1"
    )


def test_local_registration_records_exact_archive_and_immutable_response_evidence(tmp_path):
    repository, workspace, source = _layout(tmp_path)
    archive = _write_zip(
        source / "C2A_Dataset.zip",
        [("C2A/Train/images/a.jpg", b"image"), ("C2A/Train/labels/a.txt", b"0")],
    )
    original = archive.read_bytes()

    result = _register(repository, workspace, archive)

    registration = json.loads(result.evidence_path.read_text(encoding="utf-8"))
    response = json.loads(result.response_metadata_path.read_text(encoding="utf-8"))
    assert registration["schema"] == DATASET_ARCHIVE_REGISTRATION_SCHEMA
    assert response["schema"] == DATASET_RESPONSE_METADATA_SCHEMA
    assert registration["dataset"] == dataset_intake.FROZEN_DATASET_IDENTITIES[
        "c2a-v2"
    ].to_dict()
    assert registration["origin"] == {
        "kind": "local_user_provided",
        "source_url": None,
        "original_path": str(archive),
        "supplied_by": "Samik",
    }
    assert registration["archive"]["bytes"] == len(original)
    assert registration["archive"]["sha256"] == hashlib.sha256(original).hexdigest()
    assert registration["expectations"] == {"bytes": None, "sha256": None}
    assert registration["retrieval"]["retrieved_at_utc"] == NOW
    assert archive.read_bytes() == original

    registration_bytes = result.evidence_path.read_bytes()
    with pytest.raises(DatasetIntakeError, match="refusing to overwrite"):
        _register(repository, workspace, archive)
    assert result.evidence_path.read_bytes() == registration_bytes
    assert archive.read_bytes() == original


def test_download_registration_requires_public_url_and_rejects_secret_metadata(tmp_path):
    repository, workspace, source = _layout(tmp_path)
    archive = _write_zip(
        source / "people-detection.zip", [("data/a.jpg", b"x")]
    )
    common = dict(
        archive_path=archive,
        registration_path=workspace / "manifests" / "registration.json",
        response_metadata_path=workspace / "manifests" / "response.json",
        dataset_key="adilshamim8-people-detection-v1",
        workspace_root=workspace,
        repository_root=repository,
        origin_kind="download",
        dataset_use_authorization_status="authorization_recorded",
        terms_recorded_by="automated-intake-v1",
        retrieved_at_utc=NOW,
    )
    with pytest.raises(DatasetIntakeError, match="source_url"):
        register_dataset_archive(response_metadata={"status": 200}, **common)
    with pytest.raises(DatasetIntakeError, match="credential-like"):
        register_dataset_archive(
            source_url=(
                "https://www.kaggle.com/datasets/adilshamim8/people-detection/versions/1"
            ),
            response_metadata={"status": 200, "Authorization": "Bearer must-not-save"},
            **common,
        )
    with pytest.raises(DatasetIntakeError, match="frozen canonical"):
        register_dataset_archive(
            source_url="https://www.kaggle.com/datasets/adilshamim8/people-detection",
            response_metadata={"status": 200},
            expected_bytes=archive.stat().st_size,
            expected_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
            **common,
        )
    with pytest.raises(DatasetIntakeError, match="explicit expected_bytes"):
        register_dataset_archive(
            source_url=(
                "https://www.kaggle.com/datasets/adilshamim8/people-detection/versions/1"
            ),
            response_metadata={"status": 200},
            **common,
        )
    assert not (workspace / "manifests").exists()

    result = register_dataset_archive(
        source_url=(
            "https://www.kaggle.com/datasets/adilshamim8/people-detection/versions/1"
        ),
        response_metadata={"status": 200, "content_length": archive.stat().st_size},
        expected_bytes=archive.stat().st_size,
        expected_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
        **common,
    )
    written = json.loads(result.evidence_path.read_text(encoding="utf-8"))
    assert written["origin"]["kind"] == "download"
    assert written["origin"]["source_url"].startswith("https://www.kaggle.com/")


def test_registration_expectations_fail_before_any_evidence_is_created(tmp_path):
    repository, workspace, source = _layout(tmp_path)
    archive = _write_zip(source / "C2A_Dataset.zip", [("C2A/a.jpg", b"x")])
    with pytest.raises(DatasetIntakeError, match="size mismatch"):
        register_dataset_archive(
            archive,
            workspace / "manifests" / "registration.json",
            workspace / "manifests" / "response.json",
            dataset_key="c2a-v2",
            workspace_root=workspace,
            repository_root=repository,
            origin_kind="local_user_provided",
            supplied_by="Samik",
            dataset_use_authorization_status="authorization_recorded",
            terms_recorded_by="automated-intake-v1",
            response_metadata={"kind": "local"},
            expected_bytes=archive.stat().st_size + 1,
            retrieved_at_utc=NOW,
        )
    assert not (workspace / "manifests").exists()


def test_safe_extraction_writes_deterministic_inventory_and_binds_archive(tmp_path):
    repository, workspace, source = _layout(tmp_path)
    archive = _write_zip(
        source / "C2A_Dataset.zip",
        [
            ("C2A/Train/images/", b""),
            ("C2A/Train/images/a.jpg", b"image-a"),
            ("C2A/Train/images/b.PNG", b"image-b"),
            ("C2A/Train/labels/a.txt", b"0 0.5 0.5 1 1\n"),
        ],
    )
    archive_before = archive.read_bytes()
    registration = _register(repository, workspace, archive)

    result = _extract(
        repository,
        workspace,
        registration.evidence_path,
        expected_member_count=3,
        expected_image_count=2,
        required_top_level_prefixes=("C2A/Train/images", "C2A/Train/labels"),
    )

    assert result.file_count == 3
    assert result.image_count == 2
    assert result.extracted_bytes == len(b"image-aimage-b0 0.5 0.5 1 1\n")
    assert (result.extracted_root / "C2A/Train/images/a.jpg").read_bytes() == b"image-a"
    inventory = json.loads(result.inventory_path.read_text(encoding="utf-8"))
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert inventory["schema"] == DATASET_EXTRACTION_INVENTORY_SCHEMA
    assert manifest["schema"] == DATASET_EXTRACTION_MANIFEST_SCHEMA
    assert inventory["tree_sha256"] == result.tree_sha256
    assert manifest["archive"]["sha256"] == registration.archive_sha256
    assert manifest["archive"]["preserved"] is True
    assert manifest["extraction"]["root"] == str(result.extracted_root)
    assert manifest["inventory"]["sha256"] == hashlib.sha256(
        result.inventory_path.read_bytes()
    ).hexdigest()
    assert [item["relative_path"] for item in inventory["files"]] == [
        "C2A/Train/images/a.jpg",
        "C2A/Train/images/b.PNG",
        "C2A/Train/labels/a.txt",
    ]
    assert archive.read_bytes() == archive_before

    validated = validate_dataset_extraction_manifest(
        result.manifest_path,
        workspace_root=workspace,
        repository_root=repository,
        expected_dataset_key="c2a-v2",
    )
    assert validated.manifest_sha256 == hashlib.sha256(
        result.manifest_path.read_bytes()
    ).hexdigest()
    assert validated.registration_sha256 == hashlib.sha256(
        registration.evidence_path.read_bytes()
    ).hexdigest()
    assert validated.archive_sha256 == registration.archive_sha256
    assert validated.inventory_sha256 == hashlib.sha256(
        result.inventory_path.read_bytes()
    ).hexdigest()
    assert validated.tree_sha256 == result.tree_sha256
    assert validated.dataset.key == "c2a-v2"

    # Tree identity excludes machine-specific extraction/evidence paths.
    second_registration = _register(
        repository, workspace, archive, stem="c2a-second"
    )
    second = _extract(
        repository,
        workspace,
        second_registration.evidence_path,
        stem="c2a-second",
        expected_member_count=3,
        expected_image_count=2,
        required_top_level_prefixes=("C2A",),
    )
    assert second.tree_sha256 == result.tree_sha256


def test_recursive_extraction_validation_rejects_arbitrary_or_tampered_roots(tmp_path):
    repository, workspace, source = _layout(tmp_path)
    archive = _write_zip(
        source / "C2A_Dataset.zip",
        [
            ("C2A/images/a.jpg", b"image-a"),
            ("C2A/annotations/a.txt", b"1,2,3,4,1,1,0,0\n"),
        ],
    )
    registration = _register(repository, workspace, archive)
    result = _extract(repository, workspace, registration.evidence_path)

    with pytest.raises(DatasetIntakeError, match="wrong frozen dataset identity"):
        validate_dataset_extraction_manifest(
            result.manifest_path,
            workspace_root=workspace,
            repository_root=repository,
            expected_dataset_key="visdrone-det-2019-train",
        )

    arbitrary = workspace / "datasets" / "arbitrary"
    arbitrary.mkdir(parents=True)
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    manifest["extraction"]["root"] = str(arbitrary.resolve())
    result.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(DatasetIntakeError, match="inventory names a different"):
        validate_dataset_extraction_manifest(
            result.manifest_path,
            workspace_root=workspace,
            repository_root=repository,
        )
    manifest["extraction"]["root"] = str(result.extracted_root)
    result.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    (result.extracted_root / "C2A" / "images" / "a.jpg").write_bytes(b"changed")
    with pytest.raises(DatasetIntakeError, match="extracted file changed"):
        validate_dataset_extraction_manifest(
            result.manifest_path,
            workspace_root=workspace,
            repository_root=repository,
        )


def test_recursive_validation_rejects_coherently_rewritten_tree_evidence(tmp_path):
    repository, workspace, source = _layout(tmp_path)
    archive = _write_zip(
        source / "C2A_Dataset.zip",
        [("C2A/images/a.jpg", b"image-a")],
    )
    registration = _register(repository, workspace, archive)
    result = _extract(repository, workspace, registration.evidence_path)

    forged_bytes = b"forged!"
    (result.extracted_root / "C2A" / "images" / "a.jpg").write_bytes(forged_bytes)
    inventory = json.loads(result.inventory_path.read_text(encoding="utf-8"))
    inventory["files"][0]["sha256"] = hashlib.sha256(forged_bytes).hexdigest()
    tree_basis = {
        "directories": inventory["directories"],
        "files": [
            {
                "relative_path": item["relative_path"],
                "bytes": item["bytes"],
                "sha256": item["sha256"],
            }
            for item in inventory["files"]
        ],
    }
    forged_tree_sha = hashlib.sha256(
        dataset_intake.canonical_json_bytes(tree_basis)
    ).hexdigest()
    inventory["tree_sha256"] = forged_tree_sha
    result.inventory_path.write_text(json.dumps(inventory), encoding="utf-8")
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    manifest["inventory"]["sha256"] = hashlib.sha256(
        result.inventory_path.read_bytes()
    ).hexdigest()
    manifest["extraction"]["tree_sha256"] = forged_tree_sha
    result.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(DatasetIntakeError, match="registered ZIP member"):
        validate_dataset_extraction_manifest(
            result.manifest_path,
            workspace_root=workspace,
            repository_root=repository,
        )

@pytest.mark.parametrize(
    "member_name, message",
    [
        ("../escape.txt", "traverses"),
        ("/absolute.txt", "absolute"),
        ("C:/absolute.txt", "absolute"),
        ("folder\\escape.txt", "backslashes"),
        ("a//b.txt", "not canonical"),
        ("folder/CON.txt", "reserved Windows"),
        ("folder/trailing. ", "trailing"),
        ("folder/name:stream", "unsafe characters"),
    ],
)
def test_unsafe_member_paths_fail_before_creating_extraction_root(
    tmp_path, member_name, message
):
    repository, workspace, source = _layout(tmp_path)
    archive = _write_zip(source / "C2A_Dataset.zip", [(member_name, b"bad")])
    registration = _register(repository, workspace, archive)
    with pytest.raises(DatasetIntakeError, match=message):
        _extract(repository, workspace, registration.evidence_path)
    assert not (workspace / "datasets" / "c2a").exists()
    assert not (tmp_path / "escape.txt").exists()


@pytest.mark.parametrize(
    "members, message",
    [
        ([('Data/a.jpg', b'a'), ('data/A.JPG', b'b')], "case collision"),
        ([('root', b'file'), ('root/child.txt', b'child')], "prefix collision"),
    ],
)
def test_case_and_file_directory_collisions_fail_closed(tmp_path, members, message):
    repository, workspace, source = _layout(tmp_path)
    archive = _write_zip(source / "C2A_Dataset.zip", members)
    registration = _register(repository, workspace, archive)
    with pytest.raises(DatasetIntakeError, match=message):
        _extract(repository, workspace, registration.evidence_path)
    assert not (workspace / "datasets" / "c2a").exists()


def test_symlink_reparse_special_and_unsupported_compression_members_are_rejected(tmp_path):
    repository, workspace, source = _layout(tmp_path)

    symlink = zipfile.ZipInfo("data/link")
    symlink.create_system = 3
    symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
    symlink_archive = _write_zip(source / "symlink.zip", [(symlink, b"target")])
    registration = _register(repository, workspace, symlink_archive, stem="symlink")
    with pytest.raises(DatasetIntakeError, match="symlink"):
        _extract(repository, workspace, registration.evidence_path, stem="symlink")

    reparse = zipfile.ZipInfo("data/reparse")
    reparse.create_system = 0
    reparse.external_attr = 0x400
    reparse_archive = _write_zip(source / "reparse.zip", [(reparse, b"target")])
    registration = _register(repository, workspace, reparse_archive, stem="reparse")
    with pytest.raises(DatasetIntakeError, match="reparse-point"):
        _extract(repository, workspace, registration.evidence_path, stem="reparse")

    directory_mismatch = zipfile.ZipInfo("data/not-a-directory")
    directory_mismatch.create_system = 0
    directory_mismatch.external_attr = 0x10
    mismatch_archive = _write_zip(
        source / "directory-mismatch.zip", [(directory_mismatch, b"target")]
    )
    registration = _register(
        repository, workspace, mismatch_archive, stem="directory-mismatch"
    )
    with pytest.raises(DatasetIntakeError, match="attributes disagree"):
        _extract(
            repository,
            workspace,
            registration.evidence_path,
            stem="directory-mismatch",
        )

    special = zipfile.ZipInfo("data/fifo")
    special.create_system = 3
    special.external_attr = (stat.S_IFIFO | 0o600) << 16
    special_archive = _write_zip(source / "special.zip", [(special, b"target")])
    registration = _register(repository, workspace, special_archive, stem="special")
    with pytest.raises(DatasetIntakeError, match="special file"):
        _extract(repository, workspace, registration.evidence_path, stem="special")

    unsupported_archive = source / "unsupported.zip"
    with zipfile.ZipFile(unsupported_archive, "w", compression=zipfile.ZIP_BZIP2) as bundle:
        bundle.writestr("data/file.txt", b"payload")
    registration = _register(
        repository, workspace, unsupported_archive.resolve(), stem="unsupported"
    )
    with pytest.raises(DatasetIntakeError, match="unsupported ZIP compression"):
        _extract(repository, workspace, registration.evidence_path, stem="unsupported")


def test_encrypted_flag_is_rejected_before_extraction(tmp_path):
    repository, workspace, source = _layout(tmp_path)
    archive = _write_zip(source / "encrypted.zip", [("data/file.txt", b"payload")])
    raw = bytearray(archive.read_bytes())
    local = raw.index(b"PK\x03\x04")
    central = raw.index(b"PK\x01\x02")
    struct.pack_into("<H", raw, local + 6, struct.unpack_from("<H", raw, local + 6)[0] | 1)
    struct.pack_into(
        "<H", raw, central + 8, struct.unpack_from("<H", raw, central + 8)[0] | 1
    )
    archive.write_bytes(raw)
    registration = _register(repository, workspace, archive, stem="encrypted")
    with pytest.raises(DatasetIntakeError, match="encrypted ZIP member"):
        _extract(repository, workspace, registration.evidence_path, stem="encrypted")
    assert not (workspace / "datasets" / "encrypted").exists()


def test_counts_prefixes_and_resource_limits_are_pre_extraction_gates(tmp_path):
    repository, workspace, source = _layout(tmp_path)
    archive = _write_zip(
        source / "C2A_Dataset.zip",
        [("C2A/a.jpg", b"a"), ("C2A/b.txt", b"1234567890")],
    )
    registration = _register(repository, workspace, archive)

    with pytest.raises(DatasetIntakeError, match="member count mismatch"):
        _extract(
            repository,
            workspace,
            registration.evidence_path,
            expected_member_count=3,
        )
    with pytest.raises(DatasetIntakeError, match="image count mismatch"):
        _extract(
            repository,
            workspace,
            registration.evidence_path,
            expected_image_count=2,
        )
    with pytest.raises(DatasetIntakeError, match="required ZIP prefix"):
        _extract(
            repository,
            workspace,
            registration.evidence_path,
            required_top_level_prefixes=("Missing",),
        )
    with pytest.raises(DatasetIntakeError, match="member uncompressed-size limit"):
        _extract(
            repository,
            workspace,
            registration.evidence_path,
            max_member_uncompressed_bytes=5,
        )
    assert not (workspace / "datasets" / "c2a").exists()


def test_authorization_gate_and_tampered_evidence_or_archive_block_extraction(tmp_path):
    repository, workspace, source = _layout(tmp_path)
    archive = _write_zip(source / "C2A_Dataset.zip", [("C2A/a.jpg", b"image")])
    unauthorized = _register(
        repository,
        workspace,
        archive,
        stem="unauthorized",
        authorization_status="authorization_missing",
    )
    with pytest.raises(DatasetIntakeError, match="dataset-use authorization gate"):
        _extract(
            repository,
            workspace,
            unauthorized.evidence_path,
            stem="unauthorized",
        )

    registered = _register(repository, workspace, archive, stem="tamper-response")
    registered.response_metadata_path.write_text("{}\n", encoding="utf-8")
    with pytest.raises(DatasetIntakeError, match="response metadata artifact hash mismatch"):
        _extract(
            repository,
            workspace,
            registered.evidence_path,
            stem="tamper-response",
        )

    archive_two = _write_zip(source / "C2A-two.zip", [("C2A/a.jpg", b"image")])
    registered_two = _register(repository, workspace, archive_two, stem="tamper-archive")
    archive_two.write_bytes(archive_two.read_bytes() + b"changed")
    with pytest.raises(DatasetIntakeError, match="bytes or SHA-256 changed"):
        _extract(
            repository,
            workspace,
            registered_two.evidence_path,
            stem="tamper-archive",
        )


def test_existing_outputs_are_preserved_and_never_overwritten(tmp_path):
    repository, workspace, source = _layout(tmp_path)
    archive = _write_zip(source / "C2A_Dataset.zip", [("C2A/a.jpg", b"image")])
    registration = _register(repository, workspace, archive)
    destination = workspace / "datasets" / "c2a"
    destination.mkdir(parents=True)
    sentinel = destination / "owned.txt"
    sentinel.write_text("keep", encoding="utf-8")

    with pytest.raises(DatasetIntakeError, match="refusing to overwrite extraction root"):
        _extract(repository, workspace, registration.evidence_path)
    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_evidence_cannot_be_written_inside_the_extracted_raw_tree(tmp_path):
    repository, workspace, source = _layout(tmp_path)
    archive = _write_zip(source / "C2A_Dataset.zip", [("C2A/a.jpg", b"image")])
    registration = _register(repository, workspace, archive)
    destination = workspace / "datasets" / "c2a"
    with pytest.raises(DatasetIntakeError, match="outside, not inside"):
        extract_registered_zip(
            registration.evidence_path,
            destination,
            destination / "inventory.json",
            workspace / "manifests" / "extraction.json",
            workspace_root=workspace,
            repository_root=repository,
            extracted_at_utc=NOW,
        )
    assert not destination.exists()


def test_manifest_publish_failure_removes_new_extracted_tree(tmp_path, monkeypatch):
    repository, workspace, source = _layout(tmp_path)
    archive = _write_zip(source / "C2A_Dataset.zip", [("C2A/a.jpg", b"image")])
    registration = _register(repository, workspace, archive)
    real_create = dataset_intake.atomic_create_json

    def fail_manifest(path, payload):
        if Path(path).name == "c2a-extraction.json":
            raise dataset_intake.ArtifactIOError("simulated manifest publish failure")
        return real_create(path, payload)

    monkeypatch.setattr(dataset_intake, "atomic_create_json", fail_manifest)
    with pytest.raises(DatasetIntakeError, match="simulated manifest publish failure"):
        _extract(repository, workspace, registration.evidence_path)
    assert not (workspace / "datasets" / "c2a").exists()
    assert (workspace / "manifests" / "c2a-inventory.json").is_file()
    assert not (workspace / "manifests" / "c2a-extraction.json").exists()


def test_archive_and_all_generated_artifacts_must_be_outside_repository(tmp_path):
    repository, workspace, source = _layout(tmp_path)
    archive_in_repo = _write_zip(repository / "dataset.zip", [("data/a.jpg", b"x")])
    with pytest.raises(DatasetIntakeError, match="outside repository"):
        _register(repository, workspace, archive_in_repo)

    archive = _write_zip(source / "C2A_Dataset.zip", [("data/a.jpg", b"x")])
    with pytest.raises(DatasetIntakeError, match="escapes external workspace"):
        register_dataset_archive(
            archive,
            repository / "registration.json",
            workspace / "response.json",
            dataset_key="c2a-v2",
            workspace_root=workspace,
            repository_root=repository,
            origin_kind="local_user_provided",
            supplied_by="Samik",
            dataset_use_authorization_status="authorization_recorded",
            terms_recorded_by="automated-intake-v1",
            response_metadata={"kind": "local"},
            retrieved_at_utc=NOW,
        )
