import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import Mock
from uuid import UUID

import pytest
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient
from pydantic import ValidationError

import adaptsg.web_api as web_api
from adaptsg.agent import (
    ActionIntentService,
    AdaptSGService,
    AuthorizationPolicy,
    CapabilityResolver,
    DynamoDBActionIntentStore,
    DynamoDBAuditStore,
    DynamoDBAuthorityStore,
    DynamoDBConsentStore,
    DynamoDBJourneyStore,
    InMemoryAuditStore,
    InMemoryAuthorityStore,
    InMemoryConsentStore,
    InMemoryJourneyStore,
    JourneyStore,
    build_service,
)
from adaptsg.domain import (
    ActionRisk,
    ActorRole,
    ApprovalDecision,
    AuditEvent,
    AuthorityGrant,
    Capability,
    CapabilityPolicy,
    ConsentPurpose,
    ConsentRecord,
    FeatureFlag,
    Itinerary,
    JourneyRequest,
    JourneyState,
    JourneyStatus,
    Location,
    LocationSearchResult,
    MonitoringOutcome,
    ParseOutcome,
    PrincipalContext,
    ReplanTrigger,
    TransitionOutcome,
    TriggerType,
    ValidationCode,
    ValidationIssue,
    ValidationResult,
)
from adaptsg.errors import (
    AuditUnavailable,
    AuthorityGrantRequired,
    AuthorizationDenied,
    CapabilityDisabled,
    ConsentRequired,
    IdempotencyConflict,
    IntentConflict,
    InvalidJourneyTransition,
    JourneyNotFound,
    NoFeasibleItinerary,
    OperationInProgress,
    OriginNotVerified,
    ReplanLimitReached,
    RetentionConfigurationMissing,
    StaleJourneyVersion,
    ToolUnavailable,
)
from adaptsg.planning import JourneyPlanner, JourneyReplanner
from adaptsg.preference_parser import BedrockPreferenceParser, DeterministicPreferenceParser
from adaptsg.presentation import itinerary_rows, retained_segment_percentage
from adaptsg.settings import Settings
from adaptsg.tools.catalog import VenueCatalog
from adaptsg.tools.environment import DemoEnvironmentClient
from adaptsg.tools.location import DemoLocationClient, LocationClient
from adaptsg.tools.origin import DEFAULT_ORIGIN_LABEL
from adaptsg.web_api import create_app


def test_live_mode_fails_closed_without_production_trust_configuration() -> None:
    with pytest.raises(Exception, match="ADAPTSG_AUTHENTICATION_MODE"):
        build_service(Settings(adaptsg_mode="live"))


def test_local_live_mode_uses_live_providers_with_demo_auth() -> None:
    service = build_service(
        Settings(
            adaptsg_mode="live",
            adaptsg_provider_mode="live",
            adaptsg_local_live_enabled=True,
            onemap_api_token="test-onemap-token",
            lta_account_key="test-lta-key",
        )
    )
    assert service.mode == "live"
    assert service.auth_mode == "demo"
    assert service.local_live


def test_default_service_never_falls_back_from_bedrock_in_live_mode() -> None:
    service = build_service(
        Settings(
            _env_file=None,
            adaptsg_mode="live",
            adaptsg_provider_mode="live",
            adaptsg_local_live_enabled=True,
            adaptsg_bedrock_enabled=True,
            onemap_api_token="test-onemap-token",
            lta_account_key="test-lta-key",
        )
    )

    assert isinstance(service.parser, BedrockPreferenceParser)
    assert not service.parser.allow_fallback


def test_cognito_auth_is_independent_from_deterministic_providers() -> None:
    service = build_service(
        Settings(
            adaptsg_mode="demo",
            adaptsg_provider_mode="demo",
            adaptsg_authentication_mode="cognito",
        )
    )
    assert service.mode == "demo"
    assert service.auth_mode == "cognito"
    assert isinstance(service.parser, DeterministicPreferenceParser)


def test_phase_two_models_reject_unknown_fields_and_prohibited_risk_is_typed() -> None:
    with pytest.raises(ValidationError):
        PrincipalContext.model_validate(
            {"principal_id": "p", "account_id": "a", "authenticated": True, "extra": "nope"}
        )
    assert ActionRisk.PROHIBITED.value == "prohibited"


def test_capabilities_default_off_and_prohibited_booking_write_cannot_be_enabled() -> None:
    resolver = CapabilityResolver()
    assert not resolver.enabled(Capability.BOOKING_READ)
    assert not resolver.enabled(Capability.BOOKING_WRITE)
    approved = CapabilityResolver(
        CapabilityPolicy(
            flags=frozenset({FeatureFlag.BOOKING_READ}),
            production_retention_configured=True,
        )
    )
    assert approved.enabled(Capability.BOOKING_READ, production=True)
    with pytest.raises(CapabilityDisabled):
        resolver.require(Capability.BOOKING_WRITE)


def test_authorization_denies_anonymous_and_cross_account_without_active_grant() -> None:
    policy = AuthorizationPolicy()
    with pytest.raises(AuthorizationDenied):
        policy.require(PrincipalContext(principal_id="p", account_id="a"), Capability.JOURNEY_READ)
    caregiver = PrincipalContext(
        principal_id="cg",
        account_id="a",
        roles=frozenset({ActorRole.CAREGIVER}),
        authenticated=True,
    )
    with pytest.raises(Exception, match="authority grant"):
        policy.require(caregiver, Capability.JOURNEY_READ, subject="traveller")


def test_authority_grant_is_scoped_and_revocation_fails_closed() -> None:
    now = datetime.now(UTC)
    store = InMemoryAuthorityStore()
    grant = AuthorityGrant(
        subject="traveller",
        delegate="cg",
        issuer="traveller",
        capabilities=frozenset({Capability.JOURNEY_READ}),
        valid_from=now,
        valid_until=now + timedelta(minutes=5),
        scope=frozenset({"journey-1"}),
    )
    store.put(grant)
    assert store.get("traveller", "cg").active_at(now)
    revoked = store.revoke("traveller", "cg", at=now + timedelta(seconds=1))
    assert not revoked.active_at(now + timedelta(seconds=2))


def test_consent_withdrawal_blocks_processing_but_retains_record() -> None:
    now = datetime.now(UTC)
    store = InMemoryConsentStore()
    record = store.create(
        ConsentRecord(
            subject="traveller",
            policy_version="v1",
            actor="traveller",
            purpose=ConsentPurpose.JOURNEY_PLANNING,
            data_categories=frozenset({"constraints"}),
            granted_at=now,
        ),
        idempotency_key="consent-1",
    )
    revoked = store.revoke(record.id, expected_version=1, at=now + timedelta(seconds=1))
    assert store.get(record.id).revoked_at == revoked.revoked_at
    with pytest.raises(ConsentRequired):
        AuthorizationPolicy(consent=store).require(
            PrincipalContext(
                principal_id="traveller",
                account_id="a",
                roles=frozenset({ActorRole.TRAVELLER}),
                authenticated=True,
            ),
            Capability.JOURNEY_READ,
            purpose=ConsentPurpose.JOURNEY_PLANNING,
            categories=frozenset({"constraints"}),
            now=now + timedelta(seconds=2),
        )


def test_action_intent_binds_payload_version_and_replays_identical_result() -> None:
    clock = MutableClock(datetime(2026, 9, 1, 10, 0, tzinfo=UTC))
    service = ActionIntentService(clock=clock)
    principal = PrincipalContext(
        principal_id="p", account_id="a", roles=frozenset({ActorRole.TRAVELLER}), authenticated=True
    )
    intent = service.issue(
        principal=principal,
        target="journey-1",
        capability=Capability.JOURNEY_WRITE,
        payload={"decision": "approve"},
        expected_state_version=2,
    )
    assert (
        service.consume(
            intent.id,
            principal=principal,
            payload={"decision": "approve"},
            state_version=2,
            result="ok",
        )
        == "ok"
    )
    assert (
        service.consume(
            intent.id,
            principal=principal,
            payload={"decision": "approve"},
            state_version=2,
            result="different",
        )
        == "ok"
    )
    with pytest.raises(IntentConflict):
        service.consume(
            intent.id,
            principal=principal,
            payload={"decision": "reject"},
            state_version=2,
            result="bad",
        )


def test_action_intent_expiry_and_agent_issuance_are_denied() -> None:
    clock = MutableClock(datetime(2026, 9, 1, 10, 0, tzinfo=UTC))
    service = ActionIntentService(clock=clock)
    agent = PrincipalContext(
        principal_id="agent", account_id="a", roles=frozenset({ActorRole.AGENT}), authenticated=True
    )
    with pytest.raises(AuthorizationDenied):
        service.issue(
            principal=agent,
            target="x",
            capability=Capability.JOURNEY_WRITE,
            payload={},
            expected_state_version=1,
        )
    user = agent.model_copy(
        update={"principal_id": "user", "roles": frozenset({ActorRole.TRAVELLER})}
    )
    intent = service.issue(
        principal=user,
        target="x",
        capability=Capability.JOURNEY_WRITE,
        payload={},
        expected_state_version=1,
    )
    clock.value += timedelta(minutes=5)
    with pytest.raises(IntentConflict, match="expired"):
        service.consume(intent.id, principal=user, payload={}, state_version=1, result="x")


