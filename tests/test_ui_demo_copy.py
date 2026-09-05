"""Role 3 drift guards for the spoken demo narrative.

`PROGRESS.md:90` carries an open P0 decision: whether Gardens by the Bay is a soft
preference in the spoken demo, with wording kept consistent. These tests pin that from
the parser side — the constraints `SAMPLE_PROMPT` actually produces.
"""

from __future__ import annotations

from datetime import date

from adaptsg.domain import ParseOutcome
from adaptsg.preference_parser import DeterministicPreferenceParser
from adaptsg.tools.catalog import VenueCatalog
from streamlit_app import SAMPLE_PROMPT

GARDENS_BY_THE_BAY = "gardens-bay-outdoor"


def parse(prompt: str) -> ParseOutcome:
    return DeterministicPreferenceParser(VenueCatalog()).parse(
        prompt,
        journey_date=date(2026, 9, 3),
    )


def test_the_demo_prompt_makes_gardens_by_the_bay_a_preference_not_a_requirement() -> None:
    """The recorded P0 decision, guarded from both the copy side and the parser side."""
    outcome = parse(SAMPLE_PROMPT)

    assert outcome.request.hard.required_venue_ids == frozenset()
    assert GARDENS_BY_THE_BAY in outcome.request.soft.preferred_venue_ids


def test_the_demo_prompt_asks_for_gardens_by_the_bay_in_preference_wording() -> None:
    """Soft phrasing, not the venue name, is what keeps the parser treating it as soft."""
    assert "would like to" in SAMPLE_PROMPT
    assert "Gardens by the Bay" in SAMPLE_PROMPT
    assert "must visit" not in SAMPLE_PROMPT


def test_the_full_venue_name_after_must_does_produce_a_hard_constraint() -> None:
    """Without this the preference assertion could pass merely because parsing is inert."""
    outcome = parse(
        SAMPLE_PROMPT.replace(
            "We would like to visit Gardens by the Bay.",
            "We must visit Gardens by the Bay Outdoor Gardens.",
        )
    )

    assert GARDENS_BY_THE_BAY in outcome.request.hard.required_venue_ids
    assert GARDENS_BY_THE_BAY not in outcome.request.soft.preferred_venue_ids


def test_the_short_alias_after_must_produces_a_hard_constraint() -> None:
    """Explicit requirement wording must override the demo's usual soft phrasing."""
    outcome = parse(
        SAMPLE_PROMPT.replace(
            "We would like to visit Gardens by the Bay.",
            "We must visit Gardens by the Bay.",
        )
    )

    assert GARDENS_BY_THE_BAY in outcome.request.hard.required_venue_ids
    assert GARDENS_BY_THE_BAY not in outcome.request.soft.preferred_venue_ids
