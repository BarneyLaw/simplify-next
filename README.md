# AdaptSG

Inclusive journey planning that preserves accessibility, health, timing and budget constraints when conditions change.

AdaptSG is a Singapore-focused proof of concept for caregivers travelling with an elderly or mobility-limited family member. It creates a timed plan of at most three stops, monitors weather and disruption signals, then proposes the smallest safe change. It never silently relaxes a hard constraint.

> The LLM proposes; deterministic code validates.

## Problem

A caregiver planning a day out must manually reconcile wheelchair access, walking limits, rain, PSI, flood conditions, rest, meals, fixed times, disruptions and budget. One change can make the original plan unsafe or unusable.

## Solution

AdaptSG:

1. extracts hard constraints and soft preferences into typed state;
2. selects a small set of curated venues;
3. obtains route, walking, cost and environmental values from tools;
4. builds and deterministically validates a timed itinerary;
5. monitors or receives a change;
6. preserves the unaffected prefix and scores a small set of alternatives;
7. shows a before/after diff and asks for approval when cost rises materially.

The model cannot approve its own output. If no safe option exists, the service stops and asks the caregiver instead of inventing a route.

## Five-minute demo

Start with the pre-filled request:

> Plan a 10 am-5 pm day for me and my 72-year-old mother, starting from Toa Payoh. She uses a wheelchair, should not walk more than 400 metres at once, needs lunch before 1 pm, and we have a $70 transport and activity budget. We would like to visit Gardens by the Bay.

The deterministic demo produces National Gallery Singapore, an accessible lunch stop and Gardens by the Bay with route provenance, rest seating, walking and cost values.

Then:

1. select **Simulate heavy rain + flood**;
2. review the indoor replacement and 67% retained-plan metric;
3. apply it;
4. select **Mum is more tired**;
5. review the shorter activity/taxi proposal and explicit cost approval.

The demo is reproducible without cloud credentials. Demo values are labelled and must not be presented as live.

## Quick start

Requirements: Python 3.12 and Git. Node.js is only needed for the browser JavaScript syntax gate.

### Windows PowerShell

```powershell
py -3.12 -m venv .venv
./.venv/Scripts/Activate.ps1
python -m pip install --upgrade "pip>=26.2"
python -m pip install -r requirements.txt
Copy-Item .env.example .env
streamlit run streamlit_app.py
```

### macOS or Linux

```sh
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade 'pip>=26.2'
python -m pip install -r requirements.txt
cp .env.example .env
streamlit run streamlit_app.py
```

Open `http://localhost:8501`. The default `ADAPTSG_MODE=demo` requires no network or secrets.

### Local API and Vercel-style page

```sh
uvicorn adaptsg.web_api:app --reload --port 8000
```

Open `http://localhost:8000` for the lightweight web mode or `http://localhost:8000/docs` for OpenAPI.

## Docker

```sh
docker build -t adaptsg .
docker run --rm -p 8501:8501 adaptsg
```

or:

```sh
docker compose up --build
```

To pass a live configuration, use an explicit environment file:

```sh
docker run --rm -p 8501:8501 --env-file .env adaptsg
```

The image runs as a non-root user and exposes a Streamlit health check.

## Configuration and secrets

Copy `.env.example` to `.env`. `.env` and Streamlit secrets are ignored by Git.

| Variable | Default | Purpose |
|---|---:|---|
| `ADAPTSG_MODE` | `demo` | `demo` for deterministic adapters; `live` for Bedrock and official APIs |
| `AWS_REGION` | `us-east-1` | Bedrock region used by this hackathon account |
| `AWS_PROFILE` | empty | Preferred local AWS CLI/SSO profile |
| `AWS_ACCESS_KEY_ID` | empty | Temporary credential when a profile is unavailable |
| `AWS_SECRET_ACCESS_KEY` | empty | Temporary credential; never commit it |
| `AWS_SESSION_TOKEN` | empty | Required with hackathon temporary credentials |
| `BEDROCK_MODEL_ID` | Claude Haiku 4.5 global profile | Configured Bedrock Converse model |
| `ONEMAP_API_TOKEN` | empty | Required for live OneMap routing |
| `ONEMAP_BFA_ENABLED` | `false` | Enable BFA walking routes only after SLA approval |
| `DATA_GOV_SG_API_KEY` | empty | Optional for higher data.gov.sg rate limits |
| `LTA_ACCOUNT_KEY` | empty | Required for live train and PUB flood alerts |
| `ADAPTSG_APPROVAL_COST_INCREASE_SGD` | `8` | Cost increase that requires caregiver approval |
| `ADAPTSG_MAX_REPLANS` | `2` | Bounded replanning cycles; maximum accepted value is three |

