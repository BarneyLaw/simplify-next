# AdaptSG Progress

Last updated: 2026-09-06 (Asia/Singapore)

## Current status

The latest `main` deployment passes with durable consent, authority-grant, action-intent, and audit
records. Its internal Lambda/DynamoDB smoke emulates API Gateway's post-authorizer claim shape,
while a separate real API Gateway assertion still verifies that an unsigned protected request
receives `401`.

Starter codebase complete and locally verified. The deterministic demo is ready for team rehearsal. A Kubernetes development environment is running through Argo CD on the LAN. The authenticated AWS v2 stack is deployed with Cognito, an OAuth-scoped HTTP API, Python 3.12 Lambda, DynamoDB state, private S3 evidence, API access logs, dashboards and alarms. The AWS static browser client is deployed at `https://d3butmnw1t1cuw.cloudfront.net` with private S3, CloudFront Origin Access Control, same-origin API proxying, public runtime auth configuration, verified-email Cognito self-signup and authorization-code/PKCE controls. Bedrock has exact-resource invoke permission and a 256-output-token cap, while routing and environmental providers remain deterministic. No deployment inference request was made. The Role 1 identity/provider separation, Role 3 AWS browser metadata and corrected deployment smoke are merged, deployed and passing. Manual two-browser owner-isolation and one controlled Bedrock canary remain.

The deployed demo passed all 23 read-only AWS posture checks on 2026-09-06. The bootstrap stack
is updated so CI can repeat those checks after each deployment without access to application
records, Cognito users, provider secrets, or Lambda environment values. Authenticated multi-user
ownership is implemented in the deployed revision; independent browser-session verification is
still required before making the final multi-user acceptance claim.

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
- [x] Added the full Streamlit demo and a lightweight legacy serverless API mode.
- [x] Added Docker and AWS SAM deployment files; AWS is the sole public deployment target.
- [x] Added 71 tests, including 20 named evaluation scenarios.
- [x] Added Ruff, strict mypy, Bandit, dependency audit and 90% coverage gates.
- [x] Added Docker build and SAM validate/build jobs to GitHub Actions.
- [x] Added `AGENTS.md`, `ARCHITECTURE.md`, this tracker and a judge-oriented README.
- [x] Added collision-free four-role coding-agent ownership and handoff instructions.
- [x] Deployed a Python 3.12/Node 22 debug environment through the homelab Argo CD repository.
- [x] Exposed the Streamlit demo at `https://sim-next.lab.packetcraft.dev` with LAN DNS and TLS.
- [x] Retired Streamlit and restored the static browser client (`public/index.html`) with a Cognito
      authorization-code/PKCE layer, served same-origin by `uvicorn adaptsg.web_api:app` locally, in
      Docker, and on AWS CloudFront.

## Verified baseline

The current Role 1 trust scaffold was observed on `feature/r1-production-trust-foundations`
before the production-hardening changes: 163 tests passed with 90.50% branch coverage under
the local Python 3.13.7 virtual environment. This is not a completed Python 3.12 full-gate
result.

