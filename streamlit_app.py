"""AdaptSG Streamlit demo: plan, monitor, explain, approve, and minimally replan."""

from __future__ import annotations

from datetime import date, timedelta
from typing import cast

import streamlit as st

from adaptsg.agent import AdaptSGService, build_service
from adaptsg.domain import (
    Itinerary,
    MonitoringOutcome,
    PlanOutcome,
    ReplanProposal,
    ReplanTrigger,
    TriggerType,
)
from adaptsg.errors import AdaptSGError
from adaptsg.presentation import itinerary_rows, retained_segment_percentage
from adaptsg.settings import get_settings

SAMPLE_PROMPT = (
    "Plan a 10 am-5 pm day for me and my 72-year-old mother, starting from Toa Payoh. "
    "She uses a wheelchair, should not walk more than 400 metres at once, needs lunch "
    "before 1 pm, and we have a $70 transport and activity budget. We would like to "
    "visit Gardens by the Bay."
)


@st.cache_resource
def service() -> AdaptSGService:
    return build_service()


def state_value(name: str) -> object | None:
    return st.session_state.get(name)


def render_constraints(itinerary: Itinerary) -> None:
    hard = itinerary.request.hard
    st.subheader("Locked safety and budget constraints")
    columns = st.columns(5)
    columns[0].metric("Wheelchair access", "Verified only")
    columns[1].metric("Walking per leg", f"<= {hard.max_walking_distance_m} m")
    columns[2].metric("Lunch starts by", hard.lunch_latest.strftime("%H:%M"))
    columns[3].metric("Finish by", hard.finish_by.strftime("%H:%M"))
    columns[4].metric("Total budget", f"S${hard.total_budget_sgd:.0f}")
    st.caption(
        f"Rest opportunity at least every {hard.rest_interval_minutes} minutes. "
        "These values are typed state and cannot be relaxed by replanning."
    )


def render_itinerary(itinerary: Itinerary) -> None:
    st.subheader("Current safe itinerary")
    left, middle, right = st.columns(3)
    left.metric("Total cost", f"S${itinerary.total_cost_sgd:.2f}")
    middle.metric("Stops", len(itinerary.segments))
    right.metric("Replans used", f"{itinerary.replan_count}/2")
    st.dataframe(
        itinerary_rows(itinerary),
        hide_index=True,
        width="stretch",
        column_config={
            "cost_sgd": st.column_config.NumberColumn("Cost (SGD)", format="S$%.2f"),
            "walking_metres": st.column_config.NumberColumn("Walking (m)"),
            "route_source": st.column_config.TextColumn("Route source"),
        },
    )
    freshest = max(segment.route.source_timestamp for segment in itinerary.segments)
    st.caption(
        f"Route values last verified {freshest.astimezone().strftime('%d %b %Y %H:%M %Z')}. "
        f"Constraint parser: {itinerary.parser_source}."
    )


def render_monitoring(monitoring: MonitoringOutcome) -> None:
    snapshot = monitoring.snapshot
    st.info(
        f"Conditions: {snapshot.weather_summary}; 24-hour PSI {snapshot.psi}. "
        f"Observed {snapshot.observed_at.astimezone().strftime('%d %b %H:%M %Z')} "
        f"via {snapshot.source}."
    )
    if monitoring.triggers:
        for trigger in monitoring.triggers:
            st.warning(trigger.message)
    else:
        st.success("No monitored condition currently affects this itinerary.")


