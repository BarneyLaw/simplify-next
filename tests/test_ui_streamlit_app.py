"""Role 3 gate for the Streamlit demo flow, driven headlessly with AppTest."""

from __future__ import annotations

from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from adaptsg.errors import ToolUnavailable
from adaptsg.settings import get_settings
from adaptsg.tools.environment import DemoEnvironmentClient

APP = Path(__file__).resolve().parents[1] / "streamlit_app.py"

INFEASIBLE_PROMPT = (
    "Plan a wheelchair day from Toa Payoh, must visit Gardens by the Bay and "
    "National Gallery and Botanic Gardens and National Museum, finish by 10:30am, budget $5."
)


@pytest.fixture(autouse=True)
def isolated_streamlit_runtime() -> None:
    """Each app run must start from a cold service cache and settings cache."""
    st.cache_resource.clear()
    get_settings.cache_clear()


def start_app() -> AppTest:
    app = AppTest.from_file(str(APP), default_timeout=60)
    app.run()
    return app


def click(app: AppTest, label: str) -> AppTest:
    """Click by visible label so the tests do not depend on widget ordering."""
    for button in app.button:
        if button.label == label:
            return button.click().run()
    raise AssertionError(f"no button labelled {label!r}; found {[b.label for b in app.button]}")


def labels(app: AppTest) -> list[str]:
    return [button.label for button in app.button]


def html_blocks(app: AppTest) -> str:
    """The rendered content of every `st.html()` component on the page."""
    return " ".join(str(getattr(element, "value", "")) for element in app.get("html"))


def draft(app: AppTest, prompt: str | None = None) -> AppTest:
    """Create a plan and stop at the draft, before any caregiver decision."""
    if prompt is not None:
        app.text_area[0].set_value(prompt)
    return click(app, "Create safe plan")


def plan(app: AppTest, prompt: str | None = None) -> AppTest:
    """Create a plan and accept it, which is what makes the journey active."""
    return click(draft(app, prompt), "Accept this plan")


def test_app_asks_for_a_plan_before_anything_exists() -> None:
    app = start_app()

    assert not app.exception
    assert any("Create a plan to begin" in message.value for message in app.info)
    assert not app.dataframe


def test_creating_a_plan_shows_the_locked_constraints_and_the_itinerary() -> None:
    app = plan(start_app())

    assert not app.exception
    assert "itinerary" in app.session_state
    rail = html_blocks(app)
    assert "Wheelchair access" in rail
    assert "Longest walk at once" in rail
    assert "Lunch before" in rail
    assert "Home by" in rail
    assert "Budget for the day" in rail
    itinerary = app.session_state["itinerary"]
    assert itinerary.segments[0].venue.name in rail
    assert not app.dataframe


def test_a_new_plan_is_a_draft_that_gates_the_adaptation_controls() -> None:
    """The server returns DRAFT; nothing is accepted until the caregiver decides."""
    app = draft(start_app())

    assert not app.exception
    assert app.session_state["journey_status"] == "draft"
    assert "Accept this plan" in labels(app)
    assert "Reject and start again" in labels(app)
    assert "Heavy rain and flooding" not in labels(app)
    assert "Check conditions" not in labels(app)


def test_accepting_the_draft_activates_the_journey_and_advances_its_version() -> None:
    app = draft(start_app())
    version = app.session_state["journey_version"]

    accepted = click(app, "Accept this plan")

    assert accepted.session_state["journey_status"] == "active"
    assert accepted.session_state["journey_version"] > version
    assert "Heavy rain and flooding" in labels(accepted)


def test_rejecting_the_draft_leaves_no_accepted_plan() -> None:
    rejected = click(draft(start_app()), "Reject and start again")

    assert not rejected.exception
    assert rejected.session_state["journey_status"] == "rejected"
    assert rejected.session_state["itinerary"] is None
    assert any("Plan rejected" in message.value for message in rejected.info)
    assert not rejected.dataframe


def test_an_infeasible_request_stops_and_asks_instead_of_planning() -> None:
    app = draft(start_app(), INFEASIBLE_PROMPT)

    assert not app.exception
    assert "itinerary" not in app.session_state
    assert "journey_id" not in app.session_state
    assert app.error, "an infeasible request must report that no safe plan exists"
    assert not app.dataframe


def test_rain_trigger_produces_a_validated_proposal() -> None:
    app = click(plan(start_app()), "Heavy rain and flooding")

    assert not app.exception
    assert app.session_state["proposal"] is not None
    assert app.session_state["proposal"].validation.valid


def test_applying_a_proposal_advances_the_plan_and_clears_the_proposal() -> None:
    app = click(plan(start_app()), "Heavy rain and flooding")
    before = app.session_state["itinerary"]
    proposed = app.session_state["proposal"].itinerary

    applied = click(app, "Approve and apply")

    assert not applied.exception
    assert applied.session_state["proposal"] is None
    assert applied.session_state["itinerary"].id == proposed.id
    assert applied.session_state["itinerary"].id != before.id


