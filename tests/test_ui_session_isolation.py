"""Role 3 evidence that two browser sessions never share journey state.

`PROGRESS.md` records browser session QA as blocked on a connected browser. Two
independent `AppTest` instances model two browser sessions in one process, so the
isolation property is provable deterministically in CI.

Since the journey lifecycle became server-authoritative the claim is stronger than it
was: both sessions talk to one process-wide store, so isolation now has to come from
the journey identifier each session holds rather than from separate per-process state.
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


def click(app: AppTest, label: str) -> AppTest:
    for button in app.button:
        if button.label == label:
            return button.click().run()
    raise AssertionError(f"no button labelled {label!r}; found {[b.label for b in app.button]}")


def create_plan(app: AppTest) -> AppTest:
    return click(click(app, "Create safe plan"), "Accept this plan")


def test_a_plan_in_one_session_is_invisible_to_another_session() -> None:
    first = create_plan(session())
    second = session()

    assert "journey_id" in first.session_state
    assert "journey_id" not in second.session_state
    assert "itinerary" not in second.session_state
    assert any("Create a plan to begin" in message.value for message in second.info)


def test_a_replan_proposal_does_not_leak_between_sessions() -> None:
    first = click(create_plan(session()), "Heavy rain and flooding")
    second = create_plan(session())

    assert first.session_state["proposal"] is not None
    assert second.session_state["proposal"] is None


def test_each_session_owns_a_distinct_journey_in_the_shared_store() -> None:
    """The service and its journey store are cached process-wide across sessions."""
    first = create_plan(session())
    second = create_plan(session())

    assert first.session_state["journey_id"] != second.session_state["journey_id"]
    assert first.session_state["itinerary"].id != second.session_state["itinerary"].id
    assert first.session_state["itinerary"].segments[0].venue.id == (
        second.session_state["itinerary"].segments[0].venue.id
    )


def test_one_session_never_reuses_another_sessions_retry_key() -> None:
    """A shared idempotency key would replay one caregiver's decision into another session."""
    first = create_plan(session())
    second = create_plan(session())

    assert first.session_state["session_nonce"] != second.session_state["session_nonce"]
