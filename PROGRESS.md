# AdaptSG Progress

Last updated: 2026-09-07 (Asia/Singapore)

## Current status

The latest `main` deployment passes with durable consent, authority-grant, action-intent, and audit
records. Its internal Lambda/DynamoDB smoke emulates API Gateway's post-authorizer claim shape,
while a separate real API Gateway assertion still verifies that an unsigned protected request
receives `401`.

Starter codebase complete and locally verified. A Kubernetes development environment is running
through Argo CD on the LAN. The authenticated AWS v2 stack is deployed with Cognito, an OAuth-scoped
HTTP API, Python 3.12 Lambda, DynamoDB state, private S3 evidence, API access logs, dashboards and
alarms. The AWS static browser client is deployed at `https://d3butmnw1t1cuw.cloudfront.net` with
private S3, CloudFront Origin Access Control, same-origin API proxying, public runtime auth
configuration, verified-email Cognito self-signup and authorization-code/PKCE controls. The signed-out
page is responsive and the authenticated flow now requires explicit server-owned planning consent before
journey navigation is enabled. The stack runs in `live` mode with OneMap/LTA/data.gov.sg credentials
supplied by Secrets Manager and exact-resource Bedrock access to the US Claude Haiku 4.5 cross-region
inference profile, capped at 256 output tokens. A deployed end-to-end canary has exercised Bedrock,
OneMap, deterministic validation, DynamoDB persistence, scoped approval and live monitoring. Manual
two-browser owner-isolation and a complete authenticated browser rehearsal remain acceptance work.

Prompt handling no longer inherits the training itinerary. Bedrock now returns a forced typed tool
payload, malformed live extraction fails closed, ordinary venue mentions remain soft preferences,
and unknown destinations produce `NoFeasibleItinerary`. Deterministic planning consumes exact venue
and category preferences, explores bounded lunch-first and smaller candidates without dropping
required venues, and still passes every result through `ItineraryValidator`. A live matrix of nine
supported prompts produced distinct valid schedules; the unsupported Singapore Zoo case was rejected.

