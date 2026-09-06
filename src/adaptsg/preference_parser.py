"""Bedrock-backed constraint extraction with a deterministic offline fallback."""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, time, timedelta
from typing import Any, ClassVar, Protocol

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from pydantic import Field, ValidationError

from adaptsg.domain import (
    HardConstraints,
    JourneyRequest,
    ParseOutcome,
    SoftPreferences,
    StrictModel,
    TokenUsage,
    VenueCategory,
)
from adaptsg.errors import NoFeasibleItinerary
from adaptsg.settings import Settings
from adaptsg.tools.catalog import VenueCatalog
from adaptsg.tools.origin import DEFAULT_ORIGIN_LABEL, is_vague_origin

LOGGER = logging.getLogger(__name__)

DEFAULTED_ORIGIN_WARNING = (
    f"No starting point was named, so the day is planned from {DEFAULT_ORIGIN_LABEL}. "
    "Name a station or address to start somewhere else."
)


def _defaulted_origin_warnings(prompt: str, start_label: str) -> tuple[str, ...]:
    """Disclose an origin the traveller never asked for, whichever parser chose it."""
    if start_label != DEFAULT_ORIGIN_LABEL:
        return ()
    if DeterministicPreferenceParser._explicit_start_label(prompt) is not None:
        return ()
    return (DEFAULTED_ORIGIN_WARNING,)


def _defaulted_lunch_warnings(prompt: str, start_time: time, lunch_latest: time) -> tuple[str, ...]:
    if DeterministicPreferenceParser._explicit_lunch_time(prompt) is not None:
        return ()
    if start_time < time(13):
        return ()
    return (
        f"No lunch deadline was named; it was defaulted to {lunch_latest.strftime('%H:%M')} "
        "for the afternoon start.",
    )


class PreferenceParser(Protocol):
    def parse(self, prompt: str, *, journey_date: date) -> ParseOutcome: ...


class ConstraintExtraction(StrictModel):
    start_label: str = DEFAULT_ORIGIN_LABEL
    wheelchair_accessible_required: bool = True
    max_walking_distance_m: int = Field(default=400, ge=0, le=2_000)
    lunch_latest: time = time(13, 0)
    finish_by: time = time(17, 0)
    total_budget_sgd: float = Field(default=70, ge=0, le=1_000)
    rest_interval_minutes: int = Field(default=90, ge=20, le=240)
    required_venue_ids: frozenset[str] = Field(
        default=frozenset(),
        description="Catalog venue ids explicitly described as must, required, or cannot miss.",
    )
    preferred_venue_ids: frozenset[str] = Field(
        default=frozenset(),
        description="Catalog venue ids the traveller would like, without mandatory wording.",
    )
    preferred_categories: tuple[VenueCategory, ...] = ()
    unmatched_place_names: tuple[str, ...] = Field(
        default=(),
        description=(
            "Named destinations or attractions that do not map to a supplied catalog venue id. "
            "Do not include the starting location."
        ),
    )
    prefer_public_transport: bool = True
    minimise_cost: bool = True
    avoid_crowds: bool = False
    scenic_route: bool = False
    start_time: time = time(10, 0)

    def to_request(self, journey_date: date) -> JourneyRequest:
        return JourneyRequest(
            journey_date=journey_date,
            start_time=self.start_time,
            start_label=self.start_label,
            hard=HardConstraints(
                wheelchair_accessible_required=self.wheelchair_accessible_required,
                max_walking_distance_m=self.max_walking_distance_m,
                lunch_latest=self.lunch_latest,
                finish_by=self.finish_by,
                total_budget_sgd=self.total_budget_sgd,
                rest_interval_minutes=self.rest_interval_minutes,
                required_venue_ids=self.required_venue_ids,
            ),
            soft=SoftPreferences(
                preferred_categories=self.preferred_categories,
                preferred_venue_ids=self.preferred_venue_ids,
                prefer_public_transport=self.prefer_public_transport,
                minimise_cost=self.minimise_cost,
                avoid_crowds=self.avoid_crowds,
                scenic_route=self.scenic_route,
            ),
        )


