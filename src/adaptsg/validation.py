"""Deterministic itinerary validation. The model never decides safety compliance."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from math import isclose
from zoneinfo import ZoneInfo

from adaptsg.domain import (
    AccessibilityStatus,
    Itinerary,
    SegmentPurpose,
    ValidationCode,
    ValidationIssue,
    ValidationResult,
)

SINGAPORE = ZoneInfo("Asia/Singapore")


class ItineraryValidator:
    def __init__(self, *, max_replans: int = 2) -> None:
        self.max_replans = max_replans

    def validate(self, itinerary: Itinerary) -> ValidationResult:
        issues: list[ValidationIssue] = []
        request = itinerary.request
        hard = request.hard

        if len(itinerary.segments) > request.max_stops:
            issues.append(
                ValidationIssue(
                    code=ValidationCode.STOP_LIMIT,
                    message=f"itinerary has more than {request.max_stops} stops",
                )
            )
        if itinerary.replan_count > self.max_replans:
            issues.append(
                ValidationIssue(
                    code=ValidationCode.REPLAN_LIMIT,
                    message=f"replanning is capped at {self.max_replans} cycles",
                )
            )

        computed_cost = 0.0
        visited: set[str] = set()
        lunch_seen = False
        previous_end = self._at_local_time(request.journey_date, request.start_time)
        expected_origin = request.start_location
        expected_origin_label = request.start_label
        last_rest = previous_end

        for segment in itinerary.segments:
            venue = segment.venue
            route = segment.route
            visited.add(venue.id)
            computed_cost += venue.estimated_cost_sgd + route.estimated_cost_sgd

            if hard.wheelchair_accessible_required and (
                venue.accessibility_status is not AccessibilityStatus.VERIFIED
                or not venue.accessibility_source
            ):
                issues.append(
                    ValidationIssue(
                        code=ValidationCode.ACCESSIBILITY,
                        message=f"{venue.name} does not have verified accessibility data",
                        segment_id=segment.id,
                    )
                )

            if route.walking_distance_m > hard.max_walking_distance_m:
                issues.append(
                    ValidationIssue(
                        code=ValidationCode.WALKING_DISTANCE,
                        message=(
                            f"{route.destination_label} requires {route.walking_distance_m} m "
                            f"walking; limit is {hard.max_walking_distance_m} m"
                        ),
                        segment_id=segment.id,
                    )
                )

            if not route.source or route.source_timestamp is None:
                issues.append(
                    ValidationIssue(
                        code=ValidationCode.ROUTE_PROVENANCE,
                        message=f"route to {venue.name} lacks tool provenance",
                        segment_id=segment.id,
                    )
                )

            if route.destination != venue.location or route.destination_label != venue.name:
                issues.append(
                    ValidationIssue(
                        code=ValidationCode.LOCATION_PROVENANCE,
                        message=(
                            f"route destination does not match catalog location for {venue.name}"
                        ),
                        segment_id=segment.id,
                    )
                )

            if route.origin != expected_origin or route.origin_label != expected_origin_label:
                issues.append(
                    ValidationIssue(
                        code=ValidationCode.TIME_OVERLAP,
                        message=f"route continuity is broken before {venue.name}",
                        segment_id=segment.id,
                    )
                )

            if route.depart_at < previous_end or segment.activity_start < route.arrive_at:
                issues.append(
                    ValidationIssue(
                        code=ValidationCode.TIME_OVERLAP,
                        message=f"travel or activity overlaps before {venue.name}",
                        segment_id=segment.id,
                    )
                )

            local_start = segment.activity_start.astimezone(SINGAPORE)
            local_end = segment.activity_end.astimezone(SINGAPORE)
            if (
                local_start.time() < venue.opening_time
                or local_end.time() > venue.closing_time
                or local_start.date() != request.journey_date
            ):
                issues.append(
                    ValidationIssue(
                        code=ValidationCode.OPENING_HOURS,
                        message=f"{venue.name} is scheduled outside curated opening hours",
                        segment_id=segment.id,
                    )
                )

            if route.arrive_at - last_rest > self._minutes(hard.rest_interval_minutes):
                issues.append(
                    ValidationIssue(
                        code=ValidationCode.REST_INTERVAL,
                        message=f"no rest opportunity within {hard.rest_interval_minutes} minutes",
                        segment_id=segment.id,
                    )
                )
            if venue.rest_seating:
                last_rest = segment.activity_end

            if segment.purpose is SegmentPurpose.LUNCH:
                lunch_seen = True
                if local_start.time() > hard.lunch_latest:
                    issues.append(
                        ValidationIssue(
                            code=ValidationCode.LUNCH_TIMING,
                            message=f"lunch starts after {hard.lunch_latest.strftime('%H:%M')}",
                            segment_id=segment.id,
                        )
                    )

            previous_end = segment.activity_end
            expected_origin = venue.location
            expected_origin_label = venue.name

        if not lunch_seen:
            issues.append(
                ValidationIssue(
                    code=ValidationCode.LUNCH_TIMING,
                    message="itinerary has no accessible lunch stop",
                )
            )

        missing_required = hard.required_venue_ids - visited
        if missing_required:
            issues.append(
                ValidationIssue(
                    code=ValidationCode.REQUIRED_VENUE,
                    message=f"required venues missing: {', '.join(sorted(missing_required))}",
                )
            )

        if computed_cost > hard.total_budget_sgd:
            issues.append(
                ValidationIssue(
                    code=ValidationCode.BUDGET,
                    message=(
                        f"computed cost S${computed_cost:.2f} exceeds "
                        f"S${hard.total_budget_sgd:.2f}"
                    ),
                )
            )
        if not isclose(computed_cost, itinerary.total_cost_sgd, abs_tol=0.01):
            issues.append(
                ValidationIssue(
                    code=ValidationCode.COST_CALCULATION,
                    message=(
                        f"declared cost S${itinerary.total_cost_sgd:.2f} does not match "
                        f"tool-derived cost S${computed_cost:.2f}"
                    ),
                )
            )

        finish_by = self._at_local_time(request.journey_date, hard.finish_by)
        if itinerary.segments and itinerary.segments[-1].activity_end > finish_by:
            issues.append(
                ValidationIssue(
                    code=ValidationCode.FINISH_TIME,
                    message=f"itinerary finishes after {hard.finish_by.strftime('%H:%M')}",
                )
            )

        return ValidationResult(valid=not issues, issues=tuple(issues))

    @staticmethod
    def _at_local_time(day: date, value: time) -> datetime:
        return datetime.combine(day, value, tzinfo=SINGAPORE)

    @staticmethod
    def _minutes(value: int) -> timedelta:
        return timedelta(minutes=value)
