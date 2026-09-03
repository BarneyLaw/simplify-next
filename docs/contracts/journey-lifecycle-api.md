# Contract change: journey lifecycle API

| Field | Value |
|---|---|
| Status | `IMPLEMENTED` |
| Raised | 2026-09-02 by Role 3 (frontend and demo experience) |
| Owner | Role 1 (agent and integration lead) — `src/adaptsg/web_api.py` |
| Affected roles | 1 (routes and semantics), 3 (both clients), 4 (rehearsal script and gate) |
| Depends on | `feature/r1-stateful-approval-idempotency` (`bb0681e`), which already defines the types |

The `TEAM_WORKFLOW.md` one-liner for this request:

```text
CONTRACT CHANGE | src/adaptsg/web_api.py journey routes | the browser client applies a
replan with no server call and both clients hold the authoritative Itinerary, so caregiver
approval is client-side only | Roles 1, 3, 4 | clients send journey_id + expected_version
instead of a full Itinerary; UI renders the DRAFT accept step and version conflicts |
tests/test_ui_*.py, tests/test_agent_and_api.py
```

## Why this is a register entry and not a handoff line

`TEAM_WORKFLOW.md:19` has a role agent read exactly four files: `AGENTS.md`, `PROGRESS.md`,
`TEAM_WORKFLOW.md`, and its own role file. A request that lives only in chat scrollback or in a
`HANDOFF` line is invisible to the next `ROLE 1` session. `docs/roles/role-1-agent-integration.md`
therefore carries a one-line pointer here, and all volatile detail stays in this file so the role
definition itself stays stable.

## Evidence

Four defects, all in code as it stands on `feature/r3-frontend-experience`:

1. **The browser applies a replan with no server call.** `public/index.html:147` is
   `$('apply').onclick = () => renderPlan(proposal.itinerary);`. The approval rule is real —
   `AdaptSGService.apply_proposal` raises `ApprovalRequired` at `agent.py:105-108` — but it is an
   in-process static method with no route in front of it. Streamlit reaches it directly
   (`streamlit_app.py:133`); the browser cannot, so caregiver approval there is enforced by a
   button label and nothing else.
2. **Both clients hold the authoritative itinerary.** `ReplanApiRequest` (`web_api.py:27-29`)
   takes a full `Itinerary` from the client and trusts it. The browser keeps it in the
   module-level `itinerary` global (`public/index.html:81`); Streamlit keeps it in
   `session_state.itinerary`. The server holds no journey, so it cannot reject a stale or edited
   plan, and `replan_count` is only as trustworthy as the client that echoes it back.
3. **Every domain error flattens to one shape.** The single handler at `web_api.py:47-49` maps
   every `AdaptSGError` to `422 {"detail": str(exc)}`. `NoFeasibleItinerary`,
   `ToolUnavailable`, `ReplanLimitReached` and `ApprovalRequired` are distinct classes in
   `errors.py`, and Streamlit renders four distinct states by catching them directly
   (`streamlit_app.py:144-152`, `157-163`). No HTTP client can do the same, because the class is
   gone by the time the response is serialised.
4. **There is no monitor route.** The API exposes only `/api/health`, `/api/plan` and
   `/api/replan` (`web_api.py:51`, `55`, `62`). `AdaptSGService.monitor` returns a typed
   `MonitoringOutcome` (`agent.py:110-115`) that the browser has no way to request, so the
   browser demo has no conditions view at all.

## Requested routes

Expressed against symbols that already exist, so nothing new is invented. `JourneyState`,
`JourneyStatus`, `ApprovalDecision` and `JourneyDecision` are on `bb0681e`; `MonitoringOutcome`,
`ReplanTrigger` and `Itinerary` are on `main` today.

