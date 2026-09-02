"""Role 3 coverage for the pure presentation helpers shared by both front ends."""

from __future__ import annotations

from datetime import UTC, datetime

from adaptsg.domain import EnvironmentSnapshot, Itinerary, ItinerarySegment, TravelMode
from adaptsg.presentation import (
    environment_provenance_label,
    itinerary_rows,
    latest_route_timestamp,
    mode_badge,
    provenance_label,
    retained_segment_percentage,
    route_sources,
)


def with_segments(itinerary: Itinerary, segments: tuple[ItinerarySegment, ...]) -> Itinerary:
    """Rebuild an itinerary around different segments for presentation-only assertions."""
    return itinerary.model_copy(update={"segments": segments})


def with_mode(segment: ItinerarySegment, mode: TravelMode) -> ItinerarySegment:
    return segment.model_copy(update={"route": segment.route.model_copy(update={"mode": mode})})


def test_retained_percentage_is_full_when_the_previous_plan_had_no_segments(
    itinerary: Itinerary,
) -> None:
    empty = with_segments(itinerary, ())

    assert retained_segment_percentage(empty, itinerary) == 100


def test_retained_percentage_measures_against_the_previous_plan_length(
    itinerary: Itinerary,
) -> None:
    """A shorter replacement still divides by the original segment count."""
    shortened = with_segments(itinerary, itinerary.segments[:2])

    assert len(itinerary.segments) == 3
    assert retained_segment_percentage(itinerary, shortened) == 67


def test_retained_percentage_treats_a_transport_change_as_a_replacement(
    itinerary: Itinerary,
) -> None:
    retimed = with_segments(
        itinerary,
        tuple(with_mode(segment, TravelMode.TAXI) for segment in itinerary.segments),
    )

    assert retained_segment_percentage(itinerary, retimed) == 0


def test_retained_percentage_treats_a_venue_change_as_a_replacement(
    itinerary: Itinerary,
) -> None:
    first, *rest = itinerary.segments
    swapped = first.model_copy(
        update={"venue": first.venue.model_copy(update={"id": "somewhere-else"})}
    )
    replaced = with_segments(itinerary, (swapped, *rest))

    assert retained_segment_percentage(itinerary, replaced) == 67


def test_itinerary_rows_expose_per_segment_provenance(itinerary: Itinerary) -> None:
    rows = itinerary_rows(itinerary)

    assert len(rows) == len(itinerary.segments)
    assert [row["stop"] for row in rows] == [segment.venue.name for segment in itinerary.segments]
    assert {row["route_source"] for row in rows} == {"demo_route_estimator_v1"}
    assert {row["accessibility"] for row in rows} == {"verified"}
    assert rows[0]["transport"] == "Public Transport"


def test_itinerary_rows_round_combined_route_and_venue_cost(itinerary: Itinerary) -> None:
    """Route and venue costs are summed as floats, so the row must round the result."""
    first = itinerary.segments[0]
    priced = first.model_copy(
        update={
            "route": first.route.model_copy(update={"estimated_cost_sgd": 0.1}),
            "venue": first.venue.model_copy(update={"estimated_cost_sgd": 0.2}),
        }
    )

    rows = itinerary_rows(with_segments(itinerary, (priced,)))

    assert 0.1 + 0.2 != 0.3
    assert rows[0]["cost_sgd"] == 0.3


def test_itinerary_rows_are_empty_without_segments(itinerary: Itinerary) -> None:
    assert itinerary_rows(with_segments(itinerary, ())) == []


def test_latest_route_timestamp_is_absent_without_segments(itinerary: Itinerary) -> None:
    """render_itinerary previously called max() directly and would raise here."""
    assert latest_route_timestamp(with_segments(itinerary, ())) is None
    assert latest_route_timestamp(itinerary) == max(
        segment.route.source_timestamp for segment in itinerary.segments
    )


def test_route_sources_are_deduplicated_in_first_seen_order(itinerary: Itinerary) -> None:
    assert route_sources(itinerary) == ("demo_route_estimator_v1",)
    assert route_sources(with_segments(itinerary, ())) == ()


def test_demo_provenance_is_generated_and_never_claims_verification(
    itinerary: Itinerary,
) -> None:
    label = provenance_label(itinerary, mode="demo")

    assert "generated" in label
    assert "verified" not in label
    assert "demo_route_estimator_v1" in label
    assert "(demo mode)" in label


def test_live_provenance_attributes_values_to_providers(itinerary: Itinerary) -> None:
    label = provenance_label(itinerary, mode="live")

    assert "verified against providers" in label
    assert "(live mode)" in label


def test_provenance_label_handles_a_plan_without_route_values(itinerary: Itinerary) -> None:
    assert provenance_label(with_segments(itinerary, ()), mode="demo") == (
        "No route values to attribute (demo mode)."
    )


def test_mode_badge_distinguishes_demo_estimates_from_live_values() -> None:
    assert "DEMO DATA" in mode_badge("demo")
    assert "not live conditions" in mode_badge("demo")
    assert "LIVE DATA" in mode_badge("live")


def snapshot(source: str = "demo_environment_snapshot_v1") -> EnvironmentSnapshot:
    return EnvironmentSnapshot(
        weather_summary="Fair",
        psi=42,
        observed_at=datetime(2026, 9, 2, 4, 30, tzinfo=UTC),
        source=source,
    )


def test_a_demo_snapshot_is_generated_and_never_claims_observation() -> None:
    """The demo client stamps observed_at with generation time, not an observation."""
    label = environment_provenance_label(snapshot(), mode="demo")

    assert label.startswith("Generated ")
    assert "Observed" not in label
    assert "demo_environment_snapshot_v1" in label


def test_a_live_snapshot_is_reported_as_observed() -> None:
    label = environment_provenance_label(snapshot("data_gov_sg_v2"), mode="live")

    assert label.startswith("Observed ")
    assert "data_gov_sg_v2" in label


def test_environment_provenance_keeps_the_source_string_verbatim() -> None:
    """Presentation attributes the snapshot; it never rewrites the tool's own label."""
    for mode in ("demo", "live"):
        assert environment_provenance_label(snapshot("lta_pub_alerts_v1"), mode=mode).endswith(
            "via lta_pub_alerts_v1."
        )
