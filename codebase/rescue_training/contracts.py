"""Strict, immutable training-plan contracts for ``sar-rgb-person-v1``."""

from __future__ import annotations

import json
import math
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal


TRAINING_PLAN_SCHEMA = "veriswarm.rescue.training_plan.v1"
MODEL_ID = "sar-rgb-person-v1"
PERSON_CLASS_MAP = MappingProxyType({0: "person_candidate"})
RUNPOD_WORKSPACE_ROOT = "/workspace/samik-rescue-person-model-20260822"

FROZEN_RUNTIME = MappingProxyType(
    {
        "python": "3.12.13",
        "torch": "2.13.0+cu130",
        "torchvision": "0.28.0+cu130",
        "ultralytics": "8.4.56",
        "required_gpu_substring": "RTX 5090",
    }
)

FROZEN_GATES = MappingProxyType(
    {
        "official_train_images": 6471,
        "official_val_images": 548,
        "label_audit_images": 100,
        "seed": 0,
        "deterministic": True,
        "cache": False,
        "save": True,
        "save_period": 1,
        "smoke_batch_fraction": 0.70,
        "jetson_benchmark_minutes": 15,
    }
)

_ROOT_FIELDS = frozenset(
    {
        "schema",
        "model_id",
        "class_map",
        "runtime",
        "workspace_root",
        "candidates",
        "gates",
    }
)
_CANDIDATE_FIELDS = frozenset(
    {"name", "architecture", "imgsz", "epochs", "batch", "required", "stage"}
)
_FORBIDDEN_STATE_LABELS = frozenset(
    {
        "safe",
        "safe_walking",
        "disaster",
        "review_required",
        "assistance_needed",
    }
)


class TrainingContractError(ValueError):
    """A plan is ambiguous or violates a frozen training boundary."""


def _strict_json_loads(text: str) -> Any:
    def reject_constant(value: str) -> None:
        raise TrainingContractError(f"non-finite JSON constant is forbidden: {value}")

    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise TrainingContractError(f"duplicate JSON field is forbidden: {key!r}")
            result[key] = value
        return result

    try:
        return json.loads(
            text,
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicate_keys,
        )
    except TrainingContractError:
        raise
    except (json.JSONDecodeError, TypeError) as error:
        raise TrainingContractError(f"invalid training-plan JSON: {error}") from error


def _object(value: Any, field: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise TrainingContractError(f"{field} must be a JSON object")
    if not all(type(key) is str for key in value):
        raise TrainingContractError(f"{field} keys must be strings")
    return value


def _exact_fields(value: Mapping[str, Any], expected: frozenset[str], field: str) -> None:
    actual = frozenset(value)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        raise TrainingContractError(
            f"{field} fields are not frozen; missing={missing}, unknown={unknown}"
        )


def _string(value: Any, field: str) -> str:
    if type(value) is not str or not value or value.strip() != value:
        raise TrainingContractError(f"{field} must be a non-empty trimmed string")
    return value


def _integer(value: Any, field: str, *, minimum: int = 1) -> int:
    if type(value) is not int or value < minimum:
        raise TrainingContractError(f"{field} must be an integer >= {minimum}")
    return value


def _same_json_value(actual: Any, expected: Any) -> bool:
    if isinstance(expected, Mapping):
        if not isinstance(actual, Mapping):
            return False
        return actual.keys() == expected.keys() and all(
            _same_json_value(actual[key], expected[key]) for key in expected
        )
    if type(actual) is not type(expected):
        return False
    return actual == expected


def _freeze_json(value: Any, field: str) -> Any:
    if type(value) is dict:
        return MappingProxyType(
            {key: _freeze_json(item, f"{field}.{key}") for key, item in value.items()}
        )
    if type(value) is list:
        return tuple(_freeze_json(item, f"{field}[]") for item in value)
    if value is None or type(value) in {str, bool, int}:
        return value
    if type(value) is float and math.isfinite(value):
        return value
    raise TrainingContractError(f"{field} contains a non-JSON or non-finite value")


@dataclass(frozen=True, slots=True)
class CandidateSpec:
    """One predeclared detector candidate; no state classifier is permitted here."""

    name: str
    architecture: str
    imgsz: int
    epochs: int
    batch: float | int | None
    required: bool
    stage: Literal["smoke", "full"]

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CandidateSpec":
        raw = _object(value, "candidate")
        _exact_fields(raw, _CANDIDATE_FIELDS, "candidate")
        name = _string(raw["name"], "candidate.name")
        architecture = _string(raw["architecture"], "candidate.architecture")
        imgsz = _integer(raw["imgsz"], "candidate.imgsz")
        epochs = _integer(raw["epochs"], "candidate.epochs")
        required = raw["required"]
        if type(required) is not bool:
            raise TrainingContractError("candidate.required must be boolean")
        stage = raw["stage"]
        if stage not in {"smoke", "full"}:
            raise TrainingContractError("candidate.stage must be 'smoke' or 'full'")

        batch = raw["batch"]
        if stage == "smoke":
            if type(batch) not in {int, float} or isinstance(batch, bool):
                raise TrainingContractError("smoke candidate.batch must be 0.70")
            batch = float(batch)
            if not math.isfinite(batch) or batch != 0.70:
                raise TrainingContractError("smoke candidate.batch must be 0.70")
        elif batch is not None and (type(batch) is not int or batch <= 0):
            raise TrainingContractError(
                "full candidate.batch must be null until the probe, then a positive integer"
            )
        return cls(name, architecture, imgsz, epochs, batch, required, stage)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "architecture": self.architecture,
            "imgsz": self.imgsz,
            "epochs": self.epochs,
            "batch": self.batch,
            "required": self.required,
            "stage": self.stage,
        }


