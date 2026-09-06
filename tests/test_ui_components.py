"""Direct unit tests on the pure `ui.py` component renderers.

These are the checks the retired `scripts/check_web.mjs` could only approximate with
regex against the browser client; here they run against the real Python renderers.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from adaptsg import ui
from adaptsg.domain import (
    AccessibilityStatus,
    EnvironmentSnapshot,
    FreshnessStatus,
    Itinerary,
    ItinerarySegment,
)


def with_segments(itinerary: Itinerary, segments: tuple[ItinerarySegment, ...]) -> Itinerary:
    return itinerary.model_copy(update={"segments": segments})


def test_the_access_badge_needs_both_a_verified_status_and_a_source(itinerary: Itinerary) -> None:
    first = itinerary.segments[0]
    verified_without_source = first.model_copy(
        update={"venue": first.venue.model_copy(update={"accessibility_source": None})}
    )

    html = ui.timeline(with_segments(itinerary, (verified_without_source,)))

    assert "Wheelchair access not confirmed" in html
    assert "Wheelchair access checked" not in html


def test_an_inaccessible_venue_renders_the_breach_chip(itinerary: Itinerary) -> None:
    first = itinerary.segments[0]
    inaccessible = first.model_copy(
        update={
            "venue": first.venue.model_copy(
                update={
                    "accessibility_status": AccessibilityStatus.INACCESSIBLE,
                    "accessibility_source": None,
                }
            )
        }
    )

    html = ui.timeline(with_segments(itinerary, (inaccessible, *itinerary.segments[1:])))

    assert "Not wheelchair accessible" in html


def test_a_stale_leg_renders_its_freshness_chip_and_a_fixture_leg_does_not(
    itinerary: Itinerary,
) -> None:
    first = itinerary.segments[0]
    stale = first.model_copy(
        update={"route": first.route.model_copy(update={"freshness": FreshnessStatus.STALE})}
    )
    fixture = first.model_copy(
        update={"route": first.route.model_copy(update={"freshness": FreshnessStatus.FIXTURE})}
    )

    stale_html = ui.timeline(with_segments(itinerary, (stale, *itinerary.segments[1:])))
    fixture_html = ui.timeline(with_segments(itinerary, (fixture, *itinerary.segments[1:])))

    assert "Not checked recently" in stale_html
    assert "Not checked recently" not in fixture_html


def test_the_walking_meter_marks_a_breach_when_a_leg_exceeds_the_limit(
    itinerary: Itinerary,
) -> None:
    hard = itinerary.request.hard
    first = itinerary.segments[0]
    breaching = first.model_copy(
        update={
            "route": first.route.model_copy(
                update={"walking_distance_m": hard.max_walking_distance_m + 50}
            )
        }
    )
    breached = with_segments(itinerary, (breaching, *itinerary.segments[1:]))

    assert 'class="track over"' in ui.summary(breached)
    assert 'class="track over"' not in ui.summary(itinerary)


def test_the_evidence_panel_prints_a_missing_review_date_as_not_recorded(
    itinerary: Itinerary,
) -> None:
    assert all(segment.venue.data_reviewed_on is None for segment in itinerary.segments)

    html = ui.evidence(
        itinerary,
        mode="demo",
        storage="memory_demo",
        journey_id=uuid4(),
        version=1,
        status="active",
        expires_at=datetime(2026, 9, 3, tzinfo=UTC),
    )

    assert "data_reviewed_on: not recorded" in html


def test_the_summary_omits_replan_count_unless_asked_to_show_it(itinerary: Itinerary) -> None:
    assert "Changes made so far" not in ui.summary(itinerary)
    assert "Changes made so far" in ui.summary(itinerary, show_replans=True)


def test_must_haves_lists_the_rest_interval_as_a_row(itinerary: Itinerary) -> None:
    html = ui.must_haves(itinerary.request.hard)

    assert "Rest break every" in html
    assert f"{itinerary.request.hard.rest_interval_minutes} min" in html


def test_conditions_summary_includes_weather_psi_flood_and_transport_signals(
    itinerary: Itinerary,
) -> None:
    snapshot = EnvironmentSnapshot(
        weather_summary="Windy",
        psi=93,
        flood_affected_venue_ids=frozenset({itinerary.segments[-1].venue.id}),
        disrupted_route_labels=frozenset({"NSL"}),
        observed_at=datetime(2026, 9, 5, tzinfo=UTC),
        source="live-test",
    )

    text = ui.conditions_summary(snapshot, itinerary)

    assert "Windy" in text
    assert "93 (Moderate)" in text
    assert "Flood alerts: Gardens by the Bay Outdoor" in text
    assert "Transport alerts: NSL" in text


def test_map_svg_spaces_nearby_stop_labels(itinerary: Itinerary) -> None:
    html = ui.map_svg(itinerary)

    assert html.count('class="pin ') == len(itinerary.segments) + 1
    assert 'class="pin pin-right"' in html or 'class="pin pin-left"' in html
