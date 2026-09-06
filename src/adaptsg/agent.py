"""Bounded LangGraph orchestration and the public AdaptSG service facade."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from time import monotonic
from typing import Any, ClassVar, NotRequired, Protocol, TypedDict, cast, overload
from uuid import UUID, uuid4

import boto3
from botocore.exceptions import ClientError
from langgraph.graph import END, START, StateGraph

from adaptsg.domain import (
    ActionIntent,
    ActorRole,
    ApprovalDecision,
    AuditEvent,
    AuthorityGrant,
    Capability,
    CapabilityPolicy,
    ConsentPurpose,
    ConsentRecord,
    EnvironmentSnapshot,
    FeatureFlag,
    Itinerary,
    JourneyRequest,
    JourneyState,
    JourneyStatus,
    LocationSearchResult,
    MonitoringOutcome,
    ParseOutcome,
    PlanOutcome,
    PrincipalContext,
    ProposalStatus,
    ReplanProposal,
    ReplanTrigger,
    TransitionOutcome,
    TriggerType,
)
from adaptsg.errors import (
    ApprovalRequired,
    AuditUnavailable,
    AuthenticationRequired,
    AuthorityGrantRequired,
    AuthorizationDenied,
    CapabilityDisabled,
    ConsentRequired,
    IdempotencyConflict,
    IntentConflict,
    InvalidIdempotencyKey,
    InvalidJourneyTransition,
    JourneyNotFound,
    NoFeasibleItinerary,
    OperationInProgress,
    OriginNotVerified,
    RetentionConfigurationMissing,
    StaleJourneyVersion,
    ToolUnavailable,
)
from adaptsg.planning import JourneyPlanner, JourneyReplanner
from adaptsg.preference_parser import (
    BedrockPreferenceParser,
    DeterministicPreferenceParser,
    PreferenceParser,
)
from adaptsg.settings import Settings, get_settings
from adaptsg.tools.catalog import VenueCatalog
from adaptsg.tools.environment import (
    DemoEnvironmentClient,
    EnvironmentClient,
    LiveEnvironmentClient,
)
from adaptsg.tools.location import DemoLocationClient, LocationClient, OneMapLocationClient
from adaptsg.tools.origin import (
    DEFAULT_ORIGIN_LABEL,
    is_confident,
    is_vague_origin,
    origin_query_variants,
    rank_origin_candidates,
)
from adaptsg.tools.routing import DemoRoutingClient, OneMapRoutingClient
from adaptsg.validation import ItineraryValidator

LOGGER = logging.getLogger(__name__)
Clock = Callable[[], datetime]
Mutation = Callable[[], tuple[JourneyState, int | None]]
IDEMPOTENCY_KEY = re.compile(r"^[\x21-\x7e]{8,200}$")


class PlanGraphState(TypedDict):
    prompt: str
    journey_date: date
    start_label_override: NotRequired[str]
    parsed: NotRequired[ParseOutcome]
    itinerary: NotRequired[Itinerary]
    location_warnings: NotRequired[tuple[str, ...]]
    error: NotRequired[str]


class JourneyStore(Protocol):
    storage_mode: str

    def reserve(self, key_hash: str, fingerprint: str, expires_epoch: int) -> JourneyState | None:
        """Reserve a retry key, or return its completed response."""

    def get(self, journey_id: UUID) -> JourneyState: ...

    def commit(
        self,
        state: JourneyState,
        *,
        expected_version: int | None,
        key_hash: str,
        fingerprint: str,
        expires_epoch: int,
    ) -> None: ...

    def commit_with_audit(
        self,
        state: JourneyState,
        *,
        expected_version: int | None,
        key_hash: str,
        fingerprint: str,
        expires_epoch: int,
        audit: AuditStore,
        audit_event: AuditEvent,
        expected_previous_hash: str | None,
    ) -> AuditEvent: ...

    def release(self, key_hash: str, fingerprint: str) -> None: ...


class AuditStore(Protocol):
    def append(self, event: AuditEvent, *, expected_previous_hash: str | None) -> AuditEvent: ...

    def list(self, *, correlation_id: UUID | None = None) -> tuple[AuditEvent, ...]: ...

    def latest_hash(self, correlation_id: UUID) -> str | None: ...


class ConsentStore(Protocol):
    def create(self, record: ConsentRecord, *, idempotency_key: str) -> ConsentRecord: ...

    def get(self, consent_id: UUID) -> ConsentRecord: ...

    def find_current(
        self,
        *,
        subject: str,
        purpose: ConsentPurpose,
        categories: frozenset[str],
        policy_version: str | None,
        now: datetime,
    ) -> ConsentRecord | None: ...

    def revoke(self, consent_id: UUID, *, expected_version: int, at: datetime) -> ConsentRecord: ...


class AuthorityStore(Protocol):
    def put(self, grant: AuthorityGrant) -> AuthorityGrant: ...

    def get(self, subject: str, delegate: str) -> AuthorityGrant: ...

    def revoke(self, subject: str, delegate: str, *, at: datetime) -> AuthorityGrant: ...


class ActionIntentStore(Protocol):
    storage_mode: str

    def put(self, intent: ActionIntent) -> None: ...

    def get(self, intent_id: UUID) -> ActionIntent: ...

    def mark_used(self, intent: ActionIntent, *, replay_key: str, result: object) -> object: ...


class InMemoryAuditStore:
    """Deterministic append-only audit chain for demo and unit tests."""

    storage_mode = "memory_demo"

    def __init__(self) -> None:
        self._events: list[AuditEvent] = []
        self._lock = threading.RLock()
        self.fail_writes = False

    def append(self, event: AuditEvent, *, expected_previous_hash: str | None) -> AuditEvent:
        with self._lock:
            if self.fail_writes:
                raise AuditUnavailable("audit storage is unavailable")
            previous = next(
                (
                    candidate.event_hash
                    for candidate in reversed(self._events)
                    if candidate.correlation_id == event.correlation_id
                ),
                None,
            )
            if previous != expected_previous_hash:
                raise AuditUnavailable("audit chain changed; mutation must be retried")
            material = event.model_copy(update={"previous_hash": previous})
            event_hash = hashlib.sha256(
                material.model_dump_json(exclude={"event_hash"}).encode()
            ).hexdigest()
            stored = material.model_copy(update={"event_hash": event_hash})
            self._events.append(stored)
            return stored

    def list(self, *, correlation_id: UUID | None = None) -> tuple[AuditEvent, ...]:
        with self._lock:
            return tuple(
                event
                for event in self._events
                if correlation_id is None or event.correlation_id == correlation_id
            )

    def latest_hash(self, correlation_id: UUID) -> str | None:
        events = self.list(correlation_id=correlation_id)
        return events[-1].event_hash if events else None


class InMemoryConsentStore:
    storage_mode = "memory_demo"

    def __init__(self) -> None:
        self._records: dict[UUID, ConsentRecord] = {}
        self._idempotency: dict[str, tuple[str, ConsentRecord]] = {}
        self._lock = threading.RLock()

    def create(self, record: ConsentRecord, *, idempotency_key: str) -> ConsentRecord:
        with self._lock:
            fingerprint = record.model_dump_json(exclude={"id", "version"})
            old = self._idempotency.get(idempotency_key)
            if old is not None:
                if old[0] != fingerprint:
                    raise IdempotencyConflict("consent idempotency key conflicts")
                return old[1]
            self._records[record.id] = record
            self._idempotency[idempotency_key] = (fingerprint, record)
            return record

    def get(self, consent_id: UUID) -> ConsentRecord:
        try:
            return self._records[consent_id]
        except KeyError as exc:
            raise ConsentRequired("consent record was not found") from exc

    def find_current(
        self,
        *,
        subject: str,
        purpose: ConsentPurpose,
        categories: frozenset[str],
        policy_version: str | None,
        now: datetime,
    ) -> ConsentRecord | None:
        with self._lock:
            records = tuple(self._records.values())
        return next(
            (
                record
                for record in records
                if record.subject == subject
                and record.purpose is purpose
                and categories <= record.data_categories
                and (policy_version is None or record.policy_version == policy_version)
                and record.active_at(now)
            ),
            None,
        )

    def revoke(self, consent_id: UUID, *, expected_version: int, at: datetime) -> ConsentRecord:
        with self._lock:
            current = self.get(consent_id)
            if current.version != expected_version:
                raise StaleJourneyVersion(
                    "consent version changed", current_version=current.version
                )
            if current.revoked_at is not None:
                return current
            updated = current.model_copy(update={"revoked_at": at, "version": current.version + 1})
            self._records[consent_id] = updated
            return updated


class InMemoryAuthorityStore:
    storage_mode = "memory_demo"

    def __init__(self) -> None:
        self._grants: dict[str, AuthorityGrant] = {}

    def put(self, grant: AuthorityGrant) -> AuthorityGrant:
        self._grants[grant.delegate + ":" + grant.subject] = grant
        return grant

    def get(self, subject: str, delegate: str) -> AuthorityGrant:
        try:
            return self._grants[delegate + ":" + subject]
        except KeyError as exc:
            raise AuthorityGrantRequired("active authority grant is required") from exc

    def revoke(self, subject: str, delegate: str, *, at: datetime) -> AuthorityGrant:
        current = self.get(subject, delegate)
        updated = current.model_copy(update={"revoked_at": at})
        self._grants[delegate + ":" + subject] = updated
        return updated


class InMemoryActionIntentStore:
    storage_mode = "memory_demo"

    def __init__(self) -> None:
        self._intents: dict[UUID, ActionIntent] = {}
        self._results: dict[str, object] = {}
        self._lock = threading.RLock()

    def put(self, intent: ActionIntent) -> None:
        with self._lock:
            self._intents[intent.id] = intent

    def get(self, intent_id: UUID) -> ActionIntent:
        with self._lock:
            try:
                return self._intents[intent_id]
            except KeyError as exc:
                raise IntentConflict("action intent was not found") from exc

    def mark_used(self, intent: ActionIntent, *, replay_key: str, result: object) -> object:
        with self._lock:
            current = self.get(intent.id)
            if current.used_at is not None:
                if replay_key in self._results:
                    return self._results[replay_key]
                raise IntentConflict("action intent has already been used")
            self._intents[intent.id] = intent
            self._results[replay_key] = result
            return result


class CapabilityResolver:
    """Server-owned capability policy. Configuration cannot enable prohibited actions."""

    _flag_for: ClassVar[dict[Capability, FeatureFlag]] = {
        Capability.BOOKING_READ: FeatureFlag.BOOKING_READ,
        Capability.BOOKING_WRITE: FeatureFlag.BOOKING_WRITE,
        Capability.MEDICAL_INTAKE: FeatureFlag.MEDICAL_INTAKE,
        Capability.MEDICAL_CLINICIAN: FeatureFlag.MEDICAL_CLINICIAN,
        Capability.EMERGENCY_LIVE: FeatureFlag.EMERGENCY_LIVE,
        Capability.MULTI_AGENT: FeatureFlag.MULTI_AGENT,
    }

    def __init__(self, policy: CapabilityPolicy | None = None) -> None:
        self.policy = policy or CapabilityPolicy()

    def enabled(self, capability: Capability, *, production: bool = False) -> bool:
        if capability in {Capability.BOOKING_WRITE}:
            return False
        if capability in self.policy.disabled_capabilities or self.policy.kill_switch:
            return False
        flag = self._flag_for.get(capability)
        if flag is None:
            return True
        if production and not self.policy.production_retention_configured:
            return False
        return flag in self.policy.flags

    def require(self, capability: Capability, *, production: bool = False) -> None:
        if not self.enabled(capability, production=production):
            raise CapabilityDisabled(f"capability is disabled: {capability.value}")


class AuthorizationPolicy:
    def __init__(
        self,
        *,
        capabilities: CapabilityResolver | None = None,
        authority: AuthorityStore | None = None,
        consent: ConsentStore | None = None,
        consent_policy_version: str | None = None,
    ) -> None:
        self.capabilities = capabilities or CapabilityResolver()
        self.authority = authority or InMemoryAuthorityStore()
        self.consent = consent or InMemoryConsentStore()
        self.consent_policy_version = consent_policy_version

    def require(
        self,
        principal: PrincipalContext,
        capability: Capability,
        *,
        subject: str | None = None,
        purpose: ConsentPurpose | None = None,
        categories: frozenset[str] = frozenset(),
        scope: str | None = None,
        policy_version: str | None = None,
        production: bool = False,
        now: datetime | None = None,
    ) -> None:
        if not principal.authenticated or not principal.roles:
            raise AuthorizationDenied("authenticated principal is required")
        self.capabilities.require(capability, production=production)
        if subject is not None and principal.principal_id != subject:
            if ActorRole.CAREGIVER not in principal.roles:
                raise AuthorizationDenied("cross-account access is denied")
            grant = self.authority.get(subject, principal.principal_id)
            instant = now or datetime.now(UTC)
            if (
                not grant.active_at(instant)
                or capability not in grant.capabilities
                or (scope is not None and scope not in grant.scope)
            ):
                raise AuthorityGrantRequired("authority grant is expired, revoked, or out of scope")
        if purpose is not None:
            if subject is None:
                subject = principal.principal_id
            instant = now or datetime.now(UTC)
            required_version = policy_version or self.consent_policy_version
            if (
                self.consent.find_current(
                    subject=subject,
                    purpose=purpose,
                    categories=categories,
                    policy_version=required_version or None,
                    now=instant,
                )
                is None
            ):
                raise ConsentRequired("explicit current consent is required")


class ActionIntentService:
    """Server-issued, payload-bound, one-use intents; agents have no access to this service."""

    def __init__(
        self, *, clock: Clock | None = None, store: ActionIntentStore | None = None
    ) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self._store = store or InMemoryActionIntentStore()

    @property
    def storage_mode(self) -> str:
        return self._store.storage_mode

    def issue(
        self,
        *,
        principal: PrincipalContext,
        target: str,
        capability: Capability,
        payload: object,
        expected_state_version: int,
        required_approvals: frozenset[str] = frozenset(),
        source_expiry: datetime | None = None,
    ) -> ActionIntent:
        if not principal.authenticated or ActorRole.AGENT in principal.roles:
            raise AuthorizationDenied(
                "only an authenticated non-agent server principal may issue intents"
            )
        now = self._clock()
        payload_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
        ).hexdigest()
        expiry = (
            min(now + timedelta(minutes=5), source_expiry)
            if source_expiry
            else now + timedelta(minutes=5)
        )
        intent = ActionIntent(
            target=target,
            capability=capability,
            payload_hash=payload_hash,
            actor=principal.principal_id,
            expected_state_version=expected_state_version,
            issued_at=now,
            expires_at=expiry,
            nonce=uuid4().hex + uuid4().hex,
            required_approvals=required_approvals,
        )
        self._store.put(intent)
        return intent

    def consume(
        self,
        intent_id: UUID,
        *,
        principal: PrincipalContext,
        payload: object,
        state_version: int,
        result: object,
    ) -> object:
        intent = self._store.get(intent_id)
        now = self._clock()
        payload_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
        ).hexdigest()
        if intent.actor != principal.principal_id or intent.payload_hash != payload_hash:
            raise IntentConflict("action intent actor or payload does not match")
        if intent.expected_state_version != state_version:
            raise IntentConflict("action intent targets a stale state version")
        if now >= intent.expires_at:
            raise IntentConflict("action intent has expired")
        replay_key = intent.nonce + ":" + payload_hash
        updated = intent.model_copy(update={"used_at": now})
        return self._store.mark_used(updated, replay_key=replay_key, result=result)


@dataclass
class _MemoryReplay:
    fingerprint: str
    expires_epoch: int
    response: JourneyState | None = None


class InMemoryJourneyStore:
    """Process-local, thread-safe persistence for labelled demo mode."""

    storage_mode = "memory_demo"

    def __init__(self, *, clock: Clock | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self._journeys: dict[UUID, JourneyState] = {}
        self._replays: dict[str, _MemoryReplay] = {}
        self._lock = threading.RLock()

    def reserve(self, key_hash: str, fingerprint: str, expires_epoch: int) -> JourneyState | None:
        with self._lock:
            existing = self._replays.get(key_hash)
            if existing is not None and existing.expires_epoch <= self._now_epoch():
                del self._replays[key_hash]
                existing = None
            if existing is None:
                self._replays[key_hash] = _MemoryReplay(fingerprint, expires_epoch)
                return None
            if existing.fingerprint != fingerprint:
                raise IdempotencyConflict("idempotency key was already used for different input")
            if existing.response is None:
                raise OperationInProgress("an operation with this idempotency key is in progress")
            return existing.response

    def get(self, journey_id: UUID) -> JourneyState:
        with self._lock:
            state = self._journeys.get(journey_id)
            if state is None or int(state.expires_at.timestamp()) <= self._now_epoch():
                if state is not None:
                    del self._journeys[journey_id]
                raise JourneyNotFound("journey was not found or has expired")
            return state

    def commit(
        self,
        state: JourneyState,
        *,
        expected_version: int | None,
        key_hash: str,
        fingerprint: str,
        expires_epoch: int,
    ) -> None:
        with self._lock:
            replay = self._replays.get(key_hash)
            if replay is None or replay.fingerprint != fingerprint or replay.response is not None:
                raise OperationInProgress("idempotency reservation is no longer active")
            current = self._journeys.get(state.journey_id)
            if expected_version is None:
                if current is not None:
                    raise StaleJourneyVersion("journey already exists")
            elif current is None or current.version != expected_version:
                raise StaleJourneyVersion("journey version changed; reload before retrying")
            self._journeys[state.journey_id] = state
            replay.response = state
            replay.expires_epoch = expires_epoch

    def commit_with_audit(
        self,
        state: JourneyState,
        *,
        expected_version: int | None,
        key_hash: str,
        fingerprint: str,
        expires_epoch: int,
        audit: AuditStore,
        audit_event: AuditEvent,
        expected_previous_hash: str | None,
    ) -> AuditEvent:
        stored_event = audit.append(audit_event, expected_previous_hash=expected_previous_hash)
        self.commit(
            state,
            expected_version=expected_version,
            key_hash=key_hash,
            fingerprint=fingerprint,
            expires_epoch=expires_epoch,
        )
        return stored_event

    def release(self, key_hash: str, fingerprint: str) -> None:
        with self._lock:
            replay = self._replays.get(key_hash)
            if replay is not None and replay.fingerprint == fingerprint and replay.response is None:
                del self._replays[key_hash]

    def _now_epoch(self) -> int:
        return int(self._clock().timestamp())


class DynamoDBJourneyStore:
    """DynamoDB single-table store with conditional and transactional writes."""

    storage_mode = "dynamodb"

    def __init__(self, *, table_name: str, client: Any, clock: Clock | None = None) -> None:
        self.table_name = table_name
        self.client = client
        self._clock = clock or (lambda: datetime.now(UTC))

    def reserve(self, key_hash: str, fingerprint: str, expires_epoch: int) -> JourneyState | None:
        return self._reserve(key_hash, fingerprint, expires_epoch, retry=False)

    def _reserve(
        self,
        key_hash: str,
        fingerprint: str,
        expires_epoch: int,
        *,
        retry: bool,
    ) -> JourneyState | None:
        key = self._idempotency_pk(key_hash)
        try:
            self.client.put_item(
                TableName=self.table_name,
                Item={
                    "pk": {"S": key},
                    "sk": {"S": "STATE"},
                    "record_type": {"S": "idempotency"},
                    "fingerprint": {"S": fingerprint},
                    "operation_status": {"S": "in_progress"},
                    "expires_at": {"N": str(expires_epoch)},
                },
                ConditionExpression="attribute_not_exists(pk)",
            )
            return None
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
                raise

        response = self.client.get_item(
            TableName=self.table_name,
            Key={"pk": {"S": key}, "sk": {"S": "STATE"}},
            ConsistentRead=True,
        )
        item = response.get("Item")
        if item is None:
            if retry:
                raise OperationInProgress("idempotency reservation changed; retry shortly")
            return self._reserve(key_hash, fingerprint, expires_epoch, retry=True)
        if int(item["expires_at"]["N"]) <= int(self._clock().timestamp()):
            if retry:
                raise OperationInProgress("expired idempotency reservation is being replaced")
            self.client.delete_item(
                TableName=self.table_name,
                Key={"pk": {"S": key}, "sk": {"S": "STATE"}},
                ConditionExpression="expires_at = :expired",
                ExpressionAttributeValues={":expired": item["expires_at"]},
            )
            return self._reserve(key_hash, fingerprint, expires_epoch, retry=True)
        if item["fingerprint"]["S"] != fingerprint:
            raise IdempotencyConflict("idempotency key was already used for different input")
        if item["operation_status"]["S"] == "in_progress":
            raise OperationInProgress("an operation with this idempotency key is in progress")
        return JourneyState.model_validate_json(item["response_json"]["S"])

    def get(self, journey_id: UUID) -> JourneyState:
        response = self.client.get_item(
            TableName=self.table_name,
            Key={"pk": {"S": self._journey_pk(journey_id)}, "sk": {"S": "STATE"}},
            ConsistentRead=True,
        )
        item = response.get("Item")
        if item is None or int(item["expires_at"]["N"]) <= int(self._clock().timestamp()):
            raise JourneyNotFound("journey was not found or has expired")
        return JourneyState.model_validate_json(item["state_json"]["S"])

    def commit(
        self,
        state: JourneyState,
        *,
        expected_version: int | None,
        key_hash: str,
        fingerprint: str,
        expires_epoch: int,
    ) -> None:
        try:
            self.client.transact_write_items(
                TransactItems=self._commit_items(
                    state,
                    expected_version=expected_version,
                    key_hash=key_hash,
                    fingerprint=fingerprint,
                    expires_epoch=expires_epoch,
                )
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "TransactionCanceledException":
                raise StaleJourneyVersion(
                    "journey version changed; reload before retrying"
                ) from exc
            raise

    def commit_with_audit(
        self,
        state: JourneyState,
        *,
        expected_version: int | None,
        key_hash: str,
        fingerprint: str,
        expires_epoch: int,
        audit: AuditStore,
        audit_event: AuditEvent,
        expected_previous_hash: str | None,
    ) -> AuditEvent:
        if not isinstance(audit, DynamoDBAuditStore):
            raise AuditUnavailable("DynamoDB journeys require the DynamoDB audit store")
        stored_event, audit_items = audit.prepare_append(
            audit_event, expected_previous_hash=expected_previous_hash
        )
        try:
            self.client.transact_write_items(
                TransactItems=[
                    *self._commit_items(
                        state,
                        expected_version=expected_version,
                        key_hash=key_hash,
                        fingerprint=fingerprint,
                        expires_epoch=expires_epoch,
                    ),
                    *audit_items,
                ]
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "TransactionCanceledException":
                raise AuditUnavailable(
                    "atomic journey and audit commit was rejected; reload and retry"
                ) from exc
            raise
        return stored_event

    def _commit_items(
        self,
        state: JourneyState,
        *,
        expected_version: int | None,
        key_hash: str,
        fingerprint: str,
        expires_epoch: int,
    ) -> list[dict[str, Any]]:
        journey_put: dict[str, Any] = {
            "TableName": self.table_name,
            "Item": {
                "pk": {"S": self._journey_pk(state.journey_id)},
                "sk": {"S": "STATE"},
                "record_type": {"S": "journey"},
                "state_json": {"S": state.model_dump_json()},
                "version": {"N": str(state.version)},
                "expires_at": {"N": str(expires_epoch)},
            },
        }
        if expected_version is None:
            journey_put["ConditionExpression"] = "attribute_not_exists(pk)"
        else:
            journey_put["ConditionExpression"] = "version = :expected_version"
            journey_put["ExpressionAttributeValues"] = {
                ":expected_version": {"N": str(expected_version)}
            }
        return [
            {"Put": journey_put},
            {
                "Update": {
                    "TableName": self.table_name,
                    "Key": {
                        "pk": {"S": self._idempotency_pk(key_hash)},
                        "sk": {"S": "STATE"},
                    },
                    "UpdateExpression": (
                        "SET operation_status = :complete, response_json = :response, "
                        "expires_at = :expires"
                    ),
                    "ConditionExpression": (
                        "fingerprint = :fingerprint AND operation_status = :in_progress"
                    ),
                    "ExpressionAttributeValues": {
                        ":complete": {"S": "complete"},
                        ":response": {"S": state.model_dump_json()},
                        ":expires": {"N": str(expires_epoch)},
                        ":fingerprint": {"S": fingerprint},
                        ":in_progress": {"S": "in_progress"},
                    },
                }
            },
        ]

    def release(self, key_hash: str, fingerprint: str) -> None:
        try:
            self.client.delete_item(
                TableName=self.table_name,
                Key={
                    "pk": {"S": self._idempotency_pk(key_hash)},
                    "sk": {"S": "STATE"},
                },
                ConditionExpression="fingerprint = :fingerprint AND operation_status = :status",
                ExpressionAttributeValues={
                    ":fingerprint": {"S": fingerprint},
                    ":status": {"S": "in_progress"},
                },
            )
        except ClientError:
            LOGGER.warning("failed to release DynamoDB idempotency reservation")

    @staticmethod
    def _journey_pk(journey_id: UUID) -> str:
        return f"JOURNEY#{journey_id}"

    @staticmethod
    def _idempotency_pk(key_hash: str) -> str:
        return f"IDEMPOTENCY#{key_hash}"


class DynamoDBAuditStore:
    """Per-journey append-only audit chains stored in the application table."""

    storage_mode = "dynamodb"

    def __init__(self, *, table_name: str, client: Any, retention_days: int) -> None:
        self.table_name = table_name
        self.client = client
        self.retention = timedelta(days=retention_days)

    def prepare_append(
        self, event: AuditEvent, *, expected_previous_hash: str | None
    ) -> tuple[AuditEvent, list[dict[str, Any]]]:
        material = event.model_copy(update={"previous_hash": expected_previous_hash})
        event_hash = hashlib.sha256(
            material.model_dump_json(exclude={"event_hash"}).encode()
        ).hexdigest()
        stored = material.model_copy(update={"event_hash": event_hash})
        partition = self._partition(event.correlation_id)
        head_put: dict[str, Any] = {
            "TableName": self.table_name,
            "Item": {
                "pk": {"S": partition},
                "sk": {"S": "HEAD"},
                "record_type": {"S": "audit_head"},
                "head_hash": {"S": event_hash},
            },
        }
        if expected_previous_hash is None:
            head_put["ConditionExpression"] = "attribute_not_exists(pk)"
        else:
            head_put["ConditionExpression"] = "head_hash = :previous"
            head_put["ExpressionAttributeValues"] = {":previous": {"S": expected_previous_hash}}
        expires_epoch = int((event.timestamp + self.retention).timestamp())
        event_put = {
            "TableName": self.table_name,
            "Item": {
                "pk": {"S": partition},
                "sk": {"S": f"EVENT#{event.timestamp.isoformat()}#{event.event_id}"},
                "record_type": {"S": "audit_event"},
                "event_json": {"S": stored.model_dump_json()},
                "expires_at": {"N": str(expires_epoch)},
            },
            "ConditionExpression": "attribute_not_exists(pk)",
        }
        return stored, [{"Put": head_put}, {"Put": event_put}]

    def append(self, event: AuditEvent, *, expected_previous_hash: str | None) -> AuditEvent:
        stored, items = self.prepare_append(event, expected_previous_hash=expected_previous_hash)
        try:
            self.client.transact_write_items(TransactItems=items)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "TransactionCanceledException":
                raise AuditUnavailable("audit chain changed; mutation must be retried") from exc
            raise
        return stored

    def list(self, *, correlation_id: UUID | None = None) -> tuple[AuditEvent, ...]:
        if correlation_id is None:
            raise AuthorizationDenied("global audit enumeration is not available")
        response = self.client.query(
            TableName=self.table_name,
            KeyConditionExpression="pk = :pk AND begins_with(sk, :event)",
            ExpressionAttributeValues={
                ":pk": {"S": self._partition(correlation_id)},
                ":event": {"S": "EVENT#"},
            },
            ConsistentRead=True,
            ScanIndexForward=False,
            Limit=100,
        )
        newest_first = tuple(
            AuditEvent.model_validate_json(item["event_json"]["S"])
            for item in response.get("Items", [])
        )
        return tuple(reversed(newest_first))

    def latest_hash(self, correlation_id: UUID) -> str | None:
        response = self.client.get_item(
            TableName=self.table_name,
            Key={"pk": {"S": self._partition(correlation_id)}, "sk": {"S": "HEAD"}},
            ConsistentRead=True,
        )
        item = response.get("Item")
        return item["head_hash"]["S"] if item else None

    @staticmethod
    def _partition(correlation_id: UUID) -> str:
        return f"AUDIT#{correlation_id}"


class DynamoDBConsentStore:
    """Durable, owner-keyed consent records with idempotent creation and retained revocation."""

    storage_mode = "dynamodb"

    def __init__(self, *, table_name: str, client: Any, revoked_retention_days: int) -> None:
        self.table_name = table_name
        self.client = client
        self.revoked_retention = timedelta(days=revoked_retention_days)

    def create(self, record: ConsentRecord, *, idempotency_key: str) -> ConsentRecord:
        fingerprint = record.model_dump_json(exclude={"id", "version"})
        key_hash = hashlib.sha256(idempotency_key.encode()).hexdigest()
        idempotency_pk = f"CONSENT_IDEMPOTENCY#{key_hash}"
        retention_epoch = int((record.granted_at + self.revoked_retention).timestamp())
        try:
            self.client.transact_write_items(
                TransactItems=[
                    {
                        "Put": {
                            "TableName": self.table_name,
                            "Item": self._record_item(record),
                            "ConditionExpression": "attribute_not_exists(pk)",
                        }
                    },
                    {
                        "Put": {
                            "TableName": self.table_name,
                            "Item": self._current_item(record),
                        }
                    },
                    {
                        "Put": {
                            "TableName": self.table_name,
                            "Item": {
                                "pk": {"S": idempotency_pk},
                                "sk": {"S": "STATE"},
                                "record_type": {"S": "consent_idempotency"},
                                "fingerprint": {"S": fingerprint},
                                "record_json": {"S": record.model_dump_json()},
                                "expires_at": {"N": str(retention_epoch)},
                            },
                            "ConditionExpression": "attribute_not_exists(pk)",
                        }
                    },
                ]
            )
            return record
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") != "TransactionCanceledException":
                raise
        response = self.client.get_item(
            TableName=self.table_name,
            Key={"pk": {"S": idempotency_pk}, "sk": {"S": "STATE"}},
            ConsistentRead=True,
        )
        item = response.get("Item")
        if item is None:
            raise OperationInProgress("consent creation is being completed")
        if item["fingerprint"]["S"] != fingerprint:
            raise IdempotencyConflict("consent idempotency key conflicts")
        return ConsentRecord.model_validate_json(item["record_json"]["S"])

    def get(self, consent_id: UUID) -> ConsentRecord:
        response = self.client.get_item(
            TableName=self.table_name,
            Key={"pk": {"S": self._record_pk(consent_id)}, "sk": {"S": "STATE"}},
            ConsistentRead=True,
        )
        item = response.get("Item")
        if item is None:
            raise ConsentRequired("consent record was not found")
        return ConsentRecord.model_validate_json(item["record_json"]["S"])

    def find_current(
        self,
        *,
        subject: str,
        purpose: ConsentPurpose,
        categories: frozenset[str],
        policy_version: str | None,
        now: datetime,
    ) -> ConsentRecord | None:
        response = self.client.get_item(
            TableName=self.table_name,
            Key={
                "pk": {"S": self._subject_pk(subject)},
                "sk": {"S": f"PURPOSE#{purpose.value}"},
            },
            ConsistentRead=True,
        )
        item = response.get("Item")
        if item is None:
            return None
        record = ConsentRecord.model_validate_json(item["record_json"]["S"])
        if (
            not categories <= record.data_categories
            or (policy_version is not None and record.policy_version != policy_version)
            or not record.active_at(now)
        ):
            return None
        return record

    def revoke(self, consent_id: UUID, *, expected_version: int, at: datetime) -> ConsentRecord:
        current = self.get(consent_id)
        if current.version != expected_version:
            raise StaleJourneyVersion("consent version changed", current_version=current.version)
        if current.revoked_at is not None:
            return current
        updated = current.model_copy(
            update={
                "revoked_at": at,
                "version": current.version + 1,
                "retention_expires_at": at + self.revoked_retention,
            }
        )
        record_put = {
            "TableName": self.table_name,
            "Item": self._record_item(updated),
            "ConditionExpression": "version = :expected",
            "ExpressionAttributeValues": {":expected": {"N": str(expected_version)}},
        }
        items: list[dict[str, Any]] = [{"Put": record_put}]
        pointer_key = {
            "pk": {"S": self._subject_pk(current.subject)},
            "sk": {"S": f"PURPOSE#{current.purpose.value}"},
        }
        pointer = self.client.get_item(
            TableName=self.table_name, Key=pointer_key, ConsistentRead=True
        ).get("Item")
        if pointer and pointer.get("consent_id", {}).get("S") == str(consent_id):
            items.append(
                {
                    "Put": {
                        "TableName": self.table_name,
                        "Item": self._current_item(updated),
                        "ConditionExpression": "consent_id = :consent_id",
                        "ExpressionAttributeValues": {":consent_id": {"S": str(consent_id)}},
                    }
                }
            )
        try:
            self.client.transact_write_items(TransactItems=items)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "TransactionCanceledException":
                raise StaleJourneyVersion("consent version changed") from exc
            raise
        return updated

    def _record_item(self, record: ConsentRecord) -> dict[str, Any]:
        item: dict[str, Any] = {
            "pk": {"S": self._record_pk(record.id)},
            "sk": {"S": "STATE"},
            "record_type": {"S": "consent"},
            "record_json": {"S": record.model_dump_json()},
            "version": {"N": str(record.version)},
            "subject_key": {"S": self._subject_pk(record.subject)},
        }
        if record.retention_expires_at is not None:
            item["expires_at"] = {"N": str(int(record.retention_expires_at.timestamp()))}
        return item

    def _current_item(self, record: ConsentRecord) -> dict[str, Any]:
        item = {
            "pk": {"S": self._subject_pk(record.subject)},
            "sk": {"S": f"PURPOSE#{record.purpose.value}"},
            "record_type": {"S": "consent_current"},
            "consent_id": {"S": str(record.id)},
            "record_json": {"S": record.model_dump_json()},
        }
        if record.retention_expires_at is not None:
            item["expires_at"] = {"N": str(int(record.retention_expires_at.timestamp()))}
        return item

    @staticmethod
    def _record_pk(consent_id: UUID) -> str:
        return f"CONSENT#{consent_id}"

    @staticmethod
    def _subject_pk(subject: str) -> str:
        return "CONSENT_SUBJECT#" + hashlib.sha256(subject.encode()).hexdigest()


class DynamoDBAuthorityStore:
    storage_mode = "dynamodb"

    def __init__(self, *, table_name: str, client: Any) -> None:
        self.table_name = table_name
        self.client = client

    def put(self, grant: AuthorityGrant) -> AuthorityGrant:
        try:
            self.client.put_item(
                TableName=self.table_name,
                Item=self._item(grant),
                ConditionExpression=(
                    "attribute_not_exists(pk) OR attribute_exists(revoked_at) "
                    "OR expires_at <= :now OR grant_json = :grant"
                ),
                ExpressionAttributeValues={
                    ":now": {"N": str(int(grant.valid_from.timestamp()))},
                    ":grant": {"S": grant.model_dump_json()},
                },
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                raise IdempotencyConflict(
                    "an active authority grant already exists for this delegate"
                ) from exc
            raise
        return grant

    def get(self, subject: str, delegate: str) -> AuthorityGrant:
        response = self.client.get_item(
            TableName=self.table_name,
            Key=self._key(subject, delegate),
            ConsistentRead=True,
        )
        item = response.get("Item")
        if item is None:
            raise AuthorityGrantRequired("active authority grant is required")
        return AuthorityGrant.model_validate_json(item["grant_json"]["S"])

    def revoke(self, subject: str, delegate: str, *, at: datetime) -> AuthorityGrant:
        current = self.get(subject, delegate)
        if current.revoked_at is not None:
            return current
        updated = current.model_copy(update={"revoked_at": at})
        try:
            self.client.put_item(
                TableName=self.table_name,
                Item=self._item(updated),
                ConditionExpression="attribute_not_exists(revoked_at)",
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                return self.get(subject, delegate)
            raise
        return updated

    def _item(self, grant: AuthorityGrant) -> dict[str, Any]:
        item = {
            **self._key(grant.subject, grant.delegate),
            "record_type": {"S": "authority_grant"},
            "grant_json": {"S": grant.model_dump_json()},
            "expires_at": {"N": str(int(grant.valid_until.timestamp()))},
        }
        if grant.revoked_at is not None:
            item["revoked_at"] = {"S": grant.revoked_at.isoformat()}
        return item

    @staticmethod
    def _key(subject: str, delegate: str) -> dict[str, dict[str, str]]:
        subject_hash = hashlib.sha256(subject.encode()).hexdigest()
        delegate_hash = hashlib.sha256(delegate.encode()).hexdigest()
        return {
            "pk": {"S": f"AUTHORITY#{subject_hash}"},
            "sk": {"S": f"DELEGATE#{delegate_hash}"},
        }


class DynamoDBActionIntentStore:
    storage_mode = "dynamodb"

    def __init__(self, *, table_name: str, client: Any) -> None:
        self.table_name = table_name
        self.client = client

    def put(self, intent: ActionIntent) -> None:
        self.client.put_item(
            TableName=self.table_name,
            Item={
                **self._key(intent.id),
                "record_type": {"S": "action_intent"},
                "intent_json": {"S": intent.model_dump_json()},
                "expires_at": {"N": str(int(intent.expires_at.timestamp()))},
            },
            ConditionExpression="attribute_not_exists(pk)",
        )

    def get(self, intent_id: UUID) -> ActionIntent:
        response = self.client.get_item(
            TableName=self.table_name,
            Key=self._key(intent_id),
            ConsistentRead=True,
        )
        item = response.get("Item")
        if item is None:
            raise IntentConflict("action intent was not found")
        return ActionIntent.model_validate_json(item["intent_json"]["S"])

    def mark_used(self, intent: ActionIntent, *, replay_key: str, result: object) -> object:
        if intent.used_at is None:
            raise IntentConflict("action intent use timestamp is required")
        result_json = json.dumps(result, default=self._json_default, separators=(",", ":"))
        try:
            self.client.update_item(
                TableName=self.table_name,
                Key=self._key(intent.id),
                UpdateExpression=(
                    "SET intent_json = :intent, used_at = :used, "
                    "replay_key = :replay, result_json = :result"
                ),
                ConditionExpression="attribute_not_exists(used_at)",
                ExpressionAttributeValues={
                    ":intent": {"S": intent.model_dump_json()},
                    ":used": {"S": intent.used_at.isoformat()},
                    ":replay": {"S": replay_key},
                    ":result": {"S": result_json},
                },
            )
            return result
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
                raise
        response = self.client.get_item(
            TableName=self.table_name,
            Key=self._key(intent.id),
            ConsistentRead=True,
        )
        item = response.get("Item")
        if item and item.get("replay_key", {}).get("S") == replay_key:
            return json.loads(item["result_json"]["S"])
        raise IntentConflict("action intent has already been used")

    @staticmethod
    def _json_default(value: object) -> object:
        model_dump = getattr(value, "model_dump", None)
        if callable(model_dump):
            return model_dump(mode="json")
        return str(value)

    @staticmethod
    def _key(intent_id: UUID) -> dict[str, dict[str, str]]:
        return {"pk": {"S": f"INTENT#{intent_id}"}, "sk": {"S": "STATE"}}


class AdaptSGService:
    def __init__(
        self,
        *,
        parser: PreferenceParser,
        planner: JourneyPlanner,
        replanner: JourneyReplanner,
        environment: EnvironmentClient,
        location: LocationClient | None = None,
        store: JourneyStore | None = None,
        ttl_hours: int = 24,
        mode: str = "demo",
        auth_mode: str = "demo",
        local_live: bool = False,
        clock: Clock | None = None,
        policy: CapabilityResolver | None = None,
        audit: AuditStore | None = None,
        consent: ConsentStore | None = None,
        authority: AuthorityStore | None = None,
        intents: ActionIntentService | None = None,
        consent_policy_version: str | None = None,
        consent_categories: frozenset[str] = frozenset(),
    ) -> None:
        self.parser = parser
        self.planner = planner
        self.replanner = replanner
        self.environment = environment
        self.location = location
        self._clock = clock or (lambda: datetime.now(UTC))
        self.store = store or InMemoryJourneyStore(clock=self._clock)
        self.ttl = timedelta(hours=ttl_hours)
        self.mode = mode
        self._auth_mode = auth_mode
        self.local_live = local_live
        self.policy = policy or CapabilityResolver()
        self.audit = audit or InMemoryAuditStore()
        self.consent_policy_version = consent_policy_version
        self.consent_categories = consent_categories
        authorization = AuthorizationPolicy(
            capabilities=self.policy,
            authority=authority,
            consent=consent,
            consent_policy_version=consent_policy_version,
        )
        self.authorization = authorization
        self.consent = authorization.consent
        self.authority = authorization.authority
        self.intents = intents or ActionIntentService(clock=self._clock)
        self._plan_graph = self._build_plan_graph()

    @property
    def storage_mode(self) -> str:
        return self.store.storage_mode

    @property
    def auth_mode(self) -> str:
        return self._auth_mode

    def _build_plan_graph(self) -> object:
        graph = StateGraph(PlanGraphState)

        def parse_node(state: PlanGraphState) -> dict[str, object]:
            try:
                parsed = self.parser.parse(state["prompt"], journey_date=state["journey_date"])
                return {"parsed": parsed}
            except Exception as exc:  # boundary: translate provider errors to typed graph state
                return {"error": f"constraint parsing failed: {exc}"}

        def plan_node(state: PlanGraphState) -> dict[str, object]:
            if "error" in state:
                return {}
            parsed = state["parsed"]
            override = state.get("start_label_override")
            requested = (
                parsed.request.model_copy(update={"start_label": override})
                if override
                else parsed.request
            )
            try:
                request, location_warnings = self._resolve_start_location(requested)
                itinerary = self.planner.create(
                    request,
                    parser_source=parsed.source,
                )
                return {"itinerary": itinerary, "location_warnings": location_warnings}
            except NoFeasibleItinerary as exc:
                return {"error": str(exc)}

        graph.add_node("parse_preferences", parse_node)
        graph.add_node("plan_and_validate", plan_node)
        graph.add_edge(START, "parse_preferences")
        graph.add_edge("parse_preferences", "plan_and_validate")
        graph.add_edge("plan_and_validate", END)
        return graph.compile()

    def create_plan(
        self,
        prompt: str,
        *,
        journey_date: date,
        start_label: str | None = None,
    ) -> PlanOutcome:
        initial: PlanGraphState = {"prompt": prompt, "journey_date": journey_date}
        if start_label:
            initial["start_label_override"] = start_label
        result = cast(
            PlanGraphState,
            self._plan_graph.invoke(initial),  # type: ignore[attr-defined]
        )
        if "error" in result:
            raise NoFeasibleItinerary(result["error"])
        parsed = result["parsed"]
        return PlanOutcome(
            itinerary=result["itinerary"],
            warnings=parsed.warnings + result.get("location_warnings", ()),
            token_usage=parsed.token_usage,
        )

    def _resolve_start_location(
        self, request: JourneyRequest
    ) -> tuple[JourneyRequest, tuple[str, ...]]:
        """Verify the origin against the gazetteer, disclosing any reinterpretation.

        A query that matches nothing is a user-input problem, not an outage, so it
        raises OriginNotVerified (422 with candidates) rather than ToolUnavailable.
        A ToolUnavailable raised by the client itself still propagates untouched, so
        a genuine provider failure keeps failing closed.
        """
        if self.location is None:
            return request, ()
        warnings: tuple[str, ...] = ()
        typed_label = request.start_label
        if is_vague_origin(typed_label):
            warnings += (
                f"No specific starting point was given, so the day is planned from "
                f"{DEFAULT_ORIGIN_LABEL}. Name a station or address to start elsewhere.",
            )
            typed_label = DEFAULT_ORIGIN_LABEL

        ranked: tuple[LocationSearchResult, ...] = ()
        matched_query = typed_label
        for query in origin_query_variants(typed_label):
            results = self.location.search(query)
            if not results:
                continue
            candidates = rank_origin_candidates(query, results)
            if is_confident(query, candidates):
                ranked, matched_query = candidates, query
                break
            if not ranked:
                # Keep the first real result set: it becomes the chooser's options
                # if no later, narrower query resolves cleanly.
                ranked, matched_query = candidates, query

        if not ranked:
            raise OriginNotVerified(
                f"no Singapore location matches {typed_label!r}",
                query=typed_label,
            )
        if not is_confident(matched_query, ranked):
            raise OriginNotVerified(
                f"{typed_label!r} matches several places in Singapore",
                query=typed_label,
                candidates=tuple(result.label for result in ranked[:5]),
            )

        selected = ranked[0]
        if not selected.label.strip() or not selected.source.strip():
            raise ToolUnavailable("location verification returned an unverified result")
        reinterpreted = selected.label.strip().casefold() != request.start_label.strip().casefold()
        if reinterpreted and not warnings:
            warnings += (
                f"Start location {request.start_label!r} was verified as {selected.label!r}.",
            )
        return (
            request.model_copy(
                update={"start_label": selected.label, "start_location": selected.location}
            ),
            warnings,
        )

    def start_journey(
        self,
        prompt: str,
        *,
        journey_date: date,
        idempotency_key: str,
        start_label: str | None = None,
        principal: PrincipalContext | None = None,
    ) -> JourneyState:
        actor = self._principal_or_demo(principal)
        self._require_actor(actor, Capability.JOURNEY_WRITE)
        consent = self._require_processing_consent(actor)
        fingerprint = self._fingerprint(
            {
                "operation": "start_journey",
                "prompt": prompt,
                "journey_date": journey_date.isoformat(),
                # A retry that picks a different origin is a different request,
                # not a replay of the one that could not be verified.
                "start_label": start_label or "",
            }
        )

        def mutation() -> tuple[JourneyState, None]:
            outcome = self.create_plan(prompt, journey_date=journey_date, start_label=start_label)
            now = self._now()
            return (
                JourneyState(
                    owner_principal_id=actor.principal_id,
                    processing_consent_id=consent.id if consent else None,
                    processing_consent_version=consent.policy_version if consent else None,
                    status=JourneyStatus.DRAFT,
                    pending_initial_itinerary=outcome.itinerary,
                    warnings=outcome.warnings,
                    token_usage=outcome.token_usage,
                    created_at=now,
                    updated_at=now,
                    expires_at=now + self.ttl,
                ),
                None,
            )

        return self._mutate("start_journey", idempotency_key, fingerprint, mutation)

    def get_journey(
        self, journey_id: UUID, *, principal: PrincipalContext | None = None
    ) -> JourneyState:
        state = self.store.get(journey_id)
        self._require_owner(self._principal_or_demo(principal), state)
        return state

    def monitor_journey(
        self, journey_id: UUID, *, principal: PrincipalContext | None = None
    ) -> MonitoringOutcome:
        """Read current server-owned state before checking live conditions."""
        actor = self._principal_or_demo(principal)
        current = self.get_journey(journey_id, principal=actor)
        self._require_processing_consent(actor, current)
        if current.status is not JourneyStatus.ACTIVE or current.current_itinerary is None:
            raise InvalidJourneyTransition("only active journeys can be monitored")
        return self.monitor(current.current_itinerary)

    def issue_action_intent(
        self,
        journey_id: UUID,
        *,
        decision: ApprovalDecision,
        target_id: UUID,
        expected_version: int,
        principal: PrincipalContext | None = None,
    ) -> ActionIntent:
        actor = self._principal_or_demo(principal)
        self._require_actor(actor, Capability.ACTION_INTENT_ISSUE)
        current = self.get_journey(journey_id, principal=actor)
        self._require_processing_consent(actor, current)
        if current.version != expected_version:
            raise StaleJourneyVersion(
                "journey version changed; reload before issuing an intent",
                current_version=current.version,
            )
        expected_target = (
            current.pending_initial_itinerary.id
            if current.status is JourneyStatus.DRAFT and current.pending_initial_itinerary
            else current.latest_replan_proposal.id
            if current.status is JourneyStatus.ACTIVE and current.latest_replan_proposal
            else None
        )
        if expected_target != target_id:
            raise InvalidJourneyTransition("intent target is not a current decision target")
        return self.intents.issue(
            principal=actor,
            target=str(target_id),
            capability=Capability.JOURNEY_WRITE,
            payload={
                "decision": decision.value,
                "target_id": str(target_id),
                "expected_version": expected_version,
            },
            expected_state_version=expected_version,
        )

    def decide_journey(
        self,
        journey_id: UUID,
        *,
        decision: ApprovalDecision,
        target_id: UUID,
        expected_version: int,
        idempotency_key: str,
        principal: PrincipalContext | None = None,
        intent_id: UUID | None = None,
    ) -> JourneyState:
        actor = self._principal_or_demo(principal)
        self._require_actor(actor, Capability.JOURNEY_WRITE)
        current_for_auth = self.get_journey(journey_id, principal=actor)
        self._require_processing_consent(actor, current_for_auth)
        if self.mode == "live" and not self.local_live and intent_id is None:
            raise IntentConflict("a prepared action intent is required")
        fingerprint = self._fingerprint(
            {
                "operation": "decide_journey",
                "journey_id": str(journey_id),
                "decision": decision.value,
                "target_id": str(target_id),
                "expected_version": expected_version,
            }
        )

        def mutation() -> tuple[JourneyState, int]:
            current = self._load_expected(journey_id, expected_version)
            if intent_id is not None:
                self.intents.consume(
                    intent_id,
                    principal=actor,
                    payload={
                        "decision": decision.value,
                        "target_id": str(target_id),
                        "expected_version": expected_version,
                    },
                    state_version=current.version,
                    result=current,
                )
            if current.status is JourneyStatus.DRAFT:
                updated = self._decide_initial(current, decision, target_id)
            elif current.status is JourneyStatus.ACTIVE:
                updated = self._decide_replan(current, decision, target_id)
            else:
                raise InvalidJourneyTransition("rejected journeys cannot be changed or replanned")
            return updated, current.version

        return self._mutate(
            "decide_journey",
            idempotency_key,
            fingerprint,
            mutation,
            journey_id=journey_id,
            decision=decision.value,
        )

    @overload
    def propose_replan(
        self,
        journey_id: UUID,
        trigger: ReplanTrigger,
        *,
        expected_version: int,
        idempotency_key: str,
        principal: PrincipalContext | None = None,
    ) -> JourneyState: ...

    @overload
    def propose_replan(
        self,
        journey_id: Itinerary,
        trigger: ReplanTrigger,
        *,
        expected_version: None = None,
        idempotency_key: None = None,
        principal: PrincipalContext | None = None,
    ) -> ReplanProposal: ...

    def propose_replan(
        self,
        journey_id: UUID | Itinerary,
        trigger: ReplanTrigger,
        *,
        expected_version: int | None = None,
        idempotency_key: str | None = None,
        principal: PrincipalContext | None = None,
    ) -> JourneyState | ReplanProposal:
        """Propose from stored state; the itinerary overload is migration-only for Role 3."""
        if isinstance(journey_id, Itinerary):
            return self.replanner.propose(journey_id, trigger)
        if expected_version is None or idempotency_key is None:
            raise InvalidJourneyTransition(
                "stateful replanning requires version and idempotency key"
            )
        actor = self._principal_or_demo(principal)
        self._require_actor(actor, Capability.JOURNEY_WRITE)
        current_for_auth = self.get_journey(journey_id, principal=actor)
        self._require_processing_consent(actor, current_for_auth)
        fingerprint = self._fingerprint(
            {
                "operation": "propose_replan",
                "journey_id": str(journey_id),
                "trigger": trigger.model_dump(mode="json"),
                "expected_version": expected_version,
            }
        )

        def mutation() -> tuple[JourneyState, int]:
            current = self._load_expected(journey_id, expected_version)
            if current.status is JourneyStatus.REJECTED:
                raise InvalidJourneyTransition("rejected journeys cannot be replanned")
            if current.status is not JourneyStatus.ACTIVE or current.current_itinerary is None:
                raise InvalidJourneyTransition("only active journeys can be replanned")
            existing = current.latest_replan_proposal
            if existing is not None and existing.status is ProposalStatus.PENDING:
                raise InvalidJourneyTransition("the current journey already has a pending proposal")
            proposal = self.replanner.propose(current.current_itinerary, trigger)
            proposal = proposal.model_copy(
                update={
                    "changes": tuple(
                        change.model_copy(
                            update={
                                "reason": (
                                    f"Verified {trigger.type.value.replace('_', ' ')} adjustment"
                                )
                            }
                        )
                        for change in proposal.changes
                    )
                }
            )
            if (
                not proposal.validation.valid
                or proposal.original_itinerary_id != current.current_itinerary.id
            ):
                raise NoFeasibleItinerary("replan proposal failed deterministic verification")
            now = self._now()
            updated = current.model_copy(
                update={
                    "latest_replan_proposal": proposal,
                    "version": current.version + 1,
                    "updated_at": now,
                    "expires_at": now + self.ttl,
                }
            )
            return JourneyState.model_validate(updated.model_dump()), current.version

        return self._mutate(
            "propose_replan",
            idempotency_key,
            fingerprint,
            mutation,
            journey_id=journey_id,
        )

    @staticmethod
    def apply_proposal(proposal: ReplanProposal, *, approved: bool) -> Itinerary:
        """Migration-only helper; the stateful API never accepts proposals from clients."""
        if proposal.requires_approval and not approved:
            raise ApprovalRequired("this cost increase requires caregiver approval")
        return proposal.itinerary

    def _decide_initial(
        self,
        current: JourneyState,
        decision: ApprovalDecision,
        target_id: UUID,
    ) -> JourneyState:
        pending = current.pending_initial_itinerary
        if pending is None or pending.id != target_id:
            raise InvalidJourneyTransition("decision target is not the pending initial plan")
        now = self._now()
        if decision is ApprovalDecision.APPROVE:
            self._validate_before_approval(pending)
            updates: dict[str, object] = {
                "status": JourneyStatus.ACTIVE,
                "current_itinerary": pending,
                "pending_initial_itinerary": None,
            }
        else:
            updates = {
                "status": JourneyStatus.REJECTED,
                "pending_initial_itinerary": None,
            }
        updates.update(
            version=current.version + 1,
            updated_at=now,
            expires_at=now + self.ttl,
        )
        candidate = current.model_copy(update=updates)
        return JourneyState.model_validate(candidate.model_dump())

    def _decide_replan(
        self,
        current: JourneyState,
        decision: ApprovalDecision,
        target_id: UUID,
    ) -> JourneyState:
        proposal = current.latest_replan_proposal
        if (
            proposal is None
            or proposal.status is not ProposalStatus.PENDING
            or proposal.id != target_id
        ):
            raise InvalidJourneyTransition("decision target is not a pending replan proposal")
        accepted = current.current_itinerary
        if accepted is None or proposal.original_itinerary_id != accepted.id:
            raise InvalidJourneyTransition("proposal does not reference the current itinerary")
        now = self._now()
        if decision is ApprovalDecision.APPROVE:
            self._validate_before_approval(proposal.itinerary)
            decided = proposal.model_copy(update={"status": ProposalStatus.APPROVED})
            itinerary = proposal.itinerary
        else:
            decided = proposal.model_copy(update={"status": ProposalStatus.REJECTED})
            itinerary = accepted
        updated = current.model_copy(
            update={
                "current_itinerary": itinerary,
                "latest_replan_proposal": decided,
                "version": current.version + 1,
                "updated_at": now,
                "expires_at": now + self.ttl,
            }
        )
        return JourneyState.model_validate(updated.model_dump())

    def _validate_before_approval(self, itinerary: Itinerary) -> None:
        validation = self.planner.validator.validate(itinerary)
        if not validation.valid:
            codes = [issue.code.value for issue in validation.issues]
            self._log("approval_validation", "rejected", validation_issue_codes=codes)
            raise NoFeasibleItinerary("itinerary failed deterministic approval validation")

    def _load_expected(self, journey_id: UUID, expected_version: int) -> JourneyState:
        current = self.store.get(journey_id)
        if current.version != expected_version:
            raise StaleJourneyVersion(
                "journey version changed; reload before retrying",
                current_version=current.version,
            )
        return current

    @staticmethod
    def _demo_principal() -> PrincipalContext:
        return PrincipalContext(
            principal_id="demo-caregiver",
            account_id="demo-caregiver",
            roles=frozenset({ActorRole.CAREGIVER}),
            authenticated=True,
        )

    def _principal_or_demo(self, principal: PrincipalContext | None) -> PrincipalContext:
        actor = principal or self._demo_principal()
        if not actor.authenticated:
            raise AuthenticationRequired("authenticated principal is required")
        return actor

    def _require_actor(self, principal: PrincipalContext, capability: Capability) -> None:
        self.authorization.require(principal, capability)

    @staticmethod
    def _require_owner(principal: PrincipalContext, state: JourneyState) -> None:
        if state.owner_principal_id != principal.principal_id:
            raise JourneyNotFound("journey was not found")

    def _require_processing_consent(
        self, principal: PrincipalContext, state: JourneyState | None = None
    ) -> ConsentRecord | None:
        if self.mode != "live" or self.local_live:
            return None
        record = self.consent.find_current(
            subject=principal.principal_id,
            purpose=ConsentPurpose.JOURNEY_PLANNING,
            categories=self.consent_categories,
            policy_version=self.consent_policy_version,
            now=self._now(),
        )
        if record is None:
            raise ConsentRequired("current journey-planning consent is required")
        if state is not None and state.processing_consent_id != record.id:
            raise ConsentRequired("journey processing consent is no longer current")
        return record

    def _mutate(
        self,
        operation: str,
        idempotency_key: str,
        fingerprint: str,
        mutation: Mutation,
        *,
        journey_id: UUID | None = None,
        decision: str | None = None,
    ) -> JourneyState:
        key_hash = self._hash_idempotency_key(idempotency_key)
        started = monotonic()
        expires_epoch = int((self._now() + self.ttl).timestamp())
        try:
            replay = self.store.reserve(key_hash, fingerprint, expires_epoch)
        except Exception:
            self._log(
                operation,
                "failure",
                journey_id=str(journey_id) if journey_id else None,
                duration_ms=round((monotonic() - started) * 1000, 2),
                approval_decision=decision,
            )
            raise
        if replay is not None:
            self._log_operation(
                operation,
                replay,
                started,
                outcome="success",
                replay=True,
                decision=decision,
            )
            return replay
        try:
            state, expected_version = mutation()
            audit_event = AuditEvent(
                correlation_id=state.journey_id,
                actor_role=ActorRole.SYSTEM,
                capability=Capability.JOURNEY_WRITE,
                transition=operation,
                outcome=TransitionOutcome.ACCEPTED,
                timestamp=self._now(),
                metadata={
                    "operation": operation,
                    "resource_type": "journey",
                    "resource_id": str(state.journey_id),
                    "version": state.version,
                },
            )
            previous = self.audit.latest_hash(state.journey_id)
            self.store.commit_with_audit(
                state,
                expected_version=expected_version,
                key_hash=key_hash,
                fingerprint=fingerprint,
                expires_epoch=int(state.expires_at.timestamp()),
                audit=self.audit,
                audit_event=audit_event,
                expected_previous_hash=previous,
            )
        except Exception:
            self.store.release(key_hash, fingerprint)
            self._log(
                operation,
                "failure",
                journey_id=str(journey_id) if journey_id else None,
                duration_ms=round((monotonic() - started) * 1000, 2),
                approval_decision=decision,
            )
            raise
        self._log_operation(
            operation,
            state,
            started,
            outcome="success",
            replay=False,
            decision=decision,
        )
        return state

    def _log_operation(
        self,
        operation: str,
        state: JourneyState,
        started: float,
        *,
        outcome: str,
        replay: bool,
        decision: str | None,
    ) -> None:
        itinerary = state.current_itinerary or state.pending_initial_itinerary
        self._log(
            operation,
            outcome,
            journey_id=str(state.journey_id),
            duration_ms=round((monotonic() - started) * 1000, 2),
            idempotent_replay=replay,
            replan_count=itinerary.replan_count if itinerary else 0,
            approval_decision=decision,
            bedrock_input_tokens=state.token_usage.input_tokens,
            bedrock_output_tokens=state.token_usage.output_tokens,
        )

    def _log(self, operation: str, outcome: str, **fields: object) -> None:
        payload = {
            "operation": operation,
            "outcome": outcome,
            "mode": self.mode,
            "storage": self.storage_mode,
            **{key: value for key, value in fields.items() if value is not None},
        }
        LOGGER.info(json.dumps(payload, separators=(",", ":"), sort_keys=True))

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("service clock must return a timezone-aware datetime")
        return value

    @staticmethod
    def _fingerprint(payload: dict[str, object]) -> str:
        canonical = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        return hashlib.sha256(canonical.encode()).hexdigest()

    @staticmethod
    def _hash_idempotency_key(idempotency_key: str) -> str:
        if not IDEMPOTENCY_KEY.fullmatch(idempotency_key):
            raise InvalidIdempotencyKey(
                "Idempotency-Key must contain 8-200 visible ASCII characters"
            )
        return hashlib.sha256(idempotency_key.encode()).hexdigest()

    def monitor(self, itinerary: Itinerary) -> MonitoringOutcome:
        snapshot = self.environment.current()
        return MonitoringOutcome(
            snapshot=snapshot,
            triggers=self._environment_triggers(itinerary, snapshot),
        )

    @staticmethod
    def _environment_triggers(
        itinerary: Itinerary, snapshot: EnvironmentSnapshot
    ) -> tuple[ReplanTrigger, ...]:
        triggers = []
        weather = snapshot.weather_summary.casefold()
        if any(term in weather for term in ("heavy rain", "thunder", "showers")):
            triggers.append(
                ReplanTrigger(
                    type=TriggerType.HEAVY_RAIN,
                    message=f"Weather update: {snapshot.weather_summary}",
                )
            )
        if snapshot.psi >= 101:
            triggers.append(
                ReplanTrigger(
                    type=TriggerType.HIGH_PSI,
                    message=f"24-hour PSI reached {snapshot.psi}",
                )
            )
        itinerary_ids = frozenset(segment.venue.id for segment in itinerary.segments)
        affected = itinerary_ids & snapshot.flood_affected_venue_ids
        if affected:
            triggers.append(
                ReplanTrigger(
                    type=TriggerType.FLOOD_ALERT,
                    message="PUB flood alert intersects the journey",
                    affected_venue_ids=affected,
                )
            )
        if snapshot.disrupted_route_labels:
            labels = ", ".join(sorted(snapshot.disrupted_route_labels))
            triggers.append(
                ReplanTrigger(
                    type=TriggerType.TRANSPORT_DISRUPTION,
                    message=f"LTA transport disruption: {labels}",
                )
            )
        return tuple(triggers)


def build_service(settings: Settings | None = None) -> AdaptSGService:
    resolved = settings or get_settings()
    if resolved.adaptsg_audit_storage_configured and not resolved.adaptsg_journeys_table:
        raise RetentionConfigurationMissing(
            "ADAPTSG_AUDIT_STORAGE_CONFIGURED requires ADAPTSG_JOURNEYS_TABLE"
        )
    live_requirements = (
        ("ADAPTSG_AUTHENTICATION_MODE", resolved.adaptsg_authentication_mode == "cognito"),
        ("ADAPTSG_COGNITO_ISSUER", bool(resolved.adaptsg_cognito_issuer)),
        ("ADAPTSG_COGNITO_AUDIENCE", bool(resolved.adaptsg_cognito_audience)),
        ("ADAPTSG_AUTHENTICATION_CONFIGURED", resolved.adaptsg_authentication_configured),
        ("ADAPTSG_ENCRYPTION_CONFIGURED", resolved.adaptsg_encryption_configured),
        ("ADAPTSG_AUDIT_STORAGE_CONFIGURED", resolved.adaptsg_audit_storage_configured),
        (
            "ADAPTSG_PRODUCTION_RETENTION_CONFIGURED",
            resolved.adaptsg_production_retention_configured,
        ),
        ("ADAPTSG_AUDIT_RETENTION_DAYS", bool(resolved.adaptsg_audit_retention_days)),
        (
            "ADAPTSG_REVOKED_CONSENT_RETENTION_DAYS",
            bool(resolved.adaptsg_revoked_consent_retention_days),
        ),
        ("ADAPTSG_CONSENT_POLICY_VERSION", bool(resolved.adaptsg_consent_policy_version)),
        ("ADAPTSG_LIVE_CATALOG_CONFIGURED", resolved.adaptsg_live_catalog_configured),
        ("ADAPTSG_CATALOG_VERSION", bool(resolved.adaptsg_catalog_version)),
        ("ADAPTSG_COST_MODEL_VERSION", bool(resolved.adaptsg_cost_model_version)),
        (
            "ADAPTSG_INPUT_TOKEN_TARIFF_SGD",
            resolved.adaptsg_input_token_tariff_sgd is not None,
        ),
        (
            "ADAPTSG_OUTPUT_TOKEN_TARIFF_SGD",
            resolved.adaptsg_output_token_tariff_sgd is not None,
        ),
        ("ADAPTSG_JOURNEYS_TABLE", bool(resolved.adaptsg_journeys_table)),
        ("ONEMAP_API_TOKEN", bool(resolved.onemap_api_token)),
        ("LTA_ACCOUNT_KEY", bool(resolved.lta_account_key)),
    )
    missing_live_requirements = tuple(
        name for name, configured in live_requirements if not configured
    )
    if (
        resolved.adaptsg_mode == "live"
        and not resolved.adaptsg_local_live_enabled
        and missing_live_requirements
    ):
        raise RetentionConfigurationMissing(
            "live mode requires production policy and provider configuration; missing: "
            + ", ".join(missing_live_requirements)
        )
    enabled_flags = frozenset(
        flag
        for flag, enabled in (
            (FeatureFlag.BOOKING_READ, resolved.adaptsg_booking_read_enabled),
            (FeatureFlag.MEDICAL_INTAKE, resolved.adaptsg_medical_intake_enabled),
            (FeatureFlag.MEDICAL_CLINICIAN, resolved.adaptsg_medical_clinician_enabled),
            (FeatureFlag.EMERGENCY_LIVE, resolved.adaptsg_emergency_live_enabled),
            (FeatureFlag.MULTI_AGENT, resolved.adaptsg_multi_agent_enabled),
        )
        if enabled
    )
    policy = CapabilityResolver(
        CapabilityPolicy(
            flags=enabled_flags,
            production_retention_configured=resolved.adaptsg_production_retention_configured,
        )
    )
    catalog = VenueCatalog()
    validator = ItineraryValidator(max_replans=resolved.adaptsg_max_replans)
    location = (
        DemoLocationClient()
        if resolved.adaptsg_provider_mode == "demo"
        else OneMapLocationClient(token=resolved.onemap_api_token or "")
    )
    routing = (
        DemoRoutingClient()
        if resolved.adaptsg_provider_mode == "demo"
        else OneMapRoutingClient(
            token=resolved.onemap_api_token or "",
            bfa_enabled=resolved.onemap_bfa_enabled,
        )
    )
    environment: EnvironmentClient = (
        DemoEnvironmentClient()
        if resolved.adaptsg_provider_mode == "demo"
        else LiveEnvironmentClient(
            catalog=catalog,
            lta_account_key=resolved.lta_account_key or "",
            data_gov_api_key=resolved.data_gov_sg_api_key,
        )
    )
    planner = JourneyPlanner(
        catalog=catalog,
        routing=routing,
        validator=validator,
    )
    replanner = JourneyReplanner(
        planner=planner,
        approval_cost_increase_sgd=resolved.adaptsg_approval_cost_increase_sgd,
        max_replans=resolved.adaptsg_max_replans,
    )
    parser: PreferenceParser = (
        BedrockPreferenceParser(settings=resolved, catalog=catalog)
        if resolved.adaptsg_bedrock_enabled
        else DeterministicPreferenceParser(catalog)
    )
    store: JourneyStore
    audit: AuditStore
    consent: ConsentStore
    authority: AuthorityStore
    intents: ActionIntentService
    if resolved.adaptsg_journeys_table:
        session = boto3.Session(
            profile_name=resolved.aws_profile or None,
            region_name=resolved.aws_region,
        )
        dynamodb = session.client("dynamodb")
        store = DynamoDBJourneyStore(
            table_name=resolved.adaptsg_journeys_table,
            client=dynamodb,
        )
        audit = DynamoDBAuditStore(
            table_name=resolved.adaptsg_journeys_table,
            client=dynamodb,
            retention_days=resolved.adaptsg_audit_retention_days or 90,
        )
        consent = DynamoDBConsentStore(
            table_name=resolved.adaptsg_journeys_table,
            client=dynamodb,
            revoked_retention_days=resolved.adaptsg_revoked_consent_retention_days or 30,
        )
        authority = DynamoDBAuthorityStore(
            table_name=resolved.adaptsg_journeys_table,
            client=dynamodb,
        )
        intents = ActionIntentService(
            store=DynamoDBActionIntentStore(
                table_name=resolved.adaptsg_journeys_table,
                client=dynamodb,
            )
        )
    else:
        store = InMemoryJourneyStore()
        audit = InMemoryAuditStore()
        consent = InMemoryConsentStore()
        authority = InMemoryAuthorityStore()
        intents = ActionIntentService()
    return AdaptSGService(
        parser=parser,
        planner=planner,
        replanner=replanner,
        environment=environment,
        location=location,
        store=store,
        ttl_hours=resolved.adaptsg_journey_ttl_hours,
        mode=resolved.adaptsg_mode,
        auth_mode=resolved.adaptsg_authentication_mode,
        local_live=resolved.adaptsg_local_live_enabled,
        policy=policy,
        audit=audit,
        consent=consent,
        authority=authority,
        intents=intents,
        consent_policy_version=resolved.adaptsg_consent_policy_version or None,
        consent_categories=frozenset(
            value.strip()
            for value in resolved.adaptsg_consent_categories.split(",")
            if value.strip()
        ),
    )
