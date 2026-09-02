"""Environmental snapshot adapters used by monitoring and replanning."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol, cast

import httpx

from adaptsg.domain import EnvironmentSnapshot, Location, ToolResult, Venue
from adaptsg.errors import ToolUnavailable
from adaptsg.tools.catalog import VenueCatalog
from adaptsg.tools.freshness import FreshnessKind, failed_result, successful_result
from adaptsg.tools.routing import distance_metres


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
            freshness="fixture",
            is_fixture=True,
        )

    def current_result(self) -> ToolResult[EnvironmentSnapshot]:
        snapshot = self.current()
        return successful_result(
            snapshot,
            source=snapshot.source,
            source_timestamp=snapshot.observed_at,
            kind=FreshnessKind.WEATHER,
            is_fixture=True,
        )


class LiveEnvironmentClient:
    """Combine official data.gov.sg weather/PSI and LTA/PUB alert feeds."""

    weather_url = "https://api-open.data.gov.sg/v2/real-time/api/twenty-four-hr-forecast"
    psi_url = "https://api-open.data.gov.sg/v2/real-time/api/psi"
    flood_url = "https://datamall2.mytransport.sg/ltaodataservice/PubFloodAlerts"
    train_url = "https://datamall2.mytransport.sg/ltaodataservice/TrainServiceAlerts"

    def __init__(
        self,
        *,
        catalog: VenueCatalog,
        lta_account_key: str,
        data_gov_api_key: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.catalog = catalog
        self.lta_account_key = lta_account_key
        self.data_gov_api_key = data_gov_api_key
        self.client = client or httpx.Client(timeout=8)

    def current(self) -> EnvironmentSnapshot:
        if not self.lta_account_key:
            raise ToolUnavailable("LTA_ACCOUNT_KEY is required for live flood verification")
        try:
            weather = self._get(self.weather_url, self._data_headers())
            psi = self._get(self.psi_url, self._data_headers())
            floods = self._get(self.flood_url, self._lta_headers())
            train = self._get(self.train_url, self._lta_headers())
            weather_record = cast(dict[str, Any], weather["data"]["records"][0])
            psi_item = cast(dict[str, Any], psi["data"]["items"][0])
            weather_summary = str(weather_record["general"]["forecast"]["text"])
            psi_regions = cast(dict[str, Any], psi_item["readings"]["psi_twenty_four_hourly"])
            psi_value = max(int(value) for value in psi_regions.values())
            observed_at = min(
                datetime.fromisoformat(str(weather_record["updatedTimestamp"])),
                datetime.fromisoformat(str(psi_item["updatedTimestamp"])),
            )
            flood_venues = self._flood_venues(floods)
            disruptions = self._train_disruptions(train)
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            raise ToolUnavailable(f"live environment verification failed: {exc}") from exc

        return EnvironmentSnapshot(
            weather_summary=weather_summary,
            psi=psi_value,
            flood_affected_venue_ids=flood_venues,
            disrupted_route_labels=disruptions,
            observed_at=observed_at,
            source="data.gov.sg_weather_psi+lta_pub_flood_train",
            freshness="fresh",
            is_fixture=False,
        )

    def current_result(self) -> ToolResult[EnvironmentSnapshot]:
        try:
            snapshot = self.current()
        except ToolUnavailable as exc:
            return failed_result(
                source="data.gov.sg+lta_pub",
                error_code="environment_unavailable",
                error_message=str(exc),
                kind=FreshnessKind.WEATHER,
            )
        return successful_result(
            snapshot,
            source=snapshot.source,
            source_timestamp=snapshot.observed_at,
            kind=FreshnessKind.WEATHER,
        )

    def _get(self, url: str, headers: dict[str, str]) -> dict[str, Any]:
        response = self.client.get(url, headers=headers)
        response.raise_for_status()
        return cast(dict[str, Any], response.json())

    def _data_headers(self) -> dict[str, str]:
        return {"x-api-key": self.data_gov_api_key} if self.data_gov_api_key else {}

    def _lta_headers(self) -> dict[str, str]:
        return {"AccountKey": self.lta_account_key, "accept": "application/json"}

    def _flood_venues(self, payload: dict[str, Any]) -> frozenset[str]:
        alerts = cast(list[dict[str, Any]], payload.get("value", []))
        affected: set[str] = set()
        for alert in alerts:
            if str(alert.get("msgType", "")).casefold() == "cancel":
                continue
            circle = str(alert.get("circle", "")).split()
            if len(circle) != 2 or "," not in circle[0]:
                continue
            lat_text, lng_text = circle[0].split(",", maxsplit=1)
            radius_m = float(circle[1]) * 1_000
            point = self._point(lat_text, lng_text)
            affected.update(
                venue.id
                for venue in self.catalog.all()
                if self._venue_in_radius(venue, point, radius_m)
            )
        return frozenset(affected)

    @staticmethod
    def _train_disruptions(payload: dict[str, Any]) -> frozenset[str]:
        records = cast(list[dict[str, Any]], payload.get("value", []))
        return frozenset(
            str(record.get("Line", "unknown"))
            for record in records
            if int(record.get("Status", 1)) == 2
        )

    @staticmethod
    def _point(lat_text: str, lng_text: str) -> Location:
        return Location(lat=float(lat_text), lng=float(lng_text))

    @staticmethod
    def _venue_in_radius(venue: Venue, point: Location, radius_m: float) -> bool:
        return distance_metres(venue.location, point) <= radius_m
