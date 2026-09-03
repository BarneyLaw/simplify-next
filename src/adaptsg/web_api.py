"""FastAPI boundary shared by Vercel and AWS Lambda deployments."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from adaptsg.agent import AdaptSGService, build_service
from adaptsg.domain import (
    Itinerary,
    JourneyDecision,
    JourneyState,
    JourneyStatus,
    MonitoringOutcome,
    PlanOutcome,
    ProposalStatus,
    ReplanProposal,
    ReplanTrigger,
    StrictModel,
)
from adaptsg.errors import (
    AdaptSGError,
    ApprovalRequired,
    InvalidJourneyDecision,
    JourneyNotFound,
    JourneyVersionConflict,
    NoFeasibleItinerary,
    ReplanLimitReached,
    ToolUnavailable,
)
from adaptsg.persistence import DynamoDBJourneyStore, InMemoryJourneyStore, JourneyStore
from adaptsg.settings import get_settings


class PlanApiRequest(StrictModel):
    prompt: str
    journey_date: date


class ReplanApiRequest(StrictModel):
    itinerary: Itinerary
    trigger: ReplanTrigger


class JourneyCreateApiRequest(StrictModel):
    prompt: str
    journey_date: date
    idempotency_key: str | None = None


class JourneyReplanApiRequest(StrictModel):
    trigger: ReplanTrigger
    expected_version: int | None = None
    idempotency_key: str | None = None


def public_directory() -> Path | None:
    """Find browser assets in a source checkout or a non-editable installation."""
    candidates = (
        Path(__file__).resolve().parents[2] / "public",
        Path.cwd() / "public",
    )
    return next((candidate for candidate in candidates if candidate.is_dir()), None)


def create_app(
    service: AdaptSGService | None = None,
    store: JourneyStore | None = None,
) -> FastAPI:
    resolved_service = service or build_service()
    resolved_settings = get_settings()
    resolved_store = store
    if resolved_store is None:
        resolved_store = (
            DynamoDBJourneyStore(
                resolved_settings.adaptsg_journeys_table,
                ttl_hours=resolved_settings.adaptsg_journey_ttl_hours,
                region_name=resolved_settings.aws_region,
            )
            if resolved_settings.adaptsg_journeys_table
            else InMemoryJourneyStore()
        )
    app = FastAPI(
        title="AdaptSG API",
        version="0.1.0",
        description="Typed plan, monitor, validate, and minimal-replan API.",
    )

    @app.exception_handler(AdaptSGError)
    async def domain_error(_request: Request, exc: AdaptSGError) -> JSONResponse:
        error_codes = {
            NoFeasibleItinerary: "no_feasible_itinerary",
            ToolUnavailable: "tool_unavailable",
            ReplanLimitReached: "replan_limit_reached",
            ApprovalRequired: "approval_required",
            JourneyNotFound: "journey_not_found",
            JourneyVersionConflict: "journey_version_conflict",
            InvalidJourneyDecision: "invalid_journey_decision",
        }
        status_code = (
            409
            if isinstance(exc, JourneyVersionConflict)
            else 404
            if isinstance(exc, JourneyNotFound)
            else 422
        )
        content: dict[str, object] = {"code": error_codes[type(exc)], "detail": str(exc)}
        if isinstance(exc, JourneyVersionConflict):
            content["current_version"] = exc.current_version
        return JSONResponse(status_code=status_code, content=content)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "mode": get_settings().adaptsg_mode}

    @app.post("/api/plan", response_model=PlanOutcome)
    def plan(payload: PlanApiRequest) -> PlanOutcome:
        return resolved_service.create_plan(
            payload.prompt,
            journey_date=payload.journey_date,
        )

    @app.post("/api/journeys", response_model=JourneyState)
    def create_journey(payload: JourneyCreateApiRequest) -> JourneyState:
        if payload.idempotency_key:
            cached = resolved_store.get_operation(f"create:{payload.idempotency_key}")
            if cached is not None:
                return cached
        outcome = resolved_service.create_plan(payload.prompt, journey_date=payload.journey_date)
        state = JourneyState(itinerary=outcome.itinerary)
        resolved_store.save(state)
        if payload.idempotency_key:
            resolved_store.save_operation(f"create:{payload.idempotency_key}", state)
        return state

    @app.get("/api/journeys/{journey_id}", response_model=JourneyState)
    def get_journey(journey_id: UUID) -> JourneyState:
        return _get_journey(resolved_store, journey_id)

    @app.post("/api/journeys/{journey_id}/monitor", response_model=MonitoringOutcome)
    def monitor_journey(journey_id: UUID) -> MonitoringOutcome:
        state = _get_journey(resolved_store, journey_id)
        return resolved_service.monitor(state.itinerary)

    @app.post("/api/journeys/{journey_id}/replan", response_model=JourneyState)
    def replan_journey(journey_id: UUID, payload: JourneyReplanApiRequest) -> JourneyState:
        if payload.idempotency_key:
            cached = resolved_store.get_operation(f"replan:{journey_id}:{payload.idempotency_key}")
            if cached is not None:
                return cached
        state = _get_journey(resolved_store, journey_id)
        if state.status is not JourneyStatus.ACTIVE:
            raise InvalidJourneyDecision("only an active journey can be replanned")
        if payload.expected_version is not None and payload.expected_version != state.version:
            raise JourneyVersionConflict(
                "journey version is out of date; refresh before replanning",
                current_version=state.version,
            )
        proposal = resolved_service.propose_replan(state.itinerary, payload.trigger)
        updated = state.model_copy(
            update={
                "version": state.version + 1,
                "latest_replan_proposal": proposal,
            }
        )
        resolved_store.save(updated)
        if payload.idempotency_key:
            resolved_store.save_operation(f"replan:{journey_id}:{payload.idempotency_key}", updated)
        return updated

    @app.post("/api/journeys/{journey_id}/decision", response_model=JourneyState)
    def decide_journey(journey_id: UUID, payload: JourneyDecision) -> JourneyState:
        if payload.idempotency_key:
            cached = resolved_store.get_operation(
                f"decision:{journey_id}:{payload.idempotency_key}"
            )
            if cached is not None:
                return cached
        state = _get_journey(resolved_store, journey_id)
        if payload.expected_version != state.version:
            raise JourneyVersionConflict(
                "journey version is out of date; refresh before deciding",
                current_version=state.version,
            )
        if state.pending_initial_itinerary and payload.target_id == state.itinerary.id:
            updated = state.model_copy(
                update={
                    "status": JourneyStatus.ACTIVE if payload.approved else JourneyStatus.REJECTED,
                    "pending_initial_itinerary": False,
                    "version": state.version + 1,
                }
            )
        elif state.latest_replan_proposal and payload.target_id == state.latest_replan_proposal.id:
            proposal = state.latest_replan_proposal.model_copy(
                update={
                    "status": ProposalStatus.APPROVED
                    if payload.approved
                    else ProposalStatus.REJECTED
                }
            )
            updated = state.model_copy(
                update={
                    "itinerary": proposal.itinerary if payload.approved else state.itinerary,
                    "latest_replan_proposal": proposal,
                    "version": state.version + 1,
                }
            )
        else:
            raise InvalidJourneyDecision("decision does not target a pending itinerary or proposal")
        resolved_store.save(updated)
        if payload.idempotency_key:
            resolved_store.save_operation(
                f"decision:{journey_id}:{payload.idempotency_key}", updated
            )
        return updated

    @app.post("/api/replan", response_model=ReplanProposal)
    def replan(payload: ReplanApiRequest) -> ReplanProposal:
        return resolved_service.propose_replan(payload.itinerary, payload.trigger)

    static_assets = public_directory()
    if static_assets is not None:
        app.mount("/", StaticFiles(directory=static_assets, html=True), name="static-demo")

    return app


def _get_journey(store: JourneyStore, journey_id: UUID) -> JourneyState:
    state = store.get(journey_id)
    if state is None:
        raise JourneyNotFound(f"journey {journey_id} was not found")
    return state


app = create_app()
