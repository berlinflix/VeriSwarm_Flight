"""Strict loader for the frozen VeriSwarm rescue-model registry contract."""

from __future__ import annotations

import json
import hashlib
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


MODEL_SCHEMA = "veriswarm.model.v1"
CANONICAL_LABELS = frozenset(
    {
        "person_candidate",
        "water_or_flood",
        "road_blocked",
        "debris",
        "fire",
        "smoke",
        "structure_damage",
    }
)
SHA256_RE = re.compile(r"[0-9a-fA-F]{64}")
MODEL_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


class ModelRegistryError(ValueError):
    """The registry is missing, malformed, unsupported, or inconsistent."""


@dataclass(frozen=True)
class ModelInput:
    width: int
    height: int
    color: str


@dataclass(frozen=True)
class RescueModelRegistry:
    schema: str
    model_id: str
    task: str
    modality: str
    classes: tuple[str, ...]
    weights_sha256: str
    training_manifest_sha256: str
    thresholds_sha256: str
    framework: str
    input: ModelInput
    status: str

    def to_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["classes"] = list(self.classes)
        return record


def _required_text(record: Mapping[str, Any], key: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ModelRegistryError(f"registry field {key!r} must be non-empty text")
    return value.strip()


def _required_sha256(record: Mapping[str, Any], key: str) -> str:
    value = _required_text(record, key)
    if not SHA256_RE.fullmatch(value):
        raise ModelRegistryError(f"registry field {key!r} must be 64 hex digits")
    return value.lower()


def load_model_registry(path: Path) -> RescueModelRegistry:
    """Load and validate one immutable ``veriswarm.model.v1`` JSON record."""
    if not path.is_file():
        raise ModelRegistryError(f"model registry file not found: {path}")
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ModelRegistryError(f"cannot read model registry {path}: {exc}") from exc
    if not isinstance(record, Mapping):
        raise ModelRegistryError("model registry root must be a JSON object")

    schema = _required_text(record, "schema")
    if schema != MODEL_SCHEMA:
        raise ModelRegistryError(
            f"unsupported model registry schema {schema!r}; expected {MODEL_SCHEMA!r}"
        )
    model_id = _required_text(record, "model_id")
    if not MODEL_ID_RE.fullmatch(model_id):
        raise ModelRegistryError("registry model_id contains unsupported characters")
    task = _required_text(record, "task")
    if task != "detect":
        raise ModelRegistryError("multi-camera adapter supports only task='detect'")
    modality = _required_text(record, "modality").lower()
    if modality not in {"rgb", "thermal"}:
        raise ModelRegistryError(f"unsupported model modality: {modality}")

    classes_value = record.get("classes")
    if not isinstance(classes_value, list) or not classes_value:
        raise ModelRegistryError("registry classes must be a non-empty JSON array")
    if not all(isinstance(item, str) and item for item in classes_value):
        raise ModelRegistryError("every registry class must be non-empty text")
    classes = tuple(classes_value)
    if len(set(classes)) != len(classes):
        raise ModelRegistryError("registry classes must be unique")
    unsupported = sorted(set(classes) - CANONICAL_LABELS)
    if unsupported:
        raise ModelRegistryError(
            "unsupported rescue class(es): " + ", ".join(unsupported)
        )

    input_value = record.get("input")
    if not isinstance(input_value, Mapping):
        raise ModelRegistryError("registry input must be a JSON object")
    width = input_value.get("width")
    height = input_value.get("height")
    color = input_value.get("color")
    if (
        not isinstance(width, int)
        or isinstance(width, bool)
        or width <= 0
        or not isinstance(height, int)
        or isinstance(height, bool)
        or height <= 0
    ):
        raise ModelRegistryError("registry input width and height must be positive integers")
    if color != "RGB":
        raise ModelRegistryError("registry input color must be 'RGB'")

    status = _required_text(record, "status")
    if status not in {"candidate", "approved"}:
        raise ModelRegistryError("registry status must be 'candidate' or 'approved'")
    return RescueModelRegistry(
        schema=schema,
        model_id=model_id,
        task=task,
        modality=modality,
        classes=classes,
        weights_sha256=_required_sha256(record, "weights_sha256"),
        training_manifest_sha256=_required_sha256(
            record, "training_manifest_sha256"
        ),
        thresholds_sha256=_required_sha256(record, "thresholds_sha256"),
        framework=_required_text(record, "framework"),
        input=ModelInput(width=width, height=height, color=color),
        status=status,
    )


def validate_model_class_map(
    registry: RescueModelRegistry,
    model_class_names: Sequence[tuple[int, str]],
) -> None:
    """Require the loaded model's indexed names to exactly match its registry."""
    indexed = tuple(sorted((int(index), str(name)) for index, name in model_class_names))
    expected = tuple(enumerate(registry.classes))
    if indexed != expected:
        raise ModelRegistryError(
            f"model class map {indexed!r} does not match registry {expected!r}"
        )


def verify_registry_weights(
    registry: RescueModelRegistry, weights_path: Path
) -> str:
    """Hash model bytes once at startup and reject a registry mismatch."""
    if not weights_path.is_file():
        raise ModelRegistryError(f"weights file not found: {weights_path}")
    digest = hashlib.sha256()
    try:
        with weights_path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise ModelRegistryError(f"cannot read weights {weights_path}: {exc}") from exc
    actual = digest.hexdigest()
    if actual != registry.weights_sha256:
        raise ModelRegistryError(
            "model SHA-256 does not match the immutable registry value"
        )
    return actual
