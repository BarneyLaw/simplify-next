"""Journey state storage adapters used by the API boundary."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

from adaptsg.domain import JourneyState


class JourneyStore(Protocol):
    def get(self, journey_id: UUID) -> JourneyState | None: ...

    def save(self, state: JourneyState) -> None: ...

    def get_operation(self, key: str) -> JourneyState | None: ...

    def save_operation(self, key: str, state: JourneyState) -> None: ...


class InMemoryJourneyStore:
    """Deterministic store for local and fixture-backed demonstrations."""

    def __init__(self) -> None:
        self._journeys: dict[UUID, JourneyState] = {}
        self._operations: dict[str, JourneyState] = {}

    def get(self, journey_id: UUID) -> JourneyState | None:
        return self._journeys.get(journey_id)

    def save(self, state: JourneyState) -> None:
        self._journeys[state.id] = state

    def get_operation(self, key: str) -> JourneyState | None:
        return self._operations.get(key)

    def save_operation(self, key: str, state: JourneyState) -> None:
        self._operations[key] = state


class DynamoDBJourneyStore:
    """Store typed journey snapshots as JSON with a DynamoDB TTL attribute."""

    def __init__(
        self, table_name: str, *, ttl_hours: int = 24, region_name: str | None = None
    ) -> None:
        import boto3

        self._table = boto3.resource("dynamodb", region_name=region_name).Table(table_name)
        self._ttl_hours = ttl_hours

    def get(self, journey_id: UUID) -> JourneyState | None:
        item = self._table.get_item(Key={"journey_id": str(journey_id)}).get("Item")
        if item is None:
            return None
        return JourneyState.model_validate_json(item["state"])

    def get_operation(self, key: str) -> JourneyState | None:
        item = self._table.get_item(Key={"journey_id": f"operation#{key}"}).get("Item")
        if item is None:
            return None
        return JourneyState.model_validate_json(item["state"])

    def save(self, state: JourneyState) -> None:
        expires_at = int((datetime.now(UTC) + timedelta(hours=self._ttl_hours)).timestamp())
        self._table.put_item(
            Item={
                "journey_id": str(state.id),
                "state": state.model_dump_json(),
                "expires_at": expires_at,
            }
        )

    def save_operation(self, key: str, state: JourneyState) -> None:
        expires_at = int((datetime.now(UTC) + timedelta(hours=self._ttl_hours)).timestamp())
        self._table.put_item(
            Item={
                "journey_id": f"operation#{key}",
                "state": state.model_dump_json(),
                "expires_at": expires_at,
            }
        )
