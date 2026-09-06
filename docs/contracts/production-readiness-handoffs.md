# Production readiness handoffs

These handoffs preserve the ownership boundaries in `TEAM_WORKFLOW.md`. They are required before
AdaptSG can claim authenticated multi-user or live-provider readiness. Bedrock remains disabled.

## Role 1: independent authentication, provider and Bedrock modes

```text
CONTRACT CHANGE | Settings/src/adaptsg/settings.py, service construction in src/adaptsg/agent.py, and principal selection in src/adaptsg/web_api.py | Cognito identity, Singapore providers and Bedrock are currently coupled to ADAPTSG_MODE, so the token-free deployment assigns every authenticated request to demo-caregiver and live providers attempt the Bedrock parser | roles 1, 2, 3 and 4 | add independent authentication/provider/Bedrock switches; select the conservative parser whenever Bedrock is disabled; derive ownership from verified Cognito claims whenever authentication mode is cognito; preserve deterministic demo defaults | settings matrix tests, zero-Bedrock-token tests, Cognito access-token claim tests and two-owner isolation tests
```

Acceptance criteria:

- `ADAPTSG_AUTHENTICATION_MODE=cognito` uses the verified Cognito `sub` even when providers are
  deterministic.
- `ADAPTSG_BEDROCK_ENABLED=false` constructs no Bedrock client and cannot invoke Bedrock.
- A provider mode independently selects deterministic or live OneMap/data.gov.sg/LTA clients.
- Cognito access-token claims using `client_id` are accepted after API Gateway validation; raw
  client headers never become identity.
- Demo mode remains credential-free and deterministic.

Implemented on `feature/r1-auth-provider-separation`: service construction now treats
authentication, Singapore providers and Bedrock as independent switches. Cognito ownership uses
API Gateway's verified `sub` for both ID-token `aud` and access-token `client_id` claim shapes;
Bedrock-disabled construction selects the deterministic parser without creating a Bedrock parser
or runtime client. The branch is merged and deployed; two-browser owner-isolation verification
remains an operational acceptance step.

## Role 1: durable trust records

```text
CONTRACT CHANGE | persistence protocols and construction in src/adaptsg/agent.py | consent, authority grants, action intents and audit events are in-memory while the AWS runtime declares durable audit storage | roles 1 and 4 | store all trust records in the existing DynamoDB single-table schema with conditional writes, TTL/retention fields and owner keys; append audit and state mutation atomically or fail closed | cold-start continuity, replay, concurrent mutation, revoked-consent retention, owner isolation and failed-audit-write tests
```

Acceptance criteria:

- Consent and one-use action intents survive Lambda cold starts.
- Journey mutation and its audit event commit atomically; an audit failure leaves the current plan
  unchanged.
- Global audit enumeration is unavailable to ordinary caregivers; journey audit reads remain
  owner-scoped.
- `ADAPTSG_AUDIT_STORAGE_CONFIGURED=true` is set only when the durable store is actually selected.

Implemented on `feature/r1-durable-trust-records`: the existing encrypted DynamoDB application
table now stores consent, authority grants, one-use action intents and bounded per-journey audit
chains. Journey state, idempotency completion, the audit head and the audit event share one
conditional transaction. Durable-store construction is selected from `ADAPTSG_JOURNEYS_TABLE`,
and claiming durable audit configuration without that table fails closed. Cold-start continuity,
replay, retention, owner-scoped audit access and failure paths have local regression coverage.

## Roles 1 and 2: live location resolution

```text
CONTRACT CHANGE | JourneyRequest location population in src/adaptsg/preference_parser.py or orchestration in src/adaptsg/agent.py using src/adaptsg/tools/location.py | a parsed start label currently retains the default Toa Payoh coordinates and OneMapLocationClient is not in the planning flow | roles 1 and 2 | resolve the start label through the typed location tool before routing; reject ambiguous/unverified live results; retain the fixed demo lookup | non-Toa-Payoh route test, ambiguity/failure tests and live-client mock provenance test
```

Implemented in service orchestration: parsed origins are resolved through the selected typed
location client before any route is calculated. The deterministic demo lookup remains fixed;
empty, ambiguous and unverified results fail closed, while one exact match may be selected from a
multi-result response. Numerical routing continues to come only from the routing client.

## Role 3: Cognito browser client

```text
HANDOFF | role 3 | feature/static-browser-cognito | public/index.html, scripts/check_web.mjs, scripts/test_web_auth.mjs, tests/test_ui_browser_client.py | consumed /runtime-config.json's CognitoDomain-derived endpoints, CognitoClientId and same-origin apiBaseUrl; implemented authorization-code flow with PKCE, state verification, one proactive refresh before a request plus one reactive refresh-and-retry on 401, a terminal signed-out transition, and logout; never persists either bearer token | scripts/test_web_auth.mjs: 19 passing cases covering config fallback/fail-closed validation, sign-in/sign-up redirects, callback state/error handling, token exchange and refresh, the terminal 401 transition, logout, selfSignUpEnabled visibility and idempotency-key/expected_version discipline | demo/live provenance unchanged; browser never claims which parser (deterministic vs. Bedrock) served a request | coordinated callback path and API base URL with Role 4's runtime-config generation in ci.yml
```

The browser requests the declared `adaptsg/*` scopes and sends the access token, not the ID
token, to API Gateway. No client secret, AWS access key or provider credential is in browser
code. This handoff and the identity/provider separation are merged and deployed. The two-user
isolation check in `docs/contracts/static-web-auth-handoff.md` remains a manual browser acceptance
step rather than an implementation dependency.