def test_audit_chain_is_redacted_and_conditional_failure_blocks_append() -> None:
    store = InMemoryAuditStore()
    event = AuditEvent(
        correlation_id=UUID(int=1),
        actor_role=ActorRole.SYSTEM,
        capability=Capability.JOURNEY_WRITE,
        transition="approve",
        outcome=TransitionOutcome.ACCEPTED,
        timestamp=datetime.now(UTC),
        metadata={"operation": "approve", "version": 2},
    )
    stored = store.append(event, expected_previous_hash=None)
    assert stored.event_hash and stored.previous_hash is None
    with pytest.raises(ValueError, match="sensitive"):
        AuditEvent(
            correlation_id=UUID(int=2),
            actor_role=ActorRole.SYSTEM,
            capability=Capability.JOURNEY_WRITE,
            transition="x",
            outcome=TransitionOutcome.REJECTED,
            timestamp=datetime.now(UTC),
            metadata={"prompt": "symptom canary"},
        )
    with pytest.raises(AuditUnavailable):
        store.append(event, expected_previous_hash="0" * 64)
    store.fail_writes = True
    with pytest.raises(AuditUnavailable):
        store.append(event, expected_previous_hash=stored.event_hash)


class RaisingParser:
    def parse(self, _prompt: str, *, journey_date: date) -> ParseOutcome:
        raise RuntimeError(f"provider unavailable on {journey_date}")


class FixedParser:
    def __init__(self, request: JourneyRequest) -> None:
        self.request = request

    def parse(self, _prompt: str, *, journey_date: date) -> ParseOutcome:
        assert journey_date == self.request.journey_date
        return ParseOutcome(request=self.request, source="fixed")


def make_service(
    planner: JourneyPlanner,
    replanner: JourneyReplanner,
    *,
    environment: DemoEnvironmentClient | None = None,
    location: LocationClient | None = None,
    store: JourneyStore | None = None,
    clock: Any | None = None,
    ttl_hours: int = 24,
    auth_mode: str = "demo",
) -> AdaptSGService:
    return AdaptSGService(
        parser=DeterministicPreferenceParser(VenueCatalog()),
        planner=planner,
        replanner=replanner,
        environment=environment or DemoEnvironmentClient(),
        location=location,
        store=store,
        clock=clock,
        ttl_hours=ttl_hours,
        auth_mode=auth_mode,
    )


class MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


def start_and_approve(service: AdaptSGService, *, suffix: str = "base") -> JourneyState:
    draft = service.start_journey(
        "Plan 10 am-5 pm for a wheelchair user, lunch by 1 pm, budget $70.",
        journey_date=date(2026, 9, 1),
        idempotency_key=f"start-{suffix}-key",
    )
    assert draft.pending_initial_itinerary is not None
    return service.decide_journey(
        draft.journey_id,
        decision=ApprovalDecision.APPROVE,
        target_id=draft.pending_initial_itinerary.id,
        expected_version=draft.version,
        idempotency_key=f"approve-{suffix}-key",
    )


def test_start_location_is_resolved_before_live_planning(
    planner: JourneyPlanner, replanner: JourneyReplanner
) -> None:
    service = make_service(
        planner,
        replanner,
        location=DemoLocationClient(),
    )

    draft = service.start_journey(
        "Plan a safe day starting from City Hall.",
        journey_date=date(2026, 9, 2),
        idempotency_key="resolve-city-hall-1",
    )

    assert draft.pending_initial_itinerary is not None
    first_route = draft.pending_initial_itinerary.segments[0].route
    assert first_route.origin_label == "City Hall"
    assert first_route.origin == Location(lat=1.2931, lng=103.8520)


def test_start_location_asks_the_user_when_it_cannot_pin_one_place(
    planner: JourneyPlanner, replanner: JourneyReplanner
) -> None:
    """A query the gazetteer cannot resolve is an input problem, not an outage."""
    timestamp = datetime(2026, 9, 2, tzinfo=UTC)
    missing = Mock()
    missing.search.return_value = ()
    with pytest.raises(OriginNotVerified) as missing_error:
        make_service(planner, replanner, location=missing).start_journey(
            "Plan a safe day starting from Nowhere Place.",
            journey_date=date(2026, 9, 2),
            idempotency_key="missing-location-1",
        )
    assert missing_error.value.candidates == ()

    ambiguous = Mock()
    ambiguous.search.return_value = (
        LocationSearchResult(
            label="Orchard Road",
            location=Location(lat=1.3048, lng=103.8318),
            source="onemap_search",
            source_timestamp=timestamp,
        ),
        LocationSearchResult(
            label="Orchard Boulevard",
            location=Location(lat=1.3020, lng=103.8239),
            source="onemap_search",
            source_timestamp=timestamp,
        ),
    )
    with pytest.raises(OriginNotVerified) as ambiguous_error:
        make_service(planner, replanner, location=ambiguous).start_journey(
            "Plan a safe day starting from Orchard.",
            journey_date=date(2026, 9, 2),
            idempotency_key="ambiguous-location-1",
        )
    assert ambiguous_error.value.candidates == ("Orchard Road", "Orchard Boulevard")

    unverified = Mock()
    unverified.search.return_value = (
        LocationSearchResult(
            label="Orchard",
            location=Location(lat=1.3048, lng=103.8318),
            source="",
            source_timestamp=timestamp,
        ),
    )
    with pytest.raises(ToolUnavailable, match="unverified"):
        make_service(planner, replanner, location=unverified).start_journey(
            "Plan a safe day starting from Orchard.",
            journey_date=date(2026, 9, 2),
            idempotency_key="unverified-location-1",
        )


def test_provider_outage_still_fails_closed_as_tool_unavailable(
    planner: JourneyPlanner, replanner: JourneyReplanner
) -> None:
    """A gazetteer that cannot answer is distinct from one that answers 'no match'."""
    outage = Mock()
    outage.search.side_effect = ToolUnavailable("OneMap location verification failed: timeout")
    with pytest.raises(ToolUnavailable, match="OneMap"):
        make_service(planner, replanner, location=outage).start_journey(
            "Plan a safe day starting from Orchard.",
            journey_date=date(2026, 9, 2),
            idempotency_key="outage-location-1",
        )


def test_start_location_ladder_recovers_from_a_multi_code_station_label(
    planner: JourneyPlanner, replanner: JourneyReplanner
) -> None:
    """Regression: OneMap indexes (NS17) and (CC15) separately, so the pair matches nothing."""
    timestamp = datetime(2026, 9, 2, tzinfo=UTC)
    resolved = LocationSearchResult(
        label="BISHAN MRT STATION (NS17)",
        location=Location(lat=1.35131, lng=103.8491),
        source="onemap_search",
        source_timestamp=timestamp,
    )
    location = Mock()
    location.search.side_effect = lambda query: (
        (resolved,) if query == "Bishan MRT Station (NS17)" else ()
    )

    draft = make_service(planner, replanner, location=location).start_journey(
        "Plan a safe day starting from Bishan MRT Station (NS17/CC15).",
        journey_date=date(2026, 9, 2),
        idempotency_key="bishan-ladder-1",
    )

    assert draft.pending_initial_itinerary is not None
    assert draft.pending_initial_itinerary.segments[0].route.origin_label == (
        "BISHAN MRT STATION (NS17)"
    )
    assert location.search.call_args_list[0].args == ("Bishan MRT Station (NS17/CC15)",)
    assert any("was verified as" in warning for warning in draft.warnings)


def test_vague_origin_plans_from_the_documented_default_hub(
    planner: JourneyPlanner, replanner: JourneyReplanner
) -> None:
    """A prompt naming no starting point still plans, and says where it started."""
    location = Mock()
    location.search.side_effect = DemoLocationClient().search

    draft = make_service(planner, replanner, location=location).start_journey(
        "Plan a full-day outing for two people, starting from a convenient MRT station.",
        journey_date=date(2026, 9, 2),
        idempotency_key="vague-origin-1",
    )

    assert draft.pending_initial_itinerary is not None
    assert draft.pending_initial_itinerary.segments[0].route.origin_label == DEFAULT_ORIGIN_LABEL
    assert any(DEFAULT_ORIGIN_LABEL in warning for warning in draft.warnings)


def test_chosen_candidate_is_reverified_before_planning(
    planner: JourneyPlanner, replanner: JourneyReplanner
) -> None:
    """The client returns a label, never coordinates; the server geocodes it again."""
    timestamp = datetime(2026, 9, 2, tzinfo=UTC)
    chosen = LocationSearchResult(
        label="Orchard Road",
        location=Location(lat=1.3048, lng=103.8318),
        source="onemap_search",
        source_timestamp=timestamp,
    )
    location = Mock()
    location.search.side_effect = lambda query: (chosen,) if query == "Orchard Road" else ()

    draft = make_service(planner, replanner, location=location).start_journey(
        "Plan a safe day starting from Orchard.",
        journey_date=date(2026, 9, 2),
        start_label="Orchard Road",
        idempotency_key="chosen-origin-1",
    )

    assert draft.pending_initial_itinerary is not None
    assert draft.pending_initial_itinerary.segments[0].route.origin_label == "Orchard Road"
    location.search.assert_any_call("Orchard Road")


