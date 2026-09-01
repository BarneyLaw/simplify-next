# Role 3: Frontend and demo experience

Read and follow `AGENTS.md` and `TEAM_WORKFLOW.md` before starting.

## Owned paths

- `streamlit_app.py`
- `src/adaptsg/presentation.py`
- `public/**`
- `.streamlit/**`
- `scripts/check_web.mjs`
- new UI-only tests named `tests/test_ui_*.py`

## Mission and gates

Own the request experience, constraint visibility, timeline and route presentation,
environment/accessibility indicators, disruption simulation, plan differences, approval
controls, browser-session behavior, accessibility, and demo pacing. `api/index.py` and
`src/adaptsg/web_api.py` belong to Roles 4 and 1 respectively.

Presentation code displays typed state; it never decides safety, route values, accessibility,
or approval. Keep hard constraints visible, distinguish stale/demo/live provenance, and show
no-feasible and live-tool-failure states without altering the accepted plan. Fatigue wording
requests less travel and rest and does not make medical claims.

Done means separate browsers have isolated UI state, the plan diff and approval choice are
obvious, keyboard and contrast checks pass, the core judge flow is under three minutes, UI
tests and browser syntax checks pass, and no safety rule is duplicated outside the domain or
validator.