| Route | Body | Returns |
|---|---|---|
| `POST /api/journeys` | `{prompt, journey_date}` | `JourneyState` (`DRAFT`, `pending_initial_itinerary` set, `version=1`) |
| `GET /api/journeys/{id}` | — | `JourneyState`, for refresh and conflict recovery |
| `POST /api/journeys/{id}/decision` | `JourneyDecision` | `JourneyState` (`ACTIVE` or `REJECTED`) |
| `POST /api/journeys/{id}/replan` | `{trigger}` | `JourneyState` with `latest_replan_proposal` |
| `POST /api/journeys/{id}/monitor` | — | `MonitoringOutcome` |

`JourneyDecision.target_id` already distinguishes deciding the initial itinerary from deciding a
replan proposal, so one decision route serves both and no second endpoint is needed.

The point of the shape is that the client stops carrying the plan. It sends `journey_id` plus
`expected_version`; the server owns the `Itinerary` and is the only thing that can move a journey
from `DRAFT` to `ACTIVE` or apply a proposal. That is what makes approval enforceable rather than
advisory.

## Status and error codes

- **`409` on an `expected_version` mismatch**, carrying the current version in the body so the
  client re-reads rather than guesses. `JourneyDecision.expected_version` already exists; without
  a defined response it cannot be acted on.
- **`404` for an unknown or TTL-expired journey.** `adaptsg_journey_ttl_hours`
  (default 24, bounded 1-168) already sets the window on
  `feature/r1-stateful-approval-idempotency:src/adaptsg/settings.py:27`, alongside the journey
  types. It is not on `main` or on `feature/r3-frontend-experience`, so it lands with them rather
  than being a new configuration surface.
- **A typed `code` field alongside `detail` on `422`**, drawn from the existing `errors.py`
  classes: `no_feasible_itinerary`, `tool_unavailable`, `replan_limit_reached`,
  `approval_required`. This is the one item without which the rest is not usable — Role 3 has
  already built the four distinct states in Streamlit, and no HTTP client can render them while
  the class is discarded at `web_api.py:47-49`.

## One question for Role 1 to decide and record

The branch is named for idempotency, so the semantics need stating rather than assuming: **does a
retried approve return the resulting `JourneyState`, or a `409`?**

A browser retries on a flaky network, and "your own decision already applied" and "someone else
changed this journey" must not look the same to a caregiver. Idempotent-on-replay with `409`
reserved for a genuinely different expected version is the shape the UI is easiest to build
against, but this is Role 1's semantics to define. Record the answer here once decided.

## Consequence to flag, not bury

`JourneyStatus.DRAFT` means the initial plan no longer arrives accepted. It requires an explicit
caregiver decision before it becomes `ACTIVE`.

That is a **new step in the spoken demo**, between "show the five hard-constraint cards" and
"point out route source and freshness" in the rehearsal checklist at `PROGRESS.md:113-122`. It
strengthens the story — the caregiver approves the plan rather than being handed one — but it
costs time in a five-minute run and it changes the script. Role 4 owns both the checklist and the
demo timing and should agree the wording before this lands.

## Migration

| Surface | Drops | Carries instead |
|---|---|---|
| `public/index.html` | the `itinerary` global (`:81`), the local apply at `:147` | `journey_id` + `version`; apply becomes `POST .../decision` |
| `streamlit_app.py` | `session_state.itinerary` as the authority, the direct `apply_proposal` call (`:133`) | `session_state.journey_id` + `version`; server response replaces local state |
| `web_api.py` | nothing — `/api/plan` and `/api/replan` may stay for one slice | journey routes added alongside, then the stateless pair retired |

Tests: `tests/test_agent_and_api.py` covers route semantics, status codes and idempotency
(Role 1). `tests/test_ui_streamlit_app.py` and `tests/test_ui_session_isolation.py` cover the
`DRAFT` accept step and conflict recovery; `scripts/check_web.mjs` covers the browser client
(Role 3).

## Implementation note

The lifecycle routes are now implemented in `web_api.py`, and the browser sends journey IDs and
approval decisions to the server. Demo storage replays idempotent operations; the DynamoDB adapter
stores journey snapshots with TTL. Production still needs conditional DynamoDB writes so two
simultaneous requests cannot win the same idempotency key race.
