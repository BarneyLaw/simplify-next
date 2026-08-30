from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

from adaptsg.domain import Location, TravelMode, VenueCategory
from adaptsg.errors import ToolUnavailable
from adaptsg.tools.catalog import VenueCatalog
from adaptsg.tools.environment import DemoEnvironmentClient, LiveEnvironmentClient
from adaptsg.tools.routing import DemoRoutingClient, OneMapRoutingClient, distance_metres

SGT = ZoneInfo("Asia/Singapore")
START = datetime(2026, 9, 1, 10, tzinfo=SGT)
ORIGIN = Location(lat=1.3000, lng=103.8000)
DESTINATION = Location(lat=1.3040, lng=103.8040)


def test_catalog_lookup_and_filters() -> None:
    catalog = VenueCatalog()
    assert len(catalog.all()) == 18
    assert catalog.get("national-gallery").name == "National Gallery Singapore"
    with pytest.raises(KeyError, match="unknown venue"):
        catalog.get("missing")
    indoor = catalog.eligible(
        wheelchair_required=True,
        indoor_only=True,
        excluded_ids=frozenset({"national-gallery"}),
        categories=(VenueCategory.INDOOR_MUSEUM,),
    )
    assert indoor
    assert all(venue.indoor and venue.id != "national-gallery" for venue in indoor)


@pytest.mark.parametrize(
    ("mode", "expected_cost"),
    [(TravelMode.WALK, 0.0), (TravelMode.PUBLIC_TRANSPORT, 2.0), (TravelMode.TAXI, 5.59)],
)
def test_demo_routes_are_deterministic(mode: TravelMode, expected_cost: float) -> None:
    route = DemoRoutingClient().route(
        origin_label="A",
        origin=ORIGIN,
        destination_label="B",
        destination=DESTINATION,
        depart_at=START,
        mode=mode,
        max_walking_distance_m=400,
    )
    assert route.estimated_cost_sgd == expected_cost
    assert route.source == "demo_route_estimator_v1"
    assert route.arrive_at > route.depart_at


def test_distance_is_symmetric() -> None:
    assert distance_metres(ORIGIN, DESTINATION) == distance_metres(DESTINATION, ORIGIN)


def test_onemap_requires_token() -> None:
    with pytest.raises(ToolUnavailable, match="TOKEN"):
        OneMapRoutingClient(token="").route(
            origin_label="A",
            origin=ORIGIN,
            destination_label="B",
            destination=DESTINATION,
            depart_at=START,
            mode=TravelMode.WALK,
            max_walking_distance_m=400,
        )


def test_onemap_bfa_and_drive_responses() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(
            200,
            json={"route_summary": {"total_time": 600, "total_distance": 500}},
        )

    routing = OneMapRoutingClient(
        token="test",
        bfa_enabled=True,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    walk = routing.route(
        origin_label="A",
        origin=ORIGIN,
        destination_label="B",
        destination=DESTINATION,
        depart_at=START,
        mode=TravelMode.WALK,
        max_walking_distance_m=600,
    )
    taxi = routing.route(
        origin_label="A",
        origin=ORIGIN,
        destination_label="B",
        destination=DESTINATION,
        depart_at=START,
        mode=TravelMode.TAXI,
        max_walking_distance_m=400,
    )
    assert walk.source.startswith("onemap_bfa")
    assert walk.walking_distance_m == 500
    assert taxi.estimated_cost_sgd == 5.42
    assert paths == ["/api/bfa/routingsvc/route", "/api/public/routingsvc/route"]


def test_onemap_public_transport_parses_walking_legs() -> None:
    payload = {
        "plan": {
            "itineraries": [
                {"duration": 1200, "legs": [{"mode": "WALK", "distance": 180}]},
                {
                    "duration": 900,
                    "legs": [
                        {"mode": "WALK", "distance": 120},
                        {"mode": "BUS", "distance": 3000},
                        {"mode": "WALK", "distance": 80},
                    ],
                },
            ]
        }
    }
    client = httpx.Client(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=payload))
    )
    route = OneMapRoutingClient(token="test", client=client).route(
        origin_label="A",
        origin=ORIGIN,
        destination_label="B",
        destination=DESTINATION,
        depart_at=START,
        mode=TravelMode.PUBLIC_TRANSPORT,
        max_walking_distance_m=400,
    )
    assert route.duration_minutes == 15
    assert route.walking_distance_m == 200
    assert route.estimated_cost_sgd == 2


