from datetime import time

import pytest

from adaptsg.agent import AdaptSGService
from adaptsg.domain import (
    Itinerary,
    JourneyRequest,
    ReplanTrigger,
    TravelMode,
    TriggerType,
)
from adaptsg.errors import ApprovalRequired, NoFeasibleItinerary, ReplanLimitReached
from adaptsg.planning import JourneyPlanner, JourneyReplanner
from adaptsg.preference_parser import DeterministicPreferenceParser
from adaptsg.tools.catalog import VenueCatalog
from adaptsg.tools.environment import DemoEnvironmentClient


def test_initial_plan_matches_demo_and_is_tool_sourced(
    planner: JourneyPlanner, journey_request: JourneyRequest
) -> None:
    plan = planner.create(journey_request)
    assert [segment.venue.id for segment in plan.segments] == [
        "national-gallery",
        "funan-food-court",
        "gardens-bay-outdoor",
    ]
    assert plan.total_cost_sgd == 33
    assert all(segment.route.source == "demo_route_estimator_v1" for segment in plan.segments)


def test_required_unverified_venue_is_rejected(
    planner: JourneyPlanner, journey_request: JourneyRequest
) -> None:
    hard = journey_request.hard.model_copy(
        update={"required_venue_ids": frozenset({"fort-canning-park"})}
    )
    with pytest.raises(NoFeasibleItinerary, match="verified accessibility"):
        planner.create(journey_request.model_copy(update={"hard": hard}))


def test_too_many_required_venues_are_rejected(
    planner: JourneyPlanner, journey_request: JourneyRequest
) -> None:
    hard = journey_request.hard.model_copy(
        update={
            "required_venue_ids": frozenset(
                {"national-gallery", "cloud-forest", "artscience-museum"}
            )
        }
    )
    with pytest.raises(NoFeasibleItinerary, match="no room"):
        planner.create(journey_request.model_copy(update={"hard": hard}))


def test_soft_venue_is_kept_initially_but_replaceable(
    planner: JourneyPlanner, replanner: JourneyReplanner, journey_request: JourneyRequest
) -> None:
    soft = journey_request.soft.model_copy(
        update={"preferred_venue_ids": frozenset({"gardens-bay-outdoor"})}
    )
    plan = planner.create(journey_request.model_copy(update={"soft": soft}))
    proposal = replanner.propose(
        plan,
        ReplanTrigger(type=TriggerType.HEAVY_RAIN, message="Heavy rain"),
    )
    assert proposal.validation.valid
    assert proposal.itinerary.segments[2].venue.indoor
    assert proposal.itinerary.segments[:2] == plan.segments[:2]
    assert proposal.requires_approval


def test_fatigue_adds_one_taxi_and_requires_cost_approval(
    itinerary: Itinerary, replanner: JourneyReplanner
) -> None:
    proposal = replanner.propose(
        itinerary,
        ReplanTrigger(type=TriggerType.FATIGUE, message="Mum is tired"),
    )
    assert proposal.validation.valid
    assert sum(s.route.mode is TravelMode.TAXI for s in proposal.itinerary.segments) == 1
    assert proposal.requires_approval
    assert proposal.cost_delta_sgd > 8


def test_budget_replan_and_missing_budget(
    itinerary: Itinerary, replanner: JourneyReplanner
) -> None:
    proposal = replanner.propose(
        itinerary,
        ReplanTrigger(
            type=TriggerType.BUDGET_REDUCTION,
            message="Budget reduced",
            new_budget_sgd=20,
        ),
    )
    assert proposal.itinerary.total_cost_sgd <= 20
    with pytest.raises(NoFeasibleItinerary, match="requires a new budget"):
        replanner.propose(
            itinerary,
            ReplanTrigger(type=TriggerType.BUDGET_REDUCTION, message="Budget reduced"),
        )


