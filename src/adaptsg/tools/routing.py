"""Routing adapters. Demo estimates are deterministic and visibly labelled."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from math import asin, ceil, cos, radians, sin, sqrt
from typing import Protocol

from adaptsg.domain import Location, RouteLeg, TravelMode


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
