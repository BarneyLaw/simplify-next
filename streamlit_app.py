"""AdaptSG Streamlit demo: plan, accept, monitor, explain, approve, and minimally replan."""

from __future__ import annotations

import importlib.resources
from collections.abc import Callable
from datetime import date, datetime, timedelta
from uuid import UUID, uuid4

import streamlit as st

from adaptsg import ui
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
from adaptsg.presentation import environment_provenance_label, mode_badge, provenance_label
from adaptsg.settings import get_settings

SAMPLE_PROMPT = (
    "Plan a 10 am-5 pm day for me and my 72-year-old mother, starting from Toa Payoh. "
    "She uses a wheelchair, should not walk more than 400 metres at once, needs lunch "
    "before 1 pm, and we have a $70 transport and activity budget. We would like to "
    "visit Gardens by the Bay."
)

JOURNEY_KEYS = (
    "journey_id",
    "journey_version",
    "journey_status",
    "journey_expires_at",
    "itinerary",
    "proposal",
)


@st.cache_resource
def service() -> AdaptSGService:
    return build_service()


def _stylesheet() -> str:
    return importlib.resources.files("adaptsg").joinpath("ui.css").read_text(encoding="utf-8")


def state_value(name: str) -> object | None:
    return st.session_state.get(name)


def _journey_id() -> UUID:
    value = state_value("journey_id")
    if not isinstance(value, UUID):
        raise RuntimeError("journey_id is missing from session state")
    return value


def _journey_version() -> int:
    value = state_value("journey_version")
    if not isinstance(value, int):
        raise RuntimeError("journey_version is missing from session state")
    return value


def _journey_expires_at() -> datetime:
    value = state_value("journey_expires_at")
    if not isinstance(value, datetime):
        raise RuntimeError("journey_expires_at is missing from session state")
    return value


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
    st.session_state.journey_expires_at = state.expires_at
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


def reset_to_landing() -> None:
    for key in JOURNEY_KEYS:
        st.session_state.pop(key, None)
    st.session_state.monitoring = None
    st.session_state.pop("plan_attempt", None)
    st.rerun()


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


def _date_and_start(itinerary: Itinerary) -> str:
    request = itinerary.request
    return ui.date_and_start(request.journey_date, request.start_label, request.start_time)


def render_no_feasible(message: str) -> None:
    """Safety rule 5: stop and ask instead of inventing a workaround."""
    st.error(f"No safe plan exists for this request: {message.rstrip('.')}.")
    st.warning(
        "AdaptSG did not weaken any accessibility, walking, timing or budget limit to "
        "produce an alternative, and it did not invent a route. Adjust the request and "
        "plan again, or continue with the current plan if one is already accepted."
    )


def render_sidebar(itinerary: Itinerary | None) -> None:
    with st.sidebar:
        st.html(
            f'<div class="brand">{ui.brand_mark(28)}<span class="word">AdaptSG</span></div>'
            '<p class="tagline">travelling for everyone</p>'
        )
        if st.button("New trip", key="new-trip", use_container_width=True):
            reset_to_landing()
        st.divider()
        if itinerary is not None:
            st.html(
                '<p class="eyebrow">Your must-haves</p>'
                + ui.must_haves(itinerary.request.hard)
                + '<p class="railnote" style="margin-top:12px">These come from what you '
                "wrote. To change one, describe your day again and make a new plan.</p>"
            )
            st.divider()
        st.html(
            '<p class="railnote">AdaptSG does not book or pay for anything, and does not '
            "give medical advice. It suggests a plan; you decide.</p>"
        )


def render_landing_view() -> None:
    st.html(
        f'<div class="hero">{ui.brand_mark(88)}'
        '<h1 style="font-size:44px;letter-spacing:-.03em">AdaptSG</h1>'
        '<p style="font-size:18px;color:var(--blue-800);font-weight:500">'
        "travelling for everyone</p>"
        '<p class="note" style="font-size:16px;max-width:560px;margin:0 auto">Plan a day '
        "out in Singapore that respects a wheelchair, a walking limit, a lunchtime and a "
        "budget &mdash; and says so plainly when it cannot.</p></div>"
    )
    prompt = st.text_area(
        "Tell AdaptSG about the day in your own words — where you would like to go "
        "in Singapore, when, and anything needed to stay safe and comfortable.",
        value=SAMPLE_PROMPT,
        height=150,
        key="prompt",
    )
    st.caption("This box is filled in with an example. Edit it to describe your own day.")
    journey_date = st.date_input(
        "Journey date",
        value=date.today() + timedelta(days=1),
        key="journey-date",
    )
    with st.container(key="create-plan"):
        if st.button("Create safe plan", type="primary"):
            create_plan(prompt, journey_date)
    st.html(
        '<p class="foot">AdaptSG suggests a plan, checks it is safe, and you decide. It '
        "does not book, pay for or buy anything, and it does not give medical advice.</p>"
    )
    st.info("Create a plan to begin the five-minute demo.")