Hackathon AWS access credentials expire regularly. Refresh all three temporary values, including `AWS_SESSION_TOKEN`, or use the configured AWS profile. Verify identity before a live run:

```sh
aws sts get-caller-identity --profile workshop
```

Do not put secrets in source, screenshots, videos, Vercel client code or GitHub Actions logs.

## Amazon Bedrock usage

In live mode `BedrockPreferenceParser` calls the Bedrock Converse API at temperature zero. It requests one strict JSON object and records input/output token counts. The result still passes through Pydantic, the deterministic planner and `ItineraryValidator`.

If Bedrock extraction fails, the local app uses a conservative fallback and displays a warning. Routing or environment failures do not become live claims: the current plan is retained and live verification is reported as failed.

The supplied AWS account guide specifies `us-east-1` for the hackathon. Confirm that the configured model is enabled in that region before the demo.

## AWS serverless deployment

Requirements: AWS CLI v2, AWS SAM CLI, and an AWS SSO/profile or short-lived hackathon
credentials. Bedrock access is not required for the default deployment.

```sh
sam validate --lint --template-file infra/aws/template.yaml
sam build --template-file infra/aws/template.yaml
sam deploy --guided --region us-east-1 --capabilities CAPABILITY_NAMED_IAM
```

The custom SAM Makefile builds a lean API artifact without Streamlit, pandas or pyarrow. The
complete one-time GitHub OIDC bootstrap, Secrets Manager setup, deployment variables, manual
commands, verification, and teardown procedure is in [`infra/aws/README.md`](infra/aws/README.md).

The stack contains:

- Python 3.12 Lambda;
- Mangum/FastAPI handler;
- IAM-authenticated Lambda Function URL with exact-origin CORS;
- encrypted on-demand DynamoDB journey/idempotency storage with TTL;
- private encrypted/versioned S3 catalog and evaluation-evidence storage;
- reserved concurrency, X-Ray, retained logs, alarms, and an operations dashboard;
- optional exact-resource Bedrock permission, disabled by default.

`BedrockModelArns=DISABLED` is the safe default: no inference permission is attached and the
GitHub pipeline asserts zero model tokens in its deterministic Lambda/DynamoDB smoke test. The
main-branch deploy job uses GitHub OIDC rather than stored AWS access keys. Provider values are
resolved from Secrets Manager when configured. Delete the application and bootstrap stacks after
the hackathon to stop resource use:

```sh
sam delete --stack-name adaptsg-demo
```

## Vercel deployment

Vercel cannot reliably host Streamlit's long-running WebSocket process. This repository therefore uses:

- Streamlit for the full local/Docker experience;
- `public/index.html` for the Vercel browser experience;
- `api/index.py` as the Vercel FastAPI function;
- the same Python service, validator and tests in both modes.

Install and deploy:

```sh
npm install --global vercel
vercel
vercel --prod
```

Set `ADAPTSG_MODE=demo` for a credential-free public demonstration. For live Bedrock calls from the Vercel Python function, configure all required AWS and Singapore API environment variables in the Vercel project. Hackathon AWS session credentials expire, so refresh them before the judging window. Use a narrowly scoped deploy identity for any longer-lived environment.

