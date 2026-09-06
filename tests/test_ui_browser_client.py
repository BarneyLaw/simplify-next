"""Role 3 drift guard binding `public/index.html` to the routes it actually calls.

`scripts/check_web.mjs` validates the client's syntax and accessibility, and every
Streamlit test drives the Python service directly. Between them sat a gap wide enough
for the browser client to fall a whole contract behind the API while CI stayed green:
it posted to `/api/plan` with no `Idempotency-Key` and rendered the resulting `400` as
"No safe plan exists". These tests close it from both sides - the paths the client
names must exist, and the fields it reads must be in the responses.
"""

from __future__ import annotations

import re
from itertools import count
from pathlib import Path
from typing import Any

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from adaptsg.agent import AdaptSGService
from adaptsg.planning import JourneyPlanner, JourneyReplanner
from adaptsg.preference_parser import DeterministicPreferenceParser
from adaptsg.tools.catalog import VenueCatalog
from adaptsg.tools.environment import DemoEnvironmentClient
from adaptsg.web_api import create_app

BROWSER_CLIENT = Path(__file__).resolve().parents[1] / "public" / "index.html"
DEMO_PROMPT = "Plan a 10 am-5 pm wheelchair day from Toa Payoh, lunch before 1 pm, budget $70."
JOURNEY_DATE = "2026-09-01"

# The client's only path variable. FastAPI spells the same slot `{journey_id}`.
PATH_VARIABLE = re.compile(r"\$\{journeyId\}")
REQUEST_CALL = re.compile(r"\b(read|mutate)\(\s*(?:`([^`]*)`|'([^']*)')")
HELPER_METHOD = {"read": "GET", "mutate": "POST"}


def script() -> str:
    html = BROWSER_CLIENT.read_text(encoding="utf-8")
    match = re.search(r"<script>([\s\S]*?)</script>", html)
    assert match is not None, "the browser client must contain an inline script"
    return match.group(1)


def client_requests() -> set[tuple[str, str]]:
    """Every (method, path) the browser client can issue, as FastAPI spells them."""
    requests: set[tuple[str, str]] = set()
    for helper, template, literal in REQUEST_CALL.findall(script()):
        path = PATH_VARIABLE.sub("{journey_id}", template or literal)
        requests.add((HELPER_METHOD[helper], path))
    return requests


def server_requests() -> set[tuple[str, str]]:
    routes = create_app().routes
    return {
        (method, route.path)
        for route in routes
        if isinstance(route, APIRoute)
        for method in route.methods or ()
    }


def service(planner: JourneyPlanner, replanner: JourneyReplanner) -> AdaptSGService:
    return AdaptSGService(
        parser=DeterministicPreferenceParser(VenueCatalog()),
        planner=planner,
        replanner=replanner,
        environment=DemoEnvironmentClient(),
    )


@pytest.fixture
def api(planner: JourneyPlanner, replanner: JourneyReplanner) -> TestClient:
    return TestClient(create_app(service(planner, replanner)))


ACTION = count()


