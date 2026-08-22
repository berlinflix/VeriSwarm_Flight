"""Offline rescue-event collection, projection and reporting."""

from .collector import CollectResult, RescueCollector
from .multiview import (
    CameraModel,
    FusedPersonCandidate,
    MissingViewEvidence,
    MultiViewFusionError,
    PersonView,
    bearing_from_view,
    fuse_person_views,
    locate_with_metric_range,
    triangulate_positive_views,
)
from .outbox import EnqueueResult, FlushResult, RescueOutbox, RescueOutboxError
from .projection import MissionProjection
from .schema import RESCUE_SCHEMA, RescueEventError, validate_rescue_event
from .security_bridge import SecurityAssessment, assess_consensus_security

__all__ = [
    "CollectResult",
    "CameraModel",
    "EnqueueResult",
    "FusedPersonCandidate",
    "FlushResult",
    "MissingViewEvidence",
    "MissionProjection",
    "MultiViewFusionError",
    "PersonView",
    "RESCUE_SCHEMA",
    "RescueCollector",
    "RescueEventError",
    "RescueOutbox",
    "RescueOutboxError",
    "SecurityAssessment",
    "assess_consensus_security",
    "bearing_from_view",
    "fuse_person_views",
    "locate_with_metric_range",
    "triangulate_positive_views",
    "validate_rescue_event",
]
