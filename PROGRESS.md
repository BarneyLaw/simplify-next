# AdaptSG Progress

Last updated: 2026-08-30 (Asia/Singapore)

## Current status

Starter codebase complete and locally verified. The deterministic demo is ready for team rehearsal. Live cloud deployment is intentionally pending credentials, provider approvals and the first GitHub Actions run.

## Completed milestones

- [x] Narrowed scope to Singapore caregivers and mobility-limited travellers.
- [x] Separated immutable hard constraints from soft preferences.
- [x] Added 18 curated demo venues with explicit accessibility status and provenance.
- [x] Added deterministic route/cost demo tools.
- [x] Added OneMap routing and optional approved BFA integration.
- [x] Added data.gov.sg weather/PSI plus LTA/PUB alert integration.
- [x] Added Bedrock Converse preference extraction and token accounting.
- [x] Added a bounded LangGraph planning flow.
- [x] Added deterministic validation for accessibility, walking, time, lunch, rest, budget, opening hours, provenance and loop limits.
- [x] Added minimal-change replanning and caregiver cost approval.
- [x] Added the full Streamlit demo and lightweight Vercel web mode.
- [x] Added Docker, Vercel and AWS SAM deployment files.
- [x] Added 71 tests, including 20 named evaluation scenarios.
- [x] Added Ruff, strict mypy, Bandit, dependency audit and 90% coverage gates.
- [x] Added Docker build and SAM validate/build jobs to GitHub Actions.
- [x] Added `AGENTS.md`, `ARCHITECTURE.md`, this tracker and a judge-oriented README.

## Verified baseline

| Check | Result | Evidence |
|---|---:|---|
| Tests | 71 passed | `python -m pytest` |
| Named scenarios | 20 passed | `tests/test_evaluation_scenarios.py` |
| Branch coverage | 98.1% | CI threshold is 90% |
| Ruff format/lint | Passed | `scripts/check.ps1` |
| Strict mypy | Passed | 25 source/test modules checked |
| Bandit | Passed | Source, API and Streamlit entry point |
| Dependency audit | Passed | No known vulnerabilities after minimum-version update |
| Streamlit headless flow | Passed | Initial plan, rain proposal, apply and fatigue approval |
| FastAPI plan/replan | Passed | Local `TestClient` integration |
| Vercel browser JS syntax | Passed | `scripts/check_web.mjs` |
| Live provider parsers | Passed with mocks | Bedrock, OneMap, data.gov.sg, LTA/PUB |
| Docker image build | Pending CI | Docker CLI unavailable locally |
| SAM validate/build | Pending CI | SAM CLI unavailable locally; YAML parsed locally |
| Vercel platform build | Pending deployment | Vercel CLI/account unavailable locally |
| Browser visual QA of Vercel page | Pending | No in-app/extension browser connected |

## Current feature branches and merges

The repository keeps each feature boundary visible and uses incremental commits.

| Feature branch | Main content | Status |
|---|---|---|
| `feature/project-foundation` | Python packaging, dependencies, environment template | Merged |
| `feature/core-planning` | Typed state, catalog, tools, validator, planner/replanner | Merged |
| `feature/bedrock-agent` | Bedrock parser and bounded LangGraph | Merged |
| `feature/live-data-tools` | OneMap, BFA, weather, PSI, flood and train alerts | Merged |
| `feature/streamlit-ui` | Streamlit demo and approval flow | Merged |
| `feature/deployment` | Docker, FastAPI, Vercel and AWS SAM | Merged |
| `feature/ci-tests` | 71 tests, 20 scenarios and CI/security gates | Merged |
| `feature/project-documentation` | Governance, architecture, README and tracker | In progress |

## External setup still required

1. Request and verify OneMap API token access.
2. Request SLA approval for BFA routing before setting `ONEMAP_BFA_ENABLED=true`.
3. Request an LTA DataMall account key.
4. Confirm the selected Bedrock model is enabled in `us-east-1`.
5. Refresh hackathon AWS session credentials immediately before the live demo.
6. Run the GitHub Actions workflow on the remote repository.
7. Deploy the Vercel demo and inspect it in a connected browser at desktop and mobile widths.
8. Deploy the SAM stack, test the Function URL, then delete it when not in use.

Do not mark live mode demo-ready until all provider timestamps and sources appear correctly in the UI.

## Product/data work before judging

Priority 0:

- [ ] Re-verify the three demo stops' hours, cost and wheelchair claims with venue/official sources.
- [ ] Decide whether Gardens by the Bay is a soft preference in the spoken demo and keep wording consistent.
- [ ] Record one no-feasible-route example to demonstrate the stop-and-ask guardrail.
- [ ] Run the full demo twice within five minutes using a fresh browser session.
- [ ] Capture final metrics and screenshots for the deck.

Priority 1:

- [ ] Add caregiver/user evidence and cite it in the problem slide.
- [ ] Build the maximum 10-slide presentation.
- [ ] Record/caption the maximum five-minute demo video.
- [ ] Add structured CloudWatch metrics for latency, retained segments, tool success and loop caps.
- [ ] Add DynamoDB on-demand journey persistence and idempotency.
- [ ] Move production secrets to Secrets Manager or SSM.
- [ ] Replace public Function URL access with authentication and restricted CORS.

Out of MVP scope:

- flights, hotels and multi-country planning;
- payments or bookings;
- live web scraping of venue pages;
- medical diagnosis or health recommendations;
- more than three itinerary stops or more than two demo replans.

## Demo rehearsal checklist

- [ ] Start in `ADAPTSG_MODE=demo` unless every live credential has just been verified.
- [ ] Show the five hard-constraint cards before discussing the itinerary.
- [ ] Point out route source and freshness.
- [ ] Trigger rain/flood and explain why only the outdoor suffix changed.
- [ ] Apply the first proposal.
- [ ] Trigger fatigue and highlight walking reduction/taxi cost.
- [ ] Pause at the approval screen; do not skip the user decision.
- [ ] End on 0 hard violations, retained-plan percentage and bounded replans.

## Decision log

- **One orchestrator:** the problem needs bounded coordination, not a swarm.
- **Curated venues:** reliability and unsupported-claim prevention outweigh catalog breadth in the MVP.
- **Smallest change:** candidate scoring heavily penalizes changed segments before cost/walking tie-breakers.
- **Dual web mode:** Streamlit remains the full UI; Vercel uses static HTML plus FastAPI because Streamlit is not a native serverless/WebSocket fit.
- **Serverless AWS:** Lambda and Bedrock on demand avoid the always-on services warned against in the hackathon material.
- **Demo/live separation:** deterministic adapters keep CI and the recorded story reproducible while live adapters remain independently testable.

## How to update this file

Update the date and relevant section whenever a feature is merged, a gate changes, a provider is verified, a deployment is created/deleted or a blocker is discovered. Never turn a pending external check into “passed” based only on local mocks.

