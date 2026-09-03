"""Deterministic itinerary metrics derived from typed route and venue facts."""

from datetime import UTC, datetime

from adaptsg.domain import Itinerary, PlanMetrics, SegmentMetrics, ToolResult
from adaptsg.tools.freshness import FreshnessKind, successful_result


def calculate_plan_metrics(itinerary: Itinerary) -> PlanMetrics:
    """Calculate totals without trusting the itinerary's declared total cost."""
    if not itinerary.segments:
        return PlanMetrics(total_cost_sgd=0, total_walking_distance_m=0, elapsed_minutes=0)
    first_route = itinerary.segments[0].route
    last_activity = itinerary.segments[-1].activity_end
    elapsed_minutes = round((last_activity - first_route.depart_at).total_seconds() / 60)
    segment_metrics = tuple(
        SegmentMetrics(
            segment_id=segment.id,
            walking_distance_m=segment.route.walking_distance_m,
            cost_sgd=round(segment.venue.estimated_cost_sgd + segment.route.estimated_cost_sgd, 2),
        )
        for segment in itinerary.segments
    )
    return PlanMetrics(
        total_cost_sgd=round(sum(item.cost_sgd for item in segment_metrics), 2),
        total_walking_distance_m=sum(item.walking_distance_m for item in segment_metrics),
        elapsed_minutes=elapsed_minutes,
        segments=segment_metrics,
    )


def calculate_plan_metrics_result(itinerary: Itinerary) -> ToolResult[PlanMetrics]:
    timestamp = datetime.now(UTC)
    return successful_result(
        calculate_plan_metrics(itinerary),
        source="deterministic_plan_metrics_v1",
        source_timestamp=timestamp,
        kind=FreshnessKind.ROUTE,
    )
