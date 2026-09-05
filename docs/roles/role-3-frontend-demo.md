# Role 3: Frontend and demo experience

Read and follow `AGENTS.md` and `TEAM_WORKFLOW.md` before starting.

## Owned paths

- `streamlit_app.py`
- `src/adaptsg/presentation.py`
- `src/adaptsg/ui.py`
- `src/adaptsg/ui.css`
- `.streamlit/**`
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
`LANDED`). Streamlit — the only client since the browser surface was retired
(`docs/contracts/journey-lifecycle-api.md`, "Redesign claims deferred") — is bound by the same
four rules that applied when there were two clients, and regressing any of them is a safety
regression, not a styling one:

1. **The session holds a journey identifier and a version, never an itinerary it could send
   back.** `session_state.itinerary` is a rendering of what the server reports, adopted wholesale
   in `remember()`. `POST /api/replan` and the migration-only in-process helpers are not called
   from `streamlit_app.py`.
2. **Every mutation carries an `Idempotency-Key` and an `expected_version`.** `action_key()`
   derives one from the journey version, so a script rerun replays a stored decision rather than
   applying it twice. A `StaleJourneyVersion` means re-read the journey (`reload_journey()`) and
   let the caregiver choose again — never retry blindly.
3. **A plan arrives as `DRAFT`.** Only an explicit caregiver decision through
   `decide_journey()` (backing `POST /api/journeys/{id}/decision`) makes it `ACTIVE`, and the
   adaptation controls stay unavailable until it is. Applying a replan proposal is the same call,
   not a local swap.
4. **A transport fault is never a safety verdict.** Only `NoFeasibleItinerary` may render the
   stop-and-ask copy that says nothing was relaxed. A stale version, an expired journey or an
   unavailable tool each get their own wording, one `except` clause per domain error in `run()`.

## Definition of done

Done means separate browser sessions have isolated UI state (`tests/test_ui_session_isolation.py`),
the plan diff and approval choice are obvious, keyboard and contrast checks pass, the core judge
flow is under three minutes, UI tests pass, and no safety rule is duplicated outside the domain or
validator.

The gates that hold the client invariants above are Role 3's to keep sharp:
`tests/test_ui_streamlit_app.py` (one caregiver decision per action, the stop-and-ask copy pinned
to `NoFeasibleItinerary`, every button an explicit server call) and `tests/test_ui_components.py`
(the pure `ui.py` renderers — access badges, freshness chips, the walking-meter breach state,
evidence fields — tested directly rather than through a browser DOM). A syntax-and-accessibility
check alone once let a second browser client fall a whole contract behind the API while CI stayed
green; do not let a future surface ship without an equivalent two-sided gate.

Manual QA belongs to the repository owner. Verify through `./scripts/check.sh` and hand over a
numbered checklist rather than driving the app yourself.
