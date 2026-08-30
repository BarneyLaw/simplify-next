"""Routing adapters. Demo estimates are deterministic and visibly labelled."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from math import asin, ceil, cos, radians, sin, sqrt
from typing import Any, Protocol, cast

import httpx

from adaptsg.domain import Location, RouteLeg, TravelMode
from adaptsg.errors import ToolUnavailable


class RoutingClient(Protocol):
    def route(
        self,
        *,
        origin_label: str,
        origin: Location,
        destination_label: str,
        destination: Location,
        depart_at: datetime,
        mode: TravelMode,
        max_walking_distance_m: int,
    ) -> RouteLeg: ...


def distance_metres(origin: Location, destination: Location) -> int:
    """Great-circle distance used only by the labelled demo routing adapter."""

    earth_radius_m = 6_371_000
    lat1, lat2 = radians(origin.lat), radians(destination.lat)
    delta_lat = radians(destination.lat - origin.lat)
    delta_lng = radians(destination.lng - origin.lng)
    a = sin(delta_lat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(delta_lng / 2) ** 2
    return round(2 * earth_radius_m * asin(sqrt(a)))


class DemoRoutingClient:
    """Deterministic offline routing for demos and CI; never presented as live data."""

    source = "demo_route_estimator_v1"

    def route(
        self,
        *,
        origin_label: str,
        origin: Location,
        destination_label: str,
        destination: Location,
        depart_at: datetime,
        mode: TravelMode,
        max_walking_distance_m: int,
    ) -> RouteLeg:
        direct_distance = max(distance_metres(origin, destination), 100)
        if mode is TravelMode.WALK:
            walking_distance = direct_distance
            duration = max(5, ceil(direct_distance / 55))
            cost = 0.0
        elif mode is TravelMode.TAXI:
            walking_distance = min(80, max_walking_distance_m)
            duration = max(8, ceil(direct_distance / 500) + 5)
            cost = round(4.8 + direct_distance / 1_000 * 1.25, 2)
        else:
            walking_distance = min(max_walking_distance_m, max(120, ceil(direct_distance * 0.08)))
            duration = max(12, ceil(direct_distance / 350) + 8)
            cost = 2.0

        arrive_at = depart_at + timedelta(minutes=duration)
        return RouteLeg(
            origin_label=origin_label,
            destination_label=destination_label,
            origin=origin,
            destination=destination,
            mode=mode,
            depart_at=depart_at,
            arrive_at=arrive_at,
            duration_minutes=duration,
            walking_distance_m=walking_distance,
            estimated_cost_sgd=cost,
            source=self.source,
            source_timestamp=datetime.now(UTC),
        )


class OneMapRoutingClient:
    """Live OneMap routing with optional approved Barrier-Free Access routing."""

    base_url = "https://www.onemap.gov.sg"

    def __init__(
        self,
        *,
        token: str,
        bfa_enabled: bool = False,
        client: httpx.Client | None = None,
    ) -> None:
        self.token = token
        self.bfa_enabled = bfa_enabled
        self.client = client or httpx.Client(timeout=8)

    def route(
        self,
        *,
        origin_label: str,
        origin: Location,
        destination_label: str,
        destination: Location,
        depart_at: datetime,
        mode: TravelMode,
        max_walking_distance_m: int,
    ) -> RouteLeg:
        if not self.token:
            raise ToolUnavailable("ONEMAP_API_TOKEN is required in live mode")
        use_bfa = self.bfa_enabled and mode is TravelMode.WALK
        path = "/api/bfa/routingsvc/route" if use_bfa else "/api/public/routingsvc/route"
        params = {
            "start": f"{origin.lat},{origin.lng}",
            "end": f"{destination.lat},{destination.lng}",
        }
        if not use_bfa:
            params["routeType"] = self._route_type(mode)
        try:
            response = self.client.get(
                f"{self.base_url}{path}",
                params=params,
                headers={"Authorization": self.token},
            )
            response.raise_for_status()
            payload = cast(dict[str, Any], response.json())
            duration_seconds, route_distance, walking_distance = self._route_metrics(payload, mode)
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise ToolUnavailable(f"OneMap route verification failed: {exc}") from exc

        duration_minutes = max(1, ceil(duration_seconds / 60))
        cost = self._cost(mode, route_distance)
        source = "onemap_bfa" if use_bfa else f"onemap_{self._route_type(mode)}"
        return RouteLeg(
            origin_label=origin_label,
            destination_label=destination_label,
            origin=origin,
            destination=destination,
            mode=mode,
            depart_at=depart_at,
            arrive_at=depart_at + timedelta(minutes=duration_minutes),
            duration_minutes=duration_minutes,
            walking_distance_m=int(walking_distance),
            estimated_cost_sgd=cost,
            source=f"{source}+transport_cost_policy_v1",
            source_timestamp=datetime.now(UTC),
        )

    @staticmethod
    def _route_type(mode: TravelMode) -> str:
        if mode is TravelMode.TAXI:
            return "drive"
        if mode is TravelMode.PUBLIC_TRANSPORT:
            return "pt"
        return "walk"

    @staticmethod
    def _route_metrics(payload: dict[str, Any], mode: TravelMode) -> tuple[int, int, int]:
        if "plan" in payload:
            itineraries = cast(list[dict[str, Any]], payload["plan"]["itineraries"])
            if not itineraries:
                raise ValueError("OneMap returned no public-transport itinerary")
            itinerary = min(itineraries, key=lambda item: int(item["duration"]))
            duration = int(itinerary["duration"])
            legs = cast(list[dict[str, Any]], itinerary.get("legs", []))
            distance = sum(int(float(leg.get("distance", 0))) for leg in legs)
            walking = sum(
                int(float(leg.get("distance", 0)))
                for leg in legs
                if str(leg.get("mode", "")).upper() == "WALK"
            )
            if walking <= 0:
                raise ValueError("OneMap PT response lacked first/last-mile walking distance")
            return duration, distance, walking

        summary = cast(dict[str, Any], payload["route_summary"])
        duration = int(float(summary["total_time"]))
        distance = int(float(summary["total_distance"]))
        walking = distance if mode is TravelMode.WALK else min(80, distance)
        return duration, distance, walking

    @staticmethod
    def _cost(mode: TravelMode, route_distance_m: int) -> float:
        if mode is TravelMode.WALK:
            return 0.0
        if mode is TravelMode.PUBLIC_TRANSPORT:
            return 2.0
        return round(4.8 + route_distance_m / 1_000 * 1.25, 2)