After the trust-foundation changes, the local Python 3.13.7 full gate observed 170 tests
passing with 90.28% branch coverage. The dependency audit found no known vulnerabilities.
Python 3.12.3 is available in the workspace and passes the full gate. SAM CLI remains
unavailable locally, so SAM validation and builds run in GitHub Actions.

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
| Legacy serverless config syntax | Passed | Compatibility file retained but not deployed |
| Live provider parsers | Passed with mocks | Bedrock, OneMap, data.gov.sg, LTA/PUB |
| Docker image build | Passed in CI | Main run `33972607910` at commit `4dbac581` |
| SAM validate/build | Passed in CI | Main run `33972607910` at commit `4dbac581` |
| AWS CloudFormation schemas | Passed locally | `cfn-lint` 1.56.0 on application and OIDC bootstrap templates |
| AWS rollback recovery | Passed | Bootstrap role updated; stack restored to `UPDATE_ROLLBACK_COMPLETE`; empty orphaned `adaptsg-demo-state-v2` removed; original table retained |
| AWS HTTP API log-delivery bootstrap | Passed | `adaptsg-cicd-bootstrap` reached `UPDATE_COMPLETE`; effective execution-role actions verified with IAM |
| AWS Lambda/DynamoDB deployment smoke | Passed | `adaptsg-demo` reached `CREATE_COMPLETE`; main commit `d38ae48` produced DynamoDB journey and private S3 evidence with zero Bedrock tokens |
| AWS authenticated v2 deployment | Passed | `adaptsg-demo` reached `UPDATE_COMPLETE`; main run `33972607910` passed token-free smoke and public/protected route checks |
| AWS static web deployment | Passed | `adaptsg-demo` reached `UPDATE_COMPLETE`; main run `33976769643` passed CloudFront page/runtime-config/same-origin API smoke; Cognito callback and logout URLs target CloudFront; Bedrock output is `DISABLED` |
| AWS deployment posture | 23/23 passed | Main workflow-dispatch run `34024320223` verified the exact Bedrock ARN parameter and `CONNECTED` output plus private/versioned/encrypted S3, signed CloudFront origin, HTTPS and uncached API path, API metrics/logs/throttles, encrypted DynamoDB TTL, and public Cognito PKCE client |
| AWS alarm-notification bootstrap | Passed | `adaptsg-cicd-bootstrap` reached `UPDATE_COMPLETE`; the execution role can manage only `adaptsg-demo-operations-alarms`, and the GitHub deploy role has read-only attributes access to that topic |
| Current Python 3.12 CI gate | Passed | Branch run `34012492178` passed correctness, the 90% coverage threshold, dependency audit, Docker build and SAM validate/build; deployment was correctly skipped outside `main` |
| Latest merged AWS/browser deployment | Passed | Main workflow-dispatch run `34024320223` passed correctness, Docker, SAM, connected configuration deployment, static publishing and 23/23 post-deploy checks at commit `1a2bec4`; no inference smoke ran |
| Windows-local Python 3.12 gate | Platform discrepancy | 172 tests passed with 90.77% branch coverage on `feature/r4-cost-alerting`; the portable Python distribution lacks the standard-library `venv.EnvBuilder`, so local `pip-audit` cannot start, while the Linux main gate passes dependency audit |
| Durable trust/location deployment | Passed | PR #36 merged at `e599875`; main run `34022280410` deployed and verified it successfully |
| Configurable Bedrock rollout branch | 192 tests passed | Python 3.12.10, 90.43% branch coverage; Ruff, strict mypy, Bandit, CloudFormation/SAM lint, workflow YAML and 19 browser-auth tests passed locally; Windows lacks `make` for the custom SAM build and its portable Python still cannot start `pip-audit`, so Linux CI remains authoritative for those two gates |
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
- [x] Implement, merge, and deploy transactional DynamoDB persistence for consent, authority grants,
      intents and per-resource audit chains.
