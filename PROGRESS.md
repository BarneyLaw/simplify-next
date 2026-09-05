# AdaptSG Progress

Last updated: 2026-09-05 (Asia/Singapore)

## Current status

Starter codebase complete and locally verified. The deterministic demo is ready for team rehearsal. A Kubernetes development environment is running through Argo CD on the LAN. The first AWS Lambda/DynamoDB/S3 stack and zero-token smoke path are deployed. The authenticated AWS v2 code is merged; its first update rolled back because the CloudFormation role lacked API Gateway tagging permissions. The bootstrap role is corrected and rollback is complete, with the empty orphaned v2 table removed. Redeployment of the rollback-safe template remains pending. Live provider mode remains pending credentials, provider approvals, and an independent Bedrock-disable contract switch.

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
- [x] Added server-owned journey lifecycle routes with draft approval, monitoring, replanning and version conflicts.
- [x] Added deterministic in-memory demo storage and a DynamoDB JSON/TTL storage adapter.
- [x] Added idempotency-key replay for journey creation, decisions and replans.
- [x] Added the full Streamlit demo and lightweight Vercel web mode.
- [x] Added Docker, Vercel and AWS SAM deployment files.
- [x] Added 71 tests, including 20 named evaluation scenarios.
- [x] Added Ruff, strict mypy, Bandit, dependency audit and 90% coverage gates.
- [x] Added Docker build and SAM validate/build jobs to GitHub Actions.
- [x] Added `AGENTS.md`, `ARCHITECTURE.md`, this tracker and a judge-oriented README.
- [x] Added collision-free four-role coding-agent ownership and handoff instructions.
- [x] Deployed a Python 3.12/Node 22 debug environment through the homelab Argo CD repository.
- [x] Exposed the Streamlit demo at `https://sim-next.lab.packetcraft.dev` with LAN DNS and TLS.

## Verified baseline

The current Role 1 trust scaffold was observed on `feature/r1-production-trust-foundations`
before the production-hardening changes: 163 tests passed with 90.50% branch coverage under
the local Python 3.13.7 virtual environment. This is not a completed Python 3.12 full-gate
result.

After the trust-foundation changes, the local Python 3.13.7 full gate observed 170 tests
passing with 90.28% branch coverage. The dependency audit found no known vulnerabilities.
SAM CLI and Python 3.12 are not installed in this workspace, so those checks remain pending.

| Check | Result | Evidence |
|---|---:|---|
| Tests | 71 passed | `python -m pytest` |
| Tests (trust scaffold) | 163 passed | `.venv/bin/python -m pytest -q` under Python 3.13.7 |
| Tests (AWS platform branch baseline) | 158 passed | `./scripts/check.ps1` on `feature/r4-aws-platform` |
| Tests (AWS recovery hardening) | 174 passed | Python 3.12.3 full gate; 90.33% branch coverage |
| Named scenarios | 20 passed | `tests/test_evaluation_scenarios.py` |
| Branch coverage | 92.87% | CI threshold is 90% |
| Ruff format/lint | Passed | `scripts/check.ps1` |
| Strict mypy | Passed | 25 source/test modules checked |
| Bandit | Passed | Source, API and Streamlit entry point |
| Dependency audit | Passed | No known vulnerabilities after minimum-version update |
| Streamlit headless flow | Passed | Initial plan, rain proposal, apply and fatigue approval |
| FastAPI plan/replan | Passed | Local `TestClient` integration |
| Vercel browser JS syntax | Passed | `scripts/check_web.mjs`; lifecycle client uses server-owned journey routes |
| Live provider parsers | Passed with mocks | Bedrock, OneMap, data.gov.sg, LTA/PUB |
| Docker image build | Passed in CI | GitHub Actions on feature PR commit `774fd5a` |
| SAM validate/build | Passed in CI | GitHub Actions on feature PR commit `774fd5a` |
| AWS CloudFormation schemas | Passed locally | `cfn-lint` 1.56.0 on application and OIDC bootstrap templates |
| AWS rollback recovery | Passed | Bootstrap role updated; stack restored to `UPDATE_ROLLBACK_COMPLETE`; empty orphaned `adaptsg-demo-state-v2` removed; original table retained |
| AWS Lambda/DynamoDB deployment smoke | Passed | `adaptsg-demo` reached `CREATE_COMPLETE`; main commit `d38ae48` produced DynamoDB journey and private S3 evidence with zero Bedrock tokens |
| Vercel platform build | Pending deployment | Vercel CLI/account unavailable locally |
| Kubernetes in-pod full gate | Passed | 71 tests, 98.1% coverage, lint, typing, Bandit, audit and browser syntax |
| Argo CD development app | Synced / Healthy | PR-branch revision `2445468`; awaiting GitOps PR merge |
| LAN DNS/TLS/health | Passed | `sim-next.lab.packetcraft.dev` -> `192.168.1.250`; trusted HTTPS 200 |
| Browser visual/session QA | Blocked | No in-app or extension browser connected in this session |

