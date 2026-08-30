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
