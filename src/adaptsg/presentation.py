"""Pure presentation helpers shared by Streamlit and the web API."""

from __future__ import annotations

from adaptsg.domain import Itinerary


def retained_segment_percentage(before: Itinerary, after: Itinerary) -> int:
    if not before.segments:
        return 100
    retained = sum(
        1
        for left, right in zip(before.segments, after.segments, strict=False)
        if left.venue.id == right.venue.id and left.route.mode == right.route.mode
    )
    return round(retained / len(before.segments) * 100)


def itinerary_rows(itinerary: Itinerary) -> list[dict[str, str | int | float]]:
    rows: list[dict[str, str | int | float]] = []
    for segment in itinerary.segments:
        rows.append(
            {
                "start": segment.activity_start.strftime("%H:%M"),
                "end": segment.activity_end.strftime("%H:%M"),
                "stop": segment.venue.name,
                "purpose": segment.purpose.value,
                "transport": segment.route.mode.value.replace("_", " ").title(),
                "travel_minutes": segment.route.duration_minutes,
                "walking_metres": segment.route.walking_distance_m,
                "cost_sgd": round(
                    segment.route.estimated_cost_sgd + segment.venue.estimated_cost_sgd,
                    2,
                ),
                "accessibility": segment.venue.accessibility_status.value,
                "route_source": segment.route.source,
            }
        )
    return rows
