"""Fail-closed helpers for external rescue-model artifacts.

Training data, environments, weights, and generated evidence do not belong in
the VeriSwarm Git checkout.  This module centralizes that boundary and provides
small, dependency-free integrity/write primitives used by the training tools.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


DEFAULT_HASH_CHUNK_SIZE = 1024 * 1024


class ArtifactIOError(RuntimeError):
    """An artifact path or write violates the frozen evidence policy."""


def _resolved(path: str | os.PathLike[str]) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def require_external_workspace(
    workspace_root: str | os.PathLike[str],
    repository_root: str | os.PathLike[str],
) -> Path:
    """Return a normalized workspace path after proving it is outside Git.

    Both paths must be absolute as supplied.  The workspace may neither be
    inside the repository nor contain it.  Rejecting the second relationship
    prevents callers from accidentally treating a broad parent such as
    ``C:\\projects`` as an external-artifact root.
    """

    workspace = Path(workspace_root).expanduser()
    repository = Path(repository_root).expanduser()
    if not workspace.is_absolute():
        raise ArtifactIOError("external workspace_root must be an absolute path")
    if not repository.is_absolute():
        raise ArtifactIOError("repository_root must be an absolute path")

    workspace = workspace.resolve(strict=False)
    repository = repository.resolve(strict=False)
    if (
        workspace == repository
        or repository in workspace.parents
        or workspace in repository.parents
    ):
        raise ArtifactIOError(
            f"workspace must be separate from repository: {workspace}"
        )
    return workspace


def require_path_within_workspace(
    path: str | os.PathLike[str],
    workspace_root: str | os.PathLike[str],
    *,
    allow_workspace_root: bool = False,
) -> Path:
    """Return ``path`` after proving it is within ``workspace_root``.

    This is a lexical/resolved-path containment check.  Callers handling an
    untrusted pre-existing directory must additionally reject symlinks while
    walking it; the VisDrone converter does so for source files.
    """

    candidate = Path(path).expanduser()
    workspace = Path(workspace_root).expanduser()
    if not candidate.is_absolute() or not workspace.is_absolute():
        raise ArtifactIOError("artifact path and workspace_root must be absolute")
    candidate = candidate.resolve(strict=False)
    workspace = workspace.resolve(strict=False)
    if candidate == workspace:
        if allow_workspace_root:
            return candidate
        raise ArtifactIOError("artifact path must be below, not equal to, workspace_root")
    if workspace not in candidate.parents:
        raise ArtifactIOError(f"artifact path escapes external workspace: {candidate}")
    return candidate


def sha256_file(
    path: str | os.PathLike[str],
    *,
    chunk_size: int = DEFAULT_HASH_CHUNK_SIZE,
) -> str:
    """Compute a lowercase SHA-256 digest without loading the file into memory."""

    if isinstance(chunk_size, bool) or not isinstance(chunk_size, int) or chunk_size <= 0:
        raise ArtifactIOError("chunk_size must be a positive integer")
    source = Path(path)
    if source.is_symlink() or not source.is_file():
        raise ArtifactIOError(f"hash source must be one regular file: {source}")

    digest = hashlib.sha256()
    try:
        with source.open("rb") as stream:
            while chunk := stream.read(chunk_size):
                digest.update(chunk)
    except OSError as error:
        raise ArtifactIOError(f"cannot hash {source}: {error}") from error
    return digest.hexdigest()


def _strict_json_value(value: Any, field: str) -> Any:
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if type(key) is not str:
                raise ArtifactIOError(f"{field} JSON object keys must be strings")
            result[key] = _strict_json_value(item, f"{field}.{key}")
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [
            _strict_json_value(item, f"{field}[{index}]")
            for index, item in enumerate(value)
        ]
    if value is None or type(value) in {str, bool, int, float}:
        return value
    raise ArtifactIOError(f"{field} contains a value that is not strict JSON")


def canonical_json_bytes(payload: Mapping[str, Any]) -> bytes:
    """Serialize one JSON object deterministically and reject NaN/Infinity."""

    if not isinstance(payload, Mapping):
        raise ArtifactIOError("JSON artifact payload must be an object")
    try:
        rendered = json.dumps(
            _strict_json_value(payload, "payload"),
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            separators=(",", ": "),
        )
    except (TypeError, ValueError) as error:
        raise ArtifactIOError(f"payload is not strict JSON: {error}") from error
    return (rendered + "\n").encode("utf-8")


def atomic_create_json(
    target: str | os.PathLike[str], payload: Mapping[str, Any]
) -> Path:
    """Atomically create a complete JSON file and never replace an existing path.

    A fully flushed same-directory temporary file is published with a
    create-once atomic filesystem operation: ``rename`` on Windows (where it
    refuses an existing destination) and ``link`` on POSIX.  There is
    deliberately no non-atomic fallback.
    """

    destination = Path(target)
    if not destination.is_absolute():
        raise ArtifactIOError("JSON artifact target must be an absolute path")
    if os.path.lexists(destination):
        raise ArtifactIOError(f"refusing to overwrite existing artifact: {destination}")
    data = canonical_json_bytes(payload)

    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise ArtifactIOError(
            f"cannot create artifact directory {destination.parent}: {error}"
        ) from error

    temporary_name: str | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
        )
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            if os.name == "nt":
                # Windows os.rename is atomic on the same volume and raises if
                # the destination exists.  It also works on volumes (such as
                # exFAT scratch drives) that do not support hard links.
                os.rename(temporary_name, destination)
            else:
                # POSIX rename replaces an existing file, so link is the
                # create-once atomic primitive here.
                os.link(temporary_name, destination)
        except FileExistsError as error:
            raise ArtifactIOError(
                f"refusing to overwrite existing artifact: {destination}"
            ) from error
        except OSError as error:
            raise ArtifactIOError(
                f"atomic create-once publish failed for {destination}: {error}"
            ) from error
    except ArtifactIOError:
        raise
    except OSError as error:
        raise ArtifactIOError(f"cannot create JSON artifact {destination}: {error}") from error
    finally:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            except OSError:
                # The completed destination is still valid.  A stale uniquely
                # named temporary file is preferable to deleting evidence.
                pass
    return destination


def create_external_json(
    target: str | os.PathLike[str],
    payload: Mapping[str, Any],
    *,
    workspace_root: str | os.PathLike[str],
    repository_root: str | os.PathLike[str],
) -> Path:
    """Enforce the external-workspace boundary, then create JSON once."""

    workspace = require_external_workspace(workspace_root, repository_root)
    destination = require_path_within_workspace(target, workspace)
    return atomic_create_json(destination, payload)
