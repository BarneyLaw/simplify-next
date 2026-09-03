"""FastAPI boundary shared by Vercel and AWS Lambda deployments."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from uuid import UUID

from botocore.exceptions import ClientError
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import Field

from adaptsg.agent import AdaptSGService, build_service
from adaptsg.domain import (
    JourneyDecision,
    JourneyState,
    MonitoringOutcome,
    ReplanTrigger,
    StrictModel,
)
from adaptsg.errors import (
    AdaptSGError,
    ApprovalRequired,
    IdempotencyConflict,
    InvalidIdempotencyKey,
    InvalidJourneyTransition,
    JourneyConflict,
    JourneyNotFound,
    NoFeasibleItinerary,
    OperationInProgress,
    ReplanLimitReached,
    StaleJourneyVersion,
    ToolUnavailable,
)


class PlanApiRequest(StrictModel):
    prompt: str
    journey_date: date


class ReplanApiRequest(StrictModel):
    journey_id: UUID
    trigger: ReplanTrigger
    expected_version: int = Field(ge=1)


class JourneyReplanApiRequest(StrictModel):
    trigger: ReplanTrigger
    expected_version: int = Field(ge=1)


ERROR_CODES: dict[type[AdaptSGError], str] = {
    ApprovalRequired: "approval_required",
    IdempotencyConflict: "idempotency_conflict",
    InvalidIdempotencyKey: "invalid_idempotency_key",
    InvalidJourneyTransition: "invalid_journey_transition",
    JourneyNotFound: "journey_not_found",
    NoFeasibleItinerary: "no_feasible_itinerary",
    OperationInProgress: "operation_in_progress",
    ReplanLimitReached: "replan_limit_reached",
    StaleJourneyVersion: "stale_journey_version",
    ToolUnavailable: "tool_unavailable",
}


def _idempotency_key(request: Request) -> str:
    value = request.headers.get("Idempotency-Key")
    if value is None:
        raise InvalidIdempotencyKey("Idempotency-Key header is required")
    return value


def public_directory() -> Path | None:
    """Find browser assets in a source checkout or a non-editable installation."""
    candidates = (
        Path(__file__).resolve().parents[2] / "public",
        Path.cwd() / "public",
    )
    return next((candidate for candidate in candidates if candidate.is_dir()), None)


def create_app(service: AdaptSGService | None = None) -> FastAPI:
    resolved_service = service or build_service()
    app = FastAPI(
        title="AdaptSG API",
        version="0.2.0",
        description="Stateful, validated journey approval and minimal-replan API.",
    )

    @app.exception_handler(AdaptSGError)
    async def domain_error(_request: Request, exc: AdaptSGError) -> JSONResponse:
        headers = {"Retry-After": "1"} if isinstance(exc, OperationInProgress) else None
        if isinstance(exc, InvalidIdempotencyKey):
            status_code = 400
        elif isinstance(exc, JourneyNotFound):
            status_code = 404
        elif isinstance(exc, JourneyConflict):
            status_code = 409
        elif isinstance(exc, ToolUnavailable):
            status_code = 503
        elif isinstance(exc, NoFeasibleItinerary):
            status_code = 422
        else:
            status_code = 422
        content: dict[str, str | int] = {
            "code": ERROR_CODES.get(type(exc), "adaptsg_error"),
            "detail": str(exc),
        }
        if isinstance(exc, StaleJourneyVersion) and exc.current_version is not None:
            content["current_version"] = exc.current_version
        return JSONResponse(status_code=status_code, content=content, headers=headers)

    @app.exception_handler(ClientError)
    async def provider_error(_request: Request, _exc: ClientError) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={
                "code": "journey_storage_unavailable",
                "detail": "journey storage is temporarily unavailable; state was retained",
            },
        )

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {
            "status": "ok",
            "mode": resolved_service.mode,
            "storage": resolved_service.storage_mode,
        }

    @app.post("/api/journeys", response_model=JourneyState)
    @app.post("/api/plan", response_model=JourneyState)
    def plan(payload: PlanApiRequest, request: Request) -> JourneyState:
        return resolved_service.start_journey(
            payload.prompt,
            journey_date=payload.journey_date,
            idempotency_key=_idempotency_key(request),
        )

    @app.post("/api/replan", response_model=JourneyState)
    def replan(payload: ReplanApiRequest, request: Request) -> JourneyState:
        return resolved_service.propose_replan(
            payload.journey_id,
            payload.trigger,
            expected_version=payload.expected_version,
            idempotency_key=_idempotency_key(request),
        )

    @app.post("/api/journeys/{journey_id}/replan", response_model=JourneyState)
    def replan_journey(
        journey_id: UUID,
        payload: JourneyReplanApiRequest,
        request: Request,
    ) -> JourneyState:
        return resolved_service.propose_replan(
            journey_id,
            payload.trigger,
            expected_version=payload.expected_version,
            idempotency_key=_idempotency_key(request),
        )

    @app.post("/api/journeys/{journey_id}/decision", response_model=JourneyState)
    def decide(journey_id: UUID, payload: JourneyDecision, request: Request) -> JourneyState:
        return resolved_service.decide_journey(
            journey_id,
            decision=payload.decision,
            target_id=payload.target_id,
            expected_version=payload.expected_version,
            idempotency_key=_idempotency_key(request),
        )

    @app.get("/api/journeys/{journey_id}", response_model=JourneyState)
    def get_journey(journey_id: UUID) -> JourneyState:
        return resolved_service.get_journey(journey_id)

    @app.post("/api/journeys/{journey_id}/monitor", response_model=MonitoringOutcome)
    def monitor_journey(journey_id: UUID) -> MonitoringOutcome:
        return resolved_service.monitor_journey(journey_id)

    static_assets = public_directory()
    if static_assets is not None:
        app.mount("/", StaticFiles(directory=static_assets, html=True), name="static-demo")

    return app


app = create_app()
