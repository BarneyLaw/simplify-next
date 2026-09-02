"""Pure presentation helpers shared by Streamlit and the web API.

Nothing here decides safety, routing, accessibility or approval. Every function
formats state that the domain, tools and validator have already produced.
"""

from __future__ import annotations

from datetime import datetime

from adaptsg.domain import EnvironmentSnapshot, Itinerary

DEMO_MODE = "demo"


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


def latest_route_timestamp(itinerary: Itinerary) -> datetime | None:
    """Newest route source timestamp, or None when the plan carries no segments."""
    if not itinerary.segments:
        return None
    return max(segment.route.source_timestamp for segment in itinerary.segments)


def route_sources(itinerary: Itinerary) -> tuple[str, ...]:
    """Distinct route provenance labels in first-seen order."""
    return tuple(dict.fromkeys(segment.route.source for segment in itinerary.segments))


def provenance_label(itinerary: Itinerary, *, mode: str) -> str:
    """Attribute the route numbers to their source without implying live verification.

    Demo values are produced by a deterministic estimator at planning time, so the
    timestamp records when they were generated, not when anything was checked against
    a provider. Saying "verified" there would present an estimate as live data.
    """
    timestamp = latest_route_timestamp(itinerary)
    if timestamp is None:
        return f"No route values to attribute ({mode} mode)."
    when = timestamp.astimezone().strftime("%d %b %Y %H:%M %Z")
    origin = ", ".join(route_sources(itinerary))
    action = "generated" if mode == DEMO_MODE else "verified against providers"
    return f"Route values {action} {when} by {origin} ({mode} mode)."


def environment_provenance_label(snapshot: EnvironmentSnapshot, *, mode: str) -> str:
    """Attribute a conditions snapshot to its source without implying it was observed.

    The demo client stamps ``observed_at`` with generation time, so captioning it
    "observed" would present a deterministic estimate as a reading taken from the
    world. Live snapshots come from providers and are genuinely observations.
    """
    when = snapshot.observed_at.astimezone().strftime("%d %b %H:%M %Z")
    action = "Generated" if mode == DEMO_MODE else "Observed"
    return f"{action} {when} via {snapshot.source}."


def mode_badge(mode: str) -> str:
    """Short, always-visible statement of what the displayed numbers are."""
    if mode == DEMO_MODE:
        return "DEMO DATA - deterministic estimates, not live conditions"
    return "LIVE DATA - values retrieved from external providers"
