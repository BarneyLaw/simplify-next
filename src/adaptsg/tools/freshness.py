"""Shared freshness policy and provenance envelopes for tool adapters."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import TypeVar

from adaptsg.domain import FreshnessStatus, ToolResult

T = TypeVar("T")


class FreshnessKind(StrEnum):
    LOCATION = "location"
    ROUTE = "route"
    WEATHER = "weather"
    PSI = "psi"
    FLOOD = "flood"
    TRANSPORT = "transport"
    VENUE = "venue"


FRESHNESS_LIMITS: dict[FreshnessKind, timedelta] = {
    FreshnessKind.LOCATION: timedelta(minutes=15),
    FreshnessKind.ROUTE: timedelta(minutes=15),
    FreshnessKind.WEATHER: timedelta(hours=1),
    FreshnessKind.PSI: timedelta(hours=1),
    FreshnessKind.FLOOD: timedelta(minutes=15),
    FreshnessKind.TRANSPORT: timedelta(minutes=5),
    FreshnessKind.VENUE: timedelta(days=30),
}


def classify_freshness(
    source_timestamp: datetime,
    kind: FreshnessKind,
    *,
    now: datetime | None = None,
    is_fixture: bool = False,
) -> FreshnessStatus:
    if is_fixture:
        return FreshnessStatus.FIXTURE
    observed_at = source_timestamp
    reference = now or datetime.now(UTC)
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=UTC)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=UTC)
    if reference - observed_at > FRESHNESS_LIMITS[kind]:
        return FreshnessStatus.STALE
    return FreshnessStatus.FRESH


def successful_result[T](
    payload: T,
    *,
    source: str,
    source_timestamp: datetime,
    kind: FreshnessKind,
    is_fixture: bool = False,
    now: datetime | None = None,
) -> ToolResult[T]:
    return ToolResult(
        success=True,
        payload=payload,
        source=source,
        source_timestamp=source_timestamp,
        freshness=classify_freshness(source_timestamp, kind, now=now, is_fixture=is_fixture),
        is_fixture=is_fixture,
    )


def failed_result[T](
    *,
    source: str,
    error_code: str,
    error_message: str,
    kind: FreshnessKind,
    source_timestamp: datetime | None = None,
) -> ToolResult[T]:
    timestamp = source_timestamp or datetime.now(UTC)
    return ToolResult(
        success=False,
        source=source,
        source_timestamp=timestamp,
        freshness=FreshnessStatus.UNAVAILABLE,
        error_code=error_code,
        error_message=error_message,
    )
