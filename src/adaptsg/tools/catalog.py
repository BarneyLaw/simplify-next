"""Read-only access to the curated, version-controlled venue catalog."""

from __future__ import annotations

import json
from importlib.resources import files

from pydantic import TypeAdapter

from adaptsg.domain import AccessibilityStatus, Venue, VenueCategory


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
            if wheelchair_required and venue.accessibility_status is not AccessibilityStatus.VERIFIED:
                continue
            if indoor_only and not venue.indoor:
                continue
            if categories and venue.category not in categories:
                continue
            eligible.append(venue)
        return tuple(eligible)