class DeterministicPreferenceParser:
    """Conservative extraction for CI and credential-free demonstrations."""

    VENUE_ALIASES: ClassVar[dict[str, str]] = {
        "gardens by the bay": "gardens-bay-outdoor",
        "national gallery": "national-gallery",
        "asian civilisations museum": "asian-civilisations-museum",
        "peranakan museum": "peranakan-museum",
        "artscience": "artscience-museum",
        "botanic gardens": "botanic-gardens",
        "national museum": "national-museum",
        "cloud forest": "cloud-forest",
        "flower dome": "flower-dome",
        "marina barrage": "marina-barrage",
        "fort canning": "fort-canning-park",
        "esplanade": "esplanade",
        "library@orchard": "library-orchard",
        "library orchard": "library-orchard",
        "jewel": "jewel-changi",
        "science centre": "science-centre",
        "funan": "funan-food-court",
        "marina bay food hall": "marina-bay-food-hall",
        "toa payoh food hub": "toa-payoh-food-hub",
    }

    CATEGORY_KEYWORDS: ClassVar[dict[VenueCategory, tuple[str, ...]]] = {
        VenueCategory.INDOOR_MUSEUM: ("museum", "gallery", "art", "heritage"),
        VenueCategory.INDOOR_ATTRACTION: (
            "indoor",
            "air-conditioned",
            "air conditioned",
            "science",
        ),
        VenueCategory.OUTDOOR_ATTRACTION: ("outdoor", "park", "scenic"),
        VenueCategory.GARDEN: ("garden", "nature", "botanic"),
        VenueCategory.FOOD: ("food", "lunch", "eat", "meal"),
        VenueCategory.REST: ("rest", "relax", "relaxed", "quiet", "seating"),
    }

    def __init__(self, catalog: VenueCatalog) -> None:
        self.catalog = catalog

    def parse(self, prompt: str, *, journey_date: date) -> ParseOutcome:
        lowered = prompt.casefold()
        start_time, finish_by = self._time_range(prompt)
        mentioned_venue_ids = self._mentioned_venue_ids(lowered)
        required_venue_ids = self._required_venue_ids(lowered)
        extraction = ConstraintExtraction(
            wheelchair_accessible_required=not (
                "no wheelchair" in lowered or "wheelchair not required" in lowered
            ),
            max_walking_distance_m=self._integer(
                lowered,
                r"(?:walk|walking)[^\d]{0,30}(\d{2,4})\s*(?:m|metres?|meters?)",
                400,
            ),
            total_budget_sgd=float(
                self._integer(
                    lowered,
                    r"(?:budget|below|under)[^\d$]{0,20}\$?\s*(\d{1,4})",
                    70,
                )
            ),
            rest_interval_minutes=self._integer(
                lowered, r"rest[^\d]{0,20}(\d{2,3})\s*(?:minutes?|mins?)", 90
            ),
            required_venue_ids=required_venue_ids,
            preferred_venue_ids=mentioned_venue_ids - required_venue_ids,
            preferred_categories=self._preferred_categories(lowered),
            prefer_public_transport="taxi" not in lowered,
            minimise_cost="budget" in lowered or "cost" in lowered,
            avoid_crowds="avoid crowds" in lowered,
            scenic_route="scenic" in lowered,
            start_label=self._start_label(prompt),
            start_time=start_time,
            finish_by=finish_by,
            lunch_latest=self._lunch_time(prompt, start_time=start_time, finish_by=finish_by),
        )
        return ParseOutcome(
            request=extraction.to_request(journey_date),
            source="deterministic_fallback_v1",
            warnings=(
                "Bedrock was not called; review extracted constraints before use.",
                *_defaulted_origin_warnings(prompt, extraction.start_label),
                *_defaulted_lunch_warnings(prompt, extraction.start_time, extraction.lunch_latest),
            ),
        )

    def _mentioned_venue_ids(self, lowered: str) -> frozenset[str]:
        found = {venue_id for alias, venue_id in self.VENUE_ALIASES.items() if alias in lowered}
        found.update(venue.id for venue in self.catalog.all() if venue.name.casefold() in lowered)
        return frozenset(found)

    def _required_venue_ids(self, lowered: str) -> frozenset[str]:
        required = set()
        mandatory = r"(?:must(?:\s+visit)?|required|cannot\s+miss)"
        for venue_id in self._mentioned_venue_ids(lowered):
            venue = self.catalog.get(venue_id)
            aliases = (
                venue.name.casefold(),
                venue_id.replace("-", " "),
                *(alias for alias, target in self.VENUE_ALIASES.items() if target == venue_id),
            )
            for alias in aliases:
                escaped_alias = re.escape(alias)
                pattern = (
                    rf"(?:{mandatory})[^.!?]{{0,80}}{escaped_alias}"
                    rf"|{escaped_alias}\s+(?:is\s+)?(?:required|a\s+must(?:-see)?)"
                )
                if re.search(pattern, lowered):
                    required.add(venue_id)
        return frozenset(required)

    @classmethod
    def _preferred_categories(cls, lowered: str) -> tuple[VenueCategory, ...]:
        return tuple(
            category
            for category, keywords in cls.CATEGORY_KEYWORDS.items()
            if any(re.search(rf"\b{re.escape(keyword)}\b", lowered) for keyword in keywords)
        )

    @staticmethod
    def _integer(text: str, pattern: str, default: int) -> int:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        return int(match.group(1)) if match else default

    @staticmethod
    def _start_label(prompt: str) -> str:
        return DeterministicPreferenceParser._explicit_start_label(prompt) or DEFAULT_ORIGIN_LABEL

    @staticmethod
    def _explicit_start_label(prompt: str) -> str | None:
        """Capture the origin phrase only, not the rest of the sentence.

        The terminator has to include clause openers: "start at X and finish by
        5pm" would otherwise capture everything up to the full stop.
        """
        terminator = (
            r"(?:[,.;:]|\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)\b|"
            r"\s+(?:and|at|by|around|before|after|then|today|tomorrow|to\s+visit)\b|$)"
        )
        patterns = (
            rf"(?:starting|start)\s+(?:from|at)\s+([A-Za-z0-9 &'()/-]+?){terminator}",
            rf"\bfrom\s+([A-Za-z0-9 &'()/-]+?){terminator}",
        )
        for pattern in patterns:
            match = re.search(pattern, prompt, flags=re.IGNORECASE)
            if match:
                return match.group(1).strip()
        return None

    @staticmethod
    def _time_range(prompt: str) -> tuple[time, time]:
        match = re.search(
            r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)\s*(?:-|\u2013|\u2014|to)\s*"
            r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)",
            prompt,
            flags=re.IGNORECASE,
        )
        if not match:
            return time(10, 0), time(17, 0)
        return (
            DeterministicPreferenceParser._clock(match.group(1), match.group(2), match.group(3)),
            DeterministicPreferenceParser._clock(match.group(4), match.group(5), match.group(6)),
        )

    @staticmethod
    def _lunch_time(prompt: str, *, start_time: time, finish_by: time) -> time:
        explicit = DeterministicPreferenceParser._explicit_lunch_time(prompt)
        if explicit is not None:
            return explicit
        if start_time < time(13):
            return time(13)
        adjusted = (datetime.combine(date.min, start_time) + timedelta(hours=1)).time()
        return min(adjusted, finish_by)

    @staticmethod
    def _explicit_lunch_time(prompt: str) -> time | None:
        match = re.search(
            r"lunch\s+(?:before|by)\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)",
            prompt,
            flags=re.IGNORECASE,
        )
        if not match:
            return None
        return DeterministicPreferenceParser._clock(match.group(1), match.group(2), match.group(3))

    @staticmethod
    def _clock(hour: str, minute: str | None, meridiem: str) -> time:
        hour_value = int(hour) % 12
        if meridiem.casefold() == "pm":
            hour_value += 12
        return time(hour_value, int(minute or 0))


