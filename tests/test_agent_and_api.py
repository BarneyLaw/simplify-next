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
    AdaptSGService,
    DynamoDBJourneyStore,
    InMemoryJourneyStore,
    JourneyStore,
    build_service,
)
from adaptsg.domain import (
    ApprovalDecision,
    Itinerary,
    Location,
    JourneyRequest,
    JourneyState,
    JourneyStatus,
    MonitoringOutcome,
    ParseOutcome,
    ReplanTrigger,
    TriggerType,
    ValidationCode,
    ValidationIssue,
    ValidationResult,
)
from adaptsg.errors import (
    IdempotencyConflict,
    InvalidJourneyTransition,
    JourneyNotFound,
    NoFeasibleItinerary,
    OperationInProgress,
    ReplanLimitReached,
    StaleJourneyVersion,
    ToolUnavailable,
)
from adaptsg.planning import JourneyPlanner, JourneyReplanner
from adaptsg.preference_parser import DeterministicPreferenceParser
from adaptsg.presentation import itinerary_rows, retained_segment_percentage
from adaptsg.settings import Settings
from adaptsg.tools.catalog import VenueCatalog
from adaptsg.tools.environment import DemoEnvironmentClient
from adaptsg.tools.location import DemoLocationClient, LocationClient
from adaptsg.web_api import create_app


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


def test_service_runs_bounded_plan_graph(
    planner: JourneyPlanner, replanner: JourneyReplanner
) -> None:
    service = make_service(planner, replanner)
    outcome = service.create_plan(
        "Plan 10 am-5 pm from Toa Payoh. Wheelchair, 400 m maximum walking, "
        "lunch before 1 pm, budget $70, visit Gardens by the Bay.",
        journey_date=date(2026, 9, 1),
    )
    assert outcome.itinerary.total_cost_sgd == 33
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
    session.client.assert_called_once_with("dynamodb")