def render_rejected_view() -> None:
    st.html(
        '<div class="pagehead"><p class="eyebrow">Your decision</p><h1>Plan rejected</h1></div>'
    )
    st.info("Plan rejected. Nothing was applied; describe the day again to plan afresh.")
    with st.container(key="restart-day"):
        if st.button("Describe the day again", type="primary"):
            reset_to_landing()


def render_draft_view(itinerary: Itinerary, mode: str) -> None:
    st.html(
        '<div class="pagehead"><p class="eyebrow">Proposed plan</p><h1>Current plan</h1>'
        '<div class="statusline">'
        + ui.chip_row(
            [
                ("caution", ui.ICON_WARN, "Awaiting your decision"),
                ("plain", "", "Nothing is booked"),
            ]
        )
        + "</div></div>"
        + _date_and_start(itinerary)
    )
    st.html(
        '<div class="band caution" style="margin-bottom:16px">'
        f'<div class="bandhead">{ui.ICON_WARN}<div>'
        "<h3>Check your must-haves are right before you continue</h3>"
        "<p>These were read from what you wrote. Have a quick look at the list in the "
        "sidebar before you accept.</p></div></div></div>"
        '<div class="band notice" style="margin-bottom:16px"><p class="note">'
        "<strong>Weather is not shown yet.</strong> AdaptSG only checks conditions once "
        "you have accepted a plan.</p></div>"
    )
    left, right = st.columns([1, 0.42], gap="large")
    with left:
        st.html(ui.timeline(itinerary) + ui.finish_note(itinerary) + ui.summary(itinerary))
        st.caption(provenance_label(itinerary, mode=mode))
        st.divider()
        st.subheader("Accept this plan?")
        st.write(
            "Nothing is booked. Accepting records your approval on the server and "
            "unlocks the monitoring events."
        )
        accept_col, reject_col = st.columns(2)
        with accept_col, st.container(key="accept-plan"):
            if st.button("Accept this plan", type="primary", use_container_width=True):
                decide(itinerary.id, ApprovalDecision.APPROVE)
        with reject_col, st.container(key="reject-plan"):
            if st.button("Reject and start again", use_container_width=True):
                decide(itinerary.id, ApprovalDecision.REJECT)
        st.html(
            '<div class="composer" aria-disabled="true">'
            '<p class="field" style="color:var(--faint)">Available once you accept this '
            "plan.</p></div>"
        )
        st.html(
            ui.evidence(
                itinerary,
                mode=mode,
                storage=service().storage_mode,
                journey_id=_journey_id(),
                version=_journey_version(),
                status="draft",
                expires_at=_journey_expires_at(),
            )
        )
        st.html(
            '<p class="foot">AdaptSG suggests a plan, checks it is safe, and you decide. '
            "It does not book, pay for or buy anything, and it does not give medical "
            "advice.</p>"
        )
    with right:
        st.html(ui.map_svg(itinerary))


