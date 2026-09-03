"""Domain errors that can be translated safely at UI and API boundaries."""


class AdaptSGError(Exception):
    """Base class for expected application failures."""


class NoFeasibleItinerary(AdaptSGError):
    """Raised when deterministic validation rejects every candidate."""


class ReplanLimitReached(AdaptSGError):
    """Raised when a journey has exhausted its bounded replanning cycles."""


class ApprovalRequired(AdaptSGError):
    """Raised when a material proposal is applied without approval."""


class ToolUnavailable(AdaptSGError):
    """Raised when live verification cannot provide required typed data."""


class InvalidIdempotencyKey(AdaptSGError):
    """Raised when a state-changing request has no usable retry key."""


class JourneyNotFound(AdaptSGError):
    """Raised when a journey is unknown or has expired."""


class JourneyConflict(AdaptSGError):
    """Base class for lifecycle, optimistic-lock, and replay conflicts."""


class IdempotencyConflict(JourneyConflict):
    """Raised when an idempotency key is reused for different input."""


class OperationInProgress(JourneyConflict):
    """Raised when the matching idempotent operation has not completed."""


class StaleJourneyVersion(JourneyConflict):
    """Raised when a mutation targets an old journey version."""

    def __init__(self, message: str, *, current_version: int | None = None) -> None:
        super().__init__(message)
        self.current_version = current_version


class InvalidJourneyTransition(JourneyConflict):
    """Raised when a decision or replan is invalid for the lifecycle state."""