def test_start_location_prefers_one_exact_match(
    planner: JourneyPlanner, replanner: JourneyReplanner
) -> None:
    timestamp = datetime(2026, 9, 2, tzinfo=UTC)
    location = Mock()
    location.search.return_value = (
        LocationSearchResult(
            label="City Hall MRT",
            location=Location(lat=1.2932, lng=103.8522),
            source="onemap_search",
            source_timestamp=timestamp,
        ),
        LocationSearchResult(
            label="City Hall",
            location=Location(lat=1.2931, lng=103.8520),
            source="onemap_search",
            source_timestamp=timestamp,
        ),
    )
    draft = make_service(planner, replanner, location=location).start_journey(
        "Plan a safe day starting from City Hall.",
        journey_date=date(2026, 9, 2),
        idempotency_key="exact-location-1",
    )
    assert draft.pending_initial_itinerary is not None
    first_route = draft.pending_initial_itinerary.segments[0].route
    assert first_route.origin_label == "City Hall"
    assert first_route.origin == Location(lat=1.2931, lng=103.8520)


def test_journey_state_rejects_invalid_lifecycle(itinerary: Itinerary) -> None:
    now = datetime.now(UTC)
    with pytest.raises(ValidationError, match="pending initial itinerary"):
        JourneyState(
            status=JourneyStatus.DRAFT,
            current_itinerary=itinerary,
            created_at=now,
            updated_at=now,
            expires_at=now + timedelta(hours=24),
        )


def test_journey_state_requires_aware_ordered_timestamps(itinerary: Itinerary) -> None:
    now = datetime.now(UTC)
    with pytest.raises(ValidationError, match="timezone-aware"):
        JourneyState(
            status=JourneyStatus.DRAFT,
            pending_initial_itinerary=itinerary,
            created_at=now.replace(tzinfo=None),
            updated_at=now,
            expires_at=now + timedelta(hours=24),
        )


def test_journey_approval_and_rejection_are_server_authoritative(
    planner: JourneyPlanner, replanner: JourneyReplanner
) -> None:
    service = make_service(planner, replanner)
    draft = service.start_journey(
        "Plan a day. We would like to visit Gardens by the Bay.",
        journey_date=date(2026, 9, 2),
        idempotency_key="create-draft-001",
    )
    assert draft.status is JourneyStatus.DRAFT
    assert draft.current_itinerary is None
    assert draft.pending_initial_itinerary is not None

    active = service.decide_journey(
        draft.journey_id,
        decision=ApprovalDecision.APPROVE,
        target_id=draft.pending_initial_itinerary.id,
        expected_version=draft.version,
        idempotency_key="approve-draft-01",
    )
    assert active.status is JourneyStatus.ACTIVE
    assert active.current_itinerary == draft.pending_initial_itinerary
    assert service.get_journey(active.journey_id) == active

    rejected_draft = service.start_journey(
        "Plan a quiet day.",
        journey_date=date(2026, 9, 2),
        idempotency_key="create-draft-002",
    )
    assert rejected_draft.pending_initial_itinerary is not None
    rejected = service.decide_journey(
        rejected_draft.journey_id,
        decision=ApprovalDecision.REJECT,
        target_id=rejected_draft.pending_initial_itinerary.id,
        expected_version=rejected_draft.version,
        idempotency_key="reject-draft-01",
    )
    assert rejected.status is JourneyStatus.REJECTED
    with pytest.raises(InvalidJourneyTransition, match="rejected"):
        service.propose_replan(
            rejected.journey_id,
            ReplanTrigger(type=TriggerType.FATIGUE, message="Tired"),
            expected_version=rejected.version,
            idempotency_key="replan-rejected-1",
        )


def test_replan_decisions_preserve_or_replace_the_server_plan(
    planner: JourneyPlanner, replanner: JourneyReplanner
) -> None:
    service = make_service(planner, replanner)
    active = start_and_approve(service)
    assert active.current_itinerary is not None
    original_id = active.current_itinerary.id

    private_trigger_message = "Private caregiver note: traveller is tired"
    pending = service.propose_replan(
        active.journey_id,
        ReplanTrigger(type=TriggerType.FATIGUE, message=private_trigger_message),
        expected_version=active.version,
        idempotency_key="fatigue-proposal-1",
    )
    assert pending.latest_replan_proposal is not None
    assert private_trigger_message not in pending.model_dump_json()
    assert all(
        change.reason == "Verified fatigue adjustment"
        for change in pending.latest_replan_proposal.changes
    )
    rejected = service.decide_journey(
        pending.journey_id,
        decision=ApprovalDecision.REJECT,
        target_id=pending.latest_replan_proposal.id,
        expected_version=pending.version,
        idempotency_key="reject-proposal-1",
    )
    assert rejected.current_itinerary is not None
    assert rejected.current_itinerary.id == original_id

    pending_again = service.propose_replan(
        rejected.journey_id,
        ReplanTrigger(type=TriggerType.FATIGUE, message="Still tired"),
        expected_version=rejected.version,
        idempotency_key="fatigue-proposal-2",
    )
    assert pending_again.latest_replan_proposal is not None
    approved = service.decide_journey(
        pending_again.journey_id,
        decision=ApprovalDecision.APPROVE,
        target_id=pending_again.latest_replan_proposal.id,
        expected_version=pending_again.version,
        idempotency_key="approve-proposal-1",
    )
    assert approved.current_itinerary is not None
    assert approved.current_itinerary.id == pending_again.latest_replan_proposal.itinerary.id
    assert approved.current_itinerary.id != original_id


def test_idempotency_replays_and_conflicts(
    planner: JourneyPlanner, replanner: JourneyReplanner
) -> None:
    store = InMemoryJourneyStore()
    service = make_service(planner, replanner, store=store)
    first = service.start_journey(
        "Plan a safe day.",
        journey_date=date(2026, 9, 2),
        idempotency_key="same-action-key",
    )
    replay = service.start_journey(
        "Plan a safe day.",
        journey_date=date(2026, 9, 2),
        idempotency_key="same-action-key",
    )
    assert replay == first
    assert list(store._replays) == [service._hash_idempotency_key("same-action-key")]
    with pytest.raises(IdempotencyConflict):
        service.start_journey(
            "Plan a different day.",
            journey_date=date(2026, 9, 2),
            idempotency_key="same-action-key",
        )


def test_operational_logs_exclude_free_sensitive_text(
    planner: JourneyPlanner,
    replanner: JourneyReplanner,
    caplog: pytest.LogCaptureFixture,
) -> None:
    prompt = "Private care details and a safe day"
    retry_key = "private-retry-key"
    service = make_service(planner, replanner)
    with caplog.at_level("INFO", logger="adaptsg.agent"):
        state = service.start_journey(
            prompt,
            journey_date=date(2026, 9, 2),
            idempotency_key=retry_key,
        )
    event = caplog.records[-1].message
    parsed = json.loads(event)
    assert parsed["operation"] == "start_journey"
    assert parsed["journey_id"] == str(state.journey_id)
    assert parsed["storage"] == "memory_demo"
    assert prompt not in event
    assert retry_key not in event


