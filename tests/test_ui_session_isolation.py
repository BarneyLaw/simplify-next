"""Role 3 evidence that two browser sessions never share journey state.

`PROGRESS.md` records browser session QA as blocked on a connected browser. Two
independent `AppTest` instances model two browser sessions in one process, so the
isolation property is provable deterministically in CI.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from adaptsg.settings import get_settings

APP = Path(__file__).resolve().parents[1] / "streamlit_app.py"


@pytest.fixture(autouse=True)
def isolated_streamlit_runtime() -> None:
    st.cache_resource.clear()
    get_settings.cache_clear()


def session() -> AppTest:
    app = AppTest.from_file(str(APP), default_timeout=60)
    app.run()
    return app


def create_plan(app: AppTest) -> AppTest:
    for button in app.button:
        if button.label == "Create safe plan":
            return button.click().run()
    raise AssertionError("the create button is missing")


def test_a_plan_in_one_session_is_invisible_to_another_session() -> None:
    first = create_plan(session())
    second = session()

    assert "itinerary" in first.session_state
    assert "itinerary" not in second.session_state
    assert any("Create a plan to begin" in message.value for message in second.info)


def test_a_replan_proposal_does_not_leak_between_sessions() -> None:
    first = create_plan(session())
    for button in first.button:
        if button.label == "Simulate heavy rain + flood":
            first = button.click().run()
            break
    second = create_plan(session())

    assert first.session_state["proposal"] is not None
    assert second.session_state["proposal"] is None


def test_each_session_plans_independently_through_the_shared_service() -> None:
    """The service is cached process-wide, so it must hold no per-journey state."""
    first = create_plan(session())
    second = create_plan(session())

    assert first.session_state["itinerary"].id != second.session_state["itinerary"].id
    assert first.session_state["itinerary"].segments[0].venue.id == (
        second.session_state["itinerary"].segments[0].venue.id
    )
