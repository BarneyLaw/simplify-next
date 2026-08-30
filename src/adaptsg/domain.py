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


class Location(StrictModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


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


class EnvironmentSnapshot(StrictModel):
    weather_summary: str
    psi: int = Field(ge=0)
    flood_affected_venue_ids: frozenset[str] = frozenset()
    disrupted_route_labels: frozenset[str] = frozenset()
    observed_at: datetime
    source: str


class ReplanTrigger(StrictModel):
    type: TriggerType
    message: str
    affected_venue_ids: frozenset[str] = frozenset()
    new_budget_sgd: float | None = Field(default=None, ge=0)


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