def test_lunch_time_change_replans_against_the_new_hard_deadline(
    itinerary: Itinerary, replanner: JourneyReplanner
) -> None:
    proposal = replanner.propose(
        itinerary,
        ReplanTrigger(
            type=TriggerType.LUNCH_TIME_CHANGED,
            message="Lunch moved earlier",
            new_lunch_latest=time(12, 30),
        ),
    )

    lunch = next(
        segment for segment in proposal.itinerary.segments if segment.purpose.value == "lunch"
    )
    assert proposal.validation.valid
    assert proposal.itinerary.request.hard.lunch_latest == time(12, 30)
    assert lunch.activity_start.time() <= time(12, 30)


def test_appointment_time_change_replans_against_the_new_finish_deadline(
    itinerary: Itinerary, replanner: JourneyReplanner
) -> None:
    proposal = replanner.propose(
        itinerary,
        ReplanTrigger(
            type=TriggerType.APPOINTMENT_CHANGED,
            message="Appointment moved earlier",
            new_finish_by=time(16, 30),
        ),
    )

    assert proposal.validation.valid
    assert proposal.itinerary.request.hard.finish_by == time(16, 30)
    assert proposal.itinerary.segments[-1].activity_end.time() <= time(16, 30)


def test_time_change_trigger_requires_the_new_deadline() -> None:
    with pytest.raises(ValueError, match="new_lunch_latest"):
        ReplanTrigger(type=TriggerType.LUNCH_TIME_CHANGED, message="Lunch moved")
    with pytest.raises(ValueError, match="new_finish_by"):
        ReplanTrigger(type=TriggerType.APPOINTMENT_CHANGED, message="Appointment moved")


def test_no_affected_segment_returns_valid_unchanged_plan(
    itinerary: Itinerary, replanner: JourneyReplanner
) -> None:
    proposal = replanner.propose(
        itinerary,
        ReplanTrigger(
            type=TriggerType.VENUE_CLOSURE,
            message="Unrelated venue closed",
            affected_venue_ids=frozenset({"cloud-forest"}),
        ),
    )
    assert proposal.validation.valid
    assert proposal.changes == ()
    assert proposal.itinerary.replan_count == 1


def test_replanning_limit_is_enforced(itinerary: Itinerary, replanner: JourneyReplanner) -> None:
    exhausted = itinerary.model_copy(update={"replan_count": 2})
    with pytest.raises(ReplanLimitReached, match="capped"):
        replanner.propose(
            exhausted,
            ReplanTrigger(type=TriggerType.FATIGUE, message="Tired"),
        )


def test_apply_proposal_enforces_material_approval(
    itinerary: Itinerary, replanner: JourneyReplanner, planner: JourneyPlanner
) -> None:
    proposal = replanner.propose(
        itinerary,
        ReplanTrigger(type=TriggerType.FATIGUE, message="Tired"),
    )
    service = AdaptSGService(
        parser=DeterministicPreferenceParser(VenueCatalog()),
        planner=planner,
        replanner=replanner,
        environment=DemoEnvironmentClient(),
    )
    with pytest.raises(ApprovalRequired):
        service.apply_proposal(proposal, approved=False)
    assert service.apply_proposal(proposal, approved=True) == proposal.itinerary


def test_planner_reports_finish_time_infeasibility(
    planner: JourneyPlanner, journey_request: JourneyRequest
) -> None:
    hard = journey_request.hard.model_copy(update={"finish_by": time(11)})
    with pytest.raises(NoFeasibleItinerary, match="finishes after"):
        planner.create(journey_request.model_copy(update={"hard": hard}))


def test_required_accessible_lunch_is_used(
    planner: JourneyPlanner, journey_request: JourneyRequest
) -> None:
    hard = journey_request.hard.model_copy(
        update={"required_venue_ids": frozenset({"toa-payoh-food-hub"})}
    )
    plan = planner.create(journey_request.model_copy(update={"hard": hard}))
    assert plan.segments[1].venue.id == "toa-payoh-food-hub"


def test_unknown_required_venue_raises_key_error(
    planner: JourneyPlanner, journey_request: JourneyRequest
) -> None:
    hard = journey_request.hard.model_copy(
        update={"required_venue_ids": frozenset({"invented-place"})}
    )
    with pytest.raises(KeyError, match="unknown venue"):
        planner.create(journey_request.model_copy(update={"hard": hard}))
