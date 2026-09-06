"""FastAPI boundary shared by Vercel and AWS Lambda deployments."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

from botocore.exceptions import ClientError
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import Field

from adaptsg.agent import AdaptSGService, build_service
from adaptsg.domain import (
    ActionIntent,
    ActorRole,
    ApprovalDecision,
    AuditEvent,
    AuthorityGrant,
    Capability,
    ConsentPurpose,
    ConsentRecord,
    JourneyDecision,
    JourneyState,
    MonitoringOutcome,
    PrincipalContext,
    ReplanTrigger,
    StrictModel,
)
from adaptsg.errors import (
    AdaptSGError,
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


class ConsentCreateApiRequest(StrictModel):
    purpose: ConsentPurpose
    data_categories: frozenset[str] = Field(min_length=1)
    policy_version: str = Field(min_length=1, max_length=100)


class ConsentRevokeApiRequest(StrictModel):
    expected_version: int = Field(ge=1)


class ActionIntentApiRequest(StrictModel):
    decision: str
    target_id: UUID
    expected_version: int = Field(ge=1)


class ConsentStatusResponse(StrictModel):
    active: bool
    policy_version: str | None = None
    consent_id: UUID | None = None
    categories: frozenset[str] = frozenset()


class AuthorityCreateApiRequest(StrictModel):
    subject: str = Field(min_length=1, max_length=200)
    capabilities: frozenset[Capability] = frozenset()
    valid_from: datetime
    valid_until: datetime
    scope: frozenset[str] = frozenset()


class AuthorityRevokeApiRequest(StrictModel):
    expected_version: int = Field(default=1, ge=1)


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
    AuthorizationDenied: "authorization_denied",
    AuthenticationRequired: "authentication_required",
    CapabilityDisabled: "capability_disabled",
    ConsentRequired: "consent_required",
    AuthorityGrantRequired: "authority_grant_required",
    IntentConflict: "intent_conflict",
    AuditUnavailable: "audit_unavailable",
}


def _idempotency_key(request: Request) -> str:
    value = request.headers.get("Idempotency-Key")
    if value is None:
        raise InvalidIdempotencyKey("Idempotency-Key header is required")
    return value


def _principal(request: Request, *, mode: str) -> PrincipalContext:
    """Resolve only the fixed demo actor or API Gateway's verified JWT claims."""
    if mode == "demo":
        return PrincipalContext(
            principal_id="demo-caregiver",
            account_id="demo-caregiver",
            roles=frozenset({ActorRole.CAREGIVER}),
            authenticated=True,
        )
    event = request.scope.get("aws.event")
    if not isinstance(event, dict):
        raise AuthenticationRequired("API Gateway JWT claims are required")
    context = event.get("requestContext")
    authorizer = context.get("authorizer") if isinstance(context, dict) else None
    jwt = authorizer.get("jwt") if isinstance(authorizer, dict) else None
    claims = jwt.get("claims") if isinstance(jwt, dict) else None
    if not isinstance(claims, dict):
        raise AuthenticationRequired("API Gateway JWT claims are required")
    subject = claims.get("sub")
    issuer = claims.get("iss")
    audience = claims.get("aud")
    if not isinstance(subject, str) or not subject or not isinstance(issuer, str):
        raise AuthenticationRequired("verified subject and issuer claims are required")
    if audience is None:
        raise AuthenticationRequired("verified audience claim is required")
    return PrincipalContext(
        principal_id=subject,
        account_id=subject,
        roles=frozenset({ActorRole.CAREGIVER}),
        authenticated=True,
    )


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
        elif isinstance(exc, AuthenticationRequired):
            status_code = 401
        elif isinstance(exc, (AuthorizationDenied, ConsentRequired)):
            status_code = 403
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
        principal = _principal(request, mode=resolved_service.mode)
        return resolved_service.start_journey(
            payload.prompt,
            journey_date=payload.journey_date,
            idempotency_key=_idempotency_key(request),
            principal=principal,
        )

    @app.post("/api/replan", response_model=JourneyState)
    def replan(payload: ReplanApiRequest, request: Request) -> JourneyState:
        principal = _principal(request, mode=resolved_service.mode)
        return resolved_service.propose_replan(
            payload.journey_id,
            payload.trigger,
            expected_version=payload.expected_version,
            idempotency_key=_idempotency_key(request),
            principal=principal,
        )

    @app.post("/api/journeys/{journey_id}/replan", response_model=JourneyState)
    def replan_journey(
        journey_id: UUID,
        payload: JourneyReplanApiRequest,
        request: Request,
    ) -> JourneyState:
        principal = _principal(request, mode=resolved_service.mode)
        return resolved_service.propose_replan(
            journey_id,
            payload.trigger,
            expected_version=payload.expected_version,
            idempotency_key=_idempotency_key(request),
            principal=principal,
        )

    @app.post("/api/journeys/{journey_id}/decision", response_model=JourneyState)
    def decide(journey_id: UUID, payload: JourneyDecision, request: Request) -> JourneyState:
        return resolved_service.decide_journey(
            journey_id,
            decision=payload.decision,
            target_id=payload.target_id,
            expected_version=payload.expected_version,
            idempotency_key=_idempotency_key(request),
            principal=_principal(request, mode=resolved_service.mode),
            intent_id=payload.intent_id,
        )

    @app.get("/api/journeys/{journey_id}", response_model=JourneyState)
    def get_journey(journey_id: UUID, request: Request) -> JourneyState:
        return resolved_service.get_journey(
            journey_id, principal=_principal(request, mode=resolved_service.mode)
        )

    @app.post("/api/journeys/{journey_id}/monitor", response_model=MonitoringOutcome)
    def monitor_journey(journey_id: UUID, request: Request) -> MonitoringOutcome:
        return resolved_service.monitor_journey(
            journey_id, principal=_principal(request, mode=resolved_service.mode)
        )

    @app.post("/api/journeys/{journey_id}/action-intents", response_model=ActionIntent)
    def issue_action_intent(
        journey_id: UUID, payload: ActionIntentApiRequest, request: Request
    ) -> ActionIntent:
        principal = _principal(request, mode=resolved_service.mode)
        decision = ApprovalDecision(payload.decision)
        return resolved_service.issue_action_intent(
            journey_id,
            decision=decision,
            target_id=payload.target_id,
            expected_version=payload.expected_version,
            principal=principal,
        )

    @app.post("/api/v1/consents", response_model=ConsentRecord)
    def create_consent(payload: ConsentCreateApiRequest, request: Request) -> ConsentRecord:
        principal = _principal(request, mode=resolved_service.mode)
        if (
            payload.policy_version != resolved_service.consent_policy_version
            and resolved_service.mode == "live"
        ):
            raise ConsentRequired("client policy version is not current")
        return resolved_service.consent.create(
            ConsentRecord(
                subject=principal.principal_id,
                policy_version=payload.policy_version,
                actor=principal.principal_id,
                purpose=payload.purpose,
                data_categories=payload.data_categories,
                granted_at=datetime.now(UTC),
            ),
            idempotency_key=_idempotency_key(request),
        )

    @app.get("/api/v1/consents/journey-planning/status", response_model=ConsentStatusResponse)
    def consent_status(request: Request) -> ConsentStatusResponse:
        principal = _principal(request, mode=resolved_service.mode)
        record = resolved_service.consent.find_current(
            subject=principal.principal_id,
            purpose=ConsentPurpose.JOURNEY_PLANNING,
            categories=resolved_service.consent_categories,
            policy_version=resolved_service.consent_policy_version or None,
            now=datetime.now(UTC),
        )
        return ConsentStatusResponse(
            active=record is not None,
            policy_version=record.policy_version
            if record
            else resolved_service.consent_policy_version or None,
            consent_id=record.id if record else None,
            categories=record.data_categories if record else frozenset(),
        )

    @app.get("/api/v1/consents/{consent_id}", response_model=ConsentRecord)
    def read_consent(consent_id: UUID, request: Request) -> ConsentRecord:
        record = resolved_service.consent.get(consent_id)
        principal = _principal(request, mode=resolved_service.mode)
        if record.subject != principal.principal_id:
            resolved_service.authorization.require(
                principal, Capability.JOURNEY_READ, subject=record.subject
            )
        return record

    @app.post("/api/v1/consents/{consent_id}/revoke", response_model=ConsentRecord)
    def revoke_consent(
        consent_id: UUID, payload: ConsentRevokeApiRequest, request: Request
    ) -> ConsentRecord:
        record = resolved_service.consent.get(consent_id)
        principal = _principal(request, mode=resolved_service.mode)
        if record.subject != principal.principal_id:
            raise AuthorizationDenied("only the consent subject may revoke consent")
        return resolved_service.consent.revoke(
            consent_id, expected_version=payload.expected_version, at=datetime.now(UTC)
        )

    @app.post("/api/v1/authority-grants", response_model=AuthorityGrant, include_in_schema=False)
    def create_authority(payload: AuthorityCreateApiRequest, request: Request) -> AuthorityGrant:
        if resolved_service.mode == "live":
            raise AuthorizationDenied("authority grants are disabled in production")
        principal = _principal(request, mode=resolved_service.mode)
        if ActorRole.CAREGIVER not in principal.roles:
            raise AuthorizationDenied("caregiver role is required to issue a grant")
        return resolved_service.authority.put(
            AuthorityGrant(
                subject=payload.subject,
                delegate=principal.principal_id,
                capabilities=payload.capabilities,
                issuer=principal.principal_id,
                valid_from=payload.valid_from,
                valid_until=payload.valid_until,
                scope=payload.scope,
            )
        )

    @app.get("/api/v1/authority-grants/{subject}", response_model=AuthorityGrant)
    def read_authority(subject: str, request: Request) -> AuthorityGrant:
        principal = _principal(request, mode=resolved_service.mode)
        return resolved_service.authority.get(subject, principal.principal_id)

    @app.post("/api/v1/authority-grants/{subject}/revoke", response_model=AuthorityGrant)
    def revoke_authority(
        subject: str, payload: AuthorityRevokeApiRequest, request: Request
    ) -> AuthorityGrant:
        principal = _principal(request, mode=resolved_service.mode)
        if ActorRole.CAREGIVER not in principal.roles:
            raise AuthorizationDenied("caregiver role is required to revoke a grant")
        return resolved_service.authority.revoke(
            subject, principal.principal_id, at=datetime.now(UTC)
        )

    @app.get("/api/v1/audit-events", response_model=tuple[AuditEvent, ...])
    def audit_events(request: Request) -> tuple[AuditEvent, ...]:
        principal = _principal(request, mode=resolved_service.mode)
        if not principal.authenticated or not principal.roles:
            raise AuthorizationDenied("authenticated principal is required")
        return resolved_service.audit.list(correlation_id=None)

    @app.get(
        "/api/journeys/{journey_id}/audit-events",
        response_model=tuple[AuditEvent, ...],
    )
    def journey_audit_events(journey_id: UUID, request: Request) -> tuple[AuditEvent, ...]:
        principal = _principal(request, mode=resolved_service.mode)
        resolved_service.authorization.require(principal, Capability.AUDIT_READ)
        state = resolved_service.get_journey(journey_id, principal=principal)
        return resolved_service.audit.list(correlation_id=state.journey_id)

    static_assets = public_directory()
    if static_assets is not None:
        app.mount("/", StaticFiles(directory=static_assets, html=True), name="static-demo")

    return app


app = create_app()
