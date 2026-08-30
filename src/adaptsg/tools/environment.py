"""Environmental snapshot adapters used by monitoring and replanning."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from adaptsg.domain import EnvironmentSnapshot


class EnvironmentClient(Protocol):
    def current(self) -> EnvironmentSnapshot: ...


class DemoEnvironmentClient:
    def __init__(
        self,
        *,
        weather_summary: str = "Fair",
        psi: int = 42,
        flood_affected_venue_ids: frozenset[str] = frozenset(),
        disrupted_route_labels: frozenset[str] = frozenset(),
    ) -> None:
        self._weather_summary = weather_summary
        self._psi = psi
        self._floods = flood_affected_venue_ids
        self._disruptions = disrupted_route_labels

    def current(self) -> EnvironmentSnapshot:
        return EnvironmentSnapshot(
            weather_summary=self._weather_summary,
            psi=self._psi,
            flood_affected_venue_ids=self._floods,
            disrupted_route_labels=self._disruptions,
            observed_at=datetime.now(UTC),
            source="demo_environment_snapshot_v1",
        )