The Python runtime entry point and routing follow [Vercel's FastAPI convention](https://vercel.com/docs/functions/runtimes/python). `vercel.json` excludes tests, infrastructure and Streamlit UI files from the function bundle.

## API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/health` | Process health and current mode; no live tool call |
| `POST` | `/api/plan` | Parse, plan and validate one journey |
| `POST` | `/api/replan` | Validate and score a typed replan trigger |
| `POST` | `/api/journeys` | Create a server-owned draft journey |
| `GET` | `/api/journeys/{id}` | Retrieve journey state for refresh/recovery |
| `POST` | `/api/journeys/{id}/monitor` | Retrieve conditions and detected triggers |
| `POST` | `/api/journeys/{id}/replan` | Store a validated replan proposal |
| `POST` | `/api/journeys/{id}/decision` | Approve or reject an initial plan or proposal |

Example:

```sh
curl -X POST http://localhost:8000/api/plan \
  -H 'content-type: application/json' \
  -d '{"prompt":"Plan a 10 am-5 pm wheelchair day from Toa Payoh, lunch before 1 pm, budget $70.","journey_date":"2026-09-01"}'
```

No booking, payment or medical endpoint exists.

## Official data integrations

- [OneMap public routing](https://www.onemap.gov.sg/apidocs/routing) for walk, public-transport and drive routes.
- [OneMap Barrier-Free Access routing](https://www.onemap.gov.sg/apidocs/bfa), which requires approved access and currently covers selected areas.
- [data.gov.sg real-time APIs](https://guide.data.gov.sg/developer-guide/real-time-apis) for NEA 24-hour weather and PSI.
- [LTA DataMall dynamic datasets](https://datamall.lta.gov.sg/content/datamall/en/dynamic-data.html) for train service and PUB flood alerts.

`src/adaptsg/data/venues.json` contains 18 curated demonstration venues. Opening hours, costs and accessibility fields are intentionally version-controlled for demo reliability; they are not a substitute for production verification.

## Project structure and file purpose

```text
.
|-- streamlit_app.py              Full caregiver demo UI
|-- public/index.html             Lightweight Vercel web UI
|-- api/index.py                  Vercel Python/FastAPI entry point
|-- src/adaptsg/
|   |-- agent.py                  Bounded LangGraph and service facade
|   |-- domain.py                 Strict immutable journey state
|   |-- preference_parser.py      Bedrock extraction and safe fallback
|   |-- planning.py               Planner and minimal-change replanner
|   |-- validation.py             Deterministic hard-constraint authority
|   |-- presentation.py           Pure UI/API formatting helpers
|   |-- web_api.py                Shared FastAPI routes
|   |-- aws_handler.py            Lambda/Mangum adapter
|   |-- tools/catalog.py          Curated venue access
|   |-- tools/routing.py          Demo and OneMap routing clients
|   |-- tools/environment.py      Demo and official condition clients
|   `-- data/venues.json          Curated 18-venue demo dataset
|-- tests/                         Unit, contract and 20 scenario tests
|-- scripts/check.*               Local equivalents of CI gates
|-- Dockerfile                    Non-root Streamlit image
|-- vercel.json                   Lean Vercel function/static config
|-- infra/aws/template.yaml       Lambda/Bedrock SAM stack
|-- Makefile                      Lean SAM artifact builder
|-- ARCHITECTURE.md               Flows, trust boundaries and deployment
|-- AGENTS.md                     Safety, coding and Git rules for agents
`-- PROGRESS.md                   Verified progress, blockers and next work
```

## Correctness and evaluation

Install development dependencies, then run the complete local gate:

```sh
python -m pip install -r requirements-dev.txt
./scripts/check.sh
```

PowerShell:

```powershell
python -m pip install -r requirements-dev.txt
./scripts/check.ps1
```

Current verified baseline:

- 71 passing tests;
- 20 named caregiver/disruption scenarios;
- 98.1% branch coverage, with CI failing below 90%;
- strict mypy, Ruff lint/format and Bandit;
- dependency audit with no known vulnerabilities;
- API, Streamlit headless and browser JavaScript smoke tests;
- Docker build and AWS SAM validate/build jobs in GitHub Actions.

The 20 scenarios include heavy rain, high PSI, flood, closure, train disruption, fatigue, reduced budget, early lunch/finish constraints, unverified accessibility and the replan loop cap.

## Guardrails

- Hard constraints live in typed state, not conversation history.
- Only verified accessibility is eligible when wheelchair access is mandatory.
- Route destinations must match catalog coordinates and include provenance.
- Tool-derived total cost is recomputed and compared with the declared total.
- Every candidate is validated before it can be shown as safe.
- Unaffected segments dominate the deterministic change score.
- Material cost increases require explicit approval.
- No feasible route means stop and ask, never fabricate.
- Replanning is bounded and tool requests have timeouts.
- Demo, stale and live data are visibly distinguished.

See [ARCHITECTURE.md](ARCHITECTURE.md) for full flows and [AGENTS.md](AGENTS.md) for contributor rules.

## Hackathon submission alignment

The supplied training PDF asks for a concise proof of concept whose README explains execution, files, environment, paths, secrets and testing. This repository covers those items and mirrors the presentation methodology in code.

The complete submission still needs:

- project files/workflow, under the stated 5 GB limit;
- a presentation deck of at most 10 slides;
- a digital solution or simulation video of at most five minutes;
- testing/evaluation results in the slides.

Suggested slide flow: problem, caregiver evidence, solution, plan-act-adapt flow, architecture, smallest-change innovation, measured benefits, demo, roadmap and call to action.

## Known limitations

- The venue catalog is curated demo data and needs owner/official verification.
- BFA access must be requested from SLA; it is not a universally available endpoint.
- Demo transport fares use a deterministic policy and are not live fare quotations.
- OneMap BFA currently applies to walking routes; end-to-end accessible public transport requires deeper first/last-mile verification.
- Local/demo journey state is process memory; the AWS stack uses DynamoDB conditional writes and TTL.
- The AWS Function URL requires IAM/SigV4; the public Vercel demo remains a separate demo-only path.
- No booking, payment, international travel or medical interpretation is in scope.

## Git workflow

Every feature must use `feature/<name>`, receive incremental Conventional Commits, pass the full gate and merge non-fast-forward. Do not commit directly to `main`. Update `PROGRESS.md` when scope, blockers or metrics change.

## License

MIT. See [LICENSE](LICENSE).
