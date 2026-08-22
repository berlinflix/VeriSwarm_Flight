"""Deterministic dual-teacher weak labels for the person-state classifier.

This module turns detector-issued ``person_candidate`` boxes into *unverified*
training labels for the companion red/green state classifier.  Dataset names,
archive identities, source-family names, paths, and sample IDs are deliberately
absent from the object passed to either teacher.  Both teachers see only the
same hash-bound RGB context crop:

* a posture/mobility teacher votes from upright/walking versus distress cues;
* a scene-context teacher votes from no-hazard versus flood/fire/debris cues.

A sample is retained only when both independent, differently hashed teachers
make the same high-confidence vote under the frozen policy.  Everything else
is excluded from training and represented as red ``UNVERIFIED`` at runtime.
The resulting records remain compatible with ``person_state_dataset.v2`` and
preserve the already assigned group-safe train/validation/test split.

The module downloads nothing.  Production adapters implement the protocols
below around locally provisioned, hash-identifiable teacher artifacts.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
import re
import shutil
import tempfile
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal, Protocol

from .artifact_io import (
    ArtifactIOError,
    canonical_json_bytes,
    require_external_workspace,
    require_path_within_workspace,
    sha256_file,
)
from .state_dataset import (
    AUTOMATED_LABEL_SOURCE,
    FROZEN_SOURCE_DATASETS,
    SAFE_REASON,
    SPLITS,
    STATE_CLASSES,
    STATE_DATASET_SCHEMA,
    STATE_MODEL_ID,
    STATE_RECORD_SCHEMA,
    WEAK_SUPERVISION_EVIDENCE_STATUS,
    WEAK_SUPERVISION_SCHEMA,
    validate_state_dataset_manifest,
)


WEAK_LABEL_CANDIDATE_SCHEMA = "veriswarm.rescue.person_state_candidate.v1"
WEAK_LABEL_POLICY_SCHEMA = "veriswarm.rescue.person_state_weak_label_policy.v1"
WEAK_LABEL_DECISION_SCHEMA = "veriswarm.rescue.person_state_weak_label_decision.v1"
WEAK_LABEL_BUNDLE_SCHEMA = "veriswarm.rescue.person_state_labeler_bundle.v1"
WEAK_LABEL_GENERATION_SCHEMA = "veriswarm.rescue.person_state_weak_label_run.v1"

LABELER_ID = "dual-teacher-posture-hazard-consensus"
LABELER_VERSION = "1.0.0"
PERSON_LABEL = "person_candidate"

POSTURE_CATEGORIES = (
    "upright_walking",
    "fallen",
    "trapped",
    "injured",
    "partially_buried",
    "other_or_uncertain",
)
HAZARD_CATEGORIES = (
    "no_hazard",
    "flood",
    "fire",
    "debris",
    "other_or_uncertain",
)
POSTURE_DISTRESS = frozenset(
    {"fallen", "trapped", "injured", "partially_buried"}
)
HAZARD_DISTRESS = frozenset({"flood", "fire", "debris"})

MIN_TOP_PROBABILITY = 0.85
MIN_TOP_MARGIN = 0.20
MIN_SOURCE_FAMILIES_PER_CLASS = 2
CONTEXT_MARGIN_RATIO = 0.35
CROP_SIZE = (224, 224)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CANDIDATE_FIELDS = frozenset(
    {
        "schema",
        "sample_id",
        "split",
        "source_dataset_id",
        "source_family_id",
        "source_group_id",
        "source_media_path",
        "source_media_sha256",
        "frame_index",
        "detector_label",
        "detector_confidence",
        "detector_model_id",
        "detector_model_path",
        "detector_model_sha256",
        "person_bbox_xyxy_abs",
    }
)
_SOURCE_FIELDS = frozenset(
    {
        "dataset_id",
        "archive_path",
        "archive_sha256",
        "dataset_use_authorization_status",
        "terms_recorded_by",
        "role",
    }
)
_FORBIDDEN_INPUTS = [
    "source_dataset_id",
    "source_archive_identity",
    "dataset_origin",
]
_CROP_POLICY = {
    "context_margin_ratio": CONTEXT_MARGIN_RATIO,
    "output_size_hw": [CROP_SIZE[1], CROP_SIZE[0]],
    "padding": "constant_black",
    "interpolation": "bilinear",
}
_SPLIT_POLICY = {
    "group_key": "source_group_id",
    "splits": list(SPLITS),
    "test_used_for_selection": False,
}

def _frozen_policy_payload() -> dict[str, Any]:
    """Return a fresh policy so imported mutable objects cannot alter execution."""

    return {
        "schema": WEAK_LABEL_POLICY_SCHEMA,
        "policy_id": "sar-person-state-dual-teacher-consensus-v1",
        "classes": dict(STATE_CLASSES),
        "evidence_status": WEAK_SUPERVISION_EVIDENCE_STATUS,
        "human_review_required": False,
        "dataset_origin_allowed": False,
        "teacher_roles": ["posture_mobility", "scene_hazard_context"],
        "posture_categories": list(POSTURE_CATEGORIES),
        "hazard_categories": list(HAZARD_CATEGORIES),
        "minimum_top_probability": MIN_TOP_PROBABILITY,
        "minimum_top_margin": MIN_TOP_MARGIN,
        "repeat_inference_consistency_required": True,
        "consensus_required": True,
        "safe_rule": {
            "posture_vote": "upright_walking",
            "hazard_vote": "no_hazard",
            "state_label": "safe_walking",
            "state_reason": SAFE_REASON,
        },
        "disaster_rule": {
            "posture_votes": sorted(POSTURE_DISTRESS),
            "hazard_votes": sorted(HAZARD_DISTRESS),
            "state_label": "disaster_stressed",
        },
        "abstain_rule": {
            "training_action": "exclude",
            "runtime_box_color": "red",
            "runtime_status": "UNVERIFIED",
        },
        "crop_policy": dict(_CROP_POLICY),
        "split_policy": dict(_SPLIT_POLICY),
        "minimum_source_families_per_class": MIN_SOURCE_FAMILIES_PER_CLASS,
        "forbidden_teacher_inputs": list(_FORBIDDEN_INPUTS),
    }


# Public read/reference copy.  Generation always calls the private constructor
# above and therefore cannot be influenced if a caller mutates this object.
FROZEN_POLICY: dict[str, Any] = _frozen_policy_payload()


class WeakLabelerError(ValueError):
    """Weak-label inputs, teachers, decisions, or evidence fail closed."""


@dataclass(frozen=True, slots=True)
class ContextCrop:
    """The complete and intentionally origin-blind teacher input.

    No source path, dataset ID, family, group, sample ID, or frame ID is
    available through this object.  Production backends decode ``png_bytes``
    and must return one probability for every frozen category.
    """

    png_bytes: bytes
    width: int
    height: int
    sha256: str


class PostureMobilityTeacher(Protocol):
    """Production adapter for a local posture/mobility teacher model."""

    model_id: str
    model_version: str
    model_artifact_path: str | os.PathLike[str]
    deterministic: bool
    inference_configuration: Mapping[str, Any]

    def predict_posture(self, crop: ContextCrop) -> Mapping[str, float]: ...


class SceneHazardTeacher(Protocol):
    """Production adapter for a local scene hazard/rescue-context teacher."""

    model_id: str
    model_version: str
    model_artifact_path: str | os.PathLike[str]
    deterministic: bool
    inference_configuration: Mapping[str, Any]

    def predict_hazard(self, crop: ContextCrop) -> Mapping[str, float]: ...


@dataclass(frozen=True, slots=True)
class SignalSummary:
    probabilities: tuple[tuple[str, float], ...]
    top_reason: str
    top_probability: float
    margin: float
    vote: Literal["safe_walking", "disaster_stressed"] | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "probabilities": dict(self.probabilities),
            "top_reason": self.top_reason,
            "top_probability": self.top_probability,
            "margin": self.margin,
            "vote": self.vote,
        }


@dataclass(frozen=True, slots=True)
class WeakLabelDecision:
    state_label: Literal["safe_walking", "disaster_stressed"] | None
    state_reason: str
    confidence: float | None
    training_action: Literal["retain", "exclude"]
    runtime_box_color: Literal["green", "red"]
    runtime_status: str
    posture: SignalSummary
    hazard: SignalSummary

    @property
    def abstained(self) -> bool:
        return self.state_label is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "state_label": self.state_label,
            "state_reason": self.state_reason,
            "confidence": self.confidence,
            "training_action": self.training_action,
            "runtime_presentation": {
                "box_color": self.runtime_box_color,
                "status": self.runtime_status,
            },
        }


@dataclass(frozen=True, slots=True)
class _TeacherIdentity:
    role: str
    model_id: str
    model_version: str
    artifact_path: str
    artifact_sha256: str
    deterministic: bool
    inference_configuration: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "model_id": self.model_id,
            "model_version": self.model_version,
            "artifact_path": self.artifact_path,
            "artifact_sha256": self.artifact_sha256,
            "deterministic": self.deterministic,
            "inference_configuration": self.inference_configuration,
        }


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise WeakLabelerError(f"duplicate JSON field is forbidden: {key!r}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise WeakLabelerError(f"non-finite JSON constant is forbidden: {value}")


def _loads(text: str, field: str) -> Any:
    try:
        return json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except WeakLabelerError:
        raise
    except (json.JSONDecodeError, TypeError) as error:
        raise WeakLabelerError(f"{field} is not strict JSON: {error}") from error


def _exact_fields(value: Mapping[str, Any], expected: frozenset[str], field: str) -> None:
    actual = frozenset(value)
    if actual != expected:
        raise WeakLabelerError(
            f"{field} fields differ; missing={sorted(expected - actual)}, "
            f"unknown={sorted(actual - expected)}"
        )


def _text(value: Any, field: str) -> str:
    if type(value) is not str or not value or value.strip() != value:
        raise WeakLabelerError(f"{field} must be non-empty trimmed text")
    return value


def _sha256(value: Any, field: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise WeakLabelerError(f"{field} must be a lowercase SHA-256")
    return value


def _number(value: Any, field: str, *, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or type(value) not in {int, float}:
        raise WeakLabelerError(f"{field} must be numeric")
    result = float(value)
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise WeakLabelerError(f"{field} must be finite in [{minimum}, {maximum}]")
    return result


def _timestamp(value: str, field: str) -> str:
    text = _text(value, field)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise WeakLabelerError(f"{field} must be ISO-8601") from error
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise WeakLabelerError(f"{field} must identify UTC explicitly")
    return text


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _canonical_hash(value: Mapping[str, Any]) -> str:
    try:
        payload = canonical_json_bytes(value)
    except ArtifactIOError as error:
        raise WeakLabelerError(str(error)) from error
    return hashlib.sha256(payload).hexdigest()


def _normalized_json_mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or not all(type(key) is str for key in value):
        raise WeakLabelerError(f"{field} must be a JSON object")
    try:
        normalized = json.loads(canonical_json_bytes(value).decode("utf-8"))
    except (ArtifactIOError, json.JSONDecodeError) as error:
        raise WeakLabelerError(f"{field} is not strict JSON: {error}") from error
    forbidden_keys = {
        "source_dataset_id",
        "source_archive_identity",
        "dataset_origin",
        "source_family_id",
        "source_media_path",
        "sample_id",
    }

    def inspect(item: Any, path: str) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                if key.lower() in forbidden_keys:
                    raise WeakLabelerError(
                        f"{field} contains forbidden provenance input key at {path}.{key}"
                    )
                inspect(child, f"{path}.{key}")
        elif isinstance(item, list):
            for index, child in enumerate(item):
                inspect(child, f"{path}[{index}]")

    inspect(normalized, field)
    return normalized


def _bbox(value: Any, field: str) -> tuple[float, float, float, float]:
    if type(value) is not list or len(value) != 4:
        raise WeakLabelerError(f"{field} must contain four numbers")
    result = tuple(
        _number(item, f"{field}[{index}]", minimum=0.0, maximum=float("inf"))
        for index, item in enumerate(value)
    )
    if result[2] <= result[0] or result[3] <= result[1]:
        raise WeakLabelerError(f"{field} must satisfy x2>x1 and y2>y1")
    return result  # type: ignore[return-value]


def _normalize_probabilities(
    value: Mapping[str, float],
    categories: tuple[str, ...],
    field: str,
) -> tuple[tuple[str, float], ...]:
    if not isinstance(value, Mapping) or set(value) != set(categories):
        raise WeakLabelerError(
            f"{field} must contain exactly the frozen categories {list(categories)!r}"
        )
    normalized = tuple(
        (
            category,
            _number(
                value[category],
                f"{field}.{category}",
                minimum=0.0,
                maximum=1.0,
            ),
        )
        for category in categories
    )
    total = math.fsum(probability for _, probability in normalized)
    if not math.isclose(total, 1.0, abs_tol=1e-6, rel_tol=0.0):
        raise WeakLabelerError(f"{field} probabilities must sum to 1.0")
    return normalized


def _reproducible_prediction(
    callback: Callable[[ContextCrop], Mapping[str, float]],
    crop: ContextCrop,
    *,
    categories: tuple[str, ...],
    field: str,
) -> dict[str, float]:
    """Require exact repeated output for the exact same immutable crop."""

    try:
        first_raw = callback(crop)
        second_raw = callback(crop)
    except Exception as error:
        raise WeakLabelerError(
            f"{field} inference failed for a hash-bound context crop: {error}"
        ) from error
    first = _normalize_probabilities(first_raw, categories, f"{field}.first")
    second = _normalize_probabilities(second_raw, categories, f"{field}.repeat")
    if first != second:
        raise WeakLabelerError(
            f"{field} is nondeterministic for an identical context crop"
        )
    return dict(first)


def _signal_summary(
    value: Mapping[str, float],
    *,
    categories: tuple[str, ...],
    safe_category: str,
    disaster_categories: frozenset[str],
    field: str,
) -> SignalSummary:
    normalized = _normalize_probabilities(value, categories, field)
    ranked = sorted(
        normalized,
        key=lambda item: (-item[1], categories.index(item[0])),
    )
    top_reason, top_probability = ranked[0]
    margin = top_probability - ranked[1][1]
    vote: Literal["safe_walking", "disaster_stressed"] | None = None
    if top_probability >= MIN_TOP_PROBABILITY and margin >= MIN_TOP_MARGIN:
        if top_reason == safe_category:
            vote = "safe_walking"
        elif top_reason in disaster_categories:
            vote = "disaster_stressed"
    return SignalSummary(
        probabilities=normalized,
        top_reason=top_reason,
        top_probability=top_probability,
        margin=margin,
        vote=vote,
    )


def decide_teacher_signals(
    posture_probabilities: Mapping[str, float],
    hazard_probabilities: Mapping[str, float],
) -> WeakLabelDecision:
    """Apply the frozen policy without accepting any provenance arguments."""

    posture = _signal_summary(
        posture_probabilities,
        categories=POSTURE_CATEGORIES,
        safe_category="upright_walking",
        disaster_categories=POSTURE_DISTRESS,
        field="posture_probabilities",
    )
    hazard = _signal_summary(
        hazard_probabilities,
        categories=HAZARD_CATEGORIES,
        safe_category="no_hazard",
        disaster_categories=HAZARD_DISTRESS,
        field="hazard_probabilities",
    )
    if posture.vote is None or hazard.vote is None:
        low_roles = [
            role
            for role, signal in (("posture", posture), ("hazard", hazard))
            if signal.vote is None
        ]
        return WeakLabelDecision(
            state_label=None,
            state_reason="low_confidence_or_ambiguous:" + ",".join(low_roles),
            confidence=None,
            training_action="exclude",
            runtime_box_color="red",
            runtime_status="UNVERIFIED",
            posture=posture,
            hazard=hazard,
        )
    if posture.vote != hazard.vote:
        return WeakLabelDecision(
            state_label=None,
            state_reason="teacher_disagreement",
            confidence=None,
            training_action="exclude",
            runtime_box_color="red",
            runtime_status="UNVERIFIED",
            posture=posture,
            hazard=hazard,
        )
    confidence = min(posture.top_probability, hazard.top_probability)
    if posture.vote == "safe_walking":
        return WeakLabelDecision(
            state_label="safe_walking",
            state_reason=SAFE_REASON,
            confidence=confidence,
            training_action="retain",
            runtime_box_color="green",
            runtime_status="WEAK_SAFE_WALKING_UNVERIFIED",
            posture=posture,
            hazard=hazard,
        )

    posture_reasons = {
        "fallen": "fallen",
        "trapped": "trapped",
        "injured": "injured",
        "partially_buried": "partially_buried",
    }
    hazard_reasons = {
        "flood": "flood_exposure",
        "fire": "fire_exposure",
        "debris": "debris_exposure",
    }
    # Pick the strongest visual reason; exact ties deterministically prefer the
    # posture/mobility cue.  Both teachers have already voted disaster.
    reason = (
        posture_reasons[posture.top_reason]
        if posture.top_probability >= hazard.top_probability
        else hazard_reasons[hazard.top_reason]
    )
    return WeakLabelDecision(
        state_label="disaster_stressed",
        state_reason=reason,
        confidence=confidence,
        training_action="retain",
        runtime_box_color="red",
        runtime_status="WEAK_DISASTER_STRESSED_UNVERIFIED",
        posture=posture,
        hazard=hazard,
    )


def _teacher_identity(
    teacher: Any,
    *,
    role: str,
    workspace: Path,
) -> _TeacherIdentity:
    model_id = _text(getattr(teacher, "model_id", None), f"{role}.model_id")
    model_version = _text(
        getattr(teacher, "model_version", None), f"{role}.model_version"
    )
    if getattr(teacher, "deterministic", None) is not True:
        raise WeakLabelerError(f"{role} teacher must declare deterministic=True")
    try:
        artifact = require_path_within_workspace(
            getattr(teacher, "model_artifact_path"), workspace
        )
    except (ArtifactIOError, TypeError) as error:
        raise WeakLabelerError(f"{role} teacher artifact is unsafe: {error}") from error
    if artifact.is_symlink() or not artifact.is_file():
        raise WeakLabelerError(f"{role} teacher artifact must be one regular file")
    configuration = _normalized_json_mapping(
        getattr(teacher, "inference_configuration", None),
        f"{role}.inference_configuration",
    )
    return _TeacherIdentity(
        role=role,
        model_id=model_id,
        model_version=model_version,
        artifact_path=str(artifact),
        artifact_sha256=sha256_file(artifact),
        deterministic=True,
        inference_configuration=configuration,
    )


def _load_candidates(
    path: Path,
    *,
    workspace: Path,
) -> list[dict[str, Any]]:
    if path.is_symlink() or not path.is_file():
        raise WeakLabelerError("candidate records must be one regular JSONL file")
    candidates: list[dict[str, Any]] = []
    seen_samples: set[str] = set()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise WeakLabelerError(f"cannot read candidate records: {error}") from error
    if not lines or any(not line for line in lines):
        raise WeakLabelerError("candidate records must be non-empty without blank lines")
    for index, line in enumerate(lines):
        field = f"candidate_records[{index}]"
        raw = _loads(line, field)
        if type(raw) is not dict:
            raise WeakLabelerError(f"{field} must be an object")
        _exact_fields(raw, _CANDIDATE_FIELDS, field)
        if raw["schema"] != WEAK_LABEL_CANDIDATE_SCHEMA:
            raise WeakLabelerError(f"{field} has an unsupported schema")
        sample_id = _text(raw["sample_id"], f"{field}.sample_id")
        if sample_id in seen_samples:
            raise WeakLabelerError(f"duplicate candidate sample_id: {sample_id!r}")
        seen_samples.add(sample_id)
        split = raw["split"]
        if split not in SPLITS:
            raise WeakLabelerError(f"{field}.split must be train, val, or test")
        dataset_id = _text(raw["source_dataset_id"], f"{field}.source_dataset_id")
        if dataset_id not in FROZEN_SOURCE_DATASETS:
            raise WeakLabelerError(f"{field} names an unsupported source dataset")
        _text(raw["source_family_id"], f"{field}.source_family_id")
        _text(raw["source_group_id"], f"{field}.source_group_id")
        try:
            media = require_path_within_workspace(raw["source_media_path"], workspace)
            detector = require_path_within_workspace(
                raw["detector_model_path"], workspace
            )
        except (ArtifactIOError, TypeError) as error:
            raise WeakLabelerError(f"{field} contains an unsafe path: {error}") from error
        for artifact, name, expected in (
            (media, "source_media", raw["source_media_sha256"]),
            (detector, "detector_model", raw["detector_model_sha256"]),
        ):
            if artifact.is_symlink() or not artifact.is_file():
                raise WeakLabelerError(f"{field}.{name} must be one regular file")
            expected_hash = _sha256(expected, f"{field}.{name}_sha256")
            if sha256_file(artifact) != expected_hash:
                raise WeakLabelerError(f"{field}.{name} SHA-256 mismatch")
        frame_index = raw["frame_index"]
        if frame_index is not None and (
            type(frame_index) is not int or isinstance(frame_index, bool) or frame_index < 0
        ):
            raise WeakLabelerError(f"{field}.frame_index must be null or >= 0")
        if raw["detector_label"] != PERSON_LABEL:
            raise WeakLabelerError(f"{field} must be a person_candidate detection")
        _text(raw["detector_model_id"], f"{field}.detector_model_id")
        detector_confidence = _number(
            raw["detector_confidence"],
            f"{field}.detector_confidence",
            minimum=0.0,
            maximum=1.0,
        )
        if detector_confidence <= 0.0:
            raise WeakLabelerError(f"{field}.detector_confidence must be positive")
        candidate = dict(raw)
        candidate["source_media_path"] = str(media)
        candidate["detector_model_path"] = str(detector)
        candidate["person_bbox_xyxy_abs"] = list(
            _bbox(raw["person_bbox_xyxy_abs"], f"{field}.person_bbox_xyxy_abs")
        )
        candidate["detector_confidence"] = detector_confidence
        candidates.append(candidate)

    group_splits: dict[str, set[str]] = defaultdict(set)
    media_splits: dict[str, set[str]] = defaultdict(set)
    for candidate in candidates:
        group_splits[candidate["source_group_id"]].add(candidate["split"])
        media_splits[candidate["source_media_sha256"]].add(candidate["split"])
    for name, mapping in (
        ("source_group_id", group_splits),
        ("source_media_sha256", media_splits),
    ):
        leaking = sorted(key for key, splits in mapping.items() if len(splits) > 1)
        if leaking:
            raise WeakLabelerError(f"cross-split leakage by {name}: {leaking[:5]}")
    return candidates


def _make_context_crop(candidate: Mapping[str, Any]) -> tuple[ContextCrop, list[int]]:
    try:
        from PIL import Image, UnidentifiedImageError
    except ImportError as error:
        raise WeakLabelerError("Pillow is required to create context crops") from error

    source = Path(candidate["source_media_path"])
    try:
        with Image.open(source) as opened:
            frame_index = candidate["frame_index"]
            if frame_index is not None:
                opened.seek(frame_index)
            image = opened.convert("RGB")
    except (OSError, EOFError, UnidentifiedImageError) as error:
        raise WeakLabelerError(f"cannot decode source image frame: {source}: {error}") from error

    width, height = image.size
    x1, y1, x2, y2 = candidate["person_bbox_xyxy_abs"]
    if x2 > width or y2 > height:
        raise WeakLabelerError("person box lies outside the decoded source frame")
    box_width = x2 - x1
    box_height = y2 - y1
    context_bbox = [
        max(0, math.floor(x1 - box_width * CONTEXT_MARGIN_RATIO)),
        max(0, math.floor(y1 - box_height * CONTEXT_MARGIN_RATIO)),
        min(width, math.ceil(x2 + box_width * CONTEXT_MARGIN_RATIO)),
        min(height, math.ceil(y2 + box_height * CONTEXT_MARGIN_RATIO)),
    ]
    region = image.crop(tuple(context_bbox))
    target_width, target_height = CROP_SIZE
    scale = min(target_width / region.width, target_height / region.height)
    resized_size = (
        max(1, min(target_width, round(region.width * scale))),
        max(1, min(target_height, round(region.height * scale))),
    )
    resized = region.resize(resized_size, resample=Image.Resampling.BILINEAR)
    canvas = Image.new("RGB", CROP_SIZE, (0, 0, 0))
    offset = (
        (target_width - resized.width) // 2,
        (target_height - resized.height) // 2,
    )
    canvas.paste(resized, offset)
    stream = io.BytesIO()
    canvas.save(stream, format="PNG", optimize=False, compress_level=9)
    payload = stream.getvalue()
    return (
        ContextCrop(
            png_bytes=payload,
            width=target_width,
            height=target_height,
            sha256=hashlib.sha256(payload).hexdigest(),
        ),
        context_bbox,
    )


def _exclusive_bytes(path: Path, payload: bytes) -> None:
    if not path.is_absolute():
        raise WeakLabelerError("output paths must be absolute")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError as error:
        raise WeakLabelerError(f"refusing to overwrite artifact: {path}") from error
    except OSError as error:
        raise WeakLabelerError(f"cannot create artifact {path}: {error}") from error


def _jsonl_bytes(records: Sequence[Mapping[str, Any]]) -> bytes:
    try:
        return b"".join(
            json.dumps(
                record,
                allow_nan=False,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
            for record in records
        )
    except (TypeError, ValueError) as error:
        raise WeakLabelerError(f"record evidence is not strict JSON: {error}") from error


def _source_specs(
    values: Sequence[Mapping[str, Any]], *, workspace: Path
) -> list[dict[str, Any]]:
    if isinstance(values, (str, bytes, bytearray)) or len(values) != 2:
        raise WeakLabelerError("exactly two source dataset evidence records are required")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, value in enumerate(values):
        field = f"source_datasets[{index}]"
        if not isinstance(value, Mapping):
            raise WeakLabelerError(f"{field} must be an object")
        _exact_fields(value, _SOURCE_FIELDS, field)
        dataset_id = _text(value["dataset_id"], f"{field}.dataset_id")
        if dataset_id in seen:
            raise WeakLabelerError("source dataset IDs must be unique")
        seen.add(dataset_id)
        try:
            archive = require_path_within_workspace(value["archive_path"], workspace)
        except (ArtifactIOError, TypeError) as error:
            raise WeakLabelerError(f"{field}.archive_path is unsafe: {error}") from error
        if archive.is_symlink() or not archive.is_file():
            raise WeakLabelerError(f"{field}.archive must be one regular file")
        expected = _sha256(value["archive_sha256"], f"{field}.archive_sha256")
        if sha256_file(archive) != expected:
            raise WeakLabelerError(f"{field}.archive SHA-256 mismatch")
        if value["dataset_use_authorization_status"] != "authorization_recorded":
            raise WeakLabelerError(f"{field} lacks recorded dataset-use authorization")
        _text(value["terms_recorded_by"], f"{field}.terms_recorded_by")
        if value["role"] != "candidate_media_only":
            raise WeakLabelerError(f"{field}.role must be candidate_media_only")
        item = dict(value)
        item["archive_path"] = str(archive)
        result.append(item)
    if frozenset(seen) != FROZEN_SOURCE_DATASETS:
        raise WeakLabelerError("source dataset IDs do not match the frozen pair")
    return sorted(result, key=lambda item: item["dataset_id"])


def _publish_staged_tree(staging: Path, output_root: Path) -> None:
    if os.path.lexists(output_root):
        raise WeakLabelerError(f"refusing to overwrite output root: {output_root}")
    try:
        output_root.mkdir(parents=False, exist_ok=False)
    except OSError as error:
        raise WeakLabelerError(f"cannot reserve output root {output_root}: {error}") from error

    # The state manifest and generation report are publication markers.  Copy
    # them last so a torn run cannot masquerade as a complete dataset.
    relative_files = sorted(
        path.relative_to(staging)
        for path in staging.rglob("*")
        if path.is_file()
    )
    markers = {
        Path("state-dataset.json"),
        Path("weak-label-generation.json"),
    }
    ordered = [path for path in relative_files if path not in markers]
    ordered.extend(path for path in relative_files if path in markers)
    for relative in ordered:
        source = staging / relative
        destination = output_root / relative
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            with source.open("rb") as incoming, destination.open("xb") as outgoing:
                shutil.copyfileobj(incoming, outgoing, length=1024 * 1024)
                outgoing.flush()
                os.fsync(outgoing.fileno())
        except OSError as error:
            raise WeakLabelerError(
                f"create-once publication failed for {destination}: {error}"
            ) from error
        if sha256_file(destination) != sha256_file(source):
            raise WeakLabelerError(f"published artifact hash differs: {destination}")


def generate_state_weak_labels(
    *,
    candidate_records_path: str | os.PathLike[str],
    expected_candidate_records_sha256: str,
    source_datasets: Sequence[Mapping[str, Any]],
    posture_teacher: PostureMobilityTeacher,
    hazard_teacher: SceneHazardTeacher,
    output_root: str | os.PathLike[str],
    workspace_root: str | os.PathLike[str],
    repository_root: str | os.PathLike[str],
    now: Callable[[], str] = _utc_now,
) -> dict[str, Any]:
    """Generate a complete create-once state-dataset v2 weak-label corpus.

    This is the CLI-ready orchestration boundary.  A command wrapper only has
    to construct the two local teacher adapters and pass paths/options here;
    the function performs no model or dataset downloads.
    """

    try:
        workspace = require_external_workspace(workspace_root, repository_root)
        workspace.mkdir(parents=True, exist_ok=True)
        candidates_path = require_path_within_workspace(candidate_records_path, workspace)
        final_root = require_path_within_workspace(
            output_root, workspace, allow_workspace_root=False
        )
    except (ArtifactIOError, OSError) as error:
        raise WeakLabelerError(str(error)) from error
    if os.path.lexists(final_root):
        raise WeakLabelerError(f"refusing to overwrite output root: {final_root}")
    expected_candidate_hash = _sha256(
        expected_candidate_records_sha256, "expected_candidate_records_sha256"
    )
    if sha256_file(candidates_path) != expected_candidate_hash:
        raise WeakLabelerError("candidate record SHA-256 mismatch")
    sources = _source_specs(source_datasets, workspace=workspace)
    candidates = _load_candidates(candidates_path, workspace=workspace)
    declared_sources = {source["dataset_id"] for source in sources}
    if {candidate["source_dataset_id"] for candidate in candidates} - declared_sources:
        raise WeakLabelerError("candidate records name undeclared source datasets")

    if posture_teacher is hazard_teacher:
        raise WeakLabelerError("posture and hazard teachers must be distinct backends")
    posture_identity = _teacher_identity(
        posture_teacher, role="posture_mobility", workspace=workspace
    )
    hazard_identity = _teacher_identity(
        hazard_teacher, role="scene_hazard_context", workspace=workspace
    )
    if posture_identity.model_id == hazard_identity.model_id:
        raise WeakLabelerError("teacher model IDs must be independent")
    if posture_identity.artifact_sha256 == hazard_identity.artifact_sha256:
        raise WeakLabelerError("teacher model artifacts must have distinct SHA-256 values")

    created_at = _timestamp(now(), "created_at_utc")
    policy_payload = _frozen_policy_payload()
    policy_hash = _canonical_hash(policy_payload)

    final_policy = final_root / "evidence" / "weak-label-policy.json"
    final_decisions = final_root / "evidence" / "weak-label-decisions.jsonl"
    final_bundle = final_root / "evidence" / "labeler-bundle.json"
    final_records = final_root / "records.jsonl"
    final_crop_root = final_root / "crops"
    final_manifest = final_root / "state-dataset.json"
    final_report = final_root / "weak-label-generation.json"

    with tempfile.TemporaryDirectory(
        prefix=f".{final_root.name}-staging-", dir=workspace
    ) as temporary:
        staging = Path(temporary).resolve()
        decisions: list[dict[str, Any]] = []
        retained: list[tuple[dict[str, Any], WeakLabelDecision, ContextCrop, str]] = []
        family_coverage: dict[str, set[str]] = {
            label: set() for label in STATE_CLASSES.values()
        }
        source_split_counts: dict[str, dict[str, Counter[str]]] = {
            dataset_id: {split: Counter() for split in SPLITS}
            for dataset_id in declared_sources
        }
        counts: dict[str, Counter[str]] = {split: Counter() for split in SPLITS}
        abstention_reasons: Counter[str] = Counter()

        for candidate in candidates:
            crop, context_bbox = _make_context_crop(candidate)
            posture_raw = _reproducible_prediction(
                posture_teacher.predict_posture,
                crop,
                categories=POSTURE_CATEGORIES,
                field="posture_mobility_teacher",
            )
            hazard_raw = _reproducible_prediction(
                hazard_teacher.predict_hazard,
                crop,
                categories=HAZARD_CATEGORIES,
                field="scene_hazard_context_teacher",
            )
            decision = decide_teacher_signals(posture_raw, hazard_raw)
            crop_name = hashlib.sha256(
                (candidate["sample_id"] + "\0" + crop.sha256).encode("utf-8")
            ).hexdigest() + ".png"
            if decision.state_label is None:
                crop_relative = Path("crops") / "abstain" / crop_name
            else:
                crop_relative = (
                    Path("crops")
                    / candidate["split"]
                    / decision.state_label
                    / crop_name
                )
            _exclusive_bytes(staging / crop_relative, crop.png_bytes)
            final_crop = final_root / crop_relative

            decision_core = {
                "sample_id": candidate["sample_id"],
                "crop_sha256": crop.sha256,
                "detector_model_sha256": candidate["detector_model_sha256"],
                "person_bbox_xyxy_abs": candidate["person_bbox_xyxy_abs"],
                "posture_teacher_sha256": posture_identity.artifact_sha256,
                "hazard_teacher_sha256": hazard_identity.artifact_sha256,
                "posture_probabilities": dict(decision.posture.probabilities),
                "hazard_probabilities": dict(decision.hazard.probabilities),
                "policy_sha256": policy_hash,
            }
            decision_id = _canonical_hash(decision_core)
            evidence = {
                "schema": WEAK_LABEL_DECISION_SCHEMA,
                "decision_id": decision_id,
                "sample_id": candidate["sample_id"],
                "split": candidate["split"],
                "source_provenance": {
                    "dataset_id": candidate["source_dataset_id"],
                    "family_id": candidate["source_family_id"],
                    "group_id": candidate["source_group_id"],
                    "media_path": candidate["source_media_path"],
                    "media_sha256": candidate["source_media_sha256"],
                    "frame_index": candidate["frame_index"],
                },
                "detector": {
                    "label": PERSON_LABEL,
                    "confidence": candidate["detector_confidence"],
                    "model_id": candidate["detector_model_id"],
                    "model_path": candidate["detector_model_path"],
                    "model_sha256": candidate["detector_model_sha256"],
                    "person_bbox_xyxy_abs": candidate["person_bbox_xyxy_abs"],
                },
                "context_crop": {
                    "path": str(final_crop),
                    "sha256": crop.sha256,
                    "context_bbox_xyxy_abs": context_bbox,
                    "width": crop.width,
                    "height": crop.height,
                    "policy": dict(_CROP_POLICY),
                },
                "teachers": {
                    "posture_mobility": {
                        **posture_identity.to_dict(),
                        **decision.posture.to_dict(),
                    },
                    "scene_hazard_context": {
                        **hazard_identity.to_dict(),
                        **decision.hazard.to_dict(),
                    },
                },
                "policy": {"path": str(final_policy), "sha256": policy_hash},
                "decision": decision.to_dict(),
                "evidence_status": WEAK_SUPERVISION_EVIDENCE_STATUS,
                "labeled_at_utc": created_at,
            }
            decisions.append(evidence)
            if decision.state_label is None:
                abstention_reasons[decision.state_reason] += 1
                continue
            retained.append((candidate, decision, crop, decision_id))
            family_coverage[decision.state_label].add(candidate["source_family_id"])
            counts[candidate["split"]][decision.state_label] += 1
            source_split_counts[candidate["source_dataset_id"]][candidate["split"]][
                decision.state_label
            ] += 1

        for label, families in family_coverage.items():
            if len(families) < MIN_SOURCE_FAMILIES_PER_CLASS:
                raise WeakLabelerError(
                    f"{label} must span at least {MIN_SOURCE_FAMILIES_PER_CLASS} "
                    "independent source families"
                )
        for dataset_id in sorted(declared_sources):
            for split in SPLITS:
                missing = [
                    label
                    for label in STATE_CLASSES.values()
                    if source_split_counts[dataset_id][split][label] < 1
                ]
                if missing:
                    raise WeakLabelerError(
                        "dataset-source/class confounding is forbidden: "
                        f"{dataset_id!r} split {split!r} lacks {missing}"
                    )

        # Re-open every mutable input immediately before publication.  The
        # emitted hashes therefore identify the bytes actually used, not only
        # an earlier preflight snapshot.
        if sha256_file(candidates_path) != expected_candidate_hash:
            raise WeakLabelerError("candidate records changed during weak labeling")
        if sha256_file(Path(posture_identity.artifact_path)) != (
            posture_identity.artifact_sha256
        ):
            raise WeakLabelerError("posture teacher artifact changed during inference")
        if sha256_file(Path(hazard_identity.artifact_path)) != (
            hazard_identity.artifact_sha256
        ):
            raise WeakLabelerError("hazard teacher artifact changed during inference")
        for source in sources:
            if sha256_file(Path(source["archive_path"])) != source["archive_sha256"]:
                raise WeakLabelerError("source archive changed during weak labeling")
        for candidate in candidates:
            if sha256_file(Path(candidate["source_media_path"])) != (
                candidate["source_media_sha256"]
            ):
                raise WeakLabelerError("source media changed during weak labeling")
            if sha256_file(Path(candidate["detector_model_path"])) != (
                candidate["detector_model_sha256"]
            ):
                raise WeakLabelerError("detector model changed during weak labeling")

        _exclusive_bytes(
            staging / final_policy.relative_to(final_root),
            canonical_json_bytes(policy_payload),
        )
        decision_bytes = _jsonl_bytes(decisions)
        _exclusive_bytes(
            staging / final_decisions.relative_to(final_root), decision_bytes
        )
        decisions_hash = hashlib.sha256(decision_bytes).hexdigest()
        bundle = {
            "schema": WEAK_LABEL_BUNDLE_SCHEMA,
            "labeler_id": LABELER_ID,
            "labeler_version": LABELER_VERSION,
            "evidence_status": WEAK_SUPERVISION_EVIDENCE_STATUS,
            "candidate_records": {
                "path": str(candidates_path),
                "sha256": expected_candidate_hash,
            },
            "posture_teacher": posture_identity.to_dict(),
            "hazard_teacher": hazard_identity.to_dict(),
            "policy": {"path": str(final_policy), "sha256": policy_hash},
            "decision_evidence": {
                "path": str(final_decisions),
                "sha256": decisions_hash,
                "decision_count": len(decisions),
                "retained_count": len(retained),
                "abstained_count": len(decisions) - len(retained),
            },
            "forbidden_inputs": list(_FORBIDDEN_INPUTS),
            "created_at_utc": created_at,
        }
        bundle_bytes = canonical_json_bytes(bundle)
        _exclusive_bytes(
            staging / final_bundle.relative_to(final_root), bundle_bytes
        )
        bundle_hash = hashlib.sha256(bundle_bytes).hexdigest()

        records: list[dict[str, Any]] = []
        for candidate, decision, crop, decision_id in retained:
            assert decision.state_label is not None
            crop_name = hashlib.sha256(
                (candidate["sample_id"] + "\0" + crop.sha256).encode("utf-8")
            ).hexdigest() + ".png"
            crop_path = (
                final_crop_root
                / candidate["split"]
                / decision.state_label
                / crop_name
            )
            posture_hint = (
                "upright"
                if decision.posture.top_reason == "upright_walking"
                else "lying"
                if decision.posture.top_reason == "fallen"
                else None
            )
            records.append(
                {
                    "schema": STATE_RECORD_SCHEMA,
                    "sample_id": candidate["sample_id"],
                    "split": candidate["split"],
                    "source_dataset_id": candidate["source_dataset_id"],
                    "source_group_id": candidate["source_group_id"],
                    "source_media_path": candidate["source_media_path"],
                    "source_media_sha256": candidate["source_media_sha256"],
                    "frame_index": candidate["frame_index"],
                    "person_bbox_xyxy_abs": candidate["person_bbox_xyxy_abs"],
                    "crop_path": str(crop_path),
                    "crop_sha256": crop.sha256,
                    "state_label": decision.state_label,
                    "state_reason": decision.state_reason,
                    "source_pose_hint": posture_hint,
                    "label_source": AUTOMATED_LABEL_SOURCE,
                    "labeler_id": LABELER_ID,
                    "labeler_version": LABELER_VERSION,
                    "labeler_artifact_sha256": bundle_hash,
                    "label_policy_sha256": policy_hash,
                    "label_confidence": decision.confidence,
                    "labeled_at_utc": created_at,
                    "context_notes": (
                        "dual-teacher weak supervision; "
                        f"decision_id={decision_id}; state remains unverified"
                    ),
                }
            )
        records_bytes = _jsonl_bytes(records)
        _exclusive_bytes(staging / final_records.relative_to(final_root), records_bytes)
        records_hash = hashlib.sha256(records_bytes).hexdigest()

        manifest = {
            "schema": STATE_DATASET_SCHEMA,
            "model_id": STATE_MODEL_ID,
            "classes": dict(STATE_CLASSES),
            "dataset_origin_is_label": False,
            "source_datasets": sources,
            "records_path": str(final_records),
            "records_sha256": records_hash,
            "crop_root": str(final_crop_root),
            "crop_policy": dict(_CROP_POLICY),
            "split_policy": dict(_SPLIT_POLICY),
            "weak_supervision": {
                "schema": WEAK_SUPERVISION_SCHEMA,
                "evidence_status": WEAK_SUPERVISION_EVIDENCE_STATUS,
                "label_source": AUTOMATED_LABEL_SOURCE,
                "labeler_type": "automated",
                "labeler_id": LABELER_ID,
                "labeler_version": LABELER_VERSION,
                "labeler_artifact_path": str(final_bundle),
                "labeler_artifact_sha256": bundle_hash,
                "policy_path": str(final_policy),
                "policy_sha256": policy_hash,
                "forbidden_inputs": list(_FORBIDDEN_INPUTS),
            },
            "counts": {
                split: {
                    label: counts[split][label]
                    for label in STATE_CLASSES.values()
                }
                for split in SPLITS
            },
            "created_at_utc": created_at,
        }
        manifest_bytes = canonical_json_bytes(manifest)
        _exclusive_bytes(
            staging / final_manifest.relative_to(final_root), manifest_bytes
        )
        manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()

        report = {
            "schema": WEAK_LABEL_GENERATION_SCHEMA,
            "dataset_manifest": {
                "path": str(final_manifest),
                "sha256": manifest_hash,
            },
            "candidate_records": {
                "path": str(candidates_path),
                "sha256": expected_candidate_hash,
                "count": len(candidates),
            },
            "decision_evidence": {
                "path": str(final_decisions),
                "sha256": decisions_hash,
                "retained_count": len(retained),
                "abstained_count": len(decisions) - len(retained),
                "abstention_reasons": dict(sorted(abstention_reasons.items())),
            },
            "labeler_bundle": {"path": str(final_bundle), "sha256": bundle_hash},
            "policy": {"path": str(final_policy), "sha256": policy_hash},
            "teachers": {
                "posture_mobility": posture_identity.to_dict(),
                "scene_hazard_context": hazard_identity.to_dict(),
            },
            "source_family_coverage": {
                label: sorted(families) for label, families in family_coverage.items()
            },
            "group_safe_split_preserved": True,
            "human_review_performed": False,
            "dataset_origin_is_label": False,
            "evidence_status": WEAK_SUPERVISION_EVIDENCE_STATUS,
            "created_at_utc": created_at,
            "passed": True,
        }
        _exclusive_bytes(
            staging / final_report.relative_to(final_root),
            canonical_json_bytes(report),
        )
        try:
            final_root.parent.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise WeakLabelerError(f"cannot create output parent: {error}") from error
        _publish_staged_tree(staging, final_root)

    # The existing v2 validator is the final compatibility gate.  It reopens
    # every retained media/crop and enforces per-source/per-split class balance.
    try:
        state_report = validate_state_dataset_manifest(final_manifest)
    except Exception as error:
        raise WeakLabelerError(
            f"published weak labels failed state-dataset v2 validation: {error}"
        ) from error
    if sha256_file(final_report) != hashlib.sha256(
        canonical_json_bytes(report)
    ).hexdigest():
        raise WeakLabelerError("generation report changed during publication")
    return {
        **report,
        "generation_report_path": str(final_report),
        "generation_report_sha256": sha256_file(final_report),
        "state_dataset_validation": state_report,
    }


__all__ = [
    "ContextCrop",
    "FROZEN_POLICY",
    "HAZARD_CATEGORIES",
    "LABELER_ID",
    "LABELER_VERSION",
    "POSTURE_CATEGORIES",
    "PostureMobilityTeacher",
    "SceneHazardTeacher",
    "SignalSummary",
    "WEAK_LABEL_BUNDLE_SCHEMA",
    "WEAK_LABEL_CANDIDATE_SCHEMA",
    "WEAK_LABEL_DECISION_SCHEMA",
    "WEAK_LABEL_GENERATION_SCHEMA",
    "WEAK_LABEL_POLICY_SCHEMA",
    "WeakLabelDecision",
    "WeakLabelerError",
    "decide_teacher_signals",
    "generate_state_weak_labels",
]
