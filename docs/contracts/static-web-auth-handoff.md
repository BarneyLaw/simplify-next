# Static web and Cognito handoff

Status: browser implemented; deployed identity acceptance blocked.

Role 4's infrastructure contract and Role 3's browser implementation (`public/index.html`,
`scripts/check_web.mjs`, `scripts/test_web_auth.mjs`) are both done and pass the full local gate.
The deployed demo still maps every authenticated request to the fixed `demo-caregiver` principal
(`ApplicationMode=demo`), so the "one user cannot read another user's journey ID" acceptance check
below cannot yet be demonstrated against two distinct Cognito identities on AWS. This is a
blocking Role 1/4 handoff tracked in `docs/contracts/production-readiness-handoffs.md` and
`PROGRESS.md`; flip this status to "implemented" only once that acceptance check passes against
the deployed stack.

## Deployment contract

Role 3 owns the static application under `public/`. Its build output must include `public/index.html`.
The main-branch AWS job uploads the complete directory to the private web bucket and invalidates
CloudFront. When `public/index.html` is absent, CI publishes `infra/aws/web/index.html` so the AWS URL
still proves that hosting and the API proxy work.

The browser must fetch `/runtime-config.json` at startup. CI generates this file after CloudFormation
assigns the CloudFront hostname. It is public configuration and contains:

- `apiBaseUrl`: `/api`, using the same CloudFront origin;
- Cognito client ID and Managed Login endpoints;
- exact redirect and logout URLs;
- required OAuth scopes;
- `responseType=code`, `pkceRequired=true`, and the self-signup status.

Do not add environment-specific IDs or endpoints to the browser source. Never place a password,
access token, provider key, AWS credential, or OAuth client secret in `public/` or runtime config.

### Local no-auth fallback

Outside AWS, `/runtime-config.json` does not exist: `uvicorn adaptsg.web_api:app` and the Docker
image only mount `public/` and the API, never that file. The client treats this specific, expected
`404` as `{apiBaseUrl: '/api', auth: null}` and skips login entirely, booting straight into the
deterministic demo. Every other failure mode — a non-404 HTTP status, a network error, invalid
JSON, or a config object that fails schema validation (missing `schemaVersion`, `apiBaseUrl`,
`responseType`, `pkceRequired`, an endpoint, the client ID, a redirect/logout URI, or a non-empty
`scopes` array) — fails closed: the client shows a configuration error and does not boot any view,
rather than silently disabling login the way the 404 case does.

## Login and signup flow

1. Generate a cryptographically random PKCE verifier and OAuth `state`. Keep them only for the
   current browser session.
2. Derive the SHA-256 `code_challenge` and redirect to `signInEndpoint` or `signUpEndpoint` with
   `client_id`, `response_type=code`, exact `redirect_uri`, space-delimited `scope`, `state`,
   `code_challenge`, and `code_challenge_method=S256`.
3. On return, reject a missing/mismatched `state` or OAuth error. Exchange the one-time code at
   `tokenEndpoint` using form encoding and the original `code_verifier`.
4. Send the Cognito **access token**, not the ID token, as `Authorization: Bearer <token>` to API
   routes. API Gateway validates client/audience and per-route scopes before Lambda executes.
5. Treat the Cognito `sub` as the stable user ID. Do not accept a user ID from an editable browser
   field or request header.
6. On logout, clear browser-held tokens and PKCE state, then visit `logoutEndpoint` with `client_id`
   and the exact `logout_uri` from runtime config.

Avoid persistent local storage for bearer tokens. A page refresh may require a new login for the
hackathon demo; that is safer than exposing a long-lived credential to unrelated browser scripts.

## Acceptance checks

- signup sends a verification message and an unverified account cannot sign in;
- login returns an authorization code and PKCE exchange succeeds without a client secret;
- `/api/health` works without a token;
- protected routes return 401 without a token and work with the correctly scoped access token;
- one user cannot read another user's journey ID;
- logout clears the session and a subsequent protected call fails;
- the UI distinguishes deterministic demo provenance and never claims that Bedrock was invoked.
