# Role 3: Frontend and demo experience

Read and follow `AGENTS.md` and `TEAM_WORKFLOW.md` before starting.

## Owned paths

- `public/**`
- `scripts/check_web.mjs`
- `scripts/test_web_auth.mjs`
- `src/adaptsg/presentation.py`
- `tests/test_ui_*.py`
- `docs/contracts/*.md` entries this role raised

## Mission and gates

Own the request experience, constraint visibility, timeline and route presentation,
environment/accessibility indicators, disruption simulation, plan differences, approval
controls, browser-session behavior, accessibility, sign-in/sign-out, and demo pacing.
`api/index.py` and `src/adaptsg/web_api.py` belong to Roles 4 and 1 respectively; Role 3 mounts
`public/` from `web_api.py` but does not own the routes it serves.

Presentation code displays typed state; it never decides safety, route values, accessibility,
or approval. Keep hard constraints visible, distinguish stale/demo/live provenance, and show
no-feasible and live-tool-failure states without altering the accepted plan. Fatigue wording
requests less travel and rest and does not make medical claims.

## Client invariants

The journey lifecycle is server-authoritative (`docs/contracts/journey-lifecycle-api.md`,
`LANDED`). `public/index.html` — the one client; Streamlit was retired in favour of the
no-build-step static browser client CloudFront and local `uvicorn` both serve — is bound by the
same four rules that have applied since there was a single client, and regressing any of them is
a safety regression, not a styling one:

1. **The session holds a journey identifier and a version, never an itinerary it could send
   back.** Module-level `journeyId`/`version`/`state` are a rendering of what the server reports,
   replaced wholesale by `renderJourney()`. The client never calls `POST /api/replan` (the
   stateless route); only the stateful `/api/journeys/{id}/...` routes.
2. **Every mutation carries an `Idempotency-Key` and an `expected_version`.** `actionKey()`
   derives a fresh key per user action, and `mutate()` refuses to send a state-changing request
   without one. A `stale_journey_version` error means re-read the journey and let the caregiver
   choose again — `guarded()` does this, never a blind retry.
3. **A plan arrives as `draft`.** Only an explicit caregiver decision through `decide()`
   (backing `POST /api/journeys/{id}/decision`) makes it `active`, and the adaptation controls
   stay unavailable until it is. Applying a replan proposal is the same call, not a local swap.
4. **A transport fault is never a safety verdict.** Only `no_feasible_itinerary` may render the
   stop-and-ask copy that says nothing was relaxed; `ERROR_COPY` gives every other domain error
   its own wording. The transport-level `authentication_expired` code after a failed token
   refresh is a fifth, distinct case: callers must route to the signed-out view, not render a
   generic transport alert or reactivate the view they were on.

## Definition of done

Done means the plan diff and approval choice are obvious, keyboard and contrast checks pass, the
core judge flow is under three minutes, UI tests pass, and no safety rule is duplicated outside
the domain or validator.

The gates that hold the client invariants above are Role 3's to keep sharp:

- `scripts/check_web.mjs` — dependency-free syntax, accessibility, and static invariants (every
  literal `/api/...` call flows through `read()`/`mutate()`; only `request()` may attach
  `Authorization` or `Idempotency-Key`).
- `scripts/test_web_auth.mjs` — an executable `node:vm` harness that loads the real inline script
  into a mocked `fetch`/`location`/`history`/`crypto`/`sessionStorage`/clock and drives the PKCE
  state machine: config fallback and fail-closed validation, sign-in/sign-up redirects, callback
  state verification, token exchange and refresh, the terminal 401 transition, and logout.
- `tests/test_ui_browser_client.py` — the two-sided route/field drift guard: the paths the client
  names must exist on the API, and the fields it reads must be in the responses. A
  syntax-and-accessibility check alone once let a second browser client fall a whole contract
  behind the API while CI stayed green; do not let a future change ship without an equivalent
  two-sided gate.

Manual QA belongs to the repository owner. Verify through `./scripts/check.sh` and hand over a
numbered checklist rather than driving the app yourself.
