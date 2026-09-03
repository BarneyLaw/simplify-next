from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from adaptsg.domain import (
    AccessibilityStatus,
    FreshnessStatus,
    HardConstraints,
    Itinerary,
    ItinerarySegment,
    JourneyRequest,
    Location,
    RouteLeg,
    SegmentPurpose,
    TravelMode,
    ValidationCode,
)
from adaptsg.validation import ItineraryValidator

SGT = ZoneInfo("Asia/Singapore")


def test_request_rejects_finish_before_start() -> None:
    with pytest.raises(ValidationError, match="finish_by must be later"):
        JourneyRequest(
            journey_date=date(2026, 9, 1),
            start_time=time(17),
            hard=HardConstraints(finish_by=time(16)),
        )


def test_route_and_segment_reject_inconsistent_times() -> None:
    start = datetime(2026, 9, 1, 10, tzinfo=SGT)
    location = Location(lat=1.3, lng=103.8)
    with pytest.raises(ValidationError, match="route timestamps"):
        RouteLeg(
            origin_label="A",
            destination_label="B",
            origin=location,
            destination=location,
            mode=TravelMode.WALK,
            depart_at=start,
            arrive_at=start + timedelta(minutes=10),
            duration_minutes=3,
            walking_distance_m=5,
            estimated_cost_sgd=0,
            source="test",
            source_timestamp=start,
        )


def test_valid_itinerary_passes(itinerary: Itinerary, validator: ItineraryValidator) -> None:
    result = validator.validate(itinerary)
    assert result.valid
    assert result.issues == ()


def test_validator_reports_safety_and_provenance_violations(
    itinerary: Itinerary, validator: ItineraryValidator
) -> None:
    first = itinerary.segments[0]
    bad_venue = first.venue.model_copy(
        update={
            "accessibility_status": AccessibilityStatus.UNVERIFIED,
            "accessibility_source": None,
            "opening_time": time(15),
        }
    )
    bad_route = first.route.model_copy(
        update={
            "walking_distance_m": 999,
            "source": "",
            "destination": Location(lat=1.31, lng=103.81),
            "origin_label": "Wrong origin",
        }
    )
    bad_first = first.model_copy(update={"venue": bad_venue, "route": bad_route})
    bad = itinerary.model_copy(
        update={
            "segments": (bad_first, *itinerary.segments[1:]),
            "total_cost_sgd": 1,
        }
    )
    codes = {issue.code for issue in validator.validate(bad).issues}
    assert {
        ValidationCode.ACCESSIBILITY,
        ValidationCode.WALKING_DISTANCE,
        ValidationCode.ROUTE_PROVENANCE,
        ValidationCode.LOCATION_PROVENANCE,
        ValidationCode.TIME_OVERLAP,
        ValidationCode.OPENING_HOURS,
        ValidationCode.COST_CALCULATION,
    } <= codes


def test_validator_rejects_stale_route_data(
    itinerary: Itinerary, validator: ItineraryValidator
) -> None:
    first = itinerary.segments[0]
    stale_first = first.model_copy(
        update={"route": first.route.model_copy(update={"freshness": FreshnessStatus.STALE})}
    )
    result = validator.validate(
        itinerary.model_copy(update={"segments": (stale_first, *itinerary.segments[1:])})
    )
    assert ValidationCode.ROUTE_FRESHNESS in {issue.code for issue in result.issues}


def test_validator_reports_global_constraint_violations(
    itinerary: Itinerary, validator: ItineraryValidator
) -> None:
    hard = itinerary.request.hard.model_copy(
        update={
            "total_budget_sgd": 1,
            "required_venue_ids": frozenset({"cloud-forest"}),
            "finish_by": time(12),
        }
    )
    request = itinerary.request.model_copy(update={"hard": hard, "max_stops": 1})
    no_lunch = tuple(
        segment.model_copy(update={"purpose": SegmentPurpose.ACTIVITY})
        for segment in itinerary.segments
    )
    bad = itinerary.model_copy(update={"request": request, "segments": no_lunch, "replan_count": 3})
    codes = {issue.code for issue in validator.validate(bad).issues}
    assert {
        ValidationCode.STOP_LIMIT,
        ValidationCode.REPLAN_LIMIT,
        ValidationCode.LUNCH_TIMING,
        ValidationCode.REQUIRED_VENUE,
        ValidationCode.BUDGET,
        ValidationCode.FINISH_TIME,
    } <= codes


def test_validator_detects_late_lunch_and_rest_gap(
    itinerary: Itinerary, validator: ItineraryValidator
) -> None:
    lunch = itinerary.segments[1]
    delayed_route = lunch.route.model_copy(
        update={
            "depart_at": lunch.route.depart_at + timedelta(hours=3),
            "arrive_at": lunch.route.arrive_at + timedelta(hours=3),
        }
    )
    delayed_lunch = lunch.model_copy(
        update={
            "route": delayed_route,
            "activity_start": lunch.activity_start + timedelta(hours=3),
            "activity_end": lunch.activity_end + timedelta(hours=3),
        }
    )
    bad = itinerary.model_copy(
        update={"segments": (itinerary.segments[0], delayed_lunch, itinerary.segments[2])}
    )
    codes = {issue.code for issue in validator.validate(bad).issues}
    assert ValidationCode.LUNCH_TIMING in codes
    assert ValidationCode.REST_INTERVAL in codes


def test_segment_rejects_activity_before_arrival(itinerary: Itinerary) -> None:
    segment = itinerary.segments[0]
    with pytest.raises(ValidationError, match="before arrival"):
        ItinerarySegment(
            venue=segment.venue,
            route=segment.route,
            activity_start=segment.route.depart_at,
            activity_end=segment.route.arrive_at,
        )
