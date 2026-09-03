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


class JourneyNotFound(AdaptSGError):
    """Raised when a journey is missing or has expired."""


class JourneyVersionConflict(AdaptSGError):
    """Raised when a client acts on an out-of-date journey version."""

    def __init__(self, message: str, *, current_version: int) -> None:
        super().__init__(message)
        self.current_version = current_version


class InvalidJourneyDecision(AdaptSGError):
    """Raised when an approval targets no pending journey item."""