def render_active_view(itinerary: Itinerary, mode: str) -> None:
    monitoring_value = state_value("monitoring")
    monitor_failed = isinstance(monitoring_value, str)
    statusline = [("pass", ui.ICON_CHECK, "Accepted and checked")]
    if monitor_failed:
        statusline.append(("caution", ui.ICON_CLOCK, "Conditions not checked recently"))
    elif isinstance(monitoring_value, MonitoringOutcome):
        snapshot = monitoring_value.snapshot
        statusline.append(("plain", "", f"{snapshot.weather_summary}; 24-hour PSI {snapshot.psi}"))
    st.html(
        '<div class="pagehead"><p class="eyebrow">Your accepted plan</p>'
        "<h1>Current plan</h1>"
        f'<div class="statusline">{ui.chip_row(statusline)}</div></div>'
        + _date_and_start(itinerary)
    )
    if monitor_failed:
        st.html(
            '<div class="band caution"><div class="bandhead">'
            f"{ui.ICON_WARN}<div><h3>Live checking failed &mdash; your plan is "
            f"unchanged</h3><p>{monitoring_value}</p></div></div></div>"
        )

    left, right = st.columns([1, 0.42], gap="large")
    with left:
        if monitor_failed:
            button_label = "Retry verification"
        elif isinstance(monitoring_value, MonitoringOutcome):
            button_label = "Recheck conditions"
        else:
            button_label = "Check conditions"
        with st.container(key="check-conditions"):
            if st.button(button_label, key="check-conditions-button"):
                check_conditions()
        st.html(ui.timeline(itinerary) + ui.finish_note(itinerary))
        st.html(ui.summary(itinerary, show_replans=True))
        st.caption(provenance_label(itinerary, mode=mode))

        replan_limit_reached = bool(state_value("replan_limit_reached"))
        proposal_value = state_value("proposal")
        if not isinstance(proposal_value, ReplanProposal):
            st.divider()
            st.subheader("Ask AdaptSG for a change")
            if replan_limit_reached:
                reason = state_value("replan_limit_reason") or ""
                st.info(
                    "Replanning is bounded and this journey has reached its limit. "
                    f"{reason} The current plan is retained unchanged."
                )
            else:
                rain_col, fatigue_col = st.columns(2)
                if rain_col.button("Heavy rain and flooding", key="simulate-rain"):
                    outdoor_ids = frozenset(
                        segment.venue.id
                        for segment in itinerary.segments
                        if not segment.venue.indoor
                    )
                    propose(
                        ReplanTrigger(
                            type=TriggerType.FLOOD_ALERT,
                            message="Heavy rain began and a flood alert affects an "
                            "outdoor segment.",
                            affected_venue_ids=outdoor_ids,
                        )
                    )
                if fatigue_col.button("Mum is more tired", key="simulate-fatigue"):
                    propose(
                        ReplanTrigger(
                            type=TriggerType.FATIGUE,
                            message="Mum is more tired than expected; shorten travel and add rest.",
                        )
                    )

        st.html(
            ui.evidence(
                itinerary,
                mode=mode,
                storage=service().storage_mode,
                journey_id=_journey_id(),
                version=_journey_version(),
                status="active",
                expires_at=_journey_expires_at(),
            )
        )
        st.html(
            '<p class="foot">AdaptSG suggests a plan, checks it is safe, and you decide. '
            "It does not book, pay for or buy anything, and it does not give medical "
            "advice.</p>"
        )
    with right:
        st.html(ui.map_svg(itinerary))

    # Re-read: `check_conditions()` may have just updated this mid-render, above.
    refreshed_monitoring = state_value("monitoring")
    if isinstance(refreshed_monitoring, MonitoringOutcome):
        snapshot = refreshed_monitoring.snapshot
        st.info(
            f"{ui.conditions_summary(snapshot, itinerary)} "
            f"{environment_provenance_label(snapshot, mode=mode)}"
        )
        if refreshed_monitoring.triggers:
            for trigger in refreshed_monitoring.triggers:
                st.warning(trigger.message)
        else:
            st.success("No monitored condition currently affects this itinerary.")


def render_approval_view(current: Itinerary, proposal: ReplanProposal) -> None:
    heading = (
        proposal.changes[0].reason if proposal.changes else "AdaptSG suggests a change to your plan"
    )
    statusline = [
        ("caution", ui.ICON_WARN, "Your approval is needed")
        if proposal.requires_approval
        else ("pass", ui.ICON_CHECK, "No extra approval needed"),
        ("plain", "", "Your current plan has not changed"),
    ]
    st.html(
        '<div class="pagehead"><p class="eyebrow">Suggested change</p>'
        f"<h1>{heading}</h1>"
        f'<div class="statusline">{ui.chip_row(statusline)}</div></div>'
    )
    before = current.total_cost_sgd
    after = proposal.itinerary.total_cost_sgd
    delta = proposal.cost_delta_sgd
    if delta > 0:
        delta_label = f"adds S${delta:.2f}"
    elif delta < 0:
        delta_label = f"saves S${-delta:.2f}"
    else:
        delta_label = "does not change the cost"
    hard = current.request.hard
    budget_note = (
        f" &mdash; still inside your S${hard.total_budget_sgd:.2f} budget"
        if after <= hard.total_budget_sgd
        else ""
    )
    band_class = "band caution" if proposal.requires_approval else "band"
    approval_copy = (
        "AdaptSG needs your approval before it applies this."
        if proposal.requires_approval
        else "This does not need extra approval."
    )
    st.html(
        f'<div class="{band_class}"><div class="bandhead">'
        f"{ui.ICON_WARN if proposal.requires_approval else ui.ICON_CHECK}"
        f"<div><h3>This change {delta_label}</h3>"
        f"<p>{approval_copy} Your day would go from S${before:.2f} to "
        f"S${after:.2f}{budget_note}.</p></div></div></div>"
    )
    if proposal.requires_approval:
        st.warning(
            "Approval required: this option increases cost beyond the configured S$8 threshold."
        )
    st.html(f'<div class="band">{ui.proposal_diff(current, proposal)}</div>')
    st.divider()
    st.subheader("Do you approve this change?")
    st.write(
        "If you keep the current plan, nothing happens and today's plan stays exactly as it is."
    )
    apply_col, keep_col = st.columns(2)
    with apply_col, st.container(key="apply-proposal"):
        if st.button("Approve and apply", type="primary", use_container_width=True):
            decide(proposal.id, ApprovalDecision.APPROVE)
    with keep_col, st.container(key="reject-proposal"):
        if st.button("Keep current plan", use_container_width=True):
            decide(proposal.id, ApprovalDecision.REJECT)


