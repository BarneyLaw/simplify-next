"""Role 3 drift guards for the spoken demo narrative.

`PROGRESS.md:90` carries an open P0 decision: whether Gardens by the Bay is a soft
preference in the spoken demo, with wording kept consistent. Both halves of that are
true today and nothing enforces either, so these tests pin them from both sides — the
copy the judges read and the constraints the parser actually produces.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from adaptsg.domain import ParseOutcome
from adaptsg.preference_parser import DeterministicPreferenceParser
from adaptsg.tools.catalog import VenueCatalog
from streamlit_app import SAMPLE_PROMPT

BROWSER_CLIENT = Path(__file__).resolve().parents[1] / "public" / "index.html"
GARDENS_BY_THE_BAY = "gardens-bay-outdoor"


def browser_prompt() -> str:
    """The prefilled prompt a judge sees on the Vercel surface."""
    html = BROWSER_CLIENT.read_text(encoding="utf-8")
    match = re.search(r'<textarea id="prompt"[^>]*>(.*?)</textarea>', html, re.DOTALL)
    assert match is not None, "the browser client must prefill the demo prompt"
    return match.group(1)


def parse(prompt: str) -> ParseOutcome:
    return DeterministicPreferenceParser(VenueCatalog()).parse(
        prompt,
        journey_date=date(2026, 9, 3),
    )


def test_both_surfaces_ship_the_same_demo_prompt() -> None:
    """Two surfaces telling two stories would split the five-minute demo narrative."""
    assert browser_prompt() == SAMPLE_PROMPT


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


def test_the_short_alias_after_must_is_still_only_a_preference() -> None:
    """Documents a parser asymmetry that makes the demo wording load-bearing.

    `_mentioned_venue_ids` recognises the short alias "gardens by the bay", but
    `_required_venue_ids` only escalates on the full catalog name or the spaced id,
    so the phrasing a caregiver would actually type stays soft. Raised to Role 1, who
    owns `preference_parser.py`. If that is fixed this test fails, which is the point:
    the spoken demo claims Gardens by the Bay is a preference, and whoever changes the
    escalation rules must re-read that decision at `PROGRESS.md:90` before landing it.
    """
    outcome = parse(
        SAMPLE_PROMPT.replace(
            "We would like to visit Gardens by the Bay.",
            "We must visit Gardens by the Bay.",
        )
    )

    assert outcome.request.hard.required_venue_ids == frozenset()
    assert GARDENS_BY_THE_BAY in outcome.request.soft.preferred_venue_ids
