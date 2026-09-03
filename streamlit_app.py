"""AdaptSG Streamlit demo: plan, accept, monitor, explain, approve, and minimally replan."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta
from uuid import UUID, uuid4

import streamlit as st

from adaptsg.agent import AdaptSGService, build_service
from adaptsg.domain import (
    ApprovalDecision,
    Itinerary,
    JourneyState,
    JourneyStatus,
    MonitoringOutcome,
    ProposalStatus,
    ReplanProposal,
    ReplanTrigger,
    TriggerType,
)
from adaptsg.errors import (
    AdaptSGError,
    InvalidJourneyTransition,
    JourneyNotFound,
    NoFeasibleItinerary,
    ReplanLimitReached,
    StaleJourneyVersion,
    ToolUnavailable,
)
from adaptsg.presentation import (
    environment_provenance_label,
    itinerary_rows,
    mode_badge,
    provenance_label,
    retained_segment_percentage,
)
from adaptsg.settings import get_settings

SAMPLE_PROMPT = (
    "Plan a 10 am-5 pm day for me and my 72-year-old mother, starting from Toa Payoh. "
    "She uses a wheelchair, should not walk more than 400 metres at once, needs lunch "
    "before 1 pm, and we have a $70 transport and activity budget. We would like to "
    "visit Gardens by the Bay."
)

JOURNEY_KEYS = ("journey_id", "journey_version", "journey_status", "itinerary", "proposal")


@st.cache_resource
def service() -> AdaptSGService:
    return build_service()


def state_value(name: str) -> object | None:
    return st.session_state.get(name)


def session_nonce() -> str:
    """One identifier per browser session, so two caregivers never share a retry key."""
    if "session_nonce" not in st.session_state:
        st.session_state.session_nonce = uuid4().hex
    return str(st.session_state.session_nonce)


def action_key(*parts: object) -> str:
    """Retry key in the visible-ASCII shape `adaptsg.agent.IDEMPOTENCY_KEY` requires.

    Streamlit re-executes the whole script on every interaction. Deriving the key from
    the journey version makes a rerun replay the stored result rather than apply a
    caregiver decision twice, because the version advances on every committed change.
    """
    return ":".join(["adaptsg", session_nonce(), *(str(part) for part in parts)])


def remember(state: JourneyState) -> None:
    """Adopt whatever the server reports; the session never edits journey state itself."""
    st.session_state.journey_id = state.journey_id
    st.session_state.journey_version = state.version
    st.session_state.journey_status = state.status
    st.session_state.warnings = state.warnings
    st.session_state.itinerary = state.current_itinerary or state.pending_initial_itinerary
    proposal = state.latest_replan_proposal
    st.session_state.proposal = (
        proposal if proposal is not None and proposal.status is ProposalStatus.PENDING else None
    )


def forget_journey(message: str) -> None:
    for key in JOURNEY_KEYS:
        st.session_state.pop(key, None)
    st.session_state.monitoring = None
    st.error(message)


def reload_journey(message: str) -> None:
    """A stale version means the server moved on; decide against what it actually holds."""
    journey_id = state_value("journey_id")
    if not isinstance(journey_id, UUID):
        forget_journey(message)
        return
    try:
        remember(service().get_journey(journey_id))
    except AdaptSGError as exc:
        forget_journey(f"The journey could not be reloaded. {exc}")
        return
    st.warning(message)


def run(action: Callable[[], JourneyState]) -> None:
    """Perform one server mutation, mapping each domain error to its own caregiver state."""
    try:
        remember(action())
    except NoFeasibleItinerary as exc:
        st.session_state.proposal = None
        render_no_feasible(str(exc))
        return
    except ReplanLimitReached as exc:
        st.session_state.proposal = None
        st.error(f"Replanning is bounded and this journey has reached its limit. {exc}")
        return
    except StaleJourneyVersion as exc:
        reload_journey(f"This plan changed elsewhere, so it has been reloaded. {exc}")
        return
    except JourneyNotFound as exc:
        forget_journey(f"This journey has expired or is no longer available. {exc}")
        return
    except InvalidJourneyTransition as exc:
        st.error(f"That step is not available for this journey right now. {exc}")
        return
    except ToolUnavailable as exc:
        st.error(f"Live verification failed, so the current plan is retained unchanged. {exc}")
        return
    except AdaptSGError as exc:
        st.error(f"Nothing was applied; the current plan is retained unchanged. {exc}")
        return
    st.session_state.monitoring = None
    st.rerun()


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


def render_itinerary(itinerary: Itinerary, mode: str, *, heading: str) -> None:
    st.subheader(heading)
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
    st.caption(
        f"{provenance_label(itinerary, mode=mode)} Constraint parser: {itinerary.parser_source}."
    )


def render_no_feasible(message: str) -> None:
    """Safety rule 5: stop and ask instead of inventing a workaround."""
    st.error(f"No safe plan exists for this request: {message.rstrip('.')}.")
    st.warning(
        "AdaptSG did not weaken any accessibility, walking, timing or budget limit to "
        "produce an alternative, and it did not invent a route. Adjust the request and "
        "plan again, or continue with the current plan if one is already accepted."
    )


def render_monitoring(monitoring: MonitoringOutcome, mode: str) -> None:
    snapshot = monitoring.snapshot
    st.info(
        f"Conditions: {snapshot.weather_summary}; 24-hour PSI {snapshot.psi}. "
        f"{environment_provenance_label(snapshot, mode=mode)}"
    )
    if monitoring.triggers:
        for trigger in monitoring.triggers:
            st.warning(trigger.message)
    else:
        st.success("No monitored condition currently affects this itinerary.")


def decide(target_id: UUID, decision: ApprovalDecision) -> None:
    journey_id = st.session_state.journey_id
    version = st.session_state.journey_version
    run(
        lambda: service().decide_journey(
            journey_id,
            decision=decision,
            target_id=target_id,
            expected_version=version,
            idempotency_key=action_key(journey_id, version, "decide", target_id, decision.value),
        )
    )


def render_draft_decision(itinerary: Itinerary) -> None:
    """A draft plan is not an accepted plan; only the server can make it active."""
    st.divider()
    st.subheader("Accept this plan?")
    st.info(
        "Nothing is booked. Accepting records your approval on the server, which "
        "revalidates the itinerary before it becomes the active plan."
    )
    accept_col, reject_col = st.columns(2)
    if accept_col.button(
        "Accept this plan", type="primary", use_container_width=True, key="accept-plan"
    ):
        decide(itinerary.id, ApprovalDecision.APPROVE)
    if reject_col.button("Reject and start again", use_container_width=True, key="reject-plan"):
        decide(itinerary.id, ApprovalDecision.REJECT)


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
        key="apply-proposal",
    ):
        # Clicking this button is the caregiver decision the approval boundary requires.
        # The server, not this page, applies it.
        decide(proposal.id, ApprovalDecision.APPROVE)
    if reject_col.button("Keep current plan", use_container_width=True, key="reject-proposal"):
        decide(proposal.id, ApprovalDecision.REJECT)


def propose(trigger: ReplanTrigger) -> None:
    journey_id = st.session_state.journey_id
    version = st.session_state.journey_version
    run(
        lambda: service().propose_replan(
            journey_id,
            trigger,
            expected_version=version,
            idempotency_key=action_key(journey_id, version, "replan", trigger.type.value),
        )
    )


def check_conditions() -> None:
    try:
        st.session_state.monitoring = service().monitor_journey(st.session_state.journey_id)
    except ToolUnavailable as exc:
        st.error(
            f"Live verification failed, so the current plan is retained unchanged. {exc} "
            "No condition below has been refreshed."
        )
    except AdaptSGError as exc:
        st.error(f"Monitoring failed; the current plan is retained unchanged. {exc}")


def create_plan(prompt: str, journey_date: date) -> None:
    previous = state_value("plan_attempt")
    attempt = (previous if isinstance(previous, int) else 0) + 1
    st.session_state.plan_attempt = attempt
    st.session_state.monitoring = None
    run(
        lambda: service().start_journey(
            prompt,
            journey_date=journey_date,
            idempotency_key=action_key("start", attempt),
        )
    )


def render_adaptation_controls(itinerary: Itinerary) -> None:
    st.subheader("Monitor and adapt")
    monitor_col, rain_col, fatigue_col = st.columns(3)
    if monitor_col.button(
        "Check live conditions", use_container_width=True, key="check-conditions"
    ):
        check_conditions()
    if rain_col.button(
        "Simulate heavy rain + flood", use_container_width=True, key="simulate-rain"
    ):
        outdoor_ids = frozenset(
            segment.venue.id for segment in itinerary.segments if not segment.venue.indoor
        )
        propose(
            ReplanTrigger(
                type=TriggerType.FLOOD_ALERT,
                message="Heavy rain began and a flood alert affects an outdoor segment.",
                affected_venue_ids=outdoor_ids,
            )
        )
    if fatigue_col.button("Mum is more tired", use_container_width=True, key="simulate-fatigue"):
        propose(
            ReplanTrigger(
                type=TriggerType.FATIGUE,
                message="Mum is more tired than expected; shorten travel and add rest.",
            )
        )


def main() -> None:
    st.set_page_config(
        page_title="AdaptSG",
        page_icon="♿",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    settings = get_settings()
    mode = settings.adaptsg_mode
    st.title("AdaptSG")
    st.markdown(
        "**Inclusive journey planning that preserves accessibility, health and budget limits "
        "when conditions change.**"
    )
    st.info(mode_badge(mode))

    with st.sidebar:
        st.header("Runtime")
        st.write(f"Mode: **{mode.upper()}**")
        if mode == "demo":
            st.info("Offline demo data is deterministic and labelled. No live claim is implied.")
        st.header("Non-negotiable policy")
        st.write("The LLM proposes. Deterministic code validates.")
        st.write("The server owns the journey. This page holds its identifier and version.")
        st.write("No bookings, payments, diagnosis, or silent constraint relaxation.")

    prompt = st.text_area(
        "Describe the day and constraints",
        value=SAMPLE_PROMPT,
        height=150,
        key="prompt",
    )
    journey_date = st.date_input(
        "Journey date",
        value=date.today() + timedelta(days=1),
        key="journey-date",
    )
    if st.button("Create safe plan", type="primary", key="create-plan"):
        create_plan(prompt, journey_date)

    status = state_value("journey_status")
    itinerary_value = state_value("itinerary")
    if status is JourneyStatus.REJECTED:
        st.info("Plan rejected. Nothing was applied; describe the day again to plan afresh.")
        return
    if not isinstance(status, JourneyStatus) or not isinstance(itinerary_value, Itinerary):
        st.info("Create a plan to begin the five-minute demo.")
        return

    warnings = state_value("warnings")
    for warning in warnings if isinstance(warnings, tuple) else ():
        st.warning(warning)
    render_constraints(itinerary_value)

    if status is JourneyStatus.DRAFT:
        render_itinerary(itinerary_value, mode, heading="Proposed plan, awaiting your decision")
        render_draft_decision(itinerary_value)
        return

    render_itinerary(itinerary_value, mode, heading="Current safe itinerary")
    render_adaptation_controls(itinerary_value)

    monitoring_value = state_value("monitoring")
    if isinstance(monitoring_value, MonitoringOutcome):
        render_monitoring(monitoring_value, mode)
    proposal_value = state_value("proposal")
    if isinstance(proposal_value, ReplanProposal):
        render_proposal(itinerary_value, proposal_value)


if __name__ == "__main__":
    main()