- [x] Complete the Role 3 static Cognito/PKCE browser client.
- [ ] Complete live allowlist verification.
- [ ] Complete a DynamoDB point-in-time restore drill in a non-production table.
- [x] Add local CloudFormation schema lint plus rollback-safe table protection controls.
- [x] Add explicit OAuth-scoped API routes, privacy-safe API access logs and edge throttling on the Role 4 recovery branch.
- [x] Add a read-only post-deploy verifier and apply its scoped GitHub role permissions; live demo passed 22/22 checks.
- [x] Complete the remaining code handoffs in `docs/contracts/production-readiness-handoffs.md`;
      merge/deploy and manual operational acceptance remain.

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
| `feature/r3-redesigned-browser-client` | Rewrote `public/index.html` to the AdaptSG design-canvas redesign, frontend only | Superseded by `feature/r3-streamlit-redesign`; not merged |
| `feature/r3-streamlit-redesign` | Ported the design-canvas redesign into Streamlit (`src/adaptsg/ui.py`, `ui.css`) and retired the browser client — `public/index.html`, `scripts/check_web.mjs`, `tests/test_ui_browser_client.py` are deleted | Full local gate passed; awaiting manual QA and merge |
| `feature/r4-aws-platform` | DynamoDB/S3/IAM/observability stack and OIDC Lambda CI/CD | Full local gate passed; AWS deployment and review pending |
| `feature/r4-authenticated-aws-demo` | Reconcile Cognito/API Gateway and owner-scoped v2 state with token-free AWS deployment | Merged; provider credentials intentionally absent |
| `feature/r4-aws-recovery-hardening` | API Gateway deployment permission, rollback-safe DynamoDB protection and local CloudFormation lint | Merged; second deployment rolled back at access-log activation |
| `feature/r4-api-log-delivery-permissions` | CloudWatch Logs delivery permissions required by authenticated HTTP API access logging | Merged in PR #24; bootstrap and application deployment passed |
| `feature/r4-aws-web-hosting` | CloudFront/private-S3 static hosting, same-origin API, Cognito self-signup, PKCE runtime contract and CI publishing | Merged in PR #25 and deployed; main run `33976769643` passed all checks and AWS smoke tests |
| `feature/r4-deployment-posture` | Read-only live verification for token-free AWS security and service wiring | Merged; bootstrap update `UPDATE_COMPLETE`, live stack passed 22/22 checks, and branch run `34012492178` passed correctness, Docker and SAM |
| `feature/static-browser-cognito` | Restored `public/index.html` and its serving path, added the Cognito PKCE auth layer plus `scripts/test_web_auth.mjs`, retired Streamlit | Merged in PR #29; main run `34013996057` deployed the static client successfully; independent browser-session QA remains pending |
| `feature/r4-cost-alerting` | Scoped SNS delivery for Lambda error/throttle alarms plus post-deploy policy verification | Merged in PR #30 |
| `feature/r3-functional-aws-demo` | Remove stale Vercel browser metadata and reverify the complete static journey/auth flow | Merged in PR #32 |
| `feature/r4-authenticated-lambda-smoke` | Cognito-aware direct-Lambda smoke plus real unsigned API rejection assertion | Merged in PR #35; deployment passes |
| `feature/r1-durable-trust-records` | Durable consent/grants/intents, atomic per-journey audit and strict location ambiguity handling | Merged in PR #36; main run `34022280410` deployed successfully |
| `feature/r4-configurable-bedrock-rollout` | Exact-resource Bedrock rollout variables, capped output, manual deployment trigger, connected-state verification and token-safe smoke behavior | Merged in PR #37; workflow-dispatch run `34024320223` deployed successfully and passed 23/23 posture checks with Bedrock `CONNECTED`; inference smoke was intentionally skipped |

## External setup still required

1. Request and verify OneMap API token access.
2. Request SLA approval for BFA routing before setting `ONEMAP_BFA_ENABLED=true`.
3. Request an LTA DataMall account key.
4. Complete Anthropic model access if required, verify the Claude Haiku 4.5 global inference profile
   from `ap-southeast-1`, then run one controlled browser canary and inspect Bedrock token metrics.
5. Refresh hackathon AWS session credentials immediately before the live demo.
6. Obtain the required review for AdaptSG PR #9; all CI checks are passing.
7. Review and merge homelab GitOps PR #1, then retarget the live Application from the PR branch to `main`.
8. Inspect the LAN deployment in two independent browser contexts when a browser is connected.
9. Exercise Cognito signup, email verification, login, protected API access, and logout in two browsers,
    and confirm one authenticated principal cannot read another's journey.
10. Check current account spending and the hackathon threshold in the Billing console; the workshop
    organization explicitly denies Cost Explorer API access, and the application stack does not request
    account-level billing-management permissions.
11. If operational email alerts are desired, set `ADAPTSG_ALARM_NOTIFICATION_EMAIL` and confirm the
    SNS subscription.

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
- **AWS-only web delivery:** Streamlit is retired, not retained as a local interface. `public/index.html` is the one client, served same-origin by FastAPI locally and in Docker; CloudFront serves the same directory from private S3 and proxies `/api/*` to API Gateway on AWS.
- **Serverless AWS:** Lambda, DynamoDB on demand and optional Bedrock avoid always-on compute; Bedrock permission is disabled during the token-constrained phase.
- **Demo/live separation:** deterministic adapters keep CI and the recorded story reproducible while live adapters remain independently testable.

## How to update this file

Update the date and relevant section whenever a feature is merged, a gate changes, a provider is verified, a deployment is created/deleted or a blocker is discovered. Never turn a pending external check into “passed” based only on local mocks.