The deployed demo passed all 24 read-only AWS posture checks on 2026-09-06. The bootstrap stack
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
- [x] Removed training-itinerary defaults from prompt extraction and made deterministic venue
      selection honor typed venue/category preferences.
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
| AWS deployment posture | 24/24 passed | Main run `34037008396` verified the independent `us-east-1` Bedrock runtime region, exact US inference-profile/model ARNs and `CONNECTED` output plus private/versioned/encrypted S3, signed CloudFront origin, HTTPS and uncached API path, API metrics/logs/throttles, encrypted DynamoDB TTL, and public Cognito PKCE client |
| AWS alarm-notification bootstrap | Passed | `adaptsg-cicd-bootstrap` reached `UPDATE_COMPLETE`; the execution role can manage only `adaptsg-demo-operations-alarms`, and the GitHub deploy role has read-only attributes access to that topic |
| Current Python 3.12 CI gate | Passed | Branch run `34012492178` passed correctness, the 90% coverage threshold, dependency audit, Docker build and SAM validate/build; deployment was correctly skipped outside `main` |
| Latest merged AWS/browser deployment | Passed | Main run `34037008396` passed correctness, dependency audit, Docker, SAM, live configuration deployment, static publishing and 24/24 post-deploy checks at merge commit `f9907aa`; the bounded inference/provider canary then passed manually. |
| Live-provider AWS/browser deployment | Passed | Main run `34030441378` deployed commit `e5c2af1` in `live` mode, published the polished login page, verified public health and the unsigned `401` boundary, and passed 23/23 posture checks |
| OneMap credential and typed adapters | Passed live | Search returned five Toa Payoh candidates and one Gardens by the Bay candidate; walking routing returned fresh non-fixture provenance (`onemap_walk+transport_cost_policy_v1`), 76 minutes and 6,271 m |
| Controlled Bedrock canary (deterministic providers) | Passed | One regional Claude 3 Haiku inference used 783 input and 256 output tokens; deterministic validation passed before the journey was accepted, and DynamoDB/action-intent/approval/monitor readback passed |
| Live-provider journey canary | Partial / safe failure | Qualified origin `Toa Payoh MRT Station (NS19)` resolved uniquely; a validated fallback draft used fresh non-fixture OneMap drive routes, persisted in DynamoDB, required a scoped action intent, activated at version 2, and monitoring returned non-fixture `data.gov.sg_weather_psi+lta_pub_flood_train` provenance with no triggers. A later OneMap request received HTTP 429 and failed closed with `tool_unavailable`. |
| Live US Bedrock readiness | Passed | Direct `us-east-1` Converse through `us.anthropic.claude-haiku-4-5-20251001-v1:0` succeeded (11 input / 5 output tokens), and the deployed Lambda canary used the same profile (807 input / 256 output tokens). No Marketplace subscription step was required. |
| Current Python 3.12 application gate | Passed with known local audit limitation | 203 tests passed with 90.57% branch coverage; Ruff, strict mypy, Bandit, CloudFormation lint and browser gates passed. The portable Windows interpreter still lacks `venv.EnvBuilder` for local `pip-audit`; Linux main run `34037008396` passed the audit. |
| Prompt-regression gate | Passed | PRs #53, #55 and #56 passed Linux correctness, dependency audit, Docker and SAM checks. Windows Python 3.12 ran 252 tests at 91.22% branch coverage; its portable interpreter still lacks `venv.EnvBuilder` for local `pip-audit`. Live US Haiku extraction plus deterministic planning produced valid distinct schedules for all nine supported matrix prompts, while Singapore Zoo failed closed as unsupported. |
| US Bedrock/live-provider E2E canary | Passed | PR #50 preserved the exact `Toa Payoh MRT Station (NS19)` origin after Bedrock extraction. The deployed draft used three fresh non-fixture OneMap drive routes, cost $55.73, satisfied the 400 m walking/$70 budget/accessibility constraints, persisted to DynamoDB, required a scoped action intent, activated at version 2, and returned non-fixture `data.gov.sg_weather_psi+lta_pub_flood_train` monitoring (PSI 89, Fair, no triggers). |
| Windows-local Python 3.12 gate | Platform discrepancy | 172 tests passed with 90.77% branch coverage on `feature/r4-cost-alerting`; the portable Python distribution lacks the standard-library `venv.EnvBuilder`, so local `pip-audit` cannot start, while the Linux main gate passes dependency audit |
| Durable trust/location deployment | Passed | PR #36 merged at `e599875`; main run `34022280410` deployed and verified it successfully |
| Configurable Bedrock rollout branch | 192 tests passed | Python 3.12.10, 90.43% branch coverage; Ruff, strict mypy, Bandit, CloudFormation/SAM lint, workflow YAML and 19 browser-auth tests passed locally; Windows lacks `make` for the custom SAM build and its portable Python still cannot start `pip-audit`, so Linux CI remains authoritative for those two gates |
| Kubernetes in-pod full gate | Passed | 71 tests, 98.1% coverage, lint, typing, Bandit, audit and browser syntax |
| Argo CD development app | Synced / Healthy | PR-branch revision `2445468`; awaiting GitOps PR merge |
| LAN DNS/TLS/health | Passed | `sim-next.lab.packetcraft.dev` -> `192.168.1.250`; trusted HTTPS 200 |
| Browser visual/session QA | Partial | Desktop and narrow-viewport headless Chrome checks passed for the polished signed-out page; independent authenticated browser sessions remain |

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
| `feature/r4-live-provider-deployment` | Protected-environment switch between deterministic and live providers, including fail-closed secret requirements and live-aware deployment verification | Merged in PR #40 |
| `feature/r3-login-page-polish` | Responsive signed-out welcome/access layout with unavailable navigation and unresolved provenance hidden | Merged in PR #41; main run `34030441378` deployed successfully |
| `feature/r1-consent-discovery` | Inactive consent status exposes the current server-owned policy and required categories | Merged in PR #43 |
| `feature/r3-consent-onboarding` | Explicit post-login planning-consent screen with authenticated idempotent submission and retryable failures | Merged in PR #44; main run `34031706820` deployed successfully |
| `feature/r1-location-label-parsing` | Preserve qualified OneMap labels containing station codes and punctuation in deterministic fallback parsing | Merged in PR #45 |
| `feature/r3-specific-onemap-origin` | Use the unique `Toa Payoh MRT Station (NS19)` sample origin and guard it in static checks | Merged in PR #46; main run `34032472349` deployed successfully |
| `feature/r1-bedrock-runtime-region` | Separate the Bedrock runtime region from the Singapore application/data region | Merged in PR #48 |
| `feature/r4-us-bedrock-deployment` | Deploy exact-resource access for the US Haiku 4.5 cross-region inference profile and verify the independent runtime region | Merged in PR #49; main run `34036188634` passed 24/24 posture checks |
| `feature/r1-preserve-explicit-origin` | Preserve qualified user-supplied origins after Bedrock extraction so live location verification remains unambiguous | Merged in PR #50; main run `34037008396` deployed successfully |
| `feature/origin-resolution-and-mode-banner` | Normalising query ladder for start locations, a candidate chooser for origins that cannot be pinned to one place, and removal of the standing Live/Demo banner | In review |
| `feature/r1-prompt-extraction-integrity` | Forced typed Bedrock extraction, deterministic reconciliation and fail-closed unsupported destinations | Merged in PR #53 |
| `feature/r2-prompt-aware-selection` | Deterministic venue/category selection plus bounded safe order and optional-stop candidates | Merged in PR #55 |
| `feature/r1-afternoon-lunch-default` | Preserve explicit lunch deadlines and disclose feasible defaults for afternoon starts | Merged in PR #56 |

