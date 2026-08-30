from datetime import date

import pytest

from adaptsg.domain import Itinerary, JourneyRequest
from adaptsg.planning import JourneyPlanner, JourneyReplanner
from adaptsg.tools.catalog import VenueCatalog
from adaptsg.tools.routing import DemoRoutingClient
from adaptsg.validation import ItineraryValidator


@pytest.fixture
def catalog() -> VenueCatalog:
    return VenueCatalog()


@pytest.fixture
def validator() -> ItineraryValidator:
    return ItineraryValidator(max_replans=2)


@pytest.fixture
def planner(catalog: VenueCatalog, validator: ItineraryValidator) -> JourneyPlanner:
    return JourneyPlanner(
        catalog=catalog,
        routing=DemoRoutingClient(),
        validator=validator,
    )


@pytest.fixture
def replanner(planner: JourneyPlanner) -> JourneyReplanner:
    return JourneyReplanner(
        planner=planner,
        approval_cost_increase_sgd=8,
        max_replans=2,
    )


@pytest.fixture
def journey_request() -> JourneyRequest:
    return JourneyRequest(journey_date=date(2026, 9, 1))


@pytest.fixture
def itinerary(planner: JourneyPlanner, journey_request: JourneyRequest) -> Itinerary:
    return planner.create(journey_request)
