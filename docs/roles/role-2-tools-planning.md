# Role 2: Tools, data, and deterministic planning

Read and follow `AGENTS.md` and `TEAM_WORKFLOW.md` before starting.

## Owned paths

- `src/adaptsg/planning.py`
- `src/adaptsg/tools/**`
- `src/adaptsg/data/**`
- `tests/test_planning.py`
- `tests/test_tools.py`

## Mission and gates

Own OneMap routing and accessibility, environmental tools, curated venue data, deterministic
candidate construction, metrics, minimal-change scoring, timeouts, and fixture fallbacks.
Request Role 1 changes instead of editing shared schemas, settings, dependencies, or
environment contracts.

Every numerical route, distance, time, and cost value comes from a typed tool result. Live
results carry a source and retrieval timestamp; demo fixtures stay deterministic and visibly
labelled. Mandatory wheelchair access requires verified status and a source. Provider failure
must return an actionable domain failure or labelled fallback, never fabricated live data.

Done means tool payloads are small and typed, eight-second live timeouts remain enforced,
ranking preserves safety and unaffected segments first, fixtures cover failures, allocated
tests pass, and the mock path remains reproducible.