## Production trust foundations in progress

- [x] Created `feature/r1-production-trust-foundations` while preserving the six existing Role 1 files.
- [x] Added server-owned journey owner and processing-consent references.
- [x] Added demo-fixed and API Gateway-claim principal adapter seams; spoofable principal headers are ignored.
- [x] Added consent policy/readiness settings, action-intent API shape, and production authority-route disablement.
- [x] Replaced the public AWS Function URL template with an authenticated HTTP API/Cognito/DynamoDB shape.
- [x] Bound API Gateway-verified Cognito subjects to server-owned journeys.
- [x] Added the single-caregiver ADR and point-in-time recovery runbook.
- [ ] Complete transactional DynamoDB persistence for consent, intents and per-resource audit chains.
- [ ] Add Cognito browser client, live allowlist verification and production telemetry/alarm coverage.
- [ ] Run Python 3.12 full gate, SAM validate/build, staging AWS integration and restore drill.
- [x] Add local CloudFormation schema lint plus rollback-safe table protection controls.
- [x] Add explicit OAuth-scoped API routes, privacy-safe API access logs and edge throttling on the Role 4 recovery branch.
- [ ] Complete the production-readiness handoffs in `docs/contracts/production-readiness-handoffs.md`.

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
| `feature/project-documentation` | Governance, architecture, README and tracker | Merged |
| `feature/kubernetes-dev-environment` | Role workflow, dev dependency and CI portability fix | PR #9 passing; merge blocked by review policy |
| `feature/r3-redesigned-browser-client` | Rewrote `public/index.html` to the AdaptSG design-canvas redesign, frontend only | `scripts/check_web.mjs` and `tests/test_ui_browser_client.py`/`test_ui_demo_copy.py` pass; awaiting manual QA and merge |
| `feature/r4-aws-platform` | DynamoDB/S3/IAM/observability stack and OIDC Lambda CI/CD | Full local gate passed; AWS deployment and review pending |
| `feature/r4-authenticated-aws-demo` | Reconcile Cognito/API Gateway and owner-scoped v2 state with token-free AWS deployment | In progress; provider credentials intentionally absent |
| `feature/r4-aws-recovery-hardening` | API Gateway deployment permission, rollback-safe DynamoDB protection and local CloudFormation lint | In progress; locally verified and bootstrap applied |

## External setup still required

1. Request and verify OneMap API token access.
2. Request SLA approval for BFA routing before setting `ONEMAP_BFA_ENABLED=true`.
3. Request an LTA DataMall account key.
4. Confirm the selected Bedrock model is enabled in `us-east-1`.
5. Refresh hackathon AWS session credentials immediately before the live demo.
6. Obtain the required review for AdaptSG PR #9; all CI checks are passing.
7. Review and merge homelab GitOps PR #1, then retarget the live Application from the PR branch to `main`.
8. Inspect the LAN deployment in two independent browser contexts when a browser is connected.
9. Deploy the Vercel demo and inspect it in a connected browser at desktop and mobile widths.
10. Merge and deploy the rollback-safe authenticated AWS v2 stack, create one invite-only Cognito demo user, and retain the API Gateway authorization evidence.

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
- [x] Add structured CloudWatch metrics for latency, retained segments, tool success and loop caps.
- [x] Add DynamoDB conditional writes for race-safe duplicate decisions/replans.
- [x] Move AWS provider configuration to Secrets Manager dynamic references.
- [x] Replace public Function URL access with IAM authentication and restricted CORS.

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
- **Serverless AWS:** Lambda, DynamoDB on demand and optional Bedrock avoid always-on compute; Bedrock permission is disabled during the token-constrained phase.
- **Demo/live separation:** deterministic adapters keep CI and the recorded story reproducible while live adapters remain independently testable.

## How to update this file

Update the date and relevant section whenever a feature is merged, a gate changes, a provider is verified, a deployment is created/deleted or a blocker is discovered. Never turn a pending external check into “passed” based only on local mocks.
