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


class AuthorizationDenied(AdaptSGError):
    """Raised when the authenticated principal cannot perform an operation."""


class AuthenticationRequired(AuthorizationDenied):
    """Raised when no verified production principal is available."""


class ConsentRequired(AuthorizationDenied):
    """Raised when explicit, current consent is absent or withdrawn."""


class AuthorityGrantRequired(AuthorizationDenied):
    """Raised when a caregiver lacks a current scoped grant."""


class CapabilityDisabled(AuthorizationDenied):
    """Raised when a server-side feature flag or kill switch denies a capability."""


class IntentConflict(JourneyConflict):
    """Raised for stale, replayed, or payload-conflicting action intents."""


class AuditUnavailable(AdaptSGError):
    """Raised when an audit append cannot be durably confirmed."""


class RetentionConfigurationMissing(AdaptSGError):
    """Raised when production retention policy is absent."""