## External setup still required

1. Request SLA approval for BFA routing before setting `ONEMAP_BFA_ENABLED=true`.
2. Refresh hackathon AWS session credentials immediately before the live demo.
3. Obtain the required review for AdaptSG PR #9; all CI checks are passing.
4. Review and merge homelab GitOps PR #1, then retarget the live Application from the PR branch to `main`.
5. Inspect the LAN deployment in two independent browser contexts when a browser is connected.
6. Exercise Cognito signup, email verification, login, protected API access, and logout in two browsers,
    and confirm one authenticated principal cannot read another's journey.
7. Check current account spending and the hackathon threshold in the Billing console; the workshop
    organization explicitly denies Cost Explorer API access, and the application stack does not request
    account-level billing-management permissions.
8. If operational email alerts are desired, set `ADAPTSG_ALARM_NOTIFICATION_EMAIL` and confirm the
    SNS subscription.
9. Keep the deployed Bedrock runtime on `us-east-1` with the tested
   `us.anthropic.claude-haiku-4-5-20251001-v1:0` inference profile. Re-run only a bounded canary if
   the model, Region, IAM resources or workshop account policy changes.

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

## Frontend, 2026-09-06

Six commits on `fix/frontend-views-and-mascot`, Role 3.

1. **Production fix.** Every view was rendering stacked down one page. `activateView()` was
   correct; the stylesheet was not. The `hidden` attribute is enforced only by the UA rule
   `[hidden]{display:none}`, which any author `display` beats -- and `.work{display:grid}` matches
   every `.view`. `80ca854` had replaced the stylesheet wholesale and dropped
   `[hidden]{display:none!important}` with it; no gate read CSS, so CI stayed green. Also moved
   `.mtop`/`.mlocked` above the `@media` blocks that reveal them (a media rule adds no
   specificity, so the base rules that sat after them won at every viewport and the mobile top bar
   never appeared). Replaced the generated SVG logo with the new mascot, keyed off its black field
   and derived at two sizes.
2. **Design migration.** `public/index.html` moved to the achromatic system in `docs/DESIGN.md`:
   the 248px rail and its duplicate mobile bar are replaced by one floating top nav over a centred
   1280px column, must-haves became a single pill row, cards are lifted by 1px borders instead of
   shadows, and Inter is loaded from the variable axis for the 440/456/652 weights. Safety chroma
   (`--breach`/`--caution`/`--pass`) is retained deliberately and documented; everything else is
   achromatic.

3. **CI unblocked.** PR #38 was red on ruff, and the failure was not reproducible in any working
   tree: it lived only in `5d2c1a3 Merge branch 'main'`, which resolved `src/adaptsg/settings.py`
   by taking main's import line and the branch's class body, leaving `@field_validator` bound to
   nothing (F821) and `ValidationError` orphaned in `tests/test_settings.py` after the three tests
   using it were dropped (F401). Both restored, not deleted -- `.env.example` still ships four
   blank numerics, so removing `_blank_is_unset` would have traded a lint error for an import-time
   crash on any copied `.env`. Six `mypy --strict` errors sat immediately behind the ruff one.
