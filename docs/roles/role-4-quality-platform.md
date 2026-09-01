# Role 4: Guardrails, evaluation, deployment, and presentation

Read and follow `AGENTS.md` and `TEAM_WORKFLOW.md` before starting.

## Owned paths

- `src/adaptsg/validation.py`
- `tests/conftest.py`
- `tests/test_domain_and_validation.py`
- `tests/test_evaluation_scenarios.py`
- new `tests/test_validation_*.py` and `tests/test_evaluation_*.py`
- `evals/**` and `infra/**`
- `api/index.py` and `src/adaptsg/aws_handler.py`
- `Dockerfile`, `docker-compose.yml`, `Makefile`, and `vercel.json`
- `.github/**`, `scripts/check.ps1`, and `scripts/check.sh`
- `requirements*.txt`, `README.md`, `ARCHITECTURE.md`, and `PROGRESS.md`
- submission, deck, video, and other project documentation

## Mission and gates

Own the deterministic validator as final safety authority, golden evaluations, security and
malformed-input coverage, idempotency/retry evidence, observability, deployment, cost controls,
documentation, and submission evidence. Other module owners add tests only in their allocated
files or hand off a validation scenario here.

Validation must reject every hard-constraint violation, missing or stale fact, unverified
mandatory accessibility claim, infeasible transfer, and malformed tool value. Deployment must
remain reproducible, keep secrets out of Git, default demos to deterministic data, and expose
no booking, payment, arbitrary browsing, or medical capability.

Done means benefit claims have measured evidence, the complete correctness gate passes with at
least 90% branch coverage and all named scenarios, deployed and fixture paths are verified,
`PROGRESS.md` records only observed results, and the deck/video remain within submission limits.