def post(api: TestClient, path: str, body: dict[str, Any] | None = None) -> Any:
    """One key per action, exactly as the client's `actionKey()` does."""
    response = api.post(
        path, headers={"Idempotency-Key": f"browser-action-{next(ACTION)}"}, json=body
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_the_client_only_calls_routes_that_exist() -> None:
    """A renamed route must fail here rather than in a judge's browser."""
    calls = client_requests()

    assert calls, "the request-helper regex found no calls; the client shape changed"
    unknown = calls - server_requests()
    assert not unknown, f"the browser client calls routes the API does not serve: {sorted(unknown)}"


def test_the_client_uses_the_stateful_journey_routes() -> None:
    """Pin the specific flow, so a silent fallback to a stateless route is a failure."""
    calls = client_requests()

    assert ("POST", "/api/journeys") in calls
    assert ("GET", "/api/journeys/{journey_id}") in calls
    assert ("POST", "/api/journeys/{journey_id}/decision") in calls
    assert ("POST", "/api/journeys/{journey_id}/replan") in calls
    assert ("POST", "/api/journeys/{journey_id}/monitor") in calls
    assert ("POST", "/api/replan") not in calls, "the client must not use the stateless replan"


def test_the_browser_sequence_returns_every_field_the_client_reads(api: TestClient) -> None:
    """Walk the exact create -> accept -> replan -> decide path `index.html` performs."""
    draft = post(api, "/api/journeys", {"prompt": DEMO_PROMPT, "journey_date": JOURNEY_DATE})
    assert {"journey_id", "version", "status", "warnings"} <= draft.keys()
    assert draft["status"] == "draft"
    pending = draft["pending_initial_itinerary"]
    assert {"id", "request", "segments", "total_cost_sgd", "replan_count", "parser_source"} <= (
        pending.keys()
    )
    segment = pending["segments"][0]
    assert {"activity_start", "activity_end", "venue", "route"} <= segment.keys()
    assert {"name", "indoor", "id", "accessibility_status"} <= segment["venue"].keys()
    assert {"mode", "walking_distance_m", "estimated_cost_sgd", "source"} <= segment["route"].keys()

    journey = draft["journey_id"]
    active = post(
        api,
        f"/api/journeys/{journey}/decision",
        {"target_id": pending["id"], "decision": "approve", "expected_version": draft["version"]},
    )
    assert active["status"] == "active"
    assert active["current_itinerary"]["id"] == pending["id"]

    outdoor = [
        s["venue"]["id"]
        for s in active["current_itinerary"]["segments"]
        if not s["venue"]["indoor"]
    ]
    replanned = post(
        api,
        f"/api/journeys/{journey}/replan",
        {
            "expected_version": active["version"],
            "trigger": {
                "type": "flood_alert",
                "message": "Heavy rain began and a flood alert affects an outdoor segment.",
                "affected_venue_ids": outdoor,
            },
        },
    )
    proposal = replanned["latest_replan_proposal"]
    assert proposal["status"] == "pending"
    assert {"id", "changes", "cost_delta_sgd", "requires_approval", "validation"} <= (
        proposal.keys()
    )
    assert {"segment_index", "before", "after", "reason"} <= proposal["changes"][0].keys()

    applied = post(
        api,
        f"/api/journeys/{journey}/decision",
        {
            "target_id": proposal["id"],
            "decision": "approve",
            "expected_version": replanned["version"],
        },
    )
    assert applied["current_itinerary"]["id"] == proposal["itinerary"]["id"]
    assert applied["latest_replan_proposal"]["status"] == "approved"


def test_the_monitor_route_returns_the_fields_the_conditions_panel_renders(
    api: TestClient,
) -> None:
    draft = post(api, "/api/journeys", {"prompt": DEMO_PROMPT, "journey_date": JOURNEY_DATE})
    journey = draft["journey_id"]
    post(
        api,
        f"/api/journeys/{journey}/decision",
        {
            "target_id": draft["pending_initial_itinerary"]["id"],
            "decision": "approve",
            "expected_version": draft["version"],
        },
    )

    outcome = post(api, f"/api/journeys/{journey}/monitor")

    assert {"snapshot", "triggers"} <= outcome.keys()
    assert {"weather_summary", "psi", "observed_at", "source"} <= outcome["snapshot"].keys()
    assert outcome["snapshot"]["source"] == "demo_environment_snapshot_v1"


def test_a_missing_idempotency_key_is_a_typed_client_fault_not_a_safety_verdict(
    api: TestClient,
) -> None:
    """The defect this file exists for: a 400 must be distinguishable from no-feasible."""
    response = api.post(
        "/api/journeys",
        json={"prompt": DEMO_PROMPT, "journey_date": JOURNEY_DATE},
    )

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_idempotency_key"


def test_a_stale_version_carries_the_current_version_back_to_the_client(
    api: TestClient,
) -> None:
    draft = post(api, "/api/journeys", {"prompt": DEMO_PROMPT, "journey_date": JOURNEY_DATE})

    response = api.post(
        f"/api/journeys/{draft['journey_id']}/decision",
        headers={"Idempotency-Key": "browser-stale-decision"},
        json={
            "target_id": draft["pending_initial_itinerary"]["id"],
            "decision": "approve",
            "expected_version": draft["version"] + 5,
        },
    )

    assert response.status_code == 409
    assert response.json()["code"] == "stale_journey_version"
    assert response.json()["current_version"] == draft["version"]
