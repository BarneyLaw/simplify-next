# Role 1: Agent and integration lead

Read and follow `AGENTS.md` and `TEAM_WORKFLOW.md` before starting.

## Owned paths

- `src/adaptsg/agent.py`
- `src/adaptsg/preference_parser.py`
- `src/adaptsg/web_api.py`
- `src/adaptsg/domain.py`
- `src/adaptsg/errors.py`
- `src/adaptsg/settings.py`
- `src/adaptsg/__init__.py`
- `pyproject.toml`
- `.env.example`
- `tests/test_agent_and_api.py`
- `tests/test_preference_parser.py`

## Mission and gates

Own service and API semantics, bounded graph orchestration, parsing, approval boundaries, and
final integration decisions. Keep schemas frozen; coordinate a documented contract change
with every affected role before editing `domain.py`, settings, or dependency metadata.

The graph may propose but cannot bypass `ItineraryValidator`, approval, tool provenance, or
the replan cap. A live-tool failure retains the current plan, and an infeasible request raises
`NoFeasibleItinerary`. Every loop and external request remains bounded.

Done means the appropriate tools are selected, unaffected items survive replanning, material
changes require approval, failure stops safely, allocated tests pass, and the full gate is
green before integration.
