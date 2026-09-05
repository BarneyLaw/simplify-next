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

## Roles 1 and 2: live location resolution

```text
CONTRACT CHANGE | JourneyRequest location population in src/adaptsg/preference_parser.py or orchestration in src/adaptsg/agent.py using src/adaptsg/tools/location.py | a parsed start label currently retains the default Toa Payoh coordinates and OneMapLocationClient is not in the planning flow | roles 1 and 2 | resolve the start label through the typed location tool before routing; reject ambiguous/unverified live results; retain the fixed demo lookup | non-Toa-Payoh route test, ambiguity/failure tests and live-client mock provenance test
```

## Role 3: Cognito browser client

```text
HANDOFF | role 3 | pending | public/index.html and allocated UI tests | consume CognitoDomain, CognitoClientId, CognitoCallbackUrl and AdaptSgHttpApiUrl; implement authorization-code flow with PKCE, state verification, access-token refresh/logout and Authorization bearer headers; never persist refresh tokens in localStorage | browser syntax, state mismatch, callback error, token expiry and authenticated API request tests | display demo/live provenance unchanged | coordinate callback path and API base URL with Role 4 parameters
```

The browser must request the declared `adaptsg/*` scopes and send the access token, not the ID
token, to API Gateway. No client secret, AWS access key or provider credential belongs in browser
code.