def test_rejecting_a_proposal_keeps_the_current_plan() -> None:
    app = click(plan(start_app()), "Heavy rain and flooding")
    before = app.session_state["itinerary"]

    kept = click(app, "Keep current plan")

    assert not kept.exception
    assert kept.session_state["proposal"] is None
    assert kept.session_state["itinerary"].id == before.id


def test_a_stale_version_reloads_the_server_plan_instead_of_applying_blindly() -> None:
    """Optimistic concurrency: the caregiver decides against what the server holds."""
    app = plan(start_app())
    current = app.session_state["itinerary"].id
    app.session_state["journey_version"] = app.session_state["journey_version"] + 5

    stale = click(app, "Heavy rain and flooding")

    assert not stale.exception
    assert any("has been reloaded" in message.value for message in stale.warning)
    assert stale.session_state["itinerary"].id == current
    assert stale.session_state["proposal"] is None


def test_live_verification_failure_retains_the_current_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Safety rule 10: a monitoring failure must never disturb the accepted plan."""
    app = plan(start_app())
    before = app.session_state["itinerary"]

    def explode(self: DemoEnvironmentClient) -> None:
        raise ToolUnavailable("weather provider timed out")

    monkeypatch.setattr(DemoEnvironmentClient, "current", explode)
    checked = click(app, "Check conditions")

    assert not checked.exception
    assert checked.session_state["itinerary"].id == before.id
    assert checked.error, "a live-tool failure must be reported"


def test_fatigue_trigger_requires_explicit_cost_approval() -> None:
    """Safety rule 8: a material cost increase must present an approval decision."""
    app = click(plan(start_app()), "Mum is more tired")

    assert not app.exception
    proposal = app.session_state["proposal"]
    assert proposal.requires_approval
    assert any("Approval required" in message.value for message in app.warning)
    assert [button.label for button in app.button if "Approve" in button.label] == [
        "Approve and apply"
    ]


def test_the_demo_never_presents_estimates_as_live_data() -> None:
    """Safety rule 11: a demo estimate must not be captioned as live verification."""
    app = plan(start_app())

    captions = " ".join(caption.value for caption in app.caption).casefold()
    assert "demo" in captions
    assert "demo_route_estimator_v1" in captions
    assert "verified" not in captions
    assert any("DEMO DATA" in message.value for message in app.info)


def test_the_no_feasible_panel_states_that_nothing_was_relaxed() -> None:
    app = draft(start_app(), INFEASIBLE_PROMPT)

    errors = " ".join(message.value for message in app.error)
    warnings = " ".join(message.value for message in app.warning)
    assert "No safe plan exists" in errors
    assert "did not weaken" in warnings
    assert "did not invent a route" in warnings


def test_a_live_tool_failure_says_the_plan_is_retained(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = plan(start_app())

    def explode(self: DemoEnvironmentClient) -> None:
        raise ToolUnavailable("weather provider timed out")

    monkeypatch.setattr(DemoEnvironmentClient, "current", explode)
    checked = click(app, "Check conditions")

    errors = " ".join(message.value for message in checked.error)
    assert "Live verification failed" in errors
    assert "retained unchanged" in errors


def test_exhausting_the_replan_budget_is_reported_as_its_own_state() -> None:
    """Safety rule 9: replanning is bounded, and hitting the cap is not a generic failure."""
    app = plan(start_app())
    for step in (
        "Heavy rain and flooding",
        "Approve and apply",
        "Mum is more tired",
        "Approve and apply",
    ):
        app = click(app, step)

    assert app.session_state["itinerary"].replan_count == 2
    exhausted = click(app, "Heavy rain and flooding")

    assert exhausted.session_state["proposal"] is None
    errors = " ".join(message.value for message in exhausted.error)
    assert "Replanning is bounded" in errors
    assert exhausted.session_state["itinerary"].replan_count == 2


def test_a_monitored_demo_run_never_captions_the_snapshot_as_observed() -> None:
    """Safety rule 11: the conditions snapshot is generated in demo mode, not observed."""
    app = click(plan(start_app()), "Check conditions")

    assert not app.exception
    conditions = [message.value for message in app.info if "Conditions:" in message.value]
    assert conditions, "a monitored run must report the conditions snapshot"
    assert all("Observed" not in message for message in conditions)
    assert all("Generated " in message for message in conditions)
    assert all("via demo_environment_snapshot_v1." in message for message in conditions)


def test_every_interactive_widget_renders_a_non_empty_accessible_name() -> None:
    """The Streamlit counterpart of the button-name check the retired browser gate ran.

    Streamlit derives a widget's accessible name from its label, so a blank or
    collapsed label leaves a screen-reader user with an unnamed control.
    """
    app = click(plan(start_app()), "Heavy rain and flooding")

    widgets = [*app.button, *app.text_area, *app.date_input]
    assert len(widgets) >= 3, "the sidebar and approval controls must all be rendered"
    unnamed = [type(widget).__name__ for widget in widgets if not (widget.label or "").strip()]
    assert not unnamed, f"widgets without an accessible name: {unnamed}"