_FROZEN_CANDIDATE_SHAPES: Mapping[str, Mapping[str, Any]] = MappingProxyType(
    {
        "yolov8n-640-smoke": MappingProxyType(
            {
                "architecture": "yolov8n.pt",
                "imgsz": 640,
                "epochs": 5,
                "batch": 0.70,
                "required": True,
                "stage": "smoke",
            }
        ),
        "yolov8n-640": MappingProxyType(
            {
                "architecture": "yolov8n.pt",
                "imgsz": 640,
                "epochs": 40,
                "batch": None,
                "required": True,
                "stage": "full",
            }
        ),
        "yolov8n-960": MappingProxyType(
            {
                "architecture": "yolov8n.pt",
                "imgsz": 960,
                "epochs": 40,
                "batch": None,
                "required": True,
                "stage": "full",
            }
        ),
        "yolov8s-640": MappingProxyType(
            {
                "architecture": "yolov8s.pt",
                "imgsz": 640,
                "epochs": 40,
                "batch": None,
                "required": False,
                "stage": "full",
            }
        ),
    }
)


@dataclass(frozen=True, slots=True)
class TrainingPlan:
    """Validated detector-only plan with immutable nested mappings."""

    schema: str
    model_id: str
    class_map: Mapping[int, str]
    runtime: Mapping[str, str]
    workspace_root: str
    candidates: Mapping[str, CandidateSpec]
    gates: Mapping[str, Any]

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TrainingPlan":
        raw = _object(value, "training plan")
        _exact_fields(raw, _ROOT_FIELDS, "training plan")

        schema = _string(raw["schema"], "schema")
        if schema != TRAINING_PLAN_SCHEMA:
            raise TrainingContractError(f"unsupported training-plan schema: {schema!r}")
        model_id = _string(raw["model_id"], "model_id")
        if model_id != MODEL_ID:
            raise TrainingContractError(
                "this plan is detector-only; state classifier must use a separate plan"
            )

        class_map_raw = _object(raw["class_map"], "class_map")
        if class_map_raw != {"0": "person_candidate"}:
            labels = {str(item).strip().lower() for item in class_map_raw.values()}
            if labels & _FORBIDDEN_STATE_LABELS:
                raise TrainingContractError(
                    "safe/disaster state classes are forbidden in the person detector"
                )
            raise TrainingContractError(
                "detector class_map must be exactly {'0': 'person_candidate'}"
            )

        runtime_raw = _object(raw["runtime"], "runtime")
        if not _same_json_value(runtime_raw, FROZEN_RUNTIME):
            raise TrainingContractError("runtime does not match the frozen RunPod line")

        workspace_root = _string(raw["workspace_root"], "workspace_root")
        if workspace_root != RUNPOD_WORKSPACE_ROOT:
            raise TrainingContractError(
                f"workspace_root must be exactly {RUNPOD_WORKSPACE_ROOT!r}"
            )

        candidates_raw = _object(raw["candidates"], "candidates")
        allowed = frozenset(_FROZEN_CANDIDATE_SHAPES)
        mandatory = frozenset({"yolov8n-640-smoke", "yolov8n-640", "yolov8n-960"})
        actual = frozenset(candidates_raw)
        if not mandatory <= actual or not actual <= allowed:
            raise TrainingContractError(
                "candidates must contain the smoke probe, mandatory yolov8n@640/960, "
                "and only the optional yolov8s@640 candidate"
            )

        candidates: dict[str, CandidateSpec] = {}
        for key in sorted(candidates_raw):
            candidate = CandidateSpec.from_dict(candidates_raw[key])
            if candidate.name != key:
                raise TrainingContractError(
                    f"candidate map key {key!r} must match candidate.name {candidate.name!r}"
                )
            expected = _FROZEN_CANDIDATE_SHAPES[key]
            candidate_shape = candidate.to_dict()
            candidate_shape.pop("name")
            # A full-run positive integer batch is the only allowed post-probe
            # mutation from the initial frozen plan.
            if candidate.stage == "full" and type(candidate.batch) is int:
                candidate_shape["batch"] = None
            if not _same_json_value(candidate_shape, expected):
                raise TrainingContractError(f"candidate {key!r} is not frozen")
            candidates[key] = candidate

        gates_raw = _object(raw["gates"], "gates")
        if not _same_json_value(gates_raw, FROZEN_GATES):
            raise TrainingContractError("gates do not match the frozen acceptance gates")

        return cls(
            schema=schema,
            model_id=model_id,
            class_map=PERSON_CLASS_MAP,
            runtime=FROZEN_RUNTIME,
            workspace_root=workspace_root,
            candidates=MappingProxyType(candidates),
            gates=_freeze_json(dict(gates_raw), "gates"),
        )

    @classmethod
    def from_json_text(cls, text: str) -> "TrainingPlan":
        if type(text) is not str:
            raise TrainingContractError("training-plan JSON must be text")
        return cls.from_dict(_strict_json_loads(text))

    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> "TrainingPlan":
        source = Path(path)
        if source.is_symlink() or not source.is_file():
            raise TrainingContractError(f"training plan must be one regular file: {source}")
        try:
            text = source.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            raise TrainingContractError(f"cannot read training plan {source}: {error}") from error
        return cls.from_json_text(text)

    def candidate(self, name: str) -> CandidateSpec:
        try:
            return self.candidates[name]
        except (KeyError, TypeError) as error:
            raise TrainingContractError(f"unknown candidate: {name!r}") from error

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "model_id": self.model_id,
            "class_map": {str(key): value for key, value in self.class_map.items()},
            "runtime": dict(self.runtime),
            "workspace_root": self.workspace_root,
            "candidates": {
                name: candidate.to_dict()
                for name, candidate in self.candidates.items()
            },
            "gates": dict(self.gates),
        }
