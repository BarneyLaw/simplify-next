"""Environmental snapshot adapters used by monitoring and replanning."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, Protocol, cast

import httpx

from adaptsg.domain import (
    EnvironmentSnapshot,
    FreshnessStatus,
    Location,
    ToolResult,
    Venue,
)
from adaptsg.errors import ToolUnavailable
from adaptsg.tools.catalog import VenueCatalog
from adaptsg.tools.freshness import (
    FreshnessKind,
    classify_freshness,
    failed_result,
    successful_result,
)
from adaptsg.tools.routing import distance_metres

LOGGER = logging.getLogger(__name__)


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
        self.client = client

    def current(self) -> EnvironmentSnapshot:
        if not self.lta_account_key:
            raise ToolUnavailable("LTA_ACCOUNT_KEY is required for live flood verification")
        if self.client is None:
            self.client = httpx.Client(timeout=8)
        try:
            weather = self._get(self.weather_url, self._data_headers())
            psi = self._get(self.psi_url, self._data_headers())
            floods = self._get(self.flood_url, self._lta_headers())
            train = self._get(self.train_url, self._lta_headers())
            LOGGER.info("LTA train-alert response: %s", train)
            weather_record = cast(dict[str, Any], weather["data"]["records"][0])
            psi_item = cast(dict[str, Any], psi["data"]["items"][0])
            weather_summary = str(weather_record["general"]["forecast"]["text"])
            psi_regions = cast(dict[str, Any], psi_item["readings"]["psi_twenty_four_hourly"])
            psi_value = max(int(value) for value in psi_regions.values())
            observed_at = max(
                datetime.fromisoformat(str(weather_record["updatedTimestamp"])),
                datetime.fromisoformat(str(psi_item["updatedTimestamp"])),
            )
            source_freshness = (
                classify_freshness(
                    datetime.fromisoformat(str(weather_record["updatedTimestamp"])),
                    FreshnessKind.WEATHER,
                ),
                classify_freshness(
                    datetime.fromisoformat(str(psi_item["updatedTimestamp"])),
                    FreshnessKind.PSI,
                ),
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
            freshness=(
                FreshnessStatus.STALE
                if FreshnessStatus.STALE in source_freshness
                else FreshnessStatus.FRESH
            ),
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
        client = self.client
        if client is None:
            raise ToolUnavailable("live environment client is unavailable")
        response = client.get(url, headers=headers)
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
        raw_records = payload.get("value", [])
        if isinstance(raw_records, dict):
            try:
                status = int(raw_records.get("Status", 1))
            except (TypeError, ValueError) as exc:
                raise ValueError("LTA train-alert response contained an invalid status") from exc
            if status != 2:
                return frozenset()
            affected_segments = raw_records.get("AffectedSegments", [])
            if not isinstance(affected_segments, list):
                raise ValueError("LTA train-alert response contained invalid affected segments")
            labels = {
                str(segment.get("Line") or segment.get("Route") or "unknown")
                if isinstance(segment, dict)
                else str(segment)
                for segment in affected_segments
            }
            return frozenset(labels or {"unknown"})
        if not isinstance(raw_records, list):
            raise ValueError("LTA train-alert response contained an invalid value list")

        disruptions: set[str] = set()
        for record in raw_records:
            if not isinstance(record, dict):
                raise ValueError("LTA train-alert response contained an invalid record")
            try:
                status = int(record.get("Status", 1))
            except (TypeError, ValueError) as exc:
                raise ValueError("LTA train-alert response contained an invalid status") from exc
            if status == 2:
                disruptions.add(str(record.get("Line", "unknown")))
        return frozenset(disruptions)

    @staticmethod
    def _point(lat_text: str, lng_text: str) -> Location:
        return Location(lat=float(lat_text), lng=float(lng_text))

    @staticmethod
    def _venue_in_radius(venue: Venue, point: Location, radius_m: float) -> bool:
        return distance_metres(venue.location, point) <= radius_m
