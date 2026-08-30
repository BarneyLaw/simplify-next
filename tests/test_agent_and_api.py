from datetime import date

import pytest
from fastapi.testclient import TestClient

from adaptsg.agent import AdaptSGService
from adaptsg.domain import (
    Itinerary,
    JourneyRequest,
    MonitoringOutcome,
    ParseOutcome,
    ReplanTrigger,
    TriggerType,
)
from adaptsg.errors import NoFeasibleItinerary
from adaptsg.planning import JourneyPlanner, JourneyReplanner
from adaptsg.preference_parser import DeterministicPreferenceParser
from adaptsg.presentation import itinerary_rows, retained_segment_percentage
from adaptsg.tools.catalog import VenueCatalog
from adaptsg.tools.environment import DemoEnvironmentClient
from adaptsg.web_api import create_app


class RaisingParser:
    def parse(self, _prompt: str, *, journey_date: date) -> ParseOutcome:
        raise RuntimeError(f"provider unavailable on {journey_date}")


class FixedParser:
    def __init__(self, request: JourneyRequest) -> None:
        self.request = request

    def parse(self, _prompt: str, *, journey_date: date) -> ParseOutcome:
        assert journey_date == self.request.journey_date
        return ParseOutcome(request=self.request, source="fixed")


def make_service(
    planner: JourneyPlanner,
    replanner: JourneyReplanner,
    *,
    environment: DemoEnvironmentClient | None = None,
) -> AdaptSGService:
    return AdaptSGService(
        parser=DeterministicPreferenceParser(VenueCatalog()),
        planner=planner,
        replanner=replanner,
        environment=environment or DemoEnvironmentClient(),
    )


def test_service_runs_bounded_plan_graph(
    planner: JourneyPlanner, replanner: JourneyReplanner
) -> None:
    service = make_service(planner, replanner)
    outcome = service.create_plan(
        "Plan 10 am-5 pm from Toa Payoh. Wheelchair, 400 m maximum walking, "
        "lunch before 1 pm, budget $70, visit Gardens by the Bay.",
        journey_date=date(2026, 9, 1),
    )
    assert outcome.itinerary.total_cost_sgd == 33
    assert outcome.warnings


def test_graph_translates_parser_failure(
    planner: JourneyPlanner, replanner: JourneyReplanner
) -> None:
    service = AdaptSGService(
        parser=RaisingParser(),
        planner=planner,
        replanner=replanner,
        environment=DemoEnvironmentClient(),
    )
    with pytest.raises(NoFeasibleItinerary, match="constraint parsing failed"):
        service.create_plan("Plan", journey_date=date(2026, 9, 1))


def test_graph_translates_no_feasible_plan(
    planner: JourneyPlanner,
    replanner: JourneyReplanner,
    journey_request: JourneyRequest,
) -> None:
    hard = journey_request.hard.model_copy(
        update={"required_venue_ids": frozenset({"fort-canning-park"})}
    )
    service = AdaptSGService(
        parser=FixedParser(journey_request.model_copy(update={"hard": hard})),
        planner=planner,
        replanner=replanner,
        environment=DemoEnvironmentClient(),
    )
    with pytest.raises(NoFeasibleItinerary, match="verified accessibility"):
        service.create_plan("Plan", journey_date=journey_request.journey_date)


def test_monitor_translates_all_environmental_triggers(
    planner: JourneyPlanner, replanner: JourneyReplanner, itinerary: Itinerary
) -> None:
    environment = DemoEnvironmentClient(
        weather_summary="Thundery Showers",
        psi=120,
        flood_affected_venue_ids=frozenset({"gardens-bay-outdoor"}),
        disrupted_route_labels=frozenset({"NSL"}),
    )
    service = make_service(planner, replanner, environment=environment)
    monitoring = service.monitor(itinerary)
    assert isinstance(monitoring, MonitoringOutcome)
    assert {trigger.type for trigger in monitoring.triggers} == {
        TriggerType.HEAVY_RAIN,
        TriggerType.HIGH_PSI,
        TriggerType.FLOOD_ALERT,
        TriggerType.TRANSPORT_DISRUPTION,
    }


def test_monitor_has_no_false_positive(
    planner: JourneyPlanner, replanner: JourneyReplanner, itinerary: Itinerary
) -> None:
    monitoring = make_service(planner, replanner).monitor(itinerary)
    assert monitoring.triggers == ()


def test_presentation_rows_and_retention(itinerary: Itinerary, replanner: JourneyReplanner) -> None:
    rows = itinerary_rows(itinerary)
    assert rows[0]["stop"] == "National Gallery Singapore"
    walking_metres = rows[0]["walking_metres"]
    assert isinstance(walking_metres, int)
    assert walking_metres <= 400
    proposal = replanner.propose(
        itinerary,
        ReplanTrigger(type=TriggerType.HEAVY_RAIN, message="Rain"),
    )
    assert retained_segment_percentage(itinerary, itinerary) == 100
    assert retained_segment_percentage(itinerary, proposal.itinerary) == 67
    empty = itinerary.model_copy(update={"segments": (), "total_cost_sgd": 0})
    assert retained_segment_percentage(empty, itinerary) == 100


def test_fastapi_plan_replan_and_static_page(
    planner: JourneyPlanner, replanner: JourneyReplanner
) -> None:
    client = TestClient(create_app(make_service(planner, replanner)))
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert client.get("/").status_code == 200

    plan = client.post(
        "/api/plan",
        json={
            "prompt": "Plan 10 am-5 pm for a wheelchair user, lunch by 1 pm, budget $70.",
            "journey_date": "2026-09-01",
        },
    )
    assert plan.status_code == 200
    replan = client.post(
        "/api/replan",
        json={
            "itinerary": plan.json()["itinerary"],
            "trigger": {"type": "fatigue", "message": "Mum is tired"},
        },
    )
    assert replan.status_code == 200
    assert replan.json()["validation"]["valid"]


def test_fastapi_rejects_invalid_and_infeasible_requests(
    planner: JourneyPlanner, replanner: JourneyReplanner
) -> None:
    client = TestClient(create_app(make_service(planner, replanner)))
    assert client.post("/api/plan", json={"prompt": "missing date"}).status_code == 422
    response = client.post(
        "/api/plan",
        json={
            "prompt": "Must visit Fort Canning Park with a wheelchair.",
            "journey_date": "2026-09-01",
        },
    )
    assert response.status_code == 422
    assert "accessibility" in response.json()["detail"]
