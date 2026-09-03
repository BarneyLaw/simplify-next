"""Typed journey state shared by planners, validators, APIs, and the UI."""

from __future__ import annotations

from datetime import date, datetime, time
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    """Base model that rejects undeclared fields at every trust boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class AccessibilityStatus(StrEnum):
    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    INACCESSIBLE = "inaccessible"


class VenueCategory(StrEnum):
    INDOOR_MUSEUM = "indoor_museum"
    INDOOR_ATTRACTION = "indoor_attraction"
    OUTDOOR_ATTRACTION = "outdoor_attraction"
    GARDEN = "garden"
    FOOD = "food"
    REST = "rest"


class TravelMode(StrEnum):
    WALK = "walk"
    PUBLIC_TRANSPORT = "public_transport"
    TAXI = "taxi"


class SegmentPurpose(StrEnum):
    ACTIVITY = "activity"
    LUNCH = "lunch"
    REST = "rest"


class TriggerType(StrEnum):
    HEAVY_RAIN = "heavy_rain"
    HIGH_PSI = "high_psi"
    FLOOD_ALERT = "flood_alert"
    TRANSPORT_DISRUPTION = "transport_disruption"
    VENUE_CLOSURE = "venue_closure"
    FATIGUE = "fatigue"
    BUDGET_REDUCTION = "budget_reduction"


class ProposalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class JourneyStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    REJECTED = "rejected"


class ApprovalDecision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class FreshnessStatus(StrEnum):
    FRESH = "fresh"
    STALE = "stale"
    FIXTURE = "fixture"
    UNAVAILABLE = "unavailable"


class Location(StrictModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


class ToolResult[T](StrictModel):
    """Common provenance envelope for external and fixture tool responses."""

    success: bool
    payload: T | None = None
    source: str
    source_timestamp: datetime
    freshness: FreshnessStatus
    is_fixture: bool = False
    error_code: str | None = None
    error_message: str | None = None


class LocationSearchResult(StrictModel):
    label: str
    location: Location
    source: str
    source_timestamp: datetime
    freshness: FreshnessStatus = FreshnessStatus.FRESH
    is_fixture: bool = False


class AccessibilityResult(StrictModel):
    location: Location
    status: AccessibilityStatus
    source: str | None = None
    source_timestamp: datetime
    freshness: FreshnessStatus = FreshnessStatus.FRESH
    is_fixture: bool = False


class Venue(StrictModel):
    id: str = Field(pattern=r"^[a-z0-9-]+$")
    name: str
    category: VenueCategory
    location: Location
    accessibility_status: AccessibilityStatus
    accessibility_source: str | None = None
    indoor: bool
    average_duration_minutes: int = Field(gt=0, le=240)
    estimated_cost_sgd: float = Field(ge=0)
    rest_seating: bool
    opening_time: time
    closing_time: time
    tags: tuple[str, ...] = ()
    data_source: str = "curated_demo_dataset"
    data_reviewed_on: date | None = None


class VenueSearchFilters(StrictModel):
    wheelchair_required: bool = True
    indoor_only: bool = False
    categories: tuple[VenueCategory, ...] = ()
    excluded_ids: frozenset[str] = frozenset()
    tags: tuple[str, ...] = ()


class HardConstraints(StrictModel):
    wheelchair_accessible_required: bool = True
    max_walking_distance_m: int = Field(default=400, ge=0, le=2_000)
    lunch_latest: time = time(13, 0)
    finish_by: time = time(17, 0)
    total_budget_sgd: float = Field(default=70, ge=0, le=1_000)
    rest_interval_minutes: int = Field(default=90, ge=20, le=240)
    required_venue_ids: frozenset[str] = frozenset()


class SoftPreferences(StrictModel):
    preferred_categories: tuple[VenueCategory, ...] = (VenueCategory.INDOOR_MUSEUM,)
    preferred_venue_ids: frozenset[str] = frozenset()
    prefer_public_transport: bool = True
    minimise_cost: bool = True
    avoid_crowds: bool = False
    scenic_route: bool = False


class JourneyRequest(StrictModel):
    journey_date: date
    start_time: time = time(10, 0)
    start_label: str = "Toa Payoh"
    start_location: Location = Location(lat=1.3323, lng=103.8474)
    hard: HardConstraints = HardConstraints()
    soft: SoftPreferences = SoftPreferences()
    max_stops: int = Field(default=3, ge=1, le=3)

    @model_validator(mode="after")
    def finish_must_follow_start(self) -> JourneyRequest:
        if self.hard.finish_by <= self.start_time:
            raise ValueError("finish_by must be later than start_time")
        return self


class TokenUsage(StrictModel):
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)


class ParseOutcome(StrictModel):
    request: JourneyRequest
    source: str
    warnings: tuple[str, ...] = ()
    token_usage: TokenUsage = TokenUsage()


class RouteLeg(StrictModel):
    origin_label: str
    destination_label: str
    origin: Location
    destination: Location
    mode: TravelMode
    depart_at: datetime
    arrive_at: datetime
    duration_minutes: int = Field(gt=0)
    walking_distance_m: int = Field(ge=0)
    estimated_cost_sgd: float = Field(ge=0)
    source: str
    source_timestamp: datetime
    freshness: FreshnessStatus = FreshnessStatus.FRESH
    is_fixture: bool = False

    @model_validator(mode="after")
    def timestamps_match_duration(self) -> RouteLeg:
        elapsed = int((self.arrive_at - self.depart_at).total_seconds() // 60)
        if self.arrive_at <= self.depart_at or abs(elapsed - self.duration_minutes) > 1:
            raise ValueError("route timestamps must match duration_minutes")
        return self


class ItinerarySegment(StrictModel):
    id: UUID = Field(default_factory=uuid4)
    venue: Venue
    route: RouteLeg
    activity_start: datetime
    activity_end: datetime
    purpose: SegmentPurpose = SegmentPurpose.ACTIVITY
    notes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def activity_must_follow_route(self) -> ItinerarySegment:
        if self.activity_start < self.route.arrive_at:
            raise ValueError("activity cannot start before arrival")
        if self.activity_end <= self.activity_start:
            raise ValueError("activity_end must follow activity_start")
        return self


class Itinerary(StrictModel):
    id: UUID = Field(default_factory=uuid4)
    request: JourneyRequest
    segments: tuple[ItinerarySegment, ...]
    total_cost_sgd: float = Field(ge=0)
    created_at: datetime
    replan_count: int = Field(default=0, ge=0)
    parser_source: str = "deterministic"


class SegmentMetrics(StrictModel):
    segment_id: UUID
    walking_distance_m: int = Field(ge=0)
    cost_sgd: float = Field(ge=0)


class PlanMetrics(StrictModel):
    total_cost_sgd: float = Field(ge=0)
    total_walking_distance_m: int = Field(ge=0)
    elapsed_minutes: int = Field(ge=0)
    segments: tuple[SegmentMetrics, ...] = ()


class PlanOutcome(StrictModel):
    itinerary: Itinerary
    warnings: tuple[str, ...] = ()
    token_usage: TokenUsage = TokenUsage()


class EnvironmentSnapshot(StrictModel):
    weather_summary: str
    psi: int = Field(ge=0)
    flood_affected_venue_ids: frozenset[str] = frozenset()
    disrupted_route_labels: frozenset[str] = frozenset()
    observed_at: datetime
    source: str
    freshness: FreshnessStatus = FreshnessStatus.FRESH
    is_fixture: bool = False


class ReplanTrigger(StrictModel):
    type: TriggerType
    message: str
    affected_venue_ids: frozenset[str] = frozenset()
    new_budget_sgd: float | None = Field(default=None, ge=0)


class MonitoringOutcome(StrictModel):
    snapshot: EnvironmentSnapshot
    triggers: tuple[ReplanTrigger, ...]


class ValidationCode(StrEnum):
    ACCESSIBILITY = "accessibility"
    WALKING_DISTANCE = "walking_distance"
    BUDGET = "budget"
    OPENING_HOURS = "opening_hours"
    TIME_OVERLAP = "time_overlap"
    LUNCH_TIMING = "lunch_timing"
    REST_INTERVAL = "rest_interval"
    REQUIRED_VENUE = "required_venue"
    LOCATION_PROVENANCE = "location_provenance"
    ROUTE_PROVENANCE = "route_provenance"
    REPLAN_LIMIT = "replan_limit"
    STOP_LIMIT = "stop_limit"
    FINISH_TIME = "finish_time"
    COST_CALCULATION = "cost_calculation"
    ROUTE_FRESHNESS = "route_freshness"


class ValidationIssue(StrictModel):
    code: ValidationCode
    message: str
    segment_id: UUID | None = None


class ValidationResult(StrictModel):
    valid: bool
    issues: tuple[ValidationIssue, ...] = ()


class ItineraryChange(StrictModel):
    segment_index: int = Field(ge=0)
    before: str
    after: str
    reason: str


class ReplanProposal(StrictModel):
    id: UUID = Field(default_factory=uuid4)
    original_itinerary_id: UUID
    itinerary: Itinerary
    changes: tuple[ItineraryChange, ...]
    cost_delta_sgd: float
    requires_approval: bool
    validation: ValidationResult
    status: ProposalStatus = ProposalStatus.PENDING


class JourneyState(StrictModel):
    """Server-owned lifecycle state; only validated itineraries may be stored here."""

    journey_id: UUID = Field(default_factory=uuid4)
    status: JourneyStatus
    current_itinerary: Itinerary | None = None
    pending_initial_itinerary: Itinerary | None = None
    latest_replan_proposal: ReplanProposal | None = None
    version: int = Field(default=1, ge=1)
    warnings: tuple[str, ...] = ()
    token_usage: TokenUsage = TokenUsage()
    created_at: datetime
    updated_at: datetime
    expires_at: datetime

    @model_validator(mode="after")
    def lifecycle_is_consistent(self) -> JourneyState:
        if self.status is JourneyStatus.DRAFT:
            if self.pending_initial_itinerary is None or self.current_itinerary is not None:
                raise ValueError("draft journeys require one pending initial itinerary")
            if self.latest_replan_proposal is not None:
                raise ValueError("draft journeys cannot contain a replan proposal")
        elif self.status is JourneyStatus.ACTIVE:
            if self.current_itinerary is None or self.pending_initial_itinerary is not None:
                raise ValueError("active journeys require exactly one current itinerary")
            proposal = self.latest_replan_proposal
            if proposal is not None:
                if proposal.status is ProposalStatus.APPROVED:
                    if proposal.itinerary.id != self.current_itinerary.id:
                        raise ValueError("approved replan must be the current itinerary")
                elif proposal.original_itinerary_id != self.current_itinerary.id:
                    raise ValueError(
                        "pending or rejected replan must reference the current itinerary"
                    )
        elif (
            self.current_itinerary is not None
            or self.pending_initial_itinerary is not None
            or self.latest_replan_proposal is not None
        ):
            raise ValueError("rejected journeys cannot contain an itinerary or proposal")

        timestamps = (self.created_at, self.updated_at, self.expires_at)
        if any(value.tzinfo is None or value.utcoffset() is None for value in timestamps):
            raise ValueError("journey timestamps must be timezone-aware")
        if self.updated_at < self.created_at or self.expires_at <= self.updated_at:
            raise ValueError("journey timestamps are out of order")
        return self


class JourneyDecision(StrictModel):
    target_id: UUID
    decision: ApprovalDecision
    expected_version: int = Field(ge=1)
