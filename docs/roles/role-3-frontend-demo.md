# Role 3: Frontend and demo experience

Read and follow `AGENTS.md` and `TEAM_WORKFLOW.md` before starting.

## Owned paths

- `streamlit_app.py`
- `src/adaptsg/presentation.py`
- `public/**`
- `.streamlit/**`
- `scripts/check_web.mjs`
- new UI-only tests named `tests/test_ui_*.py`
- `docs/contracts/*.md` entries this role raised

## Mission and gates

Own the request experience, constraint visibility, timeline and route presentation,
environment/accessibility indicators, disruption simulation, plan differences, approval
controls, browser-session behavior, accessibility, and demo pacing. `api/index.py` and
`src/adaptsg/web_api.py` belong to Roles 4 and 1 respectively.

Presentation code displays typed state; it never decides safety, route values, accessibility,
or approval. Keep hard constraints visible, distinguish stale/demo/live provenance, and show
no-feasible and live-tool-failure states without altering the accepted plan. Fatigue wording
requests less travel and rest and does not make medical claims.

## Client invariants

The journey lifecycle is server-authoritative (`docs/contracts/journey-lifecycle-api.md`,
`LANDED`). Both clients are bound by the same four rules, and regressing any of them is a safety
regression, not a styling one:

1. **A client holds a journey identifier and a version, never an itinerary it could send back.**
   The itinerary on screen is a rendering of what the server reports. `POST /api/replan` and the
   migration-only in-process helpers are not available to a client.
2. **Every mutation carries an `Idempotency-Key` and an `expected_version`.** The browser mints
   one key per user action; Streamlit derives one from the journey version, so a script rerun
   replays a stored decision rather than applying it twice. A `409 stale_journey_version` means
   re-read the journey and let the caregiver choose again — never retry blindly.
3. **A plan arrives as `DRAFT`.** Only an explicit caregiver decision through
   `POST /api/journeys/{id}/decision` makes it `ACTIVE`, and the adaptation controls stay
   unavailable until it is. Applying a replan proposal is the same route, not a local swap.
4. **A transport fault is never a safety verdict.** Only `no_feasible_itinerary` may render the
   stop-and-ask copy that says nothing was relaxed. A missing header, a stale version, an expired
   journey or an unavailable tool each get their own wording. Errors are distinguished by the
   typed `code` field, not by parsing `detail`.

## Definition of done

Done means separate browsers have isolated UI state, the plan diff and approval choice are
obvious, keyboard and contrast checks pass, the core judge flow is under three minutes, UI
tests and browser syntax checks pass, and no safety rule is duplicated outside the domain or
validator.

The two gates that hold the client invariants above are Role 3's to keep sharp:
`scripts/check_web.mjs` (one keyed request helper, no itinerary in a body, the stop-and-ask copy
pinned to one error code) and `tests/test_ui_browser_client.py` (every route the client names
exists on `create_app()`, and the browser sequence returns the fields it reads). Both are
verified to fail against the pre-migration client. A syntax-and-accessibility check alone once let
the browser client fall a whole contract behind the API while CI stayed green; do not let a new
surface ship without an equivalent two-sided gate.

Manual and browser QA belong to the repository owner. Verify through `./scripts/check.sh` and
hand over a numbered checklist rather than driving a browser.
