"""Fail-closed ZIP intake for external rescue-model datasets.

This module does not download anything.  It registers an already-present local
or downloaded ZIP, records immutable provenance, and extracts only after a
complete central-directory safety inspection.  Source archives are never
renamed, moved, modified, or deleted.

All evidence and extracted data must live outside the Git checkout.  A local
user-provided archive (for example ``E:\\dataset\\C2A_Dataset.zip``) may live
outside the configured artifact workspace, but it must also be outside the
repository and must be a regular non-reparse file.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import unicodedata
import zipfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import MappingProxyType
from typing import Any, Literal
from urllib.parse import urlsplit

from .artifact_io import (
    ArtifactIOError,
    atomic_create_json,
    canonical_json_bytes,
    require_external_workspace,
    require_path_within_workspace,
    sha256_file,
)


DATASET_RESPONSE_METADATA_SCHEMA = "veriswarm.rescue.dataset_response_metadata.v2"
DATASET_ARCHIVE_REGISTRATION_SCHEMA = (
    "veriswarm.rescue.dataset_archive_registration.v3"
)
DATASET_EXTRACTION_INVENTORY_SCHEMA = (
    "veriswarm.rescue.dataset_extraction_inventory.v1"
)
DATASET_EXTRACTION_MANIFEST_SCHEMA = (
    "veriswarm.rescue.dataset_extraction_manifest.v2"
)

_ALLOWED_DATASET_USE_STATUSES = frozenset(
    {"authorization_recorded", "not_applicable", "authorization_missing", "rejected"}
)
_EXTRACTION_ALLOWED_DATASET_USE_STATUSES = frozenset(
    {"authorization_recorded", "not_applicable"}
)
_ALLOWED_COMPRESSION_METHODS = frozenset(
    {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
)
_IMAGE_SUFFIXES = frozenset(
    {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}
)
DEFAULT_MAX_ARCHIVE_MEMBERS = 250_000
DEFAULT_MAX_TOTAL_UNCOMPRESSED_BYTES = 40 * 1024**3
DEFAULT_MAX_MEMBER_UNCOMPRESSED_BYTES = 2 * 1024**3
DEFAULT_MAX_COMPRESSION_RATIO = 500.0
_WINDOWS_RESERVED_NAMES = frozenset(
    {"con", "prn", "aux", "nul", "clock$"}
    | {f"com{index}" for index in range(1, 10)}
    | {f"lpt{index}" for index in range(1, 10)}
)
_FORBIDDEN_RESPONSE_KEYS = frozenset(
    {
        "authorization",
        "cookie",
        "set-cookie",
        "proxy-authorization",
        "x-api-key",
        "api-key",
        "apikey",
        "access-token",
        "access_token",
        "refresh-token",
        "refresh_token",
        "password",
        "secret",
    }
)


class DatasetIntakeError(RuntimeError):
    """A dataset cannot be registered or extracted without ambiguity."""


@dataclass(frozen=True, slots=True)
class FrozenDatasetIdentity:
    """One immutable public dataset/archive identity.

    ``known_expected_bytes`` and ``known_expected_sha256`` remain ``None``
    unless this project has an independently frozen value.  ``None`` is
    deliberate: an unknown value must never be invented merely to complete a
    manifest.
    """

    key: str
    dataset_id: str
    version: str
    archive_role: str
    canonical_source_url: str
    archive_name_hint: str
    known_expected_bytes: int | None
    known_expected_sha256: str | None
    known_identity_trust: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "dataset_id": self.dataset_id,
            "version": self.version,
            "archive_role": self.archive_role,
            "canonical_source_url": self.canonical_source_url,
            "archive_name_hint": self.archive_name_hint,
            "known_expected_bytes": self.known_expected_bytes,
            "known_expected_sha256": self.known_expected_sha256,
            "known_identity_trust": self.known_identity_trust,
        }


FROZEN_DATASET_IDENTITIES: Mapping[str, FrozenDatasetIdentity] = MappingProxyType(
    {
        "visdrone-det-2019-train": FrozenDatasetIdentity(
            key="visdrone-det-2019-train",
            dataset_id="VisDrone2019-DET",
            version="2019",
            archive_role="official_train",
            canonical_source_url=(
                "https://github.com/ultralytics/assets/releases/download/"
                "v0.0.0/VisDrone2019-DET-train.zip"
            ),
            archive_name_hint="VisDrone2019-DET-train.zip",
            known_expected_bytes=1_549_875_511,
            known_expected_sha256=(
                "86a77eba93137bfc16e4993860de9245b0675c0dba0d3ab98fb458699e256f84"
            ),
            known_identity_trust=(
                "tofu_official_https_url_exact_bytes_sha256_zip_integrity"
            ),
        ),
        "visdrone-det-2019-val": FrozenDatasetIdentity(
            key="visdrone-det-2019-val",
            dataset_id="VisDrone2019-DET",
            version="2019",
            archive_role="official_validation",
            canonical_source_url=(
                "https://github.com/ultralytics/assets/releases/download/"
                "v0.0.0/VisDrone2019-DET-val.zip"
            ),
            archive_name_hint="VisDrone2019-DET-val.zip",
            known_expected_bytes=81_638_851,
            known_expected_sha256=(
                "abeea063037e5d20398837deb11084e652402a34ddf4f207bdf541a6f2a35ef9"
            ),
            known_identity_trust=(
                "tofu_official_https_url_exact_bytes_sha256_zip_integrity"
            ),
        ),
        "c2a-v2": FrozenDatasetIdentity(
            key="c2a-v2",
            dataset_id="rgbnihal/c2a-dataset",
            version="2",
            archive_role="disaster_human_source",
            canonical_source_url=(
                "https://www.kaggle.com/datasets/rgbnihal/c2a-dataset/versions/2"
            ),
            archive_name_hint="C2A_Dataset.zip",
            known_expected_bytes=4_903_081_990,
            known_expected_sha256=(
                "cc21b41d7fcd555134117f95f52eab7fd39a96cd694e8edc8728c540e9eae653"
            ),
            known_identity_trust=(
                "locally_observed_exact_bytes_sha256_not_publisher_signed"
            ),
        ),
        "adilshamim8-people-detection-v1": FrozenDatasetIdentity(
            key="adilshamim8-people-detection-v1",
            dataset_id="adilshamim8/people-detection",
            version="1",
            archive_role="safe_people_candidate_source",
            canonical_source_url=(
                "https://www.kaggle.com/datasets/adilshamim8/people-detection/versions/1"
            ),
            archive_name_hint="people-detection.zip",
            known_expected_bytes=None,
            known_expected_sha256=None,
            known_identity_trust=None,
        ),
    }
)


@dataclass(frozen=True, slots=True)
class ArchiveRegistrationResult:
    evidence_path: Path
    response_metadata_path: Path
    archive_path: Path
    archive_bytes: int
    archive_sha256: str
    dataset: FrozenDatasetIdentity


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    manifest_path: Path
    inventory_path: Path
    extracted_root: Path
    file_count: int
    image_count: int
    extracted_bytes: int
    tree_sha256: str


@dataclass(frozen=True, slots=True)
class ValidatedExtractionEvidence:
    """Recursively revalidated registration, archive, inventory, and tree."""

    manifest_path: Path
    manifest_sha256: str
    registration_path: Path
    registration_sha256: str
    archive_path: Path
    archive_bytes: int
    archive_sha256: str
    inventory_path: Path
    inventory_sha256: str
    extracted_root: Path
    tree_sha256: str
    file_count: int
    image_count: int
    extracted_bytes: int
    dataset: FrozenDatasetIdentity

    def to_dict(self) -> dict[str, Any]:
        return {
            "manifest": {
                "path": str(self.manifest_path),
                "sha256": self.manifest_sha256,
                "schema": DATASET_EXTRACTION_MANIFEST_SCHEMA,
            },
            "registration": {
                "path": str(self.registration_path),
                "sha256": self.registration_sha256,
                "schema": DATASET_ARCHIVE_REGISTRATION_SCHEMA,
            },
            "archive": {
                "path": str(self.archive_path),
                "bytes": self.archive_bytes,
                "sha256": self.archive_sha256,
                "identity_trust": self.dataset.known_identity_trust,
            },
            "inventory": {
                "path": str(self.inventory_path),
                "sha256": self.inventory_sha256,
                "schema": DATASET_EXTRACTION_INVENTORY_SCHEMA,
                "tree_sha256": self.tree_sha256,
            },
            "extraction": {
                "root": str(self.extracted_root),
                "files": self.file_count,
                "images": self.image_count,
                "extracted_bytes": self.extracted_bytes,
            },
            "dataset": self.dataset.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class _InspectedMember:
    info: zipfile.ZipInfo
    relative_path: str
    parts: tuple[str, ...]
    is_directory: bool


def _nonempty_string(value: Any, field: str) -> str:
    if type(value) is not str or not value or value.strip() != value:
        raise DatasetIntakeError(f"{field} must be a non-empty trimmed string")
    return value


def _optional_sha256(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if (
        type(value) is not str
        or len(value) != 64
        or value != value.lower()
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise DatasetIntakeError(f"{field} must be null or one lowercase SHA-256")
    return value


def _optional_positive_integer(value: Any, field: str) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value <= 0:
        raise DatasetIntakeError(f"{field} must be null or a positive integer")
    return value


def _positive_integer(value: Any, field: str) -> int:
    if type(value) is not int or value <= 0:
        raise DatasetIntakeError(f"{field} must be a positive integer")
    return value


def _nonnegative_integer(value: Any, field: str) -> int:
    if type(value) is not int or value < 0:
        raise DatasetIntakeError(f"{field} must be a non-negative integer")
    return value


def _exact_fields(value: Mapping[str, Any], expected: set[str], field: str) -> None:
    if type(value) is not dict:
        raise DatasetIntakeError(f"{field} must be a JSON object")
    actual = set(value)
    if actual != expected:
        raise DatasetIntakeError(
            f"{field} fields are not frozen; "
            f"missing={sorted(expected - actual)}, unknown={sorted(actual - expected)}"
        )


def _strict_json_object(path: Path, field: str) -> dict[str, Any]:
    if _is_link_or_reparse(path) or not path.is_file():
        raise DatasetIntakeError(f"{field} must be one regular non-link file: {path}")

    def reject_constant(value: str) -> None:
        raise DatasetIntakeError(f"non-finite JSON value is forbidden: {value}")

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise DatasetIntakeError(f"duplicate JSON field is forbidden: {key!r}")
            result[key] = item
        return result

    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicates,
        )
    except DatasetIntakeError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise DatasetIntakeError(f"cannot read {field} {path}: {error}") from error
    if type(value) is not dict:
        raise DatasetIntakeError(f"{field} must contain one JSON object")
    return value


def _is_link_or_reparse(path: Path) -> bool:
    """Detect symlinks, junctions, and Windows reparse-point files."""

    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        if callable(is_junction) and is_junction():
            return True
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except (FileNotFoundError, OSError):
        return False
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_flag)


def _require_regular_external_archive(
    archive_path: str | os.PathLike[str], repository_root: Path
) -> Path:
    source_input = Path(archive_path).expanduser()
    if not source_input.is_absolute():
        raise DatasetIntakeError("archive_path must be absolute")
    if _is_link_or_reparse(source_input):
        raise DatasetIntakeError(f"archive must not be a link or reparse point: {source_input}")
    source = source_input.resolve(strict=False)
    if not source.is_file():
        raise DatasetIntakeError(f"archive must be one regular file: {source}")
    if source.suffix.casefold() != ".zip":
        raise DatasetIntakeError(f"archive must have a .zip extension: {source}")
    if source == repository_root or repository_root in source.parents:
        raise DatasetIntakeError(f"dataset archive must be outside repository: {source}")
    try:
        if not zipfile.is_zipfile(source):
            raise DatasetIntakeError(f"archive is not a valid ZIP: {source}")
    except OSError as error:
        raise DatasetIntakeError(f"cannot inspect ZIP signature {source}: {error}") from error
    return source


def _validated_http_url(value: Any, field: str) -> str:
    url = _nonempty_string(value, field)
    parsed = urlsplit(url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise DatasetIntakeError(
            f"{field} must be an absolute HTTP(S) URL without credentials or fragment"
        )
    return url


def _timestamp_utc(
    retrieved_at_utc: str | None,
    now: Callable[[], datetime],
) -> str:
    if retrieved_at_utc is None:
        moment = now()
        if not isinstance(moment, datetime) or moment.tzinfo is None:
            raise DatasetIntakeError("now() must return a timezone-aware datetime")
        return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    value = _nonempty_string(retrieved_at_utc, "retrieved_at_utc")
    if not value.endswith("Z"):
        raise DatasetIntakeError("retrieved_at_utc must be an RFC3339 UTC timestamp ending in Z")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise DatasetIntakeError("retrieved_at_utc is not a valid RFC3339 timestamp") from error
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise DatasetIntakeError("retrieved_at_utc must be UTC")
    return value


def _check_metadata_for_secrets(value: Any, field: str = "response_metadata") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if type(key) is not str:
                raise DatasetIntakeError(f"{field} JSON keys must be strings")
            normalized = key.strip().casefold()
            if normalized in _FORBIDDEN_RESPONSE_KEYS or any(
                marker in normalized for marker in ("authorization", "password", "secret")
            ):
                raise DatasetIntakeError(
                    f"{field} must not persist credential-like field {key!r}"
                )
            _check_metadata_for_secrets(item, f"{field}.{key}")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, item in enumerate(value):
            _check_metadata_for_secrets(item, f"{field}[{index}]")


def _validate_artifact_targets(
    workspace: Path,
    *targets: str | os.PathLike[str],
) -> tuple[Path, ...]:
    resolved: list[Path] = []
    for target in targets:
        try:
            destination = require_path_within_workspace(target, workspace)
        except ArtifactIOError as error:
            raise DatasetIntakeError(str(error)) from error
        if os.path.lexists(destination):
            raise DatasetIntakeError(f"refusing to overwrite existing artifact: {destination}")
        resolved.append(destination)
    if len(set(resolved)) != len(resolved):
        raise DatasetIntakeError("dataset evidence paths must be distinct")
    return tuple(resolved)


def register_dataset_archive(
    archive_path: str | os.PathLike[str],
    registration_path: str | os.PathLike[str],
    response_metadata_path: str | os.PathLike[str],
    *,
    dataset_key: str,
    workspace_root: str | os.PathLike[str],
    repository_root: str | os.PathLike[str],
    origin_kind: Literal["download", "local_user_provided"],
    dataset_use_authorization_status: str,
    terms_recorded_by: str,
    response_metadata: Mapping[str, Any],
    source_url: str | None = None,
    supplied_by: str | None = None,
    terms_reference: str | None = None,
    expected_bytes: int | None = None,
    expected_sha256: str | None = None,
    retrieved_at_utc: str | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> ArchiveRegistrationResult:
    """Register an existing ZIP and create immutable provenance evidence.

    ``response_metadata`` should contain only non-secret retrieval facts such as
    HTTP status, final public URL, content length, ETag, and retrieval tool.  A
    local user-provided archive uses the same artifact with local intake facts.
    """

    try:
        workspace = require_external_workspace(workspace_root, repository_root)
    except ArtifactIOError as error:
        raise DatasetIntakeError(str(error)) from error
    repository = Path(repository_root).expanduser().resolve(strict=False)
    identity = FROZEN_DATASET_IDENTITIES.get(dataset_key)
    if identity is None:
        raise DatasetIntakeError(f"unknown frozen dataset identity: {dataset_key!r}")
    archive = _require_regular_external_archive(archive_path, repository)
    registration, response_artifact = _validate_artifact_targets(
        workspace, registration_path, response_metadata_path
    )
    if archive in {registration, response_artifact}:
        raise DatasetIntakeError("archive and evidence paths must be distinct")

    if origin_kind == "download":
        actual_source_url = _validated_http_url(source_url, "source_url")
        if actual_source_url != identity.canonical_source_url:
            raise DatasetIntakeError(
                "source_url does not match the frozen canonical dataset source"
            )
        if supplied_by is not None:
            raise DatasetIntakeError("supplied_by must be null for a downloaded archive")
        original_path: str | None = None
        supplier: str | None = None
    elif origin_kind == "local_user_provided":
        if source_url is not None:
            raise DatasetIntakeError(
                "source_url must be null for a local user-provided archive"
            )
        supplier = _nonempty_string(supplied_by, "supplied_by")
        actual_source_url = None
        original_path = str(archive)
    else:
        raise DatasetIntakeError(
            "origin_kind must be exactly 'download' or 'local_user_provided'"
        )

    status = _nonempty_string(
        dataset_use_authorization_status, "dataset_use_authorization_status"
    )
    if status not in _ALLOWED_DATASET_USE_STATUSES:
        raise DatasetIntakeError(
            "dataset_use_authorization_status must be one of "
            f"{sorted(_ALLOWED_DATASET_USE_STATUSES)}"
        )
    recorder = _nonempty_string(terms_recorded_by, "terms_recorded_by")
    if terms_reference is not None:
        terms_reference = _nonempty_string(terms_reference, "terms_reference")
    if not isinstance(response_metadata, Mapping):
        raise DatasetIntakeError("response_metadata must be a JSON object")
    _check_metadata_for_secrets(response_metadata)
    # Validate strict JSON (including finite floats) before creating evidence.
    try:
        canonical_json_bytes(dict(response_metadata))
    except ArtifactIOError as error:
        raise DatasetIntakeError(f"invalid response_metadata: {error}") from error

    expected_byte_count = _optional_positive_integer(expected_bytes, "expected_bytes")
    expected_digest = _optional_sha256(expected_sha256, "expected_sha256")
    if identity.known_expected_bytes is not None:
        if expected_byte_count is None:
            expected_byte_count = identity.known_expected_bytes
        elif expected_byte_count != identity.known_expected_bytes:
            raise DatasetIntakeError(
                "expected_bytes conflicts with the frozen dataset identity"
            )
    if identity.known_expected_sha256 is not None:
        if expected_digest is None:
            expected_digest = identity.known_expected_sha256
        elif expected_digest != identity.known_expected_sha256:
            raise DatasetIntakeError(
                "expected_sha256 conflicts with the frozen dataset identity"
            )
    if origin_kind == "download" and (
        expected_byte_count is None or expected_digest is None
    ):
        raise DatasetIntakeError(
            "downloaded archives require explicit expected_bytes and expected_sha256"
        )
    if (
        origin_kind == "download" or identity.known_expected_bytes is not None
    ) and archive.name.casefold() != identity.archive_name_hint.casefold():
        raise DatasetIntakeError(
            "archive filename does not match the frozen dataset identity: "
            f"expected {identity.archive_name_hint!r}, got {archive.name!r}"
        )

    timestamp = _timestamp_utc(retrieved_at_utc, now)
    try:
        actual_bytes = archive.stat().st_size
    except OSError as error:
        raise DatasetIntakeError(f"cannot stat archive {archive}: {error}") from error
    if actual_bytes <= 0:
        raise DatasetIntakeError("archive must not be empty")
    actual_sha256 = sha256_file(archive)
    if expected_byte_count is not None and actual_bytes != expected_byte_count:
        raise DatasetIntakeError(
            f"archive size mismatch: expected {expected_byte_count}, got {actual_bytes}"
        )
    if expected_digest is not None and actual_sha256 != expected_digest:
        raise DatasetIntakeError(
            f"archive SHA-256 mismatch: expected {expected_digest}, got {actual_sha256}"
        )

    response_payload = {
        "schema": DATASET_RESPONSE_METADATA_SCHEMA,
        "dataset": identity.to_dict(),
        "origin_kind": origin_kind,
        "retrieved_at_utc": timestamp,
        "metadata": dict(response_metadata),
    }
    try:
        atomic_create_json(response_artifact, response_payload)
        response_sha256 = sha256_file(response_artifact)
        # Bind the evidence to the same bytes observed before its creation.
        if archive.stat().st_size != actual_bytes or sha256_file(archive) != actual_sha256:
            raise DatasetIntakeError("archive changed while registration evidence was created")
        registration_payload = {
            "schema": DATASET_ARCHIVE_REGISTRATION_SCHEMA,
            "dataset": identity.to_dict(),
            "origin": {
                "kind": origin_kind,
                "source_url": actual_source_url,
                "original_path": original_path,
                "supplied_by": supplier,
            },
            "terms": {
                "dataset_use_authorization_status": status,
                "recorded_by": recorder,
                "reference": terms_reference,
            },
            "retrieval": {
                "retrieved_at_utc": timestamp,
                "response_metadata_path": str(response_artifact),
                "response_metadata_sha256": response_sha256,
            },
            "archive": {
                "path": str(archive),
                "name": archive.name,
                "bytes": actual_bytes,
                "sha256": actual_sha256,
            },
            "expectations": {
                "bytes": expected_byte_count,
                "sha256": expected_digest,
            },
        }
        atomic_create_json(registration, registration_payload)
    except DatasetIntakeError:
        raise
    except (ArtifactIOError, OSError) as error:
        raise DatasetIntakeError(f"cannot create archive registration: {error}") from error
    return ArchiveRegistrationResult(
        evidence_path=registration,
        response_metadata_path=response_artifact,
        archive_path=archive,
        archive_bytes=actual_bytes,
        archive_sha256=actual_sha256,
        dataset=identity,
    )


def _validate_identity_dict(value: Any) -> FrozenDatasetIdentity:
    _exact_fields(
        value,
        {
            "key",
            "dataset_id",
            "version",
            "archive_role",
            "canonical_source_url",
            "archive_name_hint",
            "known_expected_bytes",
            "known_expected_sha256",
            "known_identity_trust",
        },
        "dataset identity",
    )
    key = _nonempty_string(value["key"], "dataset.key")
    identity = FROZEN_DATASET_IDENTITIES.get(key)
    if identity is None or value != identity.to_dict():
        raise DatasetIntakeError("dataset identity does not match a frozen identity")
    if identity.known_expected_sha256 is not None:
        _nonempty_string(identity.known_identity_trust, "dataset.known_identity_trust")
    elif identity.known_identity_trust is not None:
        raise DatasetIntakeError(
            "dataset identity trust must be null without a frozen SHA-256"
        )
    return identity


def _load_and_validate_registration(
    registration_path: str | os.PathLike[str],
    *,
    workspace: Path,
    repository: Path,
) -> tuple[Path, dict[str, Any], FrozenDatasetIdentity, Path]:
    try:
        evidence = require_path_within_workspace(registration_path, workspace)
    except ArtifactIOError as error:
        raise DatasetIntakeError(str(error)) from error
    payload = _strict_json_object(evidence, "archive registration")
    _exact_fields(
        payload,
        {"schema", "dataset", "origin", "terms", "retrieval", "archive", "expectations"},
        "archive registration",
    )
    if payload["schema"] != DATASET_ARCHIVE_REGISTRATION_SCHEMA:
        raise DatasetIntakeError("unsupported archive registration schema")
    identity = _validate_identity_dict(payload["dataset"])

    origin = payload["origin"]
    _exact_fields(origin, {"kind", "source_url", "original_path", "supplied_by"}, "origin")
    if origin["kind"] == "download":
        registered_source = _validated_http_url(
            origin["source_url"], "origin.source_url"
        )
        if registered_source != identity.canonical_source_url:
            raise DatasetIntakeError(
                "registered source URL does not match the frozen canonical source"
            )
        if origin["original_path"] is not None or origin["supplied_by"] is not None:
            raise DatasetIntakeError("download origin fields are inconsistent")
    elif origin["kind"] == "local_user_provided":
        if origin["source_url"] is not None:
            raise DatasetIntakeError("local origin source_url must be null")
        _nonempty_string(origin["original_path"], "origin.original_path")
        _nonempty_string(origin["supplied_by"], "origin.supplied_by")
    else:
        raise DatasetIntakeError("registration origin kind is unsupported")

    terms = payload["terms"]
    _exact_fields(
        terms,
        {"dataset_use_authorization_status", "recorded_by", "reference"},
        "terms",
    )
    if (
        terms["dataset_use_authorization_status"]
        not in _ALLOWED_DATASET_USE_STATUSES
    ):
        raise DatasetIntakeError(
            "registration dataset-use authorization status is unsupported"
        )
    _nonempty_string(terms["recorded_by"], "terms.recorded_by")
    if terms["reference"] is not None:
        _nonempty_string(terms["reference"], "terms.reference")

    retrieval = payload["retrieval"]
    _exact_fields(
        retrieval,
        {"retrieved_at_utc", "response_metadata_path", "response_metadata_sha256"},
        "retrieval",
    )
    _timestamp_utc(retrieval["retrieved_at_utc"], lambda: datetime.now(timezone.utc))
    try:
        response_path = require_path_within_workspace(
            retrieval["response_metadata_path"], workspace
        )
    except (ArtifactIOError, TypeError) as error:
        raise DatasetIntakeError(f"invalid response metadata path: {error}") from error
    response_sha = _optional_sha256(
        retrieval["response_metadata_sha256"], "retrieval.response_metadata_sha256"
    )
    if response_sha is None or sha256_file(response_path) != response_sha:
        raise DatasetIntakeError("response metadata artifact hash mismatch")
    response = _strict_json_object(response_path, "response metadata")
    _exact_fields(
        response,
        {"schema", "dataset", "origin_kind", "retrieved_at_utc", "metadata"},
        "response metadata",
    )
    if (
        response["schema"] != DATASET_RESPONSE_METADATA_SCHEMA
        or response["dataset"] != identity.to_dict()
        or response["origin_kind"] != origin["kind"]
        or response["retrieved_at_utc"] != retrieval["retrieved_at_utc"]
        or type(response["metadata"]) is not dict
    ):
        raise DatasetIntakeError("response metadata artifact is inconsistent")
    _check_metadata_for_secrets(response["metadata"])

    archive_value = payload["archive"]
    _exact_fields(archive_value, {"path", "name", "bytes", "sha256"}, "archive")
    archive = _require_regular_external_archive(archive_value["path"], repository)
    if archive.name != archive_value["name"]:
        raise DatasetIntakeError("registered archive name/path mismatch")
    if (
        origin["kind"] == "download" or identity.known_expected_bytes is not None
    ) and archive.name.casefold() != identity.archive_name_hint.casefold():
        raise DatasetIntakeError(
            "registered archive filename does not match the frozen identity"
        )
    byte_count = _positive_integer(archive_value["bytes"], "archive.bytes")
    archive_sha = _optional_sha256(archive_value["sha256"], "archive.sha256")
    if archive_sha is None:
        raise DatasetIntakeError("archive.sha256 must not be null")
    if archive.stat().st_size != byte_count or sha256_file(archive) != archive_sha:
        raise DatasetIntakeError("registered archive bytes or SHA-256 changed")
    if origin["kind"] == "local_user_provided" and origin["original_path"] != str(archive):
        raise DatasetIntakeError("local origin path does not match archive path")

    expectations = payload["expectations"]
    _exact_fields(expectations, {"bytes", "sha256"}, "expectations")
    expected_bytes = _optional_positive_integer(expectations["bytes"], "expectations.bytes")
    expected_sha = _optional_sha256(expectations["sha256"], "expectations.sha256")
    if origin["kind"] == "download" and (
        expected_bytes is None or expected_sha is None
    ):
        raise DatasetIntakeError(
            "download registration omitted expected bytes or SHA-256"
        )
    if expected_bytes is not None and expected_bytes != byte_count:
        raise DatasetIntakeError("registered expected byte count is not satisfied")
    if expected_sha is not None and expected_sha != archive_sha:
        raise DatasetIntakeError("registered expected SHA-256 is not satisfied")
    if (
        identity.known_expected_bytes is not None
        and identity.known_expected_bytes != expected_bytes
    ):
        raise DatasetIntakeError("registration omitted or changed frozen expected bytes")
    if (
        identity.known_expected_sha256 is not None
        and identity.known_expected_sha256 != expected_sha
    ):
        raise DatasetIntakeError("registration omitted or changed frozen expected SHA-256")
    return evidence, payload, identity, archive


def _safe_relative_path(filename: str, *, directory: bool) -> tuple[str, tuple[str, ...]]:
    if type(filename) is not str or not filename or "\x00" in filename:
        raise DatasetIntakeError("ZIP member has an empty or NUL-containing name")
    if unicodedata.normalize("NFC", filename) != filename:
        raise DatasetIntakeError(f"ZIP member name is not Unicode NFC: {filename!r}")
    if "\\" in filename:
        raise DatasetIntakeError(f"ZIP member uses ambiguous backslashes: {filename!r}")
    if filename.startswith("/") or PureWindowsPath(filename).is_absolute():
        raise DatasetIntakeError(f"ZIP member path is absolute: {filename!r}")
    stripped = filename[:-1] if directory and filename.endswith("/") else filename
    if not stripped or stripped.endswith("/"):
        raise DatasetIntakeError(f"ZIP member path is ambiguous: {filename!r}")
    pure = PurePosixPath(stripped)
    if pure.is_absolute() or not pure.parts:
        raise DatasetIntakeError(f"ZIP member path is absolute or empty: {filename!r}")
    parts = tuple(pure.parts)
    if "/".join(parts) != stripped:
        raise DatasetIntakeError(f"ZIP member path is not canonical: {filename!r}")
    for component in parts:
        if component in {"", ".", ".."}:
            raise DatasetIntakeError(f"ZIP member traverses directories: {filename!r}")
        if component[-1] in {" ", "."}:
            raise DatasetIntakeError(
                f"ZIP member has Windows-ambiguous trailing characters: {filename!r}"
            )
        if any(ord(character) < 32 or character in '<>:"|?*' for character in component):
            raise DatasetIntakeError(
                f"ZIP member has cross-platform unsafe characters: {filename!r}"
            )
        reserved_base = component.split(".", 1)[0].casefold()
        if reserved_base in _WINDOWS_RESERVED_NAMES:
            raise DatasetIntakeError(
                f"ZIP member uses a reserved Windows name: {filename!r}"
            )
    return stripped, parts


def _validate_member_type(info: zipfile.ZipInfo, *, directory: bool) -> None:
    if info.flag_bits & 0x1:
        raise DatasetIntakeError(f"encrypted ZIP member is forbidden: {info.filename!r}")
    if info.flag_bits & ((1 << 5) | (1 << 6) | (1 << 13)):
        raise DatasetIntakeError(
            f"patched/strong-encrypted/masked ZIP flags are forbidden: {info.filename!r}"
        )
    if info.compress_type not in _ALLOWED_COMPRESSION_METHODS:
        raise DatasetIntakeError(
            f"unsupported ZIP compression method {info.compress_type}: {info.filename!r}"
        )
    if info.file_size < 0 or info.compress_size < 0:
        raise DatasetIntakeError(f"negative ZIP member size: {info.filename!r}")

    if info.create_system == 3:  # Unix mode is stored in the upper 16 bits.
        mode = (info.external_attr >> 16) & 0xFFFF
        file_type = stat.S_IFMT(mode)
        if file_type == stat.S_IFLNK:
            raise DatasetIntakeError(f"ZIP symlink is forbidden: {info.filename!r}")
        expected_type = stat.S_IFDIR if directory else stat.S_IFREG
        if file_type not in {0, expected_type}:
            raise DatasetIntakeError(
                f"ZIP special file is forbidden: {info.filename!r}"
            )
    elif info.create_system == 0:  # DOS/Windows attributes are in low bits.
        dos_attributes = info.external_attr & 0xFFFF
        if dos_attributes & 0x400:
            raise DatasetIntakeError(
                f"ZIP reparse-point member is forbidden: {info.filename!r}"
            )
        if dos_attributes & 0x08:  # Volume label, never a data file/directory.
            raise DatasetIntakeError(
                f"ZIP volume-label member is forbidden: {info.filename!r}"
            )
        if bool(dos_attributes & 0x10) != directory:
            raise DatasetIntakeError(
                f"ZIP filename/directory attributes disagree: {info.filename!r}"
            )
    else:
        raise DatasetIntakeError(
            f"ZIP member host type is ambiguous ({info.create_system}): {info.filename!r}"
        )


def _inspect_zip(
    archive: Path,
    *,
    max_members: int,
    max_total_uncompressed_bytes: int,
    max_member_uncompressed_bytes: int,
    max_compression_ratio: float,
) -> tuple[list[_InspectedMember], tuple[str, ...], int]:
    try:
        with zipfile.ZipFile(archive, "r") as bundle:
            infos = bundle.infolist()
    except (OSError, zipfile.BadZipFile, NotImplementedError) as error:
        raise DatasetIntakeError(f"cannot read ZIP central directory: {error}") from error
    if not infos:
        raise DatasetIntakeError("ZIP archive has no members")
    if len(infos) > max_members:
        raise DatasetIntakeError(
            f"ZIP member limit exceeded: {len(infos)} > {max_members}"
        )

    inspected: list[_InspectedMember] = []
    paths_by_casefold: dict[str, _InspectedMember] = {}
    total_bytes = 0
    for info in infos:
        directory = info.is_dir()
        _validate_member_type(info, directory=directory)
        # ``zipfile`` normalizes backslashes and truncates at NUL in
        # ``ZipInfo.filename``.  Security decisions must use ``orig_filename``
        # so the unsafe bytes represented by the decoded central-directory
        # name cannot disappear before validation.
        original_name = getattr(info, "orig_filename", info.filename)
        relative_path, parts = _safe_relative_path(original_name, directory=directory)
        if info.filename != relative_path + ("/" if directory else ""):
            raise DatasetIntakeError(
                f"ZIP member name was normalized ambiguously: {original_name!r}"
            )
        key = relative_path.casefold()
        previous = paths_by_casefold.get(key)
        if previous is not None:
            collision_kind = (
                "duplicate" if previous.relative_path == relative_path else "case collision"
            )
            raise DatasetIntakeError(
                f"ZIP member {collision_kind}: {previous.relative_path!r}, {relative_path!r}"
            )
        member = _InspectedMember(info, relative_path, parts, directory)
        paths_by_casefold[key] = member
        inspected.append(member)
        if not directory:
            if info.file_size > max_member_uncompressed_bytes:
                raise DatasetIntakeError(
                    f"ZIP member uncompressed-size limit exceeded: {relative_path!r}"
                )
            if info.file_size and info.compress_size == 0:
                raise DatasetIntakeError(
                    f"non-empty ZIP member has zero compressed size: {relative_path!r}"
                )
            if info.file_size / max(info.compress_size, 1) > max_compression_ratio:
                raise DatasetIntakeError(
                    f"ZIP member compression-ratio limit exceeded: {relative_path!r}"
                )
            total_bytes += info.file_size
            if total_bytes > max_total_uncompressed_bytes:
                raise DatasetIntakeError(
                    "ZIP total uncompressed-size limit exceeded"
                )

    # Reject a file that is also the lexical parent of another member.  This is
    # distinct from duplicate-name detection and varies by filesystem.
    files_by_key = {
        member.relative_path.casefold(): member
        for member in inspected
        if not member.is_directory
    }
    for member in inspected:
        for end in range(1, len(member.parts)):
            parent_key = "/".join(member.parts[:end]).casefold()
            if parent_key in files_by_key:
                raise DatasetIntakeError(
                    f"ZIP file/directory prefix collision: "
                    f"{files_by_key[parent_key].relative_path!r}, {member.relative_path!r}"
                )

    directories = {
        "/".join(member.parts[:end])
        for member in inspected
        for end in range(1, len(member.parts) + (1 if member.is_directory else 0))
    }
    return (
        sorted(inspected, key=lambda item: item.relative_path.encode("utf-8")),
        tuple(sorted(directories, key=lambda item: item.encode("utf-8"))),
        total_bytes,
    )


def _validated_required_prefixes(values: Sequence[str]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes, bytearray)) or not isinstance(values, Sequence):
        raise DatasetIntakeError("required_top_level_prefixes must be a sequence of paths")
    result: list[str] = []
    seen: set[str] = set()
    for index, value in enumerate(values):
        prefix, _parts = _safe_relative_path(
            _nonempty_string(value, f"required_top_level_prefixes[{index}]").rstrip("/"),
            directory=False,
        )
        key = prefix.casefold()
        if key in seen:
            raise DatasetIntakeError("required top-level prefixes contain duplicates")
        seen.add(key)
        result.append(prefix)
    return tuple(sorted(result, key=lambda item: item.encode("utf-8")))


def _remove_new_extraction_root(path: Path) -> None:
    """Best-effort cleanup confined to the newly created extraction root."""

    if not os.path.lexists(path):
        return
    if _is_link_or_reparse(path):
        # Never follow or recursively traverse a path whose identity changed.
        return
    shutil.rmtree(path, ignore_errors=True)


def _extract_members(
    archive: Path,
    destination: Path,
    members: Sequence[_InspectedMember],
) -> list[dict[str, Any]]:
    inventory: list[dict[str, Any]] = []
    try:
        with zipfile.ZipFile(archive, "r") as bundle:
            for member in members:
                target = destination.joinpath(*member.parts)
                if member.is_directory:
                    target.mkdir(parents=True, exist_ok=True)
                    if _is_link_or_reparse(target) or not target.is_dir():
                        raise DatasetIntakeError(
                            f"unsafe extracted directory appeared: {target}"
                        )
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                if _is_link_or_reparse(target.parent) or not target.parent.is_dir():
                    raise DatasetIntakeError(
                        f"unsafe extraction parent appeared: {target.parent}"
                    )
                digest = hashlib.sha256()
                written = 0
                try:
                    with bundle.open(member.info, "r") as source, target.open("xb") as output:
                        while chunk := source.read(1024 * 1024):
                            if written + len(chunk) > member.info.file_size:
                                raise DatasetIntakeError(
                                    f"ZIP member expanded beyond its declared size: "
                                    f"{member.relative_path!r}"
                                )
                            output.write(chunk)
                            digest.update(chunk)
                            written += len(chunk)
                        output.flush()
                        os.fsync(output.fileno())
                except (OSError, RuntimeError, zipfile.BadZipFile) as error:
                    raise DatasetIntakeError(
                        f"cannot extract ZIP member {member.relative_path!r}: {error}"
                    ) from error
                if written != member.info.file_size:
                    raise DatasetIntakeError(
                        f"extracted byte count mismatch for {member.relative_path!r}"
                    )
                inventory.append(
                    {
                        "relative_path": member.relative_path,
                        "bytes": written,
                        "sha256": digest.hexdigest(),
                        "zip_crc32": f"{member.info.CRC:08x}",
                        "compression_method": member.info.compress_type,
                    }
                )
    except DatasetIntakeError:
        raise
    except (OSError, zipfile.BadZipFile, NotImplementedError) as error:
        raise DatasetIntakeError(f"ZIP extraction failed: {error}") from error
    return inventory


def _verify_extracted_inventory(
    destination: Path,
    files: Sequence[Mapping[str, Any]],
    directories: Sequence[str],
) -> None:
    expected_file_paths = {item["relative_path"] for item in files}
    expected_directory_paths = set(directories)
    observed_files: set[str] = set()
    observed_directories: set[str] = set()
    try:
        for root, directory_names, file_names in os.walk(destination, followlinks=False):
            root_path = Path(root)
            if _is_link_or_reparse(root_path):
                raise DatasetIntakeError(f"extracted tree contains a link: {root_path}")
            directory_names.sort(key=lambda item: item.encode("utf-8"))
            file_names.sort(key=lambda item: item.encode("utf-8"))
            for name in directory_names:
                path = root_path / name
                if _is_link_or_reparse(path) or not path.is_dir():
                    raise DatasetIntakeError(f"extracted tree contains unsafe directory: {path}")
                observed_directories.add(path.relative_to(destination).as_posix())
            for name in file_names:
                path = root_path / name
                if _is_link_or_reparse(path) or not path.is_file():
                    raise DatasetIntakeError(f"extracted tree contains unsafe file: {path}")
                observed_files.add(path.relative_to(destination).as_posix())
    except OSError as error:
        raise DatasetIntakeError(f"cannot verify extracted tree: {error}") from error
    if observed_files != expected_file_paths or observed_directories != expected_directory_paths:
        raise DatasetIntakeError("extracted tree membership changed before evidence creation")
    for item in files:
        path = destination.joinpath(*PurePosixPath(item["relative_path"]).parts)
        if path.stat().st_size != item["bytes"] or sha256_file(path) != item["sha256"]:
            raise DatasetIntakeError(
                f"extracted file changed before evidence creation: {item['relative_path']!r}"
            )


def _workspace_evidence_file(value: Any, field: str, workspace: Path) -> Path:
    if type(value) is not str or not value:
        raise DatasetIntakeError(f"{field} must be an absolute evidence path")
    supplied = Path(value)
    if not supplied.is_absolute():
        raise DatasetIntakeError(f"{field} must be an absolute evidence path")
    if _is_link_or_reparse(supplied):
        raise DatasetIntakeError(f"{field} must not be a link or reparse point")
    try:
        path = require_path_within_workspace(supplied, workspace)
    except ArtifactIOError as error:
        raise DatasetIntakeError(str(error)) from error
    if _is_link_or_reparse(path) or not path.is_file():
        raise DatasetIntakeError(f"{field} must be one regular evidence file: {path}")
    return path


def _workspace_extraction_root(value: Any, field: str, workspace: Path) -> Path:
    if type(value) is not str or not value:
        raise DatasetIntakeError(f"{field} must be an absolute directory path")
    supplied = Path(value)
    if not supplied.is_absolute():
        raise DatasetIntakeError(f"{field} must be an absolute directory path")
    if _is_link_or_reparse(supplied):
        raise DatasetIntakeError(f"{field} must not be a link or reparse point")
    try:
        path = require_path_within_workspace(supplied, workspace)
    except ArtifactIOError as error:
        raise DatasetIntakeError(str(error)) from error
    if _is_link_or_reparse(path) or not path.is_dir():
        raise DatasetIntakeError(f"{field} must be one regular directory: {path}")
    return path


def _verify_inventory_against_archive(
    archive: Path,
    files: Sequence[Mapping[str, Any]],
    directories: Sequence[str],
) -> None:
    """Re-derive every inventory digest from immutable ZIP member bytes."""

    members, archive_directories, total_bytes = _inspect_zip(
        archive,
        max_members=DEFAULT_MAX_ARCHIVE_MEMBERS,
        max_total_uncompressed_bytes=DEFAULT_MAX_TOTAL_UNCOMPRESSED_BYTES,
        max_member_uncompressed_bytes=DEFAULT_MAX_MEMBER_UNCOMPRESSED_BYTES,
        max_compression_ratio=DEFAULT_MAX_COMPRESSION_RATIO,
    )
    payload_members = [member for member in members if not member.is_directory]
    if tuple(directories) != archive_directories:
        raise DatasetIntakeError(
            "extraction inventory directories differ from the registered ZIP"
        )
    if len(payload_members) != len(files) or total_bytes != sum(
        item["bytes"] for item in files
    ):
        raise DatasetIntakeError(
            "extraction inventory counts differ from the registered ZIP"
        )
    try:
        with zipfile.ZipFile(archive, "r") as bundle:
            for member, item in zip(payload_members, files, strict=True):
                if (
                    item["relative_path"] != member.relative_path
                    or item["bytes"] != member.info.file_size
                    or item["zip_crc32"] != f"{member.info.CRC:08x}"
                    or item["compression_method"] != member.info.compress_type
                ):
                    raise DatasetIntakeError(
                        "extraction inventory metadata differs from registered ZIP member "
                        f"{member.relative_path!r}"
                    )
                digest = hashlib.sha256()
                observed_bytes = 0
                with bundle.open(member.info, "r") as stream:
                    while chunk := stream.read(1024 * 1024):
                        digest.update(chunk)
                        observed_bytes += len(chunk)
                if observed_bytes != item["bytes"] or digest.hexdigest() != item["sha256"]:
                    raise DatasetIntakeError(
                        "extraction inventory digest differs from registered ZIP member "
                        f"{member.relative_path!r}"
                    )
    except DatasetIntakeError:
        raise
    except (OSError, zipfile.BadZipFile, RuntimeError, NotImplementedError) as error:
        raise DatasetIntakeError(
            f"cannot cryptographically bind inventory to registered ZIP: {error}"
        ) from error


def validate_dataset_extraction_manifest(
    extraction_manifest_path: str | os.PathLike[str],
    *,
    workspace_root: str | os.PathLike[str],
    repository_root: str | os.PathLike[str],
    expected_dataset_key: str | None = None,
) -> ValidatedExtractionEvidence:
    """Recursively reopen and verify a previously extracted dataset.

    The extraction manifest is only an entry point.  Authority comes from
    rehashing its registration, retrieval metadata, frozen archive, inventory,
    and every extracted file.  This prevents a caller-created directory with
    plausible names/counts from masquerading as registered source data.
    """

    try:
        workspace = require_external_workspace(workspace_root, repository_root)
    except ArtifactIOError as error:
        raise DatasetIntakeError(str(error)) from error
    repository = Path(repository_root).expanduser().resolve(strict=False)
    supplied_manifest = Path(extraction_manifest_path).expanduser()
    if not supplied_manifest.is_absolute():
        raise DatasetIntakeError("extraction_manifest_path must be absolute")
    if _is_link_or_reparse(supplied_manifest):
        raise DatasetIntakeError(
            "extraction manifest must not be a link or reparse point"
        )
    try:
        manifest_path = require_path_within_workspace(supplied_manifest, workspace)
        manifest_hash_before = sha256_file(manifest_path)
    except ArtifactIOError as error:
        raise DatasetIntakeError(str(error)) from error
    manifest = _strict_json_object(manifest_path, "dataset extraction manifest")
    _exact_fields(
        manifest,
        {
            "schema",
            "dataset",
            "registration",
            "archive",
            "extraction",
            "inventory",
            "expectations",
            "actual",
        },
        "dataset extraction manifest",
    )
    if manifest["schema"] != DATASET_EXTRACTION_MANIFEST_SCHEMA:
        raise DatasetIntakeError("unsupported dataset extraction manifest schema")
    manifest_identity = _validate_identity_dict(manifest["dataset"])
    if expected_dataset_key is not None and manifest_identity.key != expected_dataset_key:
        raise DatasetIntakeError(
            "dataset extraction manifest names the wrong frozen dataset identity"
        )

    registration_ref = manifest["registration"]
    _exact_fields(
        registration_ref, {"path", "sha256"}, "extraction manifest registration"
    )
    registration_path = _workspace_evidence_file(
        registration_ref["path"], "registration.path", workspace
    )
    registration_sha = _optional_sha256(
        registration_ref["sha256"], "registration.sha256"
    )
    if registration_sha is None or sha256_file(registration_path) != registration_sha:
        raise DatasetIntakeError("extraction registration artifact hash mismatch")
    registration_evidence, registration, registration_identity, archive = (
        _load_and_validate_registration(
            registration_path, workspace=workspace, repository=repository
        )
    )
    if registration_evidence != registration_path:
        raise DatasetIntakeError("extraction registration path changed during validation")
    if registration_identity != manifest_identity:
        raise DatasetIntakeError("extraction and registration dataset identities differ")
    if registration["terms"]["dataset_use_authorization_status"] not in (
        _EXTRACTION_ALLOWED_DATASET_USE_STATUSES
    ):
        raise DatasetIntakeError("registered dataset-use authorization no longer permits use")

    archive_ref = manifest["archive"]
    _exact_fields(
        archive_ref, {"path", "bytes", "sha256", "preserved"}, "manifest archive"
    )
    if archive_ref["preserved"] is not True:
        raise DatasetIntakeError("extraction manifest does not preserve the source archive")
    if (
        archive_ref["path"] != registration["archive"]["path"]
        or archive_ref["bytes"] != registration["archive"]["bytes"]
        or archive_ref["sha256"] != registration["archive"]["sha256"]
    ):
        raise DatasetIntakeError("manifest archive differs from its registration")
    archive_bytes = _positive_integer(archive_ref["bytes"], "manifest archive.bytes")
    archive_sha = _optional_sha256(archive_ref["sha256"], "manifest archive.sha256")
    if archive_sha is None:
        raise DatasetIntakeError("manifest archive SHA-256 must not be null")
    if str(archive) != archive_ref["path"]:
        raise DatasetIntakeError("manifest archive path differs from the live archive")

    extraction = manifest["extraction"]
    _exact_fields(
        extraction, {"root", "extracted_at_utc", "tree_sha256"}, "manifest extraction"
    )
    extracted_root = _workspace_extraction_root(
        extraction["root"], "extraction.root", workspace
    )
    _timestamp_utc(extraction["extracted_at_utc"], lambda: datetime.now(timezone.utc))
    tree_sha = _optional_sha256(extraction["tree_sha256"], "extraction.tree_sha256")
    if tree_sha is None:
        raise DatasetIntakeError("extraction.tree_sha256 must not be null")

    inventory_ref = manifest["inventory"]
    _exact_fields(
        inventory_ref, {"path", "sha256", "schema"}, "manifest inventory"
    )
    if inventory_ref["schema"] != DATASET_EXTRACTION_INVENTORY_SCHEMA:
        raise DatasetIntakeError("manifest inventory schema is unsupported")
    inventory_path = _workspace_evidence_file(
        inventory_ref["path"], "inventory.path", workspace
    )
    inventory_sha = _optional_sha256(inventory_ref["sha256"], "inventory.sha256")
    if inventory_sha is None or sha256_file(inventory_path) != inventory_sha:
        raise DatasetIntakeError("extraction inventory artifact hash mismatch")
    if extracted_root == inventory_path or extracted_root in inventory_path.parents:
        raise DatasetIntakeError("extraction inventory must remain outside the raw tree")
    inventory = _strict_json_object(inventory_path, "dataset extraction inventory")
    _exact_fields(
        inventory,
        {
            "schema",
            "archive",
            "extracted_root",
            "directories",
            "files",
            "counts",
            "tree_sha256",
        },
        "dataset extraction inventory",
    )
    if inventory["schema"] != DATASET_EXTRACTION_INVENTORY_SCHEMA:
        raise DatasetIntakeError("unsupported dataset extraction inventory schema")
    if inventory["archive"] != {
        "path": str(archive),
        "bytes": archive_bytes,
        "sha256": archive_sha,
    }:
        raise DatasetIntakeError("inventory archive differs from its registration")
    if inventory["extracted_root"] != str(extracted_root):
        raise DatasetIntakeError("inventory names a different extraction root")

    directories_value = inventory["directories"]
    if type(directories_value) is not list:
        raise DatasetIntakeError("inventory.directories must be a list")
    directories: list[str] = []
    for index, value in enumerate(directories_value):
        normalized, _ = _safe_relative_path(
            _nonempty_string(value, f"inventory.directories[{index}]"),
            directory=False,
        )
        directories.append(normalized)
    if directories != sorted(set(directories), key=lambda item: item.encode("utf-8")):
        raise DatasetIntakeError("inventory directories are duplicate or non-deterministic")

    files_value = inventory["files"]
    if type(files_value) is not list:
        raise DatasetIntakeError("inventory.files must be a list")
    files: list[dict[str, Any]] = []
    for index, value in enumerate(files_value):
        _exact_fields(
            value,
            {
                "relative_path",
                "bytes",
                "sha256",
                "zip_crc32",
                "compression_method",
            },
            f"inventory.files[{index}]",
        )
        relative, _ = _safe_relative_path(
            _nonempty_string(value["relative_path"], f"inventory.files[{index}].relative_path"),
            directory=False,
        )
        byte_count = _nonnegative_integer(value["bytes"], f"inventory.files[{index}].bytes")
        digest = _optional_sha256(value["sha256"], f"inventory.files[{index}].sha256")
        crc = value["zip_crc32"]
        if (
            digest is None
            or type(crc) is not str
            or len(crc) != 8
            or crc != crc.lower()
            or any(character not in "0123456789abcdef" for character in crc)
            or type(value["compression_method"]) is not int
            or value["compression_method"] not in _ALLOWED_COMPRESSION_METHODS
        ):
            raise DatasetIntakeError(f"inventory.files[{index}] metadata is invalid")
        files.append(
            {
                "relative_path": relative,
                "bytes": byte_count,
                "sha256": digest,
                "zip_crc32": crc,
                "compression_method": value["compression_method"],
            }
        )
    relative_paths = [item["relative_path"] for item in files]
    if relative_paths != sorted(set(relative_paths), key=lambda item: item.encode("utf-8")):
        raise DatasetIntakeError("inventory files are duplicate or non-deterministic")

    counts = inventory["counts"]
    _exact_fields(
        counts,
        {"files", "images", "directories", "extracted_bytes"},
        "inventory counts",
    )
    file_count = _nonnegative_integer(counts["files"], "inventory counts.files")
    image_count = _nonnegative_integer(counts["images"], "inventory counts.images")
    directory_count = _nonnegative_integer(
        counts["directories"], "inventory counts.directories"
    )
    extracted_bytes = _nonnegative_integer(
        counts["extracted_bytes"], "inventory counts.extracted_bytes"
    )
    computed_image_count = sum(
        PurePosixPath(item["relative_path"]).suffix.casefold() in _IMAGE_SUFFIXES
        for item in files
    )
    if (
        file_count != len(files)
        or image_count != computed_image_count
        or directory_count != len(directories)
        or extracted_bytes != sum(item["bytes"] for item in files)
    ):
        raise DatasetIntakeError("inventory counts differ from the recorded file tree")
    inventory_tree_sha = _optional_sha256(
        inventory["tree_sha256"], "inventory.tree_sha256"
    )
    tree_basis = {
        "directories": directories,
        "files": [
            {
                "relative_path": item["relative_path"],
                "bytes": item["bytes"],
                "sha256": item["sha256"],
            }
            for item in files
        ],
    }
    computed_tree_sha = hashlib.sha256(canonical_json_bytes(tree_basis)).hexdigest()
    if inventory_tree_sha != tree_sha or computed_tree_sha != tree_sha:
        raise DatasetIntakeError("extraction tree SHA-256 evidence is inconsistent")
    _verify_inventory_against_archive(archive, files, directories)

    expectations = manifest["expectations"]
    _exact_fields(
        expectations,
        {"payload_member_count", "image_count", "required_top_level_prefixes"},
        "manifest expectations",
    )
    expected_files = _optional_positive_integer(
        expectations["payload_member_count"], "expectations.payload_member_count"
    )
    expected_images = _optional_positive_integer(
        expectations["image_count"], "expectations.image_count"
    )
    prefixes = _validated_required_prefixes(expectations["required_top_level_prefixes"])
    if list(prefixes) != expectations["required_top_level_prefixes"]:
        raise DatasetIntakeError("manifest required prefixes are non-deterministic")
    if expected_files is not None and expected_files != file_count:
        raise DatasetIntakeError("manifest expected file count differs from inventory")
    if expected_images is not None and expected_images != image_count:
        raise DatasetIntakeError("manifest expected image count differs from inventory")
    available_paths = set(relative_paths) | set(directories)
    for prefix in prefixes:
        key = prefix.casefold()
        if not any(
            candidate.casefold() == key
            or candidate.casefold().startswith(key + "/")
            for candidate in available_paths
        ):
            raise DatasetIntakeError("manifest required prefix is absent from inventory")

    actual = manifest["actual"]
    _exact_fields(
        actual,
        {
            "payload_member_count",
            "image_count",
            "directory_count",
            "extracted_bytes",
        },
        "manifest actual",
    )
    if actual != {
        "payload_member_count": file_count,
        "image_count": image_count,
        "directory_count": directory_count,
        "extracted_bytes": extracted_bytes,
    }:
        raise DatasetIntakeError("manifest actual counts differ from inventory")

    _verify_extracted_inventory(extracted_root, files, directories)
    try:
        if (
            sha256_file(manifest_path) != manifest_hash_before
            or sha256_file(registration_path) != registration_sha
            or sha256_file(inventory_path) != inventory_sha
            or archive.stat().st_size != archive_bytes
            or sha256_file(archive) != archive_sha
        ):
            raise DatasetIntakeError("dataset lineage evidence changed during validation")
    except ArtifactIOError as error:
        raise DatasetIntakeError(str(error)) from error

    return ValidatedExtractionEvidence(
        manifest_path=manifest_path,
        manifest_sha256=manifest_hash_before,
        registration_path=registration_path,
        registration_sha256=registration_sha,
        archive_path=archive,
        archive_bytes=archive_bytes,
        archive_sha256=archive_sha,
        inventory_path=inventory_path,
        inventory_sha256=inventory_sha,
        extracted_root=extracted_root,
        tree_sha256=tree_sha,
        file_count=file_count,
        image_count=image_count,
        extracted_bytes=extracted_bytes,
        dataset=manifest_identity,
    )


def extract_registered_zip(
    registration_path: str | os.PathLike[str],
    extraction_root: str | os.PathLike[str],
    inventory_path: str | os.PathLike[str],
    extraction_manifest_path: str | os.PathLike[str],
    *,
    workspace_root: str | os.PathLike[str],
    repository_root: str | os.PathLike[str],
    expected_member_count: int | None = None,
    expected_image_count: int | None = None,
    required_top_level_prefixes: Sequence[str] = (),
    max_members: int = DEFAULT_MAX_ARCHIVE_MEMBERS,
    max_total_uncompressed_bytes: int = DEFAULT_MAX_TOTAL_UNCOMPRESSED_BYTES,
    max_member_uncompressed_bytes: int = DEFAULT_MAX_MEMBER_UNCOMPRESSED_BYTES,
    max_compression_ratio: float = DEFAULT_MAX_COMPRESSION_RATIO,
    extracted_at_utc: str | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> ExtractionResult:
    """Safely extract a registered ZIP and bind an exact deterministic tree.

    ``expected_member_count`` means non-directory payload members.  Image count
    is determined from common image suffixes.  The destination root and both
    evidence files must not already exist.  The source archive is re-hashed
    before and after extraction and is always preserved.
    """

    try:
        workspace = require_external_workspace(workspace_root, repository_root)
    except ArtifactIOError as error:
        raise DatasetIntakeError(str(error)) from error
    repository = Path(repository_root).expanduser().resolve(strict=False)
    try:
        destination = require_path_within_workspace(extraction_root, workspace)
    except ArtifactIOError as error:
        raise DatasetIntakeError(str(error)) from error
    inventory_evidence, manifest_evidence = _validate_artifact_targets(
        workspace, inventory_path, extraction_manifest_path
    )
    if any(
        evidence == destination or destination in evidence.parents
        for evidence in (inventory_evidence, manifest_evidence)
    ):
        raise DatasetIntakeError(
            "extraction evidence must be outside, not inside, the extracted dataset tree"
        )
    if os.path.lexists(destination):
        raise DatasetIntakeError(f"refusing to overwrite extraction root: {destination}")
    for candidate in (destination, inventory_evidence, manifest_evidence):
        if candidate == repository or repository in candidate.parents:
            raise DatasetIntakeError("dataset extraction artifacts must be outside repository")

    expected_files = _optional_positive_integer(
        expected_member_count, "expected_member_count"
    )
    expected_images = _optional_positive_integer(
        expected_image_count, "expected_image_count"
    )
    max_members = _positive_integer(max_members, "max_members")
    max_total_uncompressed_bytes = _positive_integer(
        max_total_uncompressed_bytes, "max_total_uncompressed_bytes"
    )
    max_member_uncompressed_bytes = _positive_integer(
        max_member_uncompressed_bytes, "max_member_uncompressed_bytes"
    )
    if (
        type(max_compression_ratio) not in {int, float}
        or isinstance(max_compression_ratio, bool)
        or not (1.0 <= float(max_compression_ratio) < float("inf"))
    ):
        raise DatasetIntakeError("max_compression_ratio must be a finite number >= 1")
    prefixes = _validated_required_prefixes(required_top_level_prefixes)
    timestamp = _timestamp_utc(extracted_at_utc, now)

    try:
        registration_evidence, registration, identity, archive = (
            _load_and_validate_registration(
                registration_path, workspace=workspace, repository=repository
            )
        )
    except ArtifactIOError as error:
        raise DatasetIntakeError(f"invalid archive registration evidence: {error}") from error
    authorization_status = registration["terms"][
        "dataset_use_authorization_status"
    ]
    if authorization_status not in _EXTRACTION_ALLOWED_DATASET_USE_STATUSES:
        raise DatasetIntakeError(
            "dataset-use authorization gate blocks extraction: "
            f"status={authorization_status!r}"
        )
    archive_sha = registration["archive"]["sha256"]
    archive_bytes = registration["archive"]["bytes"]
    members, directories, declared_uncompressed_bytes = _inspect_zip(
        archive,
        max_members=max_members,
        max_total_uncompressed_bytes=max_total_uncompressed_bytes,
        max_member_uncompressed_bytes=max_member_uncompressed_bytes,
        max_compression_ratio=float(max_compression_ratio),
    )
    payload_members = tuple(member for member in members if not member.is_directory)
    file_count = len(payload_members)
    image_count = sum(
        PurePosixPath(member.relative_path).suffix.casefold() in _IMAGE_SUFFIXES
        for member in payload_members
    )
    if expected_files is not None and file_count != expected_files:
        raise DatasetIntakeError(
            f"ZIP payload member count mismatch: expected {expected_files}, got {file_count}"
        )
    if expected_images is not None and image_count != expected_images:
        raise DatasetIntakeError(
            f"ZIP image count mismatch: expected {expected_images}, got {image_count}"
        )
    available_paths = {member.relative_path.casefold() for member in members}
    for prefix in prefixes:
        key = prefix.casefold()
        if not any(path == key or path.startswith(key + "/") for path in available_paths):
            raise DatasetIntakeError(f"required ZIP prefix is missing: {prefix!r}")

    created_root = False
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if _is_link_or_reparse(destination.parent):
            raise DatasetIntakeError(
                f"extraction parent must not be a link or reparse point: {destination.parent}"
            )
        destination.mkdir(exist_ok=False)
        created_root = True
        file_inventory = _extract_members(archive, destination, members)
        _verify_extracted_inventory(destination, file_inventory, directories)
        if sum(item["bytes"] for item in file_inventory) != declared_uncompressed_bytes:
            raise DatasetIntakeError("extracted total does not match ZIP central directory")
        if archive.stat().st_size != archive_bytes or sha256_file(archive) != archive_sha:
            raise DatasetIntakeError("archive changed during extraction")

        tree_basis = {
            "directories": list(directories),
            "files": [
                {
                    "relative_path": item["relative_path"],
                    "bytes": item["bytes"],
                    "sha256": item["sha256"],
                }
                for item in file_inventory
            ],
        }
        tree_sha256 = hashlib.sha256(canonical_json_bytes(tree_basis)).hexdigest()
        inventory_payload = {
            "schema": DATASET_EXTRACTION_INVENTORY_SCHEMA,
            "archive": {
                "path": str(archive),
                "bytes": archive_bytes,
                "sha256": archive_sha,
            },
            "extracted_root": str(destination),
            "directories": list(directories),
            "files": file_inventory,
            "counts": {
                "files": file_count,
                "images": image_count,
                "directories": len(directories),
                "extracted_bytes": declared_uncompressed_bytes,
            },
            "tree_sha256": tree_sha256,
        }
        atomic_create_json(inventory_evidence, inventory_payload)
        inventory_sha256 = sha256_file(inventory_evidence)
        manifest_payload = {
            "schema": DATASET_EXTRACTION_MANIFEST_SCHEMA,
            "dataset": identity.to_dict(),
            "registration": {
                "path": str(registration_evidence),
                "sha256": sha256_file(registration_evidence),
            },
            "archive": {
                "path": str(archive),
                "bytes": archive_bytes,
                "sha256": archive_sha,
                "preserved": True,
            },
            "extraction": {
                "root": str(destination),
                "extracted_at_utc": timestamp,
                "tree_sha256": tree_sha256,
            },
            "inventory": {
                "path": str(inventory_evidence),
                "sha256": inventory_sha256,
                "schema": DATASET_EXTRACTION_INVENTORY_SCHEMA,
            },
            "expectations": {
                "payload_member_count": expected_files,
                "image_count": expected_images,
                "required_top_level_prefixes": list(prefixes),
            },
            "actual": {
                "payload_member_count": file_count,
                "image_count": image_count,
                "directory_count": len(directories),
                "extracted_bytes": declared_uncompressed_bytes,
            },
        }
        atomic_create_json(manifest_evidence, manifest_payload)
    except DatasetIntakeError:
        if created_root:
            _remove_new_extraction_root(destination)
        raise
    except (ArtifactIOError, FileExistsError, OSError) as error:
        if created_root:
            _remove_new_extraction_root(destination)
        raise DatasetIntakeError(f"dataset extraction failed: {error}") from error

    return ExtractionResult(
        manifest_path=manifest_evidence,
        inventory_path=inventory_evidence,
        extracted_root=destination,
        file_count=file_count,
        image_count=image_count,
        extracted_bytes=declared_uncompressed_bytes,
        tree_sha256=tree_sha256,
    )


__all__ = [
    "ArchiveRegistrationResult",
    "DATASET_ARCHIVE_REGISTRATION_SCHEMA",
    "DEFAULT_MAX_ARCHIVE_MEMBERS",
    "DEFAULT_MAX_COMPRESSION_RATIO",
    "DEFAULT_MAX_MEMBER_UNCOMPRESSED_BYTES",
    "DEFAULT_MAX_TOTAL_UNCOMPRESSED_BYTES",
    "DATASET_EXTRACTION_INVENTORY_SCHEMA",
    "DATASET_EXTRACTION_MANIFEST_SCHEMA",
    "DATASET_RESPONSE_METADATA_SCHEMA",
    "DatasetIntakeError",
    "ExtractionResult",
    "FROZEN_DATASET_IDENTITIES",
    "FrozenDatasetIdentity",
    "ValidatedExtractionEvidence",
    "extract_registered_zip",
    "register_dataset_archive",
    "validate_dataset_extraction_manifest",
]
