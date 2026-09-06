from datetime import time

import pytest

from adaptsg.agent import AdaptSGService
from adaptsg.domain import (
    Itinerary,
    JourneyRequest,
    ReplanTrigger,
    TravelMode,
    TriggerType,
    VenueCategory,
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
    assert all(
        segment.venue.indoor
        for segment in proposal.itinerary.segments
        if segment.venue.category is not VenueCategory.FOOD
    )
    original_lunch = next(
        segment for segment in plan.segments if segment.venue.category is VenueCategory.FOOD
    )
    replacement_lunch = next(
        segment
        for segment in proposal.itinerary.segments
        if segment.venue.category is VenueCategory.FOOD
    )
    assert replacement_lunch.venue.id == original_lunch.venue.id
    assert proposal.requires_approval


def test_category_preferences_replace_training_defaults(
    planner: JourneyPlanner, journey_request: JourneyRequest
) -> None:
    soft = journey_request.soft.model_copy(
        update={
            "preferred_categories": (
                VenueCategory.GARDEN,
                VenueCategory.OUTDOOR_ATTRACTION,
            )
        }
    )

    selected = planner._select_initial_venues(journey_request.model_copy(update={"soft": soft}))

    assert [venue.category for venue in selected] == [
        VenueCategory.GARDEN,
        VenueCategory.FOOD,
        VenueCategory.OUTDOOR_ATTRACTION,
    ]
    assert all(venue.id != "national-gallery" for venue in selected)


def test_single_category_adds_one_relevant_activity_without_padding(
    planner: JourneyPlanner, journey_request: JourneyRequest
) -> None:
    soft = journey_request.soft.model_copy(update={"preferred_categories": (VenueCategory.GARDEN,)})

    selected = planner._select_initial_venues(journey_request.model_copy(update={"soft": soft}))

    assert [venue.category for venue in selected] == [
        VenueCategory.GARDEN,
        VenueCategory.FOOD,
    ]


def test_lunch_is_scheduled_first_when_activity_first_is_infeasible(
    planner: JourneyPlanner, journey_request: JourneyRequest
) -> None:
    soft = journey_request.soft.model_copy(update={"preferred_categories": (VenueCategory.REST,)})
    request = journey_request.model_copy(update={"start_time": time(12), "soft": soft})

    plan = planner.create(request)

    assert plan.segments[0].venue.category is VenueCategory.FOOD
    assert plan.segments[0].activity_start.time() <= request.hard.lunch_latest


def test_soft_activity_can_be_dropped_to_satisfy_hard_budget(
    planner: JourneyPlanner, journey_request: JourneyRequest
) -> None:
    hard = journey_request.hard.model_copy(update={"total_budget_sgd": 30})
    soft = journey_request.soft.model_copy(
        update={
            "preferred_categories": (
                VenueCategory.INDOOR_MUSEUM,
                VenueCategory.REST,
            )
        }
    )
    request = journey_request.model_copy(update={"hard": hard, "soft": soft})

    plan = planner.create(request)

    assert planner.validator.validate(plan).valid
    assert plan.total_cost_sgd <= 30
    assert any(segment.venue.category in soft.preferred_categories for segment in plan.segments)


def test_rest_is_prioritised_when_activity_slots_are_limited(
    planner: JourneyPlanner, journey_request: JourneyRequest
) -> None:
    soft = journey_request.soft.model_copy(
        update={
            "preferred_categories": (
                VenueCategory.INDOOR_MUSEUM,
                VenueCategory.INDOOR_ATTRACTION,
                VenueCategory.REST,
            )
        }
    )

    selected = planner._select_initial_venues(journey_request.model_copy(update={"soft": soft}))

    assert selected[0].id == "library-orchard"
    assert selected[2].category is VenueCategory.INDOOR_MUSEUM


def test_exact_preference_is_not_padded_with_same_category_defaults(
    planner: JourneyPlanner, journey_request: JourneyRequest
) -> None:
    soft = journey_request.soft.model_copy(
        update={
            "preferred_venue_ids": frozenset({"asian-civilisations-museum"}),
            "preferred_categories": (VenueCategory.INDOOR_MUSEUM,),
        }
    )

    selected = planner._select_initial_venues(journey_request.model_copy(update={"soft": soft}))

    assert [venue.id for venue in selected] == [
        "asian-civilisations-museum",
        "funan-food-court",
    ]


def test_preferred_food_venue_is_used_for_lunch(
    planner: JourneyPlanner, journey_request: JourneyRequest
) -> None:
    soft = journey_request.soft.model_copy(
        update={"preferred_venue_ids": frozenset({"toa-payoh-food-hub"})}
    )

    selected = planner._select_initial_venues(journey_request.model_copy(update={"soft": soft}))

    assert selected[1].id == "toa-payoh-food-hub"


def test_unverified_soft_preference_is_excluded_when_access_is_required(
    planner: JourneyPlanner, journey_request: JourneyRequest
) -> None:
    soft = journey_request.soft.model_copy(
        update={"preferred_venue_ids": frozenset({"fort-canning-park"})}
    )

    selected = planner._select_initial_venues(journey_request.model_copy(update={"soft": soft}))

    assert all(venue.id != "fort-canning-park" for venue in selected)


def test_one_stop_limit_is_rejected_because_lunch_is_mandatory(
    planner: JourneyPlanner, journey_request: JourneyRequest
) -> None:
    with pytest.raises(NoFeasibleItinerary, match="at least two stops"):
        planner._select_initial_venues(journey_request.model_copy(update={"max_stops": 1}))


def test_multiple_required_lunch_venues_are_rejected(
    planner: JourneyPlanner, journey_request: JourneyRequest
) -> None:
    hard = journey_request.hard.model_copy(
        update={"required_venue_ids": frozenset({"funan-food-court", "toa-payoh-food-hub"})}
    )

    with pytest.raises(NoFeasibleItinerary, match="multiple required food venues"):
        planner._select_initial_venues(journey_request.model_copy(update={"hard": hard}))


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