def test_versions_pending_conflicts_and_validator_reruns(
    planner: JourneyPlanner,
    replanner: JourneyReplanner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = make_service(planner, replanner)
    draft = service.start_journey(
        "Plan a safe day.",
        journey_date=date(2026, 9, 2),
        idempotency_key="validator-draft-1",
    )
    assert draft.pending_initial_itinerary is not None
    original_validate = planner.validator.validate
    calls: list[UUID] = []

    def spy_validate(candidate: Itinerary) -> Any:
        calls.append(candidate.id)
        return original_validate(candidate)

    monkeypatch.setattr(planner.validator, "validate", spy_validate)
    active = service.decide_journey(
        draft.journey_id,
        decision=ApprovalDecision.APPROVE,
        target_id=draft.pending_initial_itinerary.id,
        expected_version=draft.version,
        idempotency_key="validator-approve-1",
    )
    assert calls == [draft.pending_initial_itinerary.id]
    with pytest.raises(StaleJourneyVersion):
        service.propose_replan(
            active.journey_id,
            ReplanTrigger(type=TriggerType.FATIGUE, message="Tired"),
            expected_version=draft.version,
            idempotency_key="stale-version-key",
        )

    pending = service.propose_replan(
        active.journey_id,
        ReplanTrigger(type=TriggerType.FATIGUE, message="Tired"),
        expected_version=active.version,
        idempotency_key="pending-version-key",
    )
    with pytest.raises(InvalidJourneyTransition, match="pending proposal"):
        service.propose_replan(
            pending.journey_id,
            ReplanTrigger(type=TriggerType.HEAVY_RAIN, message="Rain"),
            expected_version=pending.version,
            idempotency_key="second-pending-key",
        )


def test_failed_approval_retains_the_pending_draft(
    planner: JourneyPlanner,
    replanner: JourneyReplanner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = make_service(planner, replanner)
    draft = service.start_journey(
        "Plan a safe day.",
        journey_date=date(2026, 9, 2),
        idempotency_key="invalid-approval-draft",
    )
    assert draft.pending_initial_itinerary is not None
    monkeypatch.setattr(
        planner.validator,
        "validate",
        lambda _candidate: ValidationResult(
            valid=False,
            issues=(ValidationIssue(code=ValidationCode.BUDGET, message="invalid"),),
        ),
    )
    with pytest.raises(NoFeasibleItinerary, match="approval validation"):
        service.decide_journey(
            draft.journey_id,
            decision=ApprovalDecision.APPROVE,
            target_id=draft.pending_initial_itinerary.id,
            expected_version=draft.version,
            idempotency_key="invalid-approval-key",
        )
    assert service.get_journey(draft.journey_id) == draft


def test_ttl_and_in_progress_reservations_use_injected_clock(
    planner: JourneyPlanner, replanner: JourneyReplanner
) -> None:
    clock = MutableClock(datetime(2026, 9, 2, tzinfo=UTC))
    store = InMemoryJourneyStore(clock=clock)
    service = make_service(planner, replanner, store=store, clock=clock)
    state = service.start_journey(
        "Plan a safe day.",
        journey_date=date(2026, 9, 2),
        idempotency_key="expiring-state-1",
    )
    assert state.expires_at == clock.value + timedelta(hours=24)
    store.reserve("reserved-hash", "request-hash", int(state.expires_at.timestamp()))
    with pytest.raises(OperationInProgress):
        store.reserve("reserved-hash", "request-hash", int(state.expires_at.timestamp()))
    clock.value += timedelta(hours=24, seconds=1)
    with pytest.raises(JourneyNotFound):
        service.get_journey(state.journey_id)


def test_failed_replan_releases_retry_key_without_mutating_state(
    planner: JourneyPlanner,
    replanner: JourneyReplanner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = make_service(planner, replanner)
    active = start_and_approve(service, suffix="failure")
    original = replanner.propose

    def unavailable(_itinerary: Itinerary, _trigger: ReplanTrigger) -> Any:
        raise ToolUnavailable("live route verification failed")

    monkeypatch.setattr(replanner, "propose", unavailable)
    trigger = ReplanTrigger(type=TriggerType.FATIGUE, message="Tired")
    with pytest.raises(ToolUnavailable):
        service.propose_replan(
            active.journey_id,
            trigger,
            expected_version=active.version,
            idempotency_key="retry-after-fail",
        )
    assert service.get_journey(active.journey_id) == active
    monkeypatch.setattr(replanner, "propose", original)
    retried = service.propose_replan(
        active.journey_id,
        trigger,
        expected_version=active.version,
        idempotency_key="retry-after-fail",
    )
    assert retried.version == active.version + 1


def test_no_feasible_start_releases_reservation_without_state(
    planner: JourneyPlanner,
    replanner: JourneyReplanner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = InMemoryJourneyStore()
    service = make_service(planner, replanner, store=store)
    original = planner.create

    def infeasible(*_args: Any, **_kwargs: Any) -> Any:
        raise NoFeasibleItinerary("no safe plan")

    monkeypatch.setattr(planner, "create", infeasible)
    with pytest.raises(NoFeasibleItinerary):
        service.start_journey(
            "Plan a safe day.",
            journey_date=date(2026, 9, 2),
            idempotency_key="failed-start-key",
        )
    assert store._journeys == {}
    assert store._replays == {}
    monkeypatch.setattr(planner, "create", original)
    assert (
        service.start_journey(
            "Plan a safe day.",
            journey_date=date(2026, 9, 2),
            idempotency_key="failed-start-key",
        ).status
        is JourneyStatus.DRAFT
    )


def test_stateful_replanning_enforces_configured_cap(
    planner: JourneyPlanner, replanner: JourneyReplanner
) -> None:
    service = make_service(planner, replanner)
    state = start_and_approve(service, suffix="cap")
    for index in range(2):
        pending = service.propose_replan(
            state.journey_id,
            ReplanTrigger(type=TriggerType.FATIGUE, message=f"Tired {index}"),
            expected_version=state.version,
            idempotency_key=f"cap-proposal-{index}",
        )
        assert pending.latest_replan_proposal is not None
        state = service.decide_journey(
            pending.journey_id,
            decision=ApprovalDecision.APPROVE,
            target_id=pending.latest_replan_proposal.id,
            expected_version=pending.version,
            idempotency_key=f"cap-approval-{index}",
        )
    with pytest.raises(ReplanLimitReached):
        service.propose_replan(
            state.journey_id,
            ReplanTrigger(type=TriggerType.FATIGUE, message="Tired again"),
            expected_version=state.version,
            idempotency_key="cap-proposal-final",
        )


def test_dynamodb_store_uses_conditional_transaction(itinerary: Itinerary) -> None:
    now = datetime(2026, 9, 2, tzinfo=UTC)
    state = JourneyState(
        status=JourneyStatus.DRAFT,
        pending_initial_itinerary=itinerary,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(hours=24),
    )
    client = Mock()
    store = DynamoDBJourneyStore(table_name="journeys", client=client, clock=lambda: now)
    assert store.reserve("key-hash", "fingerprint", int(state.expires_at.timestamp())) is None
    store.commit(
        state,
        expected_version=None,
        key_hash="key-hash",
        fingerprint="fingerprint",
        expires_epoch=int(state.expires_at.timestamp()),
    )
    reserve_call = client.put_item.call_args.kwargs
    assert reserve_call["Item"]["pk"]["S"] == "IDEMPOTENCY#key-hash"
    assert reserve_call["ConditionExpression"] == "attribute_not_exists(pk)"
    transaction = client.transact_write_items.call_args.kwargs["TransactItems"]
    assert transaction[0]["Put"]["ConditionExpression"] == "attribute_not_exists(pk)"
    assert transaction[1]["Update"]["ConditionExpression"].startswith("fingerprint =")

    client.get_item.return_value = {
        "Item": {
            "expires_at": {"N": str(int(state.expires_at.timestamp()))},
            "state_json": {"S": state.model_dump_json()},
        }
    }
    assert store.get(state.journey_id) == state


def test_dynamodb_store_maps_transaction_conflict(itinerary: Itinerary) -> None:
    now = datetime(2026, 9, 2, tzinfo=UTC)
    state = JourneyState(
        status=JourneyStatus.DRAFT,
        pending_initial_itinerary=itinerary,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(hours=24),
    )
    client = Mock()
    client.transact_write_items.side_effect = ClientError(
        {"Error": {"Code": "TransactionCanceledException", "Message": "conflict"}},
        "TransactWriteItems",
    )
    store = DynamoDBJourneyStore(table_name="journeys", client=client, clock=lambda: now)
    with pytest.raises(StaleJourneyVersion):
        store.commit(
            state,
            expected_version=1,
            key_hash="key-hash",
            fingerprint="fingerprint",
            expires_epoch=int(state.expires_at.timestamp()),
        )


@pytest.mark.parametrize(
    ("operation_status", "stored_fingerprint", "expected_error"),
    (
        ("in_progress", "fingerprint", OperationInProgress),
        ("complete", "other-fingerprint", IdempotencyConflict),
    ),
)
def test_dynamodb_reservation_conflicts(
    operation_status: str,
    stored_fingerprint: str,
    expected_error: type[Exception],
) -> None:
    now = datetime(2026, 9, 2, tzinfo=UTC)
    client = Mock()
    client.put_item.side_effect = ClientError(
        {"Error": {"Code": "ConditionalCheckFailedException", "Message": "exists"}},
        "PutItem",
    )
    client.get_item.return_value = {
        "Item": {
            "fingerprint": {"S": stored_fingerprint},
            "operation_status": {"S": operation_status},
            "expires_at": {"N": str(int((now + timedelta(hours=1)).timestamp()))},
        }
    }
    store = DynamoDBJourneyStore(table_name="journeys", client=client, clock=lambda: now)
    with pytest.raises(expected_error):
        store.reserve("key-hash", "fingerprint", int((now + timedelta(hours=24)).timestamp()))


def test_dynamodb_completed_replay_and_expiry(itinerary: Itinerary) -> None:
    now = datetime(2026, 9, 2, tzinfo=UTC)
    state = JourneyState(
        status=JourneyStatus.DRAFT,
        pending_initial_itinerary=itinerary,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(hours=24),
    )
    client = Mock()
    client.put_item.side_effect = ClientError(
        {"Error": {"Code": "ConditionalCheckFailedException", "Message": "exists"}},
        "PutItem",
    )
    client.get_item.return_value = {
        "Item": {
            "fingerprint": {"S": "fingerprint"},
            "operation_status": {"S": "complete"},
            "response_json": {"S": state.model_dump_json()},
            "expires_at": {"N": str(int(state.expires_at.timestamp()))},
        }
    }
    store = DynamoDBJourneyStore(table_name="journeys", client=client, clock=lambda: now)
    assert store.reserve("key-hash", "fingerprint", int(state.expires_at.timestamp())) == state

    client.get_item.return_value = {}
    with pytest.raises(JourneyNotFound):
        store.get(state.journey_id)
    client.get_item.return_value = {
        "Item": {
            "state_json": {"S": state.model_dump_json()},
            "expires_at": {"N": str(int(now.timestamp()))},
        }
    }
    with pytest.raises(JourneyNotFound):
        store.get(state.journey_id)


def test_dynamodb_release_contains_no_raw_key(caplog: pytest.LogCaptureFixture) -> None:
    now = datetime(2026, 9, 2, tzinfo=UTC)
    client = Mock()
    client.delete_item.side_effect = ClientError(
        {"Error": {"Code": "ConditionalCheckFailedException", "Message": "changed"}},
        "DeleteItem",
    )
    store = DynamoDBJourneyStore(table_name="journeys", client=client, clock=lambda: now)
    store.release("hashed-key", "fingerprint")
    assert "failed to release" in caplog.text
    assert "hashed-key" not in caplog.text


def test_dynamodb_journey_and_audit_commit_are_atomic(itinerary: Itinerary) -> None:
    now = datetime(2026, 9, 2, tzinfo=UTC)
    state = JourneyState(
        status=JourneyStatus.DRAFT,
        pending_initial_itinerary=itinerary,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(hours=24),
    )
    event = AuditEvent(
        correlation_id=state.journey_id,
        actor_role=ActorRole.SYSTEM,
        capability=Capability.JOURNEY_WRITE,
        transition="start_journey",
        outcome=TransitionOutcome.ACCEPTED,
        timestamp=now,
        metadata={"operation": "start_journey", "version": 1},
    )
    client = Mock()
    journey_store = DynamoDBJourneyStore(table_name="state", client=client)
    audit_store = DynamoDBAuditStore(table_name="state", client=client, retention_days=90)

    stored = journey_store.commit_with_audit(
        state,
        expected_version=None,
        key_hash="key-hash",
        fingerprint="fingerprint",
        expires_epoch=int(state.expires_at.timestamp()),
        audit=audit_store,
        audit_event=event,
        expected_previous_hash=None,
    )

    items = client.transact_write_items.call_args.kwargs["TransactItems"]
    assert len(items) == 4
    assert items[0]["Put"]["Item"]["record_type"]["S"] == "journey"
    assert items[2]["Put"]["Item"]["record_type"]["S"] == "audit_head"
    assert items[3]["Put"]["Item"]["record_type"]["S"] == "audit_event"
    assert stored.event_hash is not None

    client.transact_write_items.side_effect = ClientError(
        {"Error": {"Code": "TransactionCanceledException", "Message": "audit conflict"}},
        "TransactWriteItems",
    )
    with pytest.raises(AuditUnavailable):
        journey_store.commit_with_audit(
            state,
            expected_version=None,
            key_hash="other-key",
            fingerprint="fingerprint",
            expires_epoch=int(state.expires_at.timestamp()),
            audit=audit_store,
            audit_event=event,
            expected_previous_hash=None,
        )


def test_dynamodb_audit_is_owner_partitioned_and_bounded() -> None:
    correlation_id = UUID(int=1)
    first = AuditEvent(
        correlation_id=correlation_id,
        actor_role=ActorRole.SYSTEM,
        capability=Capability.JOURNEY_WRITE,
        transition="start_journey",
        outcome=TransitionOutcome.ACCEPTED,
        timestamp=datetime(2026, 9, 2, 10, tzinfo=UTC),
        metadata={"operation": "start_journey", "version": 1},
    )
    second = first.model_copy(
        update={
            "timestamp": datetime(2026, 9, 2, 11, tzinfo=UTC),
            "transition": "approve_journey",
        }
    )
    client = Mock()
    store = DynamoDBAuditStore(table_name="state", client=client, retention_days=90)
    stored_first, first_items = store.prepare_append(first, expected_previous_hash=None)
    stored_second, second_items = store.prepare_append(
        second, expected_previous_hash=stored_first.event_hash
    )
    client.query.return_value = {
        "Items": [second_items[1]["Put"]["Item"], first_items[1]["Put"]["Item"]]
    }

    assert store.list(correlation_id=correlation_id) == (stored_first, stored_second)
    query = client.query.call_args.kwargs
    assert query["Limit"] == 100
    assert query["ScanIndexForward"] is False
    assert query["ExpressionAttributeValues"][":pk"]["S"] == f"AUDIT#{correlation_id}"
    with pytest.raises(AuthorizationDenied, match="global audit"):
        store.list()


def test_dynamodb_audit_append_and_head_fail_closed() -> None:
    event = AuditEvent(
        correlation_id=UUID(int=2),
        actor_role=ActorRole.SYSTEM,
        capability=Capability.JOURNEY_WRITE,
        transition="start_journey",
        outcome=TransitionOutcome.ACCEPTED,
        timestamp=datetime(2026, 9, 2, tzinfo=UTC),
        metadata={"operation": "start_journey", "version": 1},
    )
    client = Mock()
    store = DynamoDBAuditStore(table_name="state", client=client, retention_days=90)
    stored = store.append(event, expected_previous_hash=None)
    assert stored.event_hash is not None

    client.get_item.return_value = {"Item": {"head_hash": {"S": stored.event_hash}}}
    assert store.latest_hash(event.correlation_id) == stored.event_hash
    client.get_item.return_value = {}
    assert store.latest_hash(event.correlation_id) is None

    client.transact_write_items.side_effect = ClientError(
        {"Error": {"Code": "TransactionCanceledException", "Message": "conflict"}},
        "TransactWriteItems",
    )
    with pytest.raises(AuditUnavailable, match="audit chain changed"):
        store.append(event, expected_previous_hash=None)
    client.transact_write_items.side_effect = ClientError(
        {"Error": {"Code": "InternalServerError", "Message": "unavailable"}},
        "TransactWriteItems",
    )
    with pytest.raises(ClientError):
        store.append(event, expected_previous_hash=None)


def test_dynamodb_consent_survives_cold_start_and_retains_revocation() -> None:
    now = datetime(2026, 9, 2, tzinfo=UTC)
    record = ConsentRecord(
        subject="caregiver-sub",
        policy_version="consent-v1",
        actor="caregiver-sub",
        purpose=ConsentPurpose.JOURNEY_PLANNING,
        data_categories=frozenset({"journey_input", "location_routing"}),
        granted_at=now,
    )
    writer_client = Mock()
    writer = DynamoDBConsentStore(
        table_name="state", client=writer_client, revoked_retention_days=30
    )
    assert writer.create(record, idempotency_key="create-consent-key") == record
    transaction = writer_client.transact_write_items.call_args.kwargs["TransactItems"]
    record_item = transaction[0]["Put"]["Item"]
    current_item = transaction[1]["Put"]["Item"]

    cold_client = Mock()
    cold_client.get_item.side_effect = [
        {"Item": record_item},
        {"Item": current_item},
        {"Item": record_item},
        {"Item": current_item},
    ]
    cold_store = DynamoDBConsentStore(
        table_name="state", client=cold_client, revoked_retention_days=30
    )
    assert cold_store.get(record.id) == record
    assert (
        cold_store.find_current(
            subject=record.subject,
            purpose=record.purpose,
            categories=frozenset({"journey_input"}),
            policy_version="consent-v1",
            now=now,
        )
        == record
    )
    revoked = cold_store.revoke(record.id, expected_version=1, at=now + timedelta(hours=1))
    assert revoked.version == 2
    assert revoked.retention_expires_at == now + timedelta(days=30, hours=1)
    revoke_items = cold_client.transact_write_items.call_args.kwargs["TransactItems"]
    assert revoke_items[0]["Put"]["Item"]["expires_at"]["N"] == str(
        int(revoked.retention_expires_at.timestamp())
    )


def test_dynamodb_consent_idempotency_and_failure_paths() -> None:
    now = datetime(2026, 9, 2, tzinfo=UTC)
    record = ConsentRecord(
        subject="caregiver-sub",
        policy_version="consent-v1",
        actor="caregiver-sub",
        purpose=ConsentPurpose.JOURNEY_PLANNING,
        data_categories=frozenset({"journey_input"}),
        granted_at=now,
    )
    seed_client = Mock()
    seed = DynamoDBConsentStore(table_name="state", client=seed_client, revoked_retention_days=30)
    seed.create(record, idempotency_key="same-consent-key")
    idempotency_item = seed_client.transact_write_items.call_args.kwargs["TransactItems"][2]["Put"][
        "Item"
    ]
    cancelled = ClientError(
        {"Error": {"Code": "TransactionCanceledException", "Message": "duplicate"}},
        "TransactWriteItems",
    )

    replay_client = Mock()
    replay_client.transact_write_items.side_effect = cancelled
    replay_client.get_item.return_value = {"Item": idempotency_item}
    replay_store = DynamoDBConsentStore(
        table_name="state", client=replay_client, revoked_retention_days=30
    )
    assert replay_store.create(record, idempotency_key="same-consent-key") == record

    conflict_client = Mock()
    conflict_client.transact_write_items.side_effect = cancelled
    conflict_client.get_item.return_value = {
        "Item": {**idempotency_item, "fingerprint": {"S": "different"}}
    }
    with pytest.raises(IdempotencyConflict, match="consent idempotency"):
        DynamoDBConsentStore(
            table_name="state", client=conflict_client, revoked_retention_days=30
        ).create(record, idempotency_key="same-consent-key")

    pending_client = Mock()
    pending_client.transact_write_items.side_effect = cancelled
    pending_client.get_item.return_value = {}
    with pytest.raises(OperationInProgress, match="being completed"):
        DynamoDBConsentStore(
            table_name="state", client=pending_client, revoked_retention_days=30
        ).create(record, idempotency_key="same-consent-key")

    unavailable_client = Mock()
    unavailable_client.transact_write_items.side_effect = ClientError(
        {"Error": {"Code": "InternalServerError", "Message": "unavailable"}},
        "TransactWriteItems",
    )
    with pytest.raises(ClientError):
        DynamoDBConsentStore(
            table_name="state", client=unavailable_client, revoked_retention_days=30
        ).create(record, idempotency_key="same-consent-key")

    empty_client = Mock()
    empty_client.get_item.return_value = {}
    empty_store = DynamoDBConsentStore(
        table_name="state", client=empty_client, revoked_retention_days=30
    )
    with pytest.raises(ConsentRequired, match="not found"):
        empty_store.get(record.id)
    assert (
        empty_store.find_current(
            subject=record.subject,
            purpose=record.purpose,
            categories=record.data_categories,
            policy_version=record.policy_version,
            now=now,
        )
        is None
    )


def test_dynamodb_authority_grant_survives_cold_start() -> None:
    now = datetime(2026, 9, 2, tzinfo=UTC)
    grant = AuthorityGrant(
        subject="traveller-sub",
        delegate="caregiver-sub",
        capabilities=frozenset({Capability.JOURNEY_READ}),
        issuer="traveller-sub",
        valid_from=now,
        valid_until=now + timedelta(days=1),
    )
    writer_client = Mock()
    DynamoDBAuthorityStore(table_name="state", client=writer_client).put(grant)
    put = writer_client.put_item.call_args.kwargs
    stored_item = put["Item"]
    assert "attribute_not_exists(pk)" in put["ConditionExpression"]
    cold_client = Mock()
    cold_client.get_item.return_value = {"Item": stored_item}
    assert (
        DynamoDBAuthorityStore(table_name="state", client=cold_client).get(
            grant.subject, grant.delegate
        )
        == grant
    )


def test_dynamodb_authority_conflicts_and_revocation_are_conditional() -> None:
    now = datetime(2026, 9, 2, tzinfo=UTC)
    grant = AuthorityGrant(
        subject="traveller-sub",
        delegate="caregiver-sub",
        capabilities=frozenset({Capability.JOURNEY_READ}),
        issuer="traveller-sub",
        valid_from=now,
        valid_until=now + timedelta(days=1),
    )
    conflict_client = Mock()
    conflict_client.put_item.side_effect = ClientError(
        {"Error": {"Code": "ConditionalCheckFailedException", "Message": "active"}},
        "PutItem",
    )
    with pytest.raises(IdempotencyConflict, match="active authority grant"):
        DynamoDBAuthorityStore(table_name="state", client=conflict_client).put(grant)

    empty_client = Mock()
    empty_client.get_item.return_value = {}
    with pytest.raises(AuthorityGrantRequired):
        DynamoDBAuthorityStore(table_name="state", client=empty_client).get(
            grant.subject, grant.delegate
        )

    seed_client = Mock()
    seed_store = DynamoDBAuthorityStore(table_name="state", client=seed_client)
    seed_store.put(grant)
    item = seed_client.put_item.call_args.kwargs["Item"]
    revoke_client = Mock()
    revoke_client.get_item.return_value = {"Item": item}
    revoked = DynamoDBAuthorityStore(table_name="state", client=revoke_client).revoke(
        grant.subject, grant.delegate, at=now + timedelta(hours=1)
    )
    assert revoked.revoked_at == now + timedelta(hours=1)
    assert "revoked_at" in revoke_client.put_item.call_args.kwargs["Item"]

    revoked_item = revoke_client.put_item.call_args.kwargs["Item"]
    revoke_client.get_item.return_value = {"Item": revoked_item}
    assert (
        DynamoDBAuthorityStore(table_name="state", client=revoke_client).revoke(
            grant.subject, grant.delegate, at=now + timedelta(hours=2)
        )
        == revoked
    )


def test_dynamodb_action_intent_survives_cold_start_and_is_one_use() -> None:
    now = datetime(2026, 9, 2, tzinfo=UTC)
    principal = PrincipalContext(
        principal_id="caregiver-sub",
        account_id="caregiver-sub",
        roles=frozenset({ActorRole.CAREGIVER}),
        authenticated=True,
    )
    writer_client = Mock()
    writer = ActionIntentService(
        clock=lambda: now,
        store=DynamoDBActionIntentStore(table_name="state", client=writer_client),
    )
    intent = writer.issue(
        principal=principal,
        target="journey-1",
        capability=Capability.JOURNEY_WRITE,
        payload={"decision": "approve"},
        expected_state_version=1,
    )
    stored_item = writer_client.put_item.call_args.kwargs["Item"]

    cold_client = Mock()
    cold_client.get_item.return_value = {"Item": stored_item}
    cold = ActionIntentService(
        clock=lambda: now + timedelta(seconds=1),
        store=DynamoDBActionIntentStore(table_name="state", client=cold_client),
    )
    assert (
        cold.consume(
            intent.id,
            principal=principal,
            payload={"decision": "approve"},
            state_version=1,
            result="accepted",
        )
        == "accepted"
    )
    update = cold_client.update_item.call_args.kwargs
    assert update["ConditionExpression"] == "attribute_not_exists(used_at)"
    assert "used_at" in update["UpdateExpression"]

    used = intent.model_copy(update={"used_at": now + timedelta(seconds=1)})
    assert used.used_at is not None
    used_item = {
        **stored_item,
        "intent_json": {"S": used.model_dump_json()},
        "used_at": {"S": used.used_at.isoformat()},
        "replay_key": {"S": intent.nonce + ":" + intent.payload_hash},
        "result_json": {"S": json.dumps("accepted")},
    }
    replay_client = Mock()
    replay_client.get_item.side_effect = [{"Item": used_item}, {"Item": used_item}]
    replay_client.update_item.side_effect = ClientError(
        {"Error": {"Code": "ConditionalCheckFailedException", "Message": "already used"}},
        "UpdateItem",
    )
    replay = ActionIntentService(
        clock=lambda: now + timedelta(seconds=2),
        store=DynamoDBActionIntentStore(table_name="state", client=replay_client),
    )
    assert (
        replay.consume(
            intent.id,
            principal=principal,
            payload={"decision": "approve"},
            state_version=1,
            result="accepted",
        )
        == "accepted"
    )


def test_service_runs_bounded_plan_graph(
    planner: JourneyPlanner, replanner: JourneyReplanner
) -> None:
    service = make_service(planner, replanner)
    outcome = service.create_plan(
        "Plan 10 am-5 pm from Toa Payoh. Wheelchair, 400 m maximum walking, "
        "lunch before 1 pm, budget $70, visit Gardens by the Bay.",
        journey_date=date(2026, 9, 1),
    )
    assert {segment.venue.id for segment in outcome.itinerary.segments} == {
        "funan-food-court",
        "gardens-bay-outdoor",
    }
    assert planner.validator.validate(outcome.itinerary).valid
    assert outcome.itinerary.total_cost_sgd <= outcome.itinerary.request.hard.total_budget_sgd
    assert outcome.warnings


def test_graph_translates_parser_failure(
    planner: JourneyPlanner, replanner: JourneyReplanner
) -> None:
    service = AdaptSGService(
        parser=RaisingParser(),
        planner=planner,
        replanner=replanner,
        environment=DemoEnvironmentClient(),
    )
    with pytest.raises(NoFeasibleItinerary, match="constraint parsing failed"):
        service.create_plan("Plan", journey_date=date(2026, 9, 1))


def test_graph_translates_no_feasible_plan(
    planner: JourneyPlanner,
    replanner: JourneyReplanner,
    journey_request: JourneyRequest,
) -> None:
    hard = journey_request.hard.model_copy(
        update={"required_venue_ids": frozenset({"fort-canning-park"})}
    )
    service = AdaptSGService(
        parser=FixedParser(journey_request.model_copy(update={"hard": hard})),
        planner=planner,
        replanner=replanner,
        environment=DemoEnvironmentClient(),
    )
    with pytest.raises(NoFeasibleItinerary, match="verified accessibility"):
        service.create_plan("Plan", journey_date=journey_request.journey_date)


def test_monitor_translates_all_environmental_triggers(
    planner: JourneyPlanner, replanner: JourneyReplanner, itinerary: Itinerary
) -> None:
    environment = DemoEnvironmentClient(
        weather_summary="Thundery Showers",
        psi=120,
        flood_affected_venue_ids=frozenset({"gardens-bay-outdoor"}),
        disrupted_route_labels=frozenset({"NSL"}),
    )
    service = make_service(planner, replanner, environment=environment)
    monitoring = service.monitor(itinerary)
    assert isinstance(monitoring, MonitoringOutcome)
    assert {trigger.type for trigger in monitoring.triggers} == {
        TriggerType.HEAVY_RAIN,
        TriggerType.HIGH_PSI,
        TriggerType.FLOOD_ALERT,
        TriggerType.TRANSPORT_DISRUPTION,
    }


def test_monitor_has_no_false_positive(
    planner: JourneyPlanner, replanner: JourneyReplanner, itinerary: Itinerary
) -> None:
    monitoring = make_service(planner, replanner).monitor(itinerary)
    assert monitoring.triggers == ()


def test_stateful_monitor_requires_an_active_journey(
    planner: JourneyPlanner, replanner: JourneyReplanner
) -> None:
    service = make_service(planner, replanner)
    draft = service.start_journey(
        "Plan a safe day.",
        journey_date=date(2026, 9, 2),
        idempotency_key="monitor-draft-key",
    )
    assert draft.pending_initial_itinerary is not None
    with pytest.raises(InvalidJourneyTransition, match="only active journeys"):
        service.monitor_journey(draft.journey_id)

    active = service.decide_journey(
        draft.journey_id,
        decision=ApprovalDecision.APPROVE,
        target_id=draft.pending_initial_itinerary.id,
        expected_version=draft.version,
        idempotency_key="monitor-approve-key",
    )
    assert service.monitor_journey(active.journey_id).triggers == ()


def test_presentation_rows_and_retention(itinerary: Itinerary, replanner: JourneyReplanner) -> None:
    rows = itinerary_rows(itinerary)
    assert rows[0]["stop"] == "National Gallery Singapore"
    walking_metres = rows[0]["walking_metres"]
    assert isinstance(walking_metres, int)
    assert walking_metres <= 400
    proposal = replanner.propose(
        itinerary,
        ReplanTrigger(type=TriggerType.HEAVY_RAIN, message="Rain"),
    )
    assert retained_segment_percentage(itinerary, itinerary) == 100
    assert retained_segment_percentage(itinerary, proposal.itinerary) == 67
    empty = itinerary.model_copy(update={"segments": (), "total_cost_sgd": 0})
    assert retained_segment_percentage(empty, itinerary) == 100


def test_consent_store_find_current_is_typed_and_policy_bound() -> None:
    now = datetime(2026, 9, 2, tzinfo=UTC)
    store = InMemoryConsentStore()
    record = store.create(
        ConsentRecord(
            subject="caregiver",
            policy_version="policy-2",
            actor="caregiver",
            purpose=ConsentPurpose.JOURNEY_PLANNING,
            data_categories=frozenset({"journey_input", "location_routing"}),
            granted_at=now,
        ),
        idempotency_key="current-consent",
    )
    assert (
        store.find_current(
            subject="caregiver",
            purpose=ConsentPurpose.JOURNEY_PLANNING,
            categories=frozenset({"journey_input"}),
            policy_version="policy-2",
            now=now,
        )
        == record
    )
    assert (
        store.find_current(
            subject="caregiver",
            purpose=ConsentPurpose.JOURNEY_PLANNING,
            categories=frozenset({"journey_input"}),
            policy_version="policy-1",
            now=now,
        )
        is None
    )


def test_service_hides_other_owner_journey(
    planner: JourneyPlanner, replanner: JourneyReplanner
) -> None:
    service = make_service(planner, replanner)
    draft = service.start_journey(
        "Plan a safe day.", journey_date=date(2026, 9, 2), idempotency_key="owner-test-key"
    )
    other = PrincipalContext(
        principal_id="other-caregiver",
        account_id="other-caregiver",
        roles=frozenset({ActorRole.CAREGIVER}),
        authenticated=True,
    )
    with pytest.raises(JourneyNotFound):
        service.get_journey(draft.journey_id, principal=other)


def test_api_ignores_spoofed_identity_headers(
    planner: JourneyPlanner, replanner: JourneyReplanner
) -> None:
    client = TestClient(create_app(make_service(planner, replanner)))
    response = client.post(
        "/api/journeys",
        headers={
            "Idempotency-Key": "spoofed-header-key",
            "X-Principal-ID": "attacker",
            "X-Principal-Roles": "traveller",
        },
        json={"prompt": "Plan a safe day.", "journey_date": "2026-09-02"},
    )
    assert response.status_code == 200
    assert response.json()["owner_principal_id"] == "demo-caregiver"


def test_live_api_requires_gateway_claims(
    planner: JourneyPlanner, replanner: JourneyReplanner
) -> None:
    service = make_service(planner, replanner, auth_mode="cognito")
    client = TestClient(create_app(service))
    response = client.post(
        "/api/journeys",
        headers={
            "Idempotency-Key": "live-claims-key",
            "X-Principal-ID": "spoofed-caregiver",
            "X-Principal-Roles": "caregiver",
        },
        json={"prompt": "Plan a safe day.", "journey_date": "2026-09-02"},
    )
    assert response.status_code == 401


def test_live_principal_adapter_reads_only_gateway_claims() -> None:
    from starlette.requests import Request

    request = Request(
        {
            "type": "http",
            "aws.event": {
                "requestContext": {
                    "authorizer": {
                        "jwt": {
                            "claims": {
                                "sub": "cognito-sub",
                                "iss": "https://issuer.example",
                                "aud": "client-id",
                            }
                        }
                    }
                }
            },
        }
    )
    principal = web_api._principal(request, mode="live")
    assert principal.principal_id == "cognito-sub"
    assert principal.account_id == "cognito-sub"
    assert principal.is_caregiver


def test_cognito_access_token_client_id_is_accepted() -> None:
    from starlette.requests import Request

    request = Request(
        {
            "type": "http",
            "aws.event": {
                "requestContext": {
                    "authorizer": {
                        "jwt": {
                            "claims": {
                                "sub": "access-token-sub",
                                "iss": "https://issuer.example",
                                "client_id": "public-client",
                            }
                        }
                    }
                }
            },
        }
    )
    principal = web_api._principal(request, mode="cognito")
    assert principal.principal_id == "access-token-sub"


def test_action_intent_route_binds_decision(
    planner: JourneyPlanner, replanner: JourneyReplanner
) -> None:
    client = TestClient(create_app(make_service(planner, replanner)))
    headers = {"Idempotency-Key": "intent-plan-key"}
    draft = client.post(
        "/api/journeys",
        headers=headers,
        json={"prompt": "Plan a safe day.", "journey_date": "2026-09-02"},
    ).json()
    target_id = draft["pending_initial_itinerary"]["id"]
    intent = client.post(
        f"/api/journeys/{draft['journey_id']}/action-intents",
        headers={"Idempotency-Key": "intent-issue-key"},
        json={"decision": "approve", "target_id": target_id, "expected_version": 1},
    )
    assert intent.status_code == 200
    approved = client.post(
        f"/api/journeys/{draft['journey_id']}/decision",
        headers={"Idempotency-Key": "intent-consume-key"},
        json={
            "decision": "approve",
            "target_id": target_id,
            "expected_version": 1,
            "intent_id": intent.json()["id"],
        },
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "active"


def test_api_consent_status_and_owner_audit(
    planner: JourneyPlanner, replanner: JourneyReplanner
) -> None:
    client = TestClient(create_app(make_service(planner, replanner)))
    consent = client.post(
        "/api/v1/consents",
        headers={"Idempotency-Key": "api-consent-key"},
        json={
            "purpose": "journey_planning",
            "data_categories": ["journey_input"],
            "policy_version": "demo-policy",
        },
    )
    assert consent.status_code == 200
    status = client.get("/api/v1/consents/journey-planning/status")
    assert status.status_code == 200
    assert status.json()["active"] is True
    revoked = client.post(
        f"/api/v1/consents/{consent.json()['id']}/revoke",
        json={"expected_version": 1},
    )
    assert revoked.status_code == 200
    plan = client.post(
        "/api/journeys",
        headers={"Idempotency-Key": "audit-plan-key"},
        json={"prompt": "Plan a safe day.", "journey_date": "2026-09-02"},
    )
    journey_id = plan.json()["journey_id"]
    events = client.get(f"/api/journeys/{journey_id}/audit-events")
    assert events.status_code == 200
    assert events.json()[0]["metadata"]["operation"] == "start_journey"
    global_events = client.get("/api/v1/audit-events")
    assert global_events.status_code == 403
    assert global_events.json()["code"] == "authorization_denied"


def test_inactive_consent_status_exposes_current_contract(
    planner: JourneyPlanner, replanner: JourneyReplanner
) -> None:
    service = make_service(planner, replanner)
    service.consent_policy_version = "consent-v1"
    service.consent_categories = frozenset(
        {
            "journey_input",
            "mobility_accessibility",
            "location_routing",
            "provider_processing",
        }
    )
    client = TestClient(create_app(service))

    response = client.get("/api/v1/consents/journey-planning/status")

    assert response.status_code == 200
    payload = response.json()
    assert {key: payload[key] for key in ("active", "policy_version", "consent_id")} == {
        "active": False,
        "policy_version": "consent-v1",
        "consent_id": None,
    }
    assert set(payload["categories"]) == {
        "journey_input",
        "location_routing",
        "mobility_accessibility",
        "provider_processing",
    }


def test_fastapi_stateful_approval_replan_and_static_page(
    planner: JourneyPlanner,
    replanner: JourneyReplanner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    monkeypatch.chdir(repository_root)
    monkeypatch.setattr(
        web_api,
        "__file__",
        "/opt/hostedtoolcache/Python/3.12/site-packages/adaptsg/web_api.py",
    )
    client = TestClient(create_app(make_service(planner, replanner)))
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json() == {"status": "ok", "mode": "demo", "storage": "memory_demo"}
    assert client.get("/").status_code == 200

    plan = client.post(
        "/api/journeys",
        headers={"Idempotency-Key": "http-plan-action"},
        json={
            "prompt": "Plan 10 am-5 pm for a wheelchair user, lunch by 1 pm, budget $70.",
            "journey_date": "2026-09-01",
        },
    )
    assert plan.status_code == 200
    draft = plan.json()
    assert draft["status"] == "draft"
    decision = client.post(
        f"/api/journeys/{draft['journey_id']}/decision",
        headers={"Idempotency-Key": "http-plan-approve"},
        json={
            "target_id": draft["pending_initial_itinerary"]["id"],
            "decision": "approve",
            "expected_version": draft["version"],
        },
    )
    assert decision.status_code == 200
    active = decision.json()
    assert active["status"] == "active"
    monitored = client.post(f"/api/journeys/{active['journey_id']}/monitor")
    assert monitored.status_code == 200
    assert monitored.json()["triggers"] == []

    replan = client.post(
        f"/api/journeys/{active['journey_id']}/replan",
        headers={"Idempotency-Key": "http-replan-action"},
        json={
            "expected_version": active["version"],
            "trigger": {"type": "fatigue", "message": "Mum is tired"},
        },
    )
    assert replan.status_code == 200
    pending = replan.json()
    assert pending["latest_replan_proposal"]["validation"]["valid"]
    assert client.get(f"/api/journeys/{active['journey_id']}").json() == pending

    rejected = client.post(
        f"/api/journeys/{active['journey_id']}/decision",
        headers={"Idempotency-Key": "http-replan-reject"},
        json={
            "target_id": pending["latest_replan_proposal"]["id"],
            "decision": "reject",
            "expected_version": pending["version"],
        },
    )
    assert rejected.status_code == 200
    assert rejected.json()["current_itinerary"]["id"] == active["current_itinerary"]["id"]


def test_fastapi_accepts_lunch_time_change_trigger(
    planner: JourneyPlanner,
    replanner: JourneyReplanner,
) -> None:
    client = TestClient(create_app(make_service(planner, replanner)))
    plan = client.post(
        "/api/journeys",
        headers={"Idempotency-Key": "http-lunch-plan"},
        json={
            "prompt": "Plan 10 am-5 pm for a wheelchair user, lunch by 1 pm, budget $70.",
            "journey_date": "2026-09-01",
        },
    )
    draft = plan.json()
    approved = client.post(
        f"/api/journeys/{draft['journey_id']}/decision",
        headers={"Idempotency-Key": "http-lunch-approve"},
        json={
            "target_id": draft["pending_initial_itinerary"]["id"],
            "decision": "approve",
            "expected_version": draft["version"],
        },
    )
    active = approved.json()

    replanned = client.post(
        f"/api/journeys/{draft['journey_id']}/replan",
        headers={"Idempotency-Key": "http-lunch-replan"},
        json={
            "expected_version": active["version"],
            "trigger": {
                "type": "lunch_time_changed",
                "message": "Lunch moved earlier",
                "new_lunch_latest": "12:30:00",
            },
        },
    )

    assert replanned.status_code == 200
    proposal = replanned.json()["latest_replan_proposal"]
    assert proposal["validation"]["valid"]
    assert proposal["itinerary"]["request"]["hard"]["lunch_latest"] == "12:30:00"


def test_fastapi_rejects_invalid_and_infeasible_requests(
    planner: JourneyPlanner, replanner: JourneyReplanner
) -> None:
    client = TestClient(create_app(make_service(planner, replanner)))
    assert client.post("/api/plan", json={"prompt": "missing date"}).status_code == 422
    response = client.post(
        "/api/plan",
        headers={"Idempotency-Key": "infeasible-plan-1"},
        json={
            "prompt": "Must visit Fort Canning Park with a wheelchair.",
            "journey_date": "2026-09-01",
        },
    )
    assert response.status_code == 422
    assert response.json()["code"] == "no_feasible_itinerary"
    assert "accessibility" in response.json()["detail"]


def test_fastapi_idempotency_versions_and_strict_payloads(
    planner: JourneyPlanner, replanner: JourneyReplanner
) -> None:
    client = TestClient(create_app(make_service(planner, replanner)))
    payload = {"prompt": "Plan a safe day", "journey_date": "2026-09-02"}
    assert client.post("/api/plan", json=payload).status_code == 400
    assert (
        client.post(
            "/api/plan",
            headers={"Idempotency-Key": "bad key with spaces"},
            json=payload,
        ).status_code
        == 400
    )
    first = client.post("/api/plan", headers={"Idempotency-Key": "replay-http-key"}, json=payload)
    replay = client.post("/api/plan", headers={"Idempotency-Key": "replay-http-key"}, json=payload)
    assert first.json() == replay.json()
    conflict = client.post(
        "/api/plan",
        headers={"Idempotency-Key": "replay-http-key"},
        json={**payload, "prompt": "Different"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "idempotency_conflict"
    missing = client.get(f"/api/journeys/{UUID(int=0)}")
    assert missing.status_code == 404
    assert missing.json()["code"] == "journey_not_found"

    draft = first.json()
    stale = client.post(
        f"/api/journeys/{draft['journey_id']}/decision",
        headers={"Idempotency-Key": "stale-http-key1"},
        json={
            "target_id": draft["pending_initial_itinerary"]["id"],
            "decision": "approve",
            "expected_version": draft["version"] + 1,
            "unexpected": True,
        },
    )
    assert stale.status_code == 422
    stale_version = client.post(
        f"/api/journeys/{draft['journey_id']}/decision",
        headers={"Idempotency-Key": "stale-http-key2"},
        json={
            "target_id": draft["pending_initial_itinerary"]["id"],
            "decision": "approve",
            "expected_version": draft["version"] + 1,
        },
    )
    assert stale_version.status_code == 409
    assert stale_version.json()["code"] == "stale_journey_version"
    assert stale_version.json()["current_version"] == draft["version"]
    legacy = client.post(
        "/api/replan",
        headers={"Idempotency-Key": "legacy-payload-1"},
        json={
            "itinerary": draft["pending_initial_itinerary"],
            "trigger": {"type": "fatigue", "message": "Tired"},
        },
    )
    assert legacy.status_code == 422


def test_fastapi_maps_storage_failure_to_safe_503(
    planner: JourneyPlanner, replanner: JourneyReplanner
) -> None:
    store = Mock()
    store.storage_mode = "dynamodb"
    store.reserve.side_effect = ClientError(
        {"Error": {"Code": "Unavailable", "Message": "provider details"}},
        "PutItem",
    )
    client = TestClient(create_app(make_service(planner, replanner, store=store)))
    response = client.post(
        "/api/plan",
        headers={"Idempotency-Key": "storage-failure-key"},
        json={"prompt": "Plan a day", "journey_date": "2026-09-02"},
    )
    assert response.status_code == 503
    assert response.json() == {
        "code": "journey_storage_unavailable",
        "detail": "journey storage is temporarily unavailable; state was retained",
    }
    assert "provider details" not in response.text


def test_fastapi_in_progress_returns_retry_after(
    planner: JourneyPlanner, replanner: JourneyReplanner
) -> None:
    store = InMemoryJourneyStore()
    service = make_service(planner, replanner, store=store)
    payload = {"prompt": "Plan a safe day", "journey_date": "2026-09-02"}
    key = "concurrent-http-key"
    fingerprint = service._fingerprint(
        {
            "operation": "start_journey",
            "prompt": payload["prompt"],
            "journey_date": payload["journey_date"],
            "start_label": "",
        }
    )
    store.reserve(
        service._hash_idempotency_key(key),
        fingerprint,
        int((datetime.now(UTC) + timedelta(hours=24)).timestamp()),
    )
    response = TestClient(create_app(service)).post(
        "/api/plan", headers={"Idempotency-Key": key}, json=payload
    )
    assert response.status_code == 409
    assert response.json()["code"] == "operation_in_progress"
    assert response.headers["Retry-After"] == "1"


def test_build_service_selects_dynamodb_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = Mock()
    session.client.return_value = Mock()
    monkeypatch.setattr("adaptsg.agent.boto3.Session", Mock(return_value=session))
    service = build_service(Settings(adaptsg_journeys_table="journeys"))
    assert service.storage_mode == "dynamodb"
    assert isinstance(service.audit, DynamoDBAuditStore)
    assert isinstance(service.consent, DynamoDBConsentStore)
    assert isinstance(service.authority, DynamoDBAuthorityStore)
    assert service.intents.storage_mode == "dynamodb"
    session.client.assert_called_once_with("dynamodb")


def test_build_service_rejects_durable_audit_claim_without_table() -> None:
    with pytest.raises(RetentionConfigurationMissing, match="ADAPTSG_JOURNEYS_TABLE"):
        build_service(
            Settings(
                adaptsg_mode="demo",
                adaptsg_audit_storage_configured=True,
                adaptsg_journeys_table=None,
            )
        )