def render_proposal(before: Itinerary, proposal: ReplanProposal) -> None:
    st.divider()
    st.subheader("Proposed smallest safe adjustment")
    retained = retained_segment_percentage(before, proposal.itinerary)
    left, middle, right = st.columns(3)
    left.metric("Unaffected plan retained", f"{retained}%")
    middle.metric(
        "Cost change",
        f"S${proposal.cost_delta_sgd:+.2f}",
        delta=f"New total S${proposal.itinerary.total_cost_sgd:.2f}",
    )
    right.metric("Deterministic validation", "PASS" if proposal.validation.valid else "FAIL")

    for change in proposal.changes:
        st.write(f"Stop {change.segment_index + 1}: **{change.before}** -> **{change.after}**")
        st.caption(change.reason)
    if not proposal.changes:
        st.info("The monitored event does not require an itinerary change.")

    if proposal.requires_approval:
        st.warning(
            "Approval required: this option increases cost beyond the configured S$8 threshold."
        )
    apply_col, reject_col = st.columns(2)
    if apply_col.button(
        "Approve and apply" if proposal.requires_approval else "Apply adjustment",
        type="primary",
        use_container_width=True,
    ):
        st.session_state.itinerary = service().apply_proposal(proposal, approved=True)
        st.session_state.proposal = None
        st.session_state.monitoring = None
        st.rerun()
    if reject_col.button("Keep current plan", use_container_width=True):
        st.session_state.proposal = None
        st.rerun()


def propose(trigger: ReplanTrigger, itinerary: Itinerary) -> None:
    try:
        st.session_state.proposal = service().propose_replan(itinerary, trigger)
    except AdaptSGError as exc:
        st.error(str(exc))


def main() -> None:
    st.set_page_config(
        page_title="AdaptSG",
        page_icon="♿",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    settings = get_settings()
    st.title("AdaptSG")
    st.markdown(
        "**Inclusive journey planning that preserves accessibility, health and budget limits "
        "when conditions change.**"
    )

    with st.sidebar:
        st.header("Runtime")
        st.write(f"Mode: **{settings.adaptsg_mode.upper()}**")
        if settings.adaptsg_mode == "demo":
            st.info("Offline demo data is deterministic and labelled. No live claim is implied.")
        st.header("Non-negotiable policy")
        st.write("The LLM proposes. Deterministic code validates.")
        st.write("No bookings, payments, diagnosis, or silent constraint relaxation.")

    prompt = st.text_area("Describe the day and constraints", value=SAMPLE_PROMPT, height=150)
    journey_date = cast(
        date,
        st.date_input("Journey date", value=date.today() + timedelta(days=1)),
    )
    if st.button("Create safe plan", type="primary"):
        try:
            outcome = service().create_plan(prompt, journey_date=journey_date)
            st.session_state.plan_outcome = outcome
            st.session_state.itinerary = outcome.itinerary
            st.session_state.proposal = None
            st.session_state.monitoring = None
        except AdaptSGError as exc:
            st.error(str(exc))

    outcome_value = state_value("plan_outcome")
    itinerary_value = state_value("itinerary")
    if not isinstance(outcome_value, PlanOutcome) or not isinstance(itinerary_value, Itinerary):
        st.info("Create a plan to begin the five-minute demo.")
        return

    for warning in outcome_value.warnings:
        st.warning(warning)
    render_constraints(itinerary_value)
    render_itinerary(itinerary_value)

    st.subheader("Monitor and adapt")
    monitor_col, rain_col, fatigue_col = st.columns(3)
    if monitor_col.button("Check live conditions", use_container_width=True):
        try:
            st.session_state.monitoring = service().monitor(itinerary_value)
        except AdaptSGError as exc:
            st.error(f"Live verification failed; current plan retained. {exc}")
    if rain_col.button("Simulate heavy rain + flood", use_container_width=True):
        outdoor_ids = frozenset(
            segment.venue.id for segment in itinerary_value.segments if not segment.venue.indoor
        )
        propose(
            ReplanTrigger(
                type=TriggerType.FLOOD_ALERT,
                message="Heavy rain began and a flood alert affects an outdoor segment.",
                affected_venue_ids=outdoor_ids,
            ),
            itinerary_value,
        )
    if fatigue_col.button("Mum is more tired", use_container_width=True):
        propose(
            ReplanTrigger(
                type=TriggerType.FATIGUE,
                message="Mum is more tired than expected; shorten travel and add rest.",
            ),
            itinerary_value,
        )

    monitoring_value = state_value("monitoring")
    if isinstance(monitoring_value, MonitoringOutcome):
        render_monitoring(monitoring_value)
    proposal_value = state_value("proposal")
    if isinstance(proposal_value, ReplanProposal):
        render_proposal(itinerary_value, proposal_value)


if __name__ == "__main__":
    main()
