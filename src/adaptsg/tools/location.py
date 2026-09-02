"""Typed location-search adapters for origins and destinations."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol, cast

import httpx

from adaptsg.domain import Location, LocationSearchResult, ToolResult
from adaptsg.errors import ToolUnavailable
from adaptsg.tools.freshness import FreshnessKind, failed_result, successful_result


class LocationClient(Protocol):
    def search(self, query: str) -> tuple[LocationSearchResult, ...]: ...


class DemoLocationClient:
    """Deterministic origin lookup for the offline demo."""

    _locations = {
        "toa payoh": Location(lat=1.3323, lng=103.8474),
        "city hall": Location(lat=1.2931, lng=103.8520),
    }

    def search(self, query: str) -> tuple[LocationSearchResult, ...]:
        clean_query = query.strip()
        location = self._locations.get(clean_query.casefold())
        if not clean_query or location is None:
            return ()
        return (
            LocationSearchResult(
                label=clean_query,
                location=location,
                source="demo_location_fixture_v1",
                source_timestamp=datetime.now(UTC),
                freshness="fixture",
                is_fixture=True,
            ),
        )

    def search_result(self, query: str) -> ToolResult[tuple[LocationSearchResult, ...]]:
        timestamp = datetime.now(UTC)
        return successful_result(
            self.search(query),
            source="demo_location_fixture_v1",
            source_timestamp=timestamp,
            kind=FreshnessKind.LOCATION,
            is_fixture=True,
        )


class OneMapLocationClient:
    """Live OneMap address search with strict response parsing."""

    base_url = "https://www.onemap.gov.sg/api/common/elastic/search"

    def __init__(self, *, token: str, client: httpx.Client | None = None) -> None:
        self.token = token
        self.client = client or httpx.Client(timeout=8)

    def search(self, query: str) -> tuple[LocationSearchResult, ...]:
        if not query.strip():
            return ()
        if not self.token:
            raise ToolUnavailable("ONEMAP_API_TOKEN is required in live mode")
        try:
            response = self.client.get(
                self.base_url,
                params={
                    "searchVal": query.strip(),
                    "returnGeom": "Y",
                    "getAddrDetails": "Y",
                    "pageNum": 1,
                    "token": self.token,
                },
            )
            response.raise_for_status()
            payload = cast(dict[str, Any], response.json())
            results = cast(list[dict[str, Any]], payload["results"])
            timestamp = datetime.now(UTC)
            return tuple(
                LocationSearchResult(
                    label=str(item["SEARCHVAL"]),
                    location=Location(
                        lat=float(item["LATITUDE"]),
                        lng=float(item["LONGITUDE"]),
                    ),
                    source="onemap_search",
                    source_timestamp=timestamp,
                    freshness="fresh",
                )
                for item in results
            )
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise ToolUnavailable(f"OneMap location verification failed: {exc}") from exc

    def search_result(self, query: str) -> ToolResult[tuple[LocationSearchResult, ...]]:
        try:
            results = self.search(query)
        except ToolUnavailable as exc:
            return failed_result(
                source="onemap_search",
                error_code="location_unavailable",
                error_message=str(exc),
                kind=FreshnessKind.LOCATION,
            )
        timestamp = datetime.now(UTC)
        return successful_result(
            results,
            source="onemap_search",
            source_timestamp=timestamp,
            kind=FreshnessKind.LOCATION,
        )