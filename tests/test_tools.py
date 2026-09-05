from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest

from adaptsg.domain import (
    AccessibilityStatus,
    FreshnessStatus,
    Itinerary,
    Location,
    TravelMode,
    VenueCategory,
    VenueSearchFilters,
)
from adaptsg.errors import ToolUnavailable
from adaptsg.tools.catalog import VenueCatalog
from adaptsg.tools.environment import DemoEnvironmentClient, LiveEnvironmentClient
from adaptsg.tools.freshness import FreshnessKind, classify_freshness
from adaptsg.tools.location import DemoLocationClient, OneMapLocationClient
from adaptsg.tools.metrics import calculate_plan_metrics
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
    assert catalog.audit() == ()
    evidence = catalog.get_accessibility("national-gallery")
    assert evidence.status is AccessibilityStatus.VERIFIED
    assert evidence.source == "curated_demo_dataset"
    result = catalog.search_result(
        VenueSearchFilters(indoor_only=True, categories=(VenueCategory.FOOD,))
    )
    assert result.success and result.is_fixture
    assert result.payload and all(venue.indoor for venue in result.payload)


def test_freshness_policy_marks_stale_and_fixture_data() -> None:
    now = datetime(2026, 9, 2, 12, tzinfo=UTC)
    assert (
        classify_freshness(now - timedelta(minutes=16), FreshnessKind.ROUTE, now=now)
        is FreshnessStatus.STALE
    )
    assert (
        classify_freshness(now - timedelta(days=365), FreshnessKind.ROUTE, now=now, is_fixture=True)
        is FreshnessStatus.FIXTURE
    )


def test_demo_location_lookup_is_typed_and_bounded() -> None:
    result = DemoLocationClient().search_result("Toa Payoh")
    assert result.success and result.freshness is FreshnessStatus.FIXTURE
    assert result.payload and result.payload[0].location == Location(lat=1.3323, lng=103.8474)
    assert DemoLocationClient().search(" ") == ()


def test_onemap_location_search_rejects_malformed_response() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json={"results": [{"SEARCHVAL": "bad"}]})
        )
    )
    result = OneMapLocationClient(token="test", client=client).search_result("bad")
    assert not result.success
    assert result.error_code == "location_unavailable"


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


def test_demo_route_does_not_hide_walking_limit_violations() -> None:
    routing = DemoRoutingClient()
    short_limit = routing.route(
        origin_label="A",
        origin=ORIGIN,
        destination_label="B",
        destination=DESTINATION,
        depart_at=START,
        mode=TravelMode.PUBLIC_TRANSPORT,
        max_walking_distance_m=100,
    )
    generous_limit = routing.route(
        origin_label="A",
        origin=ORIGIN,
        destination_label="B",
        destination=DESTINATION,
        depart_at=START,
        mode=TravelMode.PUBLIC_TRANSPORT,
        max_walking_distance_m=400,
    )
    assert short_limit.walking_distance_m == generous_limit.walking_distance_m
    far_route = routing.route(
        origin_label="A",
        origin=ORIGIN,
        destination_label="Far away",
        destination=Location(lat=1.35, lng=103.90),
        depart_at=START,
        mode=TravelMode.PUBLIC_TRANSPORT,
        max_walking_distance_m=400,
    )
    assert far_route.walking_distance_m > 400
    assert (
        routing.route_result(
            origin_label="A",
            origin=ORIGIN,
            destination_label="B",
            destination=DESTINATION,
            depart_at=START,
            mode=TravelMode.WALK,
            max_walking_distance_m=400,
        ).freshness
        is FreshnessStatus.FIXTURE
    )


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
        transport=httpx.MockTransport(
            lambda request: (
                httpx.Response(200, json=payload)
                if request.url.params["date"] == "09-01-2026"
                and request.url.params["time"] == "10:00:00"
                and request.url.params["mode"] == "TRANSIT"
                and request.url.params["maxWalkDistance"] == "400"
                and request.url.params["numItineraries"] == "1"
                else httpx.Response(400)
            )
        )
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


def test_onemap_public_transport_rejects_summary_without_walking_legs() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200, json={"route_summary": {"total_time": 600, "total_distance": 500}}
            )
        )
    )
    with pytest.raises(ToolUnavailable, match="walking distance"):
        OneMapRoutingClient(token="test", client=client).route(
            origin_label="A",
            origin=ORIGIN,
            destination_label="B",
            destination=DESTINATION,
            depart_at=START,
            mode=TravelMode.PUBLIC_TRANSPORT,
            max_walking_distance_m=400,
        )


def test_plan_metrics_are_typed_and_reconcile_cost(itinerary: Itinerary) -> None:
    metrics = calculate_plan_metrics(itinerary)
    assert metrics.total_cost_sgd == itinerary.total_cost_sgd
    assert len(metrics.segments) == len(itinerary.segments)


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
    assert snapshot.freshness is FreshnessStatus.STALE


def test_live_environment_accepts_current_train_alert_shape(catalog: VenueCatalog) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("TrainServiceAlerts"):
            return httpx.Response(
                200,
                json={
                    "value": {
                        "Status": 1,
                        "AffectedSegments": [],
                        "Message": [{"Content": "Planned service adjustment"}],
                    }
                },
            )
        return live_handler(request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    snapshot = LiveEnvironmentClient(
        catalog=catalog,
        lta_account_key="lta-key",
        client=client,
    ).current()
    assert snapshot.disrupted_route_labels == frozenset()


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


def test_live_environment_translates_malformed_train_alerts(catalog: VenueCatalog) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("TrainServiceAlerts"):
            return httpx.Response(200, json={"value": [{"Status": {"unexpected": True}}]})
        return live_handler(request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(ToolUnavailable, match="verification failed"):
        LiveEnvironmentClient(
            catalog=catalog,
            lta_account_key="lta-key",
            client=client,
        ).current()
