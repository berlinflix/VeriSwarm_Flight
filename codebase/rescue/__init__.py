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
from .movement_security import (
    AuthorizationResolution,
    CellReassignment,
    GateDecision,
    MovementSecurityError,
    evaluate_movement_command,
    load_movement_contract,
    reassign_unfinished_cells,
    resolve_authorization,
)
from .outbox import EnqueueResult, FlushResult, RescueOutbox, RescueOutboxError
from .projection import MissionProjection
from .schema import RESCUE_SCHEMA, RescueEventError, validate_rescue_event
from .security_bridge import SecurityAssessment, assess_consensus_security

__all__ = [
    "CollectResult",
    "CameraModel",
    "CellReassignment",
    "EnqueueResult",
    "FusedPersonCandidate",
    "GateDecision",
    "FlushResult",
    "MissingViewEvidence",
    "MissionProjection",
    "MultiViewFusionError",
    "MovementSecurityError",
    "PersonView",
    "RESCUE_SCHEMA",
    "RescueCollector",
    "RescueEventError",
    "RescueOutbox",
    "RescueOutboxError",
    "SecurityAssessment",
    "AuthorizationResolution",
    "assess_consensus_security",
    "bearing_from_view",
    "evaluate_movement_command",
    "fuse_person_views",
    "locate_with_metric_range",
    "load_movement_contract",
    "reassign_unfinished_cells",
    "resolve_authorization",
    "triangulate_positive_views",
    "validate_rescue_event",
]
