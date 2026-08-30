"""Twenty named regression scenarios used for hackathon evaluation metrics."""

from dataclasses import dataclass
from datetime import time
from typing import Literal

import pytest

from adaptsg.domain import Itinerary, JourneyRequest, ReplanTrigger, TriggerType
from adaptsg.errors import NoFeasibleItinerary, ReplanLimitReached
from adaptsg.planning import JourneyPlanner, JourneyReplanner


@dataclass(frozen=True)
class Scenario:
    name: str
    kind: Literal["initial", "replan"]
    expected_feasible: bool
    hard_updates: dict[str, object] | None = None
    trigger: ReplanTrigger | None = None
    starting_replans: int = 0


SCENARIOS = (
    Scenario("baseline-caregiver-day", "initial", True),
    Scenario(
        "required-accessible-venue",
        "initial",
        True,
        {"required_venue_ids": frozenset({"cloud-forest"})},
    ),
    Scenario(
        "unverified-accessibility",
        "initial",
        False,
        {"required_venue_ids": frozenset({"fort-canning-park"})},
    ),
    Scenario(
        "too-many-required-stops",
        "initial",
        False,
        {
            "required_venue_ids": frozenset(
                {"national-gallery", "cloud-forest", "artscience-museum"}
            )
        },
    ),
    Scenario("budget-below-plan", "initial", False, {"total_budget_sgd": 32}),
    Scenario("lunch-deadline-too-early", "initial", False, {"lunch_latest": time(11)}),
    Scenario("finish-deadline-too-early", "initial", False, {"finish_by": time(13)}),
    Scenario("very-short-walking-limit", "initial", True, {"max_walking_distance_m": 100}),
    Scenario(
        "heavy-rain",
        "replan",
        True,
        trigger=ReplanTrigger(type=TriggerType.HEAVY_RAIN, message="Heavy rain"),
    ),
    Scenario(
        "high-psi",
        "replan",
        True,
        trigger=ReplanTrigger(type=TriggerType.HIGH_PSI, message="PSI 120"),
    ),
    Scenario(
        "pub-flood-alert",
        "replan",
        True,
        trigger=ReplanTrigger(
            type=TriggerType.FLOOD_ALERT,
            message="Flood",
            affected_venue_ids=frozenset({"gardens-bay-outdoor"}),
        ),
    ),
    Scenario(
        "venue-closure",
        "replan",
        True,
        trigger=ReplanTrigger(
            type=TriggerType.VENUE_CLOSURE,
            message="Closed",
            affected_venue_ids=frozenset({"gardens-bay-outdoor"}),
        ),
    ),
    Scenario(
        "transport-disruption",
        "replan",
        True,
        trigger=ReplanTrigger(type=TriggerType.TRANSPORT_DISRUPTION, message="NSL down"),
    ),
    Scenario(
        "unexpected-fatigue",
        "replan",
        True,
        trigger=ReplanTrigger(type=TriggerType.FATIGUE, message="Tired"),
    ),
    Scenario(
        "budget-reduced-to-25",
        "replan",
        True,
        trigger=ReplanTrigger(
            type=TriggerType.BUDGET_REDUCTION,
            message="Budget S$25",
            new_budget_sgd=25,
        ),
    ),
    Scenario(
        "budget-reduced-to-20",
        "replan",
        True,
        trigger=ReplanTrigger(
            type=TriggerType.BUDGET_REDUCTION,
            message="Budget S$20",
            new_budget_sgd=20,
        ),
    ),
    Scenario(
        "budget-reduced-below-feasible",
        "replan",
        False,
        trigger=ReplanTrigger(
            type=TriggerType.BUDGET_REDUCTION,
            message="Budget S$17",
            new_budget_sgd=17,
        ),
    ),
    Scenario(
        "budget-value-missing",
        "replan",
        False,
        trigger=ReplanTrigger(type=TriggerType.BUDGET_REDUCTION, message="Less budget"),
    ),
    Scenario(
        "unrelated-closure",
        "replan",
        True,
        trigger=ReplanTrigger(
            type=TriggerType.VENUE_CLOSURE,
            message="Other venue closed",
            affected_venue_ids=frozenset({"science-centre"}),
        ),
    ),
    Scenario(
        "replan-loop-cap",
        "replan",
        False,
        trigger=ReplanTrigger(type=TriggerType.FATIGUE, message="Tired again"),
        starting_replans=2,
    ),
)


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda scenario: scenario.name)
def test_evaluation_scenario(
    scenario: Scenario,
    planner: JourneyPlanner,
    replanner: JourneyReplanner,
    journey_request: JourneyRequest,
    itinerary: Itinerary,
) -> None:
    assert len(SCENARIOS) == 20
    try:
        if scenario.kind == "initial":
            hard = journey_request.hard.model_copy(update=scenario.hard_updates or {})
            plan_result = planner.create(journey_request.model_copy(update={"hard": hard}))
            feasible = planner.validator.validate(plan_result).valid
        else:
            assert scenario.trigger is not None
            start = itinerary.model_copy(update={"replan_count": scenario.starting_replans})
            proposal_result = replanner.propose(start, scenario.trigger)
            feasible = proposal_result.validation.valid
    except (NoFeasibleItinerary, ReplanLimitReached, KeyError):
        feasible = False
    assert feasible is scenario.expected_feasible
