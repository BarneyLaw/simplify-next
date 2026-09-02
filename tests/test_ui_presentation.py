"""Role 3 coverage for the pure presentation helpers shared by both front ends."""

from __future__ import annotations

from adaptsg.domain import Itinerary, ItinerarySegment, TravelMode
from adaptsg.presentation import itinerary_rows, retained_segment_percentage


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