class BedrockPreferenceParser:
    """Use Bedrock for typed extraction, falling back safely if configured to do so."""

    def __init__(
        self,
        *,
        settings: Settings,
        catalog: VenueCatalog,
        client: Any | None = None,
        allow_fallback: bool = True,
    ) -> None:
        self.settings = settings
        self.catalog = catalog
        self.fallback = DeterministicPreferenceParser(catalog)
        self.allow_fallback = allow_fallback
        self._client = client

    def parse(self, prompt: str, *, journey_date: date) -> ParseOutcome:
        if not self.settings.adaptsg_bedrock_enabled:
            return self.fallback.parse(prompt, journey_date=journey_date)
        try:
            response = self._bedrock_client().converse(
                modelId=self.settings.bedrock_model_id,
                system=[{"text": self._system_prompt()}],
                messages=[{"role": "user", "content": [{"text": prompt}]}],
                toolConfig=self._tool_config(),
                inferenceConfig={
                    "maxTokens": self.settings.bedrock_max_tokens,
                    "temperature": 0,
                },
            )
            content = response["output"]["message"]["content"]
            extraction = self._extract_response(content)
            extraction = self._reconcile_start_label(prompt, extraction)
            extraction = self._reconcile_lunch_deadline(prompt, extraction)
            extraction = self._reconcile_venue_preferences(prompt, extraction)
            if extraction.unmatched_place_names:
                unavailable = ", ".join(extraction.unmatched_place_names)
                raise NoFeasibleItinerary(
                    "the verified venue catalog does not include the requested place(s): "
                    f"{unavailable}; choose a supported venue or describe a venue category"
                )
            usage = response.get("usage", {})
            return ParseOutcome(
                request=extraction.to_request(journey_date),
                source=f"bedrock:{self.settings.bedrock_model_id}",
                warnings=(
                    *_defaulted_origin_warnings(prompt, extraction.start_label),
                    *_defaulted_lunch_warnings(
                        prompt, extraction.start_time, extraction.lunch_latest
                    ),
                ),
                token_usage=TokenUsage(
                    input_tokens=int(usage.get("inputTokens", 0)),
                    output_tokens=int(usage.get("outputTokens", 0)),
                ),
            )
        except (
            BotoCoreError,
            ClientError,
            KeyError,
            StopIteration,
            ValidationError,
            ValueError,
        ) as exc:
            if not self.allow_fallback:
                raise
            LOGGER.warning("Bedrock extraction failed; using deterministic fallback", exc_info=exc)
            fallback = self.fallback.parse(prompt, journey_date=journey_date)
            return fallback.model_copy(
                update={
                    "warnings": (
                        "Live Bedrock extraction failed; deterministic fallback was used.",
                    )
                }
            )

    @staticmethod
    def _reconcile_start_label(
        prompt: str, extraction: ConstraintExtraction
    ) -> ConstraintExtraction:
        """Keep the origin the user actually typed, unless it names no place.

        The regex sees the literal phrasing, which is what the traveller has to
        recognise in the plan; normalising it for the gazetteer is the
        resolver's job, not the parser's. A vague capture ("a convenient MRT
        station") is the one case where the model's reading is worth more.
        """
        explicit = DeterministicPreferenceParser._explicit_start_label(prompt)
        if explicit is None or is_vague_origin(explicit):
            return extraction.model_copy(update={"start_label": DEFAULT_ORIGIN_LABEL})
        return extraction.model_copy(update={"start_label": explicit})

    @staticmethod
    def _reconcile_lunch_deadline(
        prompt: str, extraction: ConstraintExtraction
    ) -> ConstraintExtraction:
        lunch_latest = DeterministicPreferenceParser._lunch_time(
            prompt,
            start_time=extraction.start_time,
            finish_by=extraction.finish_by,
        )
        return extraction.model_copy(update={"lunch_latest": lunch_latest})

    def _reconcile_venue_preferences(
        self, prompt: str, extraction: ConstraintExtraction
    ) -> ConstraintExtraction:
        """Do not let model wording turn a preference into a hard requirement."""
        lowered = prompt.casefold()
        known_ids = {venue.id for venue in self.catalog.all()}
        model_ids = extraction.required_venue_ids | extraction.preferred_venue_ids
        unknown_ids = model_ids - known_ids
        explicit_required = self.fallback._required_venue_ids(lowered)
        preferred = (model_ids | self.fallback._mentioned_venue_ids(lowered)) - explicit_required
        categories = tuple(
            dict.fromkeys(
                (*extraction.preferred_categories, *self.fallback._preferred_categories(lowered))
            )
        )
        unmatched = tuple(dict.fromkeys((*extraction.unmatched_place_names, *sorted(unknown_ids))))
        return extraction.model_copy(
            update={
                "required_venue_ids": explicit_required,
                "preferred_venue_ids": frozenset(preferred & known_ids),
                "preferred_categories": categories,
                "unmatched_place_names": unmatched,
            }
        )

    def _bedrock_client(self) -> Any:
        if self._client is None:
            session = boto3.Session(
                profile_name=self.settings.aws_profile or None,
                region_name=self.settings.bedrock_region or self.settings.aws_region,
            )
            self._client = session.client("bedrock-runtime")
        return self._client

    def _system_prompt(self) -> str:
        venue_ids = ", ".join(venue.id for venue in self.catalog.all())
        return (
            "Extract travel constraints by calling the supplied tool exactly once. "
            "Never diagnose health conditions. Fatigue only affects walking and rest needs. "
            "A venue mention or wording such as 'would like to visit' is a soft preference. "
            "Put a venue in required_venue_ids only when the user explicitly says must, "
            "required, or cannot miss; otherwise put it in preferred_venue_ids. "
            "Use unmatched_place_names for every named destination that is not in the catalog, "
            "but never put the journey origin there. Do not invent venue ids; venue-id fields "
            f"may contain only these ids: {venue_ids}. "
            "Use conservative defaults for omitted fields."
        )

    @staticmethod
    def _tool_config() -> dict[str, object]:
        name = "record_travel_constraints"
        return {
            "tools": [
                {
                    "toolSpec": {
                        "name": name,
                        "description": "Record the traveller's typed itinerary constraints.",
                        "inputSchema": {"json": ConstraintExtraction.model_json_schema()},
                    }
                }
            ],
            "toolChoice": {"tool": {"name": name}},
        }

    @staticmethod
    def _extract_response(content: list[dict[str, Any]]) -> ConstraintExtraction:
        for item in content:
            tool_use = item.get("toolUse")
            if isinstance(tool_use, dict) and isinstance(tool_use.get("input"), dict):
                return ConstraintExtraction.model_validate(tool_use["input"])
        text = next(item["text"] for item in content if "text" in item)
        return ConstraintExtraction.model_validate_json(BedrockPreferenceParser._clean_json(text))

    @staticmethod
    def _clean_json(text: str) -> str:
        stripped = text.strip()
        if stripped.startswith("```"):
            stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
            stripped = re.sub(r"\s*```$", "", stripped)
        start, end = stripped.find("{"), stripped.rfind("}")
        if start < 0 or end < start:
            raise ValueError("Bedrock response did not contain a JSON object")
        return stripped[start : end + 1]
