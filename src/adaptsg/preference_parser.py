"""Bedrock-backed constraint extraction with a deterministic offline fallback."""

from __future__ import annotations

import json
import logging
import re
from datetime import date, time
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
from adaptsg.settings import Settings
from adaptsg.tools.catalog import VenueCatalog

LOGGER = logging.getLogger(__name__)


class PreferenceParser(Protocol):
    def parse(self, prompt: str, *, journey_date: date) -> ParseOutcome: ...


class ConstraintExtraction(StrictModel):
    start_label: str = "Toa Payoh"
    wheelchair_accessible_required: bool = True
    max_walking_distance_m: int = Field(default=400, ge=0, le=2_000)
    lunch_latest: time = time(13, 0)
    finish_by: time = time(17, 0)
    total_budget_sgd: float = Field(default=70, ge=0, le=1_000)
    rest_interval_minutes: int = Field(default=90, ge=20, le=240)
    required_venue_ids: frozenset[str] = frozenset()
    preferred_venue_ids: frozenset[str] = frozenset()
    preferred_categories: tuple[VenueCategory, ...] = (VenueCategory.INDOOR_MUSEUM,)
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
        "artscience": "artscience-museum",
        "botanic gardens": "botanic-gardens",
        "national museum": "national-museum",
    }

    def __init__(self, catalog: VenueCatalog) -> None:
        self.catalog = catalog

    def parse(self, prompt: str, *, journey_date: date) -> ParseOutcome:
        lowered = prompt.casefold()
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
            prefer_public_transport="taxi" not in lowered,
            minimise_cost="budget" in lowered or "cost" in lowered,
            avoid_crowds="avoid crowds" in lowered,
            scenic_route="scenic" in lowered,
            start_label=self._start_label(prompt),
            start_time=self._time_range(prompt)[0],
            finish_by=self._time_range(prompt)[1],
            lunch_latest=self._lunch_time(prompt),
        )
        return ParseOutcome(
            request=extraction.to_request(journey_date),
            source="deterministic_fallback_v1",
            warnings=("Bedrock was not called; review extracted constraints before use.",),
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

    @staticmethod
    def _integer(text: str, pattern: str, default: int) -> int:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        return int(match.group(1)) if match else default

    @staticmethod
    def _start_label(prompt: str) -> str:
        match = re.search(
            r"(?:starting|start)\s+(?:from|at)\s+([A-Za-z ]+?)(?:[,.]|$)",
            prompt,
            flags=re.IGNORECASE,
        )
        return match.group(1).strip() if match else "Toa Payoh"

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
    def _lunch_time(prompt: str) -> time:
        match = re.search(
            r"lunch\s+(?:before|by)\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)",
            prompt,
            flags=re.IGNORECASE,
        )
        if not match:
            return time(13, 0)
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
        if self.settings.adaptsg_mode == "demo":
            return self.fallback.parse(prompt, journey_date=journey_date)
        try:
            response = self._bedrock_client().converse(
                modelId=self.settings.bedrock_model_id,
                system=[{"text": self._system_prompt()}],
                messages=[{"role": "user", "content": [{"text": prompt}]}],
                inferenceConfig={
                    "maxTokens": self.settings.bedrock_max_tokens,
                    "temperature": 0,
                },
            )
            content = response["output"]["message"]["content"]
            text = next(item["text"] for item in content if "text" in item)
            extraction = ConstraintExtraction.model_validate_json(self._clean_json(text))
            usage = response.get("usage", {})
            return ParseOutcome(
                request=extraction.to_request(journey_date),
                source=f"bedrock:{self.settings.bedrock_model_id}",
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

    def _bedrock_client(self) -> Any:
        if self._client is None:
            session = boto3.Session(
                profile_name=self.settings.aws_profile or None,
                region_name=self.settings.aws_region,
            )
            self._client = session.client("bedrock-runtime")
        return self._client

    def _system_prompt(self) -> str:
        venue_ids = ", ".join(venue.id for venue in self.catalog.all())
        schema = json.dumps(ConstraintExtraction.model_json_schema(), separators=(",", ":"))
        return (
            "Extract travel constraints as one JSON object matching the supplied schema. "
            "Never diagnose health conditions. Fatigue only affects walking and rest needs. "
            "A venue mention or wording such as 'would like to visit' is a soft preference. "
            "Put a venue in required_venue_ids only when the user explicitly says must, "
            "required, or cannot miss; otherwise put it in preferred_venue_ids. "
            "Do not invent venue ids; required_venue_ids may contain only these ids: "
            f"{venue_ids}. Use conservative defaults for omitted fields. Schema: {schema}"
        )

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