@pytest.mark.parametrize(
    "payload",
    [
        {"plan": {"itineraries": []}},
        {"plan": {"itineraries": [{"duration": 100, "legs": [{"mode": "BUS"}]}]}},
        {},
    ],
)
def test_onemap_rejects_incomplete_responses(payload: dict[str, object]) -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=payload))
    )
    with pytest.raises(ToolUnavailable, match="verification failed"):
        OneMapRoutingClient(token="test", client=client).route(
            origin_label="A",
            origin=ORIGIN,
            destination_label="B",
            destination=DESTINATION,
            depart_at=START,
            mode=TravelMode.PUBLIC_TRANSPORT,
            max_walking_distance_m=400,
        )


def test_onemap_translates_http_errors() -> None:
    client = httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(500)))
    with pytest.raises(ToolUnavailable):
        OneMapRoutingClient(token="test", client=client).route(
            origin_label="A",
            origin=ORIGIN,
            destination_label="B",
            destination=DESTINATION,
            depart_at=START,
            mode=TravelMode.WALK,
            max_walking_distance_m=400,
        )


def test_demo_environment_is_labelled() -> None:
    snapshot = DemoEnvironmentClient(weather_summary="Rain", psi=55).current()
    assert snapshot.weather_summary == "Rain"
    assert snapshot.psi == 55
    assert snapshot.source.startswith("demo_")


def live_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if "twenty-four" in path:
        return httpx.Response(
            200,
            json={
                "data": {
                    "records": [
                        {
                            "updatedTimestamp": "2026-09-01T10:00:00+08:00",
                            "general": {"forecast": {"text": "Thundery Showers"}},
                        }
                    ]
                }
            },
        )
    if path.endswith("/psi"):
        return httpx.Response(
            200,
            json={
                "data": {
                    "items": [
                        {
                            "updatedTimestamp": "2026-09-01T10:15:00+08:00",
                            "readings": {"psi_twenty_four_hourly": {"central": 105, "east": 90}},
                        }
                    ]
                }
            },
        )
    if path.endswith("PubFloodAlerts"):
        assert request.headers["accountkey"] == "lta-key"
        return httpx.Response(
            200,
            json={
                "value": [
                    {"msgType": "Alert", "circle": "1.2903,103.8519 0.5"},
                    {"msgType": "Cancel", "circle": "1.3,103.8 5"},
                    {"msgType": "Alert", "circle": "invalid"},
                ]
            },
        )
    return httpx.Response(
        200,
        json={"value": [{"Status": 2, "Line": "NSL"}, {"Status": 1, "Line": "EWL"}]},
    )


def test_live_environment_combines_official_feeds(catalog: VenueCatalog) -> None:
    client = httpx.Client(transport=httpx.MockTransport(live_handler))
    snapshot = LiveEnvironmentClient(
        catalog=catalog,
        lta_account_key="lta-key",
        data_gov_api_key="data-key",
        client=client,
    ).current()
    assert snapshot.weather_summary == "Thundery Showers"
    assert snapshot.psi == 105
    assert "national-gallery" in snapshot.flood_affected_venue_ids
    assert snapshot.disrupted_route_labels == frozenset({"NSL"})
    assert snapshot.observed_at.isoformat() == "2026-09-01T10:15:00+08:00"


def test_live_environment_requires_lta_key(catalog: VenueCatalog) -> None:
    with pytest.raises(ToolUnavailable, match="LTA_ACCOUNT_KEY"):
        LiveEnvironmentClient(catalog=catalog, lta_account_key="").current()


def test_live_environment_translates_provider_errors(catalog: VenueCatalog) -> None:
    client = httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(500)))
    with pytest.raises(ToolUnavailable, match="verification failed"):
        LiveEnvironmentClient(
            catalog=catalog,
            lta_account_key="test",
            client=client,
        ).current()