def decide(target_id: UUID, decision: ApprovalDecision) -> None:
    journey_id = st.session_state.journey_id
    version = st.session_state.journey_version
    st.session_state.replan_limit_reached = False
    run(
        lambda: service().decide_journey(
            journey_id,
            decision=decision,
            target_id=target_id,
            expected_version=version,
            idempotency_key=action_key(journey_id, version, "decide", target_id, decision.value),
        )
    )


def propose(trigger: ReplanTrigger) -> None:
    journey_id = st.session_state.journey_id
    version = st.session_state.journey_version
    try:
        remember(
            service().propose_replan(
                journey_id,
                trigger,
                expected_version=version,
                idempotency_key=action_key(journey_id, version, "replan", trigger.type.value),
            )
        )
    except ReplanLimitReached as exc:
        st.session_state.proposal = None
        st.session_state.replan_limit_reached = True
        st.session_state.replan_limit_reason = str(exc)
        st.error(f"Replanning is bounded and this journey has reached its limit. {exc}")
        return
    except NoFeasibleItinerary as exc:
        st.session_state.proposal = None
        render_no_feasible(str(exc))
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
    st.session_state.replan_limit_reached = False
    st.session_state.monitoring = None
    st.rerun()


def check_conditions() -> None:
    try:
        st.session_state.monitoring = service().monitor_journey(st.session_state.journey_id)
    except ToolUnavailable as exc:
        st.session_state.monitoring = str(exc)
        st.error(
            f"Live verification failed, so the current plan is retained unchanged. {exc} "
            "No condition below has been refreshed."
        )
    except AdaptSGError as exc:
        st.session_state.monitoring = str(exc)
        st.error(f"Monitoring failed; the current plan is retained unchanged. {exc}")


def create_plan(prompt: str, journey_date: date) -> None:
    previous = state_value("plan_attempt")
    attempt = (previous if isinstance(previous, int) else 0) + 1
    st.session_state.plan_attempt = attempt
    st.session_state.monitoring = None
    st.session_state.replan_limit_reached = False
    with st.spinner("Working out a safe plan…"):
        run(
            lambda: service().start_journey(
                prompt,
                journey_date=journey_date,
                idempotency_key=action_key("start", attempt),
            )
        )


def main() -> None:
    st.set_page_config(
        page_title="AdaptSG",
        page_icon="♿",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    st.html(f"<style>{_stylesheet()}</style>")
    settings = get_settings()
    mode = settings.adaptsg_mode

    status = state_value("journey_status")
    itinerary_value = state_value("itinerary")
    render_sidebar(itinerary_value if isinstance(itinerary_value, Itinerary) else None)

    with st.container(key="mode-banner"):
        st.info(mode_badge(mode))

    if status is JourneyStatus.REJECTED:
        render_rejected_view()
        return
    if not isinstance(status, JourneyStatus) or not isinstance(itinerary_value, Itinerary):
        render_landing_view()
        return

    warnings = state_value("warnings")
    for warning in warnings if isinstance(warnings, tuple) else ():
        st.warning(warning)

    if status is JourneyStatus.DRAFT:
        render_draft_view(itinerary_value, mode)
        return

    proposal_value = state_value("proposal")
    if isinstance(proposal_value, ReplanProposal):
        render_approval_view(itinerary_value, proposal_value)
        return

    render_active_view(itinerary_value, mode)


if __name__ == "__main__":
    main()