4. **LM Studio removed** (owner's call: extraction is tested against Bedrock directly). main had
   already dropped the provider; the merge kept this branch's `LMStudioPreferenceParser` reading
   `settings.lmstudio_*` fields that no longer exist, which is what those six type errors were.
   `preference_parser.py` and its tests take main's side; `.env.example` and the README section go
   with them. The parser protocol, deterministic fallback and Bedrock path are untouched.
5. **Mascot re-derived.** `public/mascot-{128,512}.png` carried an opaque blue field -- the old
   master's neon glow, preserved as paint by the flood-fill key that used to be documented in the
   role file. The master is now already keyed, so derivation is crop-to-alpha-bbox plus a
   premultiplied LANCZOS resize (premultiply is not optional: the transparent field is RGB black,
   and Pillow resizes channels independently). Alpha-0 pixels 48.6% -> 59.0%; 512px file 166 KB ->
   135 KB; intrinsic sizes 512x352 / 128x88, with the three `<img class="mark">` tags updated.
6. **Nav floats.** `.topbar` no longer paints an opaque `--canvas` band; the pill is `--veil`
   (`--mist` at 72%) behind a 14px backdrop blur, so content is partly visible scrolling under it.
   `pointer-events` is split band/pill so the transparent gutter cannot eat clicks. No `saturate()`
   -- the only chroma on the page is a safety verdict and must not be amplified in passing. Through
   the veil `--disabled` measures 4.2:1 on canvas and 2.5:1 over a `--breach` chip, so `.pill.off`
   took the opaque `--canvas` ground `.pill.go` already had. Recorded as deviation 4 in
   `docs/DESIGN.md`.

`scripts/check_web.mjs` gained four gates so none of this can regress silently: the `[hidden]`
rule, the `@media` source-order shape, local `src="/..."` assets existing in `public/`, and `--ash`
never carrying text. Each was confirmed to fail before being confirmed to pass.

**HANDOFF (Role 4, `.github/workflows/ci.yml`):** the S3 sync applies
`--cache-control "no-cache"` to every object, so `mascot-128.png` (15 KB) and `mascot-512.png`
(135 KB) revalidate on every load. Not a blocker; `*.png` deserves a long `max-age`.

**Gate status:** `./scripts/check.sh` passes locally except `sam`, which is not installed here
(`cfn-lint` covers both templates).

**HANDOFF (Role 1, `tests/test_agent_and_api.py`):**
`test_live_mode_fails_closed_without_production_trust_configuration` passes in CI and from any
other working directory, but fails whenever it is run from a repo root holding a configured `.env`.
`Settings(env_file=".env")` reads that file regardless of the process environment, so a developer
with `ADAPTSG_AUTHENTICATION_MODE=cognito` set locally inverts a fail-closed assertion into a pass
and sees a permanently red local gate. `Settings(_env_file=None, adaptsg_mode="live")` -- the form
the other tests in `tests/test_settings.py` already use -- would pin it. Not Role 3's file.

The earlier note here about two pre-existing LM Studio failures is superseded: the second test went
with the provider, and the first is the `.env` issue above rather than a defect.

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

- **Origins are normalised, not demanded:** OneMap holds each MRT line code as its own row, so
  `Bishan MRT Station (NS17/CC15)` — the label the station actually carries — matched nothing and
  failed the whole plan. Resolution now tries a bounded ladder of derived queries rather than
  requiring a verbatim gazetteer hit, which had confined the product to hand-tuned sample prompts.
- **"No match" is not "no service":** a query the gazetteer answered with nothing is a user-input
  problem and raises `origin_not_verified` (422, with candidates to choose from). Only a provider
  that could not answer raises `ToolUnavailable` (503) and fails closed under rule 10. Conflating
  the two had shown an outage message for an unrecognised place name.
- **Provider credentials never travel in error text:** httpx carries the full request URL in its
  exception message and OneMap authenticates by query parameter, so a provider failure published
  the API token in the 503 body the browser renders. Credential parameters are redacted at the
  three tool boundaries that interpolate an exception.
- **Provenance moved, not removed:** the standing Live/Demo banner stated the runtime mode before
  there was a plan to qualify. Rule 11 is now carried by the evidence panel's `Runtime mode` row,
  where it sits beside the values it describes, and a static gate holds it to the resolved
  `/api/health` value.
- **Eyebrows removed, em dashes removed:** every screen carried a small uppercase kicker above its
  heading, which named the screen a second time in a smaller voice and, on the signed-out page,
  named nothing at all (`Plan with confidence`, `Secure access`). All nine are gone with the
  `.eyebrow` class, along with the 01/02/03 step row, and the signed-out headline and lede were
  rewritten in concrete terms. Two labels stayed as plain words on `.label` because nothing else on
  screen carries their information: `Your must-haves` over the pill row, and `Stop N, now` /
  `Stop N, suggested` in the approval comparison. Em dashes are out of UI copy in all eleven places
  they appeared; en dashes stay in numeric ranges, and `preference_parser.py` still accepts all
  three dashes as traveller input. Recorded as deviation 6 in `docs/DESIGN.md`.

- **One orchestrator:** the problem needs bounded coordination, not a swarm.
- **Curated venues:** reliability and unsupported-claim prevention outweigh catalog breadth in the MVP.
- **Smallest change:** candidate scoring heavily penalizes changed segments before cost/walking tie-breakers.
- **AWS-only web delivery:** Streamlit is retired, not retained as a local interface. `public/index.html` is the one client, served same-origin by FastAPI locally and in Docker; CloudFront serves the same directory from private S3 and proxies `/api/*` to API Gateway on AWS.
- **Serverless AWS:** Lambda, DynamoDB on demand and exact-resource Bedrock access avoid always-on
  compute; live deployment invokes the verified US Claude Haiku 4.5 cross-region inference profile
  from `us-east-1` with a 256-token output cap while application state remains in Singapore.
- **Demo/live separation:** deterministic adapters keep CI and the recorded story reproducible while live adapters remain independently testable.

## How to update this file

Update the date and relevant section whenever a feature is merged, a gate changes, a provider is verified, a deployment is created/deleted or a blocker is discovered. Never turn a pending external check into “passed” based only on local mocks.
