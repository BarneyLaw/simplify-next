"""Read-only access to the curated, version-controlled venue catalog."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from importlib.resources import files

from pydantic import TypeAdapter

from adaptsg.domain import (
    AccessibilityResult,
    AccessibilityStatus,
    ToolResult,
    Venue,
    VenueSearchFilters,
    VenueCategory,
)
from adaptsg.tools.freshness import FreshnessKind, successful_result


class VenueCatalog:
    def __init__(self, venues: tuple[Venue, ...] | None = None) -> None:
        self._venues = venues or self._load_packaged_venues()
        self._by_id = {venue.id: venue for venue in self._venues}

    @staticmethod
    def _load_packaged_venues() -> tuple[Venue, ...]:
        payload = files("adaptsg.data").joinpath("venues.json").read_text(encoding="utf-8")
        return TypeAdapter(tuple[Venue, ...]).validate_python(json.loads(payload))

    def all(self) -> tuple[Venue, ...]:
        return self._venues

    def get(self, venue_id: str) -> Venue:
        try:
            return self._by_id[venue_id]
        except KeyError as exc:
            raise KeyError(f"unknown venue: {venue_id}") from exc

    def get_accessibility(self, venue_id: str) -> AccessibilityResult:
        venue = self.get(venue_id)
        return AccessibilityResult(
            location=venue.location,
            status=venue.accessibility_status,
            source=venue.accessibility_source,
            source_timestamp=datetime.now(UTC),
            freshness="fixture",
            is_fixture=True,
        )

    def get_accessibility_result(self, venue_id: str) -> ToolResult[AccessibilityResult]:
        evidence = self.get_accessibility(venue_id)
        return successful_result(
            evidence,
            source=evidence.source or "curated_demo_dataset",
            source_timestamp=evidence.source_timestamp,
            kind=FreshnessKind.VENUE,
            is_fixture=True,
        )

    def eligible(
        self,
        *,
        wheelchair_required: bool,
        indoor_only: bool = False,
        excluded_ids: frozenset[str] = frozenset(),
        categories: tuple[VenueCategory, ...] = (),
    ) -> tuple[Venue, ...]:
        eligible = []
        for venue in self._venues:
            if venue.id in excluded_ids:
                continue
            if (
                wheelchair_required
                and venue.accessibility_status is not AccessibilityStatus.VERIFIED
            ):
                continue
            if indoor_only and not venue.indoor:
                continue
            if categories and venue.category not in categories:
                continue
            eligible.append(venue)
        return tuple(eligible)

    def search(self, filters: VenueSearchFilters) -> tuple[Venue, ...]:
        return tuple(
            venue
            for venue in self._venues
            if venue.id not in filters.excluded_ids
            and (not filters.wheelchair_required or venue.accessibility_status is AccessibilityStatus.VERIFIED)
            and (not filters.indoor_only or venue.indoor)
            and (not filters.categories or venue.category in filters.categories)
            and all(tag in venue.tags for tag in filters.tags)
        )

    def search_result(self, filters: VenueSearchFilters) -> ToolResult[tuple[Venue, ...]]:
        timestamp = datetime.now(UTC)
        return successful_result(
            self.search(filters),
            source="curated_demo_dataset",
            source_timestamp=timestamp,
            kind=FreshnessKind.VENUE,
            is_fixture=True,
        )

    def audit(self) -> tuple[str, ...]:
        """Return data-quality issues without silently correcting curated claims."""
        issues: list[str] = []
        for venue in self._venues:
            if not 1.1 <= venue.location.lat <= 1.5 or not 103.5 <= venue.location.lng <= 104.2:
                issues.append(f"{venue.id}: coordinates are outside Singapore bounds")
            if venue.opening_time >= venue.closing_time:
                issues.append(f"{venue.id}: opening hours are invalid")
            if venue.accessibility_status is AccessibilityStatus.VERIFIED and not venue.accessibility_source:
                issues.append(f"{venue.id}: verified accessibility lacks a source")
            if venue.estimated_cost_sgd < 0:
                issues.append(f"{venue.id}: cost cannot be negative")
        return tuple(issues)
