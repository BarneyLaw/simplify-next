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

    static_assets = public_directory()
    if static_assets is not None:
        app.mount("/", StaticFiles(directory=static_assets, html=True), name="static-demo")

    return app


app = create_app()
