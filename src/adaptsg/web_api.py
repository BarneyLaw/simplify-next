"""FastAPI boundary shared by Vercel and AWS Lambda deployments."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from adaptsg.agent import AdaptSGService, build_service
from adaptsg.domain import (
    Itinerary,
    PlanOutcome,
    ReplanProposal,
    ReplanTrigger,
    StrictModel,
)
from adaptsg.errors import AdaptSGError
from adaptsg.settings import get_settings


class PlanApiRequest(StrictModel):
    prompt: str
    journey_date: date


class ReplanApiRequest(StrictModel):
    itinerary: Itinerary
    trigger: ReplanTrigger


def create_app(service: AdaptSGService | None = None) -> FastAPI:
    resolved_service = service or build_service()
    app = FastAPI(
        title="AdaptSG API",
        version="0.1.0",
        description="Typed plan, monitor, validate, and minimal-replan API.",
    )

    @app.exception_handler(AdaptSGError)
    async def domain_error(_request: Request, exc: AdaptSGError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "mode": get_settings().adaptsg_mode}

    @app.post("/api/plan", response_model=PlanOutcome)
    def plan(payload: PlanApiRequest) -> PlanOutcome:
        return resolved_service.create_plan(
            payload.prompt,
            journey_date=payload.journey_date,
        )

    @app.post("/api/replan", response_model=ReplanProposal)
    def replan(payload: ReplanApiRequest) -> ReplanProposal:
        return resolved_service.propose_replan(payload.itinerary, payload.trigger)

    public_directory = Path(__file__).resolve().parents[2] / "public"
    if public_directory.is_dir():
        app.mount("/", StaticFiles(directory=public_directory, html=True), name="static-demo")

    return app


app = create_app()
