"""Offline rescue-event collection, projection and reporting."""

from .collector import CollectResult, RescueCollector
from .outbox import EnqueueResult, FlushResult, RescueOutbox, RescueOutboxError
from .projection import MissionProjection
from .schema import RESCUE_SCHEMA, RescueEventError, validate_rescue_event

__all__ = [
    "CollectResult",
    "EnqueueResult",
    "FlushResult",
    "MissionProjection",
    "RESCUE_SCHEMA",
    "RescueCollector",
    "RescueEventError",
    "RescueOutbox",
    "RescueOutboxError",
    "validate_rescue_event",
]
