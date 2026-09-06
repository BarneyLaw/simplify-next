# AdaptSG AWS deployment runbook

The AWS deployment is deliberately token-free by default. `BedrockModelArns=DISABLED` removes
`bedrock:InvokeModel` from the Lambda role, while `ApplicationMode=demo` keeps routing and
environment inputs deterministic. Bedrock preference extraction is independently configurable.

## What is provisioned

- a Cognito user pool with configurable email self-signup and a public OAuth/PKCE client with no
  embedded client secret;
- a JWT-authenticated API Gateway HTTP API plus an IAM-only Function URL for CI operations;
- a private S3 static-web bucket behind CloudFront Origin Access Control, with `/api/*` proxied to
  API Gateway on the same HTTPS origin;
- an encrypted, on-demand DynamoDB v2 state table with TTL, point-in-time recovery support,
  optional deletion protection, and retained replacements;
- a private, encrypted, versioned S3 bucket for curated catalog and evaluation evidence;
- a least-privilege Lambda role, X-Ray tracing, 14-day logs, alarms, and a dashboard;
- a scoped SNS operations topic for CloudWatch alarms, with optional confirmed email delivery;
- a separate bootstrap stack for GitHub OIDC, the SAM artifact bucket, and deployment roles.

The CloudFormation execution role can expand only the regional
`Serverless-2016-10-31` SAM transform; this permission is required in addition to the deploy
role's permission to create the application stack change set.

There is no VPC, NAT gateway, provisioned concurrency, EC2, RDS, booking, or payment resource.
Lambda reserved concurrency defaults to five, but CI sets `LambdaReservedConcurrency=-1` because
the workshop account cannot reserve capacity while retaining AWS's mandatory unreserved pool.
DynamoDB uses on-demand billing. Temporary S3 objects and old SAM artifacts expire automatically.

## 1. Install and authenticate

Install AWS CLI v2 and AWS SAM CLI, then use an AWS SSO/profile or the hackathon's short-lived
session credentials. Do not create access keys for GitHub Actions.

```powershell
aws sts get-caller-identity --profile workshop
sam --version
```

This project is deployed in `ap-southeast-1`. The application stack, bootstrap stack, S3 buckets,
Lambda, API Gateway, DynamoDB, and Cognito pool must use that same region. CloudFront is global but
is still managed by the regional CloudFormation stack.

## 2. Bootstrap GitHub OIDC once

Check whether the account already has the GitHub OIDC provider:

```powershell
aws iam list-open-id-connect-providers --profile workshop
```

If it does not exist, omit `ExistingGitHubOidcProviderArn` and the template creates it:

```powershell
aws cloudformation deploy `
  --profile workshop `
  --region ap-southeast-1 `
  --stack-name adaptsg-cicd-bootstrap `
  --template-file infra/aws/bootstrap.yaml `
  --capabilities CAPABILITY_NAMED_IAM `
  --parameter-overrides `
    GitHubRepository=BarneyLaw/simplify-next `
    GitHubImmutableRepositoryPattern='BarneyLaw@*/simplify-next@*' `
    GitHubEnvironment=aws-demo `
    ApplicationStackName=adaptsg-demo
```

If the provider already exists, repeat that command with its ARN:

```text
ExistingGitHubOidcProviderArn=arn:aws:iam::<account-id>:oidc-provider/token.actions.githubusercontent.com
```

The trust policy is restricted to this repository and the `aws-demo` environment. GitHub
repositories created after 15 July 2026 use immutable OIDC subjects containing owner and
repository IDs. The bootstrap accepts both the legacy subject and the immutable subject pattern;
replace the two `*` values in `GitHubImmutableRepositoryPattern` with the exact numeric IDs when
available. In GitHub, create an `aws-demo` environment, allow deployments only from `main`, and
add a required reviewer if the account supports it.

Copy the bootstrap stack outputs into GitHub Actions environment variables:

| GitHub variable | Bootstrap output / value |
|---|---|
| `AWS_ACCOUNT_ID` | account ID from `sts get-caller-identity` |
| `AWS_REGION` | `ap-southeast-1` |
| `AWS_DEPLOY_ROLE_ARN` | `GitHubDeployRoleArn` |
| `AWS_CLOUDFORMATION_ROLE_ARN` | `CloudFormationExecutionRoleArn` |
| `AWS_SAM_ARTIFACT_BUCKET` | `SamArtifactBucketName` |
| `ADAPTSG_STACK_NAME` | `adaptsg-demo` |
| `ADAPTSG_APPLICATION_MODE` | `demo` until every live provider has been verified; then `live` |
| `ADAPTSG_ALLOWED_CORS_ORIGIN` | exact trusted UI origin, never `*` |
| `ADAPTSG_COGNITO_CALLBACK_URL` | exact OAuth callback URL; may include a callback path |
| `ADAPTSG_COGNITO_LOGOUT_URL` | exact browser destination after logout |
| `ADAPTSG_PROVIDER_SECRET_NAME` | leave empty until `adaptsg/demo/providers` exists |
| `ADAPTSG_ALARM_NOTIFICATION_EMAIL` | optional address for operational alerts; confirm the SNS subscription after deployment |
| `ADAPTSG_BEDROCK_MODEL_ID` | `global.anthropic.claude-haiku-4-5-20251001-v1:0` |
| `ADAPTSG_BEDROCK_MODEL_ARNS` | `DISABLED` until the controlled activation in section 7 |
| `ADAPTSG_BEDROCK_MAX_TOKENS` | `256` for the first controlled canary |

Read the outputs with:

```powershell
aws cloudformation describe-stacks `
  --profile workshop `
  --region ap-southeast-1 `
  --stack-name adaptsg-cicd-bootstrap `
  --query "Stacks[0].Outputs[*].[OutputKey,OutputValue]" `
  --output table
```

## 3. Add optional provider secrets

No provider secret is required for the first authenticated deterministic deployment. When a key
arrives, open **AWS Console -> Secrets Manager -> Store a new secret -> Other type of secret** in
`ap-southeast-1`, choose the plaintext JSON editor, and create `adaptsg/demo/providers` with this
shape:

```json
{
  "ONEMAP_API_TOKEN": "replace in Secrets Manager",
  "LTA_ACCOUNT_KEY": "replace in Secrets Manager",
  "DATA_GOV_SG_API_KEY": ""
}
```

Prefer the Secrets Manager console so values do not enter terminal history. If using the CLI,
read `--secret-string` from a local ignored file and securely remove that file afterwards. Set
`ADAPTSG_PROVIDER_SECRET_NAME` to the secret name, not its ARN or value. CloudFormation resolves
the secret during deployment; redeploy after rotating it.

Set the GitHub `aws-demo` environment variable
`ADAPTSG_PROVIDER_SECRET_NAME=adaptsg/demo/providers` only after all JSON keys referenced by the
stack exist. Keep `ADAPTSG_APPLICATION_MODE=demo` while connecting or rotating those values so they
are not called. Set it to `live` only after OneMap search/routing and LTA/data.gov.sg responses have
been verified with typed, timestamped, non-fixture provenance; `ADAPTSG_BEDROCK_MODEL_ARNS` remains
an independent switch. CI rejects live mode when the provider secret name is empty.

## 4. Deploy

Every push to `main` first runs correctness, Docker, and SAM gates. The workflow can also be run
manually on `main`, which is useful after changing only a protected environment variable. If the
OIDC variables are configured, the `Deploy AWS demo` job assumes the short-lived deployment role
and deploys the stack. CI creates CloudFront on the first pass, then updates CORS and Cognito
callback/logout URLs to the assigned CloudFront URL. It publishes `public/` when a static UI exists,
otherwise the infrastructure placeholder, plus a generated `/runtime-config.json`.

With `ADAPTSG_BEDROCK_MODEL_ARNS=DISABLED`, smoke tests cover the DynamoDB-backed journey path and
assert zero Bedrock tokens. With a connected ARN list, that inference-producing smoke is skipped so
a deployment cannot unexpectedly spend tokens; health, authentication boundary, static web,
evidence upload, and read-only AWS posture checks still run.

CloudWatch error and throttle alarms always publish to the scoped operations SNS topic. Set
`ADAPTSG_ALARM_NOTIFICATION_EMAIL` to receive those messages and confirm the subscription from the
AWS email before relying on it. Account-wide AWS Budgets require separate billing-management
authority and are not created by this application stack. Check the workshop account's spending
threshold and current spend in the Billing console instead.

For a manual token-free deployment:

```powershell
sam validate --lint --template-file infra/aws/template.yaml
sam build --template-file infra/aws/template.yaml
sam deploy `
  --profile workshop `
  --region ap-southeast-1 `
  --stack-name adaptsg-demo `
  --s3-bucket <SamArtifactBucketName> `
  --role-arn <CloudFormationExecutionRoleArn> `
  --capabilities CAPABILITY_NAMED_IAM `
  --no-confirm-changeset `
  --no-fail-on-empty-changeset `
  --parameter-overrides `
    EnvironmentName=demo `
    ApplicationMode=demo `
    AllowedCorsOrigin=https://your-ui.example `
    CognitoCallbackUrl=https://your-ui.example/auth/callback `
    CognitoLogoutUrl=https://your-ui.example/ `
    EnableSelfSignUp=true `
    BedrockModelId=global.anthropic.claude-haiku-4-5-20251001-v1:0 `
    BedrockModelArns=DISABLED `
    BedrockMaxTokens=256 `
    EnablePointInTimeRecovery=false `
    EnableDeletionProtection=false `
    LambdaReservedConcurrency=-1 `
    AlarmNotificationEmail=alerts@example.com
```

Keep deletion protection disabled for the first deployment so CloudFormation can roll back a
partially created table. After the stack and restore procedure have been verified, enable it in a
separate production update with `EnableDeletionProtection=true`. `DeletionPolicy: Retain` and
`UpdateReplacePolicy: Retain` continue to preserve an established table during stack replacement.

Do not put temporary AWS credentials or provider values in `--parameter-overrides`.

## 5. Login/signup integration contract

CI sets `EnableSelfSignUp=true`, so Cognito Managed Login exposes email signup, verification,
password reset, login, and logout. The template default remains `false` so staging/production are
invite-only unless explicitly approved. Public signup means anyone who reaches the page can create
an account; turn it off after the demo if that is not intended.

The browser login must use Cognito authorization code flow with PKCE and send the access token,
not the ID token, as a bearer token. API Gateway validates the JWT and the route-specific OAuth
scope before Lambda runs; application code then binds the verified `sub` claim to the journey owner.
The UI should load `/runtime-config.json`, generate a fresh PKCE verifier/challenge and OAuth `state`,
redirect to `authorizationEndpoint`, exchange the returned code at `tokenEndpoint`, and clear local
tokens before visiting `logoutEndpoint`. The runtime file contains only public identifiers—never a
client secret. These browser controls are implemented in `public/index.html`; deployed two-user
isolation still depends on the Role 1 identity/provider separation handoff because demo provider
mode currently selects the fixed `demo-caregiver` principal.

## 6. Verify and operate

Confirm the deployed outputs and resource state:

```powershell
aws cloudformation describe-stacks --stack-name adaptsg-demo --query "Stacks[0].Outputs" --profile workshop
aws dynamodb describe-time-to-live --table-name adaptsg-demo-state-v2 --profile workshop
aws lambda get-function-url-config --function-name adaptsg-demo-api --profile workshop
aws apigatewayv2 get-apis --profile workshop
aws cognito-idp list-user-pools --max-results 10 --profile workshop
```

Run the read-only deployment posture verifier after a deployment or before the demo:

```powershell
python infra/aws/verify_deployment.py `
  --profile workshop `
  --region ap-southeast-1 `
  --stack-name adaptsg-demo
```

By default it verifies that Bedrock is disabled. CI additionally supplies its protected ARN value,
so connected deployments must report `CONNECTED` and the deployed CloudFormation parameter must
exactly match that allowlist. The verifier also checks that both application buckets are private,
encrypted, and versioned; CloudFront uses signed S3 access and HTTPS; `/api/*` is uncached; the API
stage is logged and throttled; DynamoDB is encrypted with TTL; and Cognito remains a public
OAuth/PKCE client without a client secret. It reads configuration only and does not enumerate
users, application records, secret values, or Lambda environment variables. The same check runs
after each successful deployment from `main`.

Open the `WebAppUrl` output to view the AWS-hosted page. The private web bucket is not a website
endpoint and is deliberately inaccessible directly; CloudFront is the only public entry point.

The Function URL uses `AWS_IAM`; HTTP callers must sign requests with SigV4 and have both
`lambda:InvokeFunctionUrl` and `lambda:InvokeFunction`. The CI smoke test invokes the function
through the Lambda API instead, which is intentionally the only invocation granted to its role.

Inspect `<stack-name>-operations` in CloudWatch. The Lambda emits low-cardinality EMF metrics for
request latency/errors, validated itineraries, retained segments, tool verification, loop-cap
hits, replans, and Bedrock tokens. It never emits prompts, journey IDs, or response bodies.

The `OperationsAlarmTopicArn` output identifies the SNS topic used by the Lambda error and throttle
alarms. If email delivery is configured, its subscription remains `PendingConfirmation` until the
recipient accepts the AWS confirmation message. Alarm payloads contain operational metric state,
not prompts, journey identifiers, or response bodies. A customer-managed KMS key was deliberately
not added because CloudWatch service publishing requires additional key-policy management and a
paid KMS key.

## 7. Enable Bedrock later

Do not set the ARN variable to the word `ENABLED`; CloudFormation needs actual IAM resources. For
account `138851097788`, source region `ap-southeast-1`, and the configured Claude Haiku 4.5 global
profile, set the protected GitHub environment variables to:

```text
ADAPTSG_BEDROCK_MODEL_ID=global.anthropic.claude-haiku-4-5-20251001-v1:0
ADAPTSG_BEDROCK_MODEL_ARNS=arn:aws:bedrock:ap-southeast-1:138851097788:inference-profile/global.anthropic.claude-haiku-4-5-20251001-v1:0,arn:aws:bedrock:ap-southeast-1::foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0,arn:aws:bedrock:::foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0
ADAPTSG_BEDROCK_MAX_TOKENS=256
```

Activation sequence:

1. Keep `ADAPTSG_BEDROCK_MODEL_ARNS=DISABLED` until this rollout code is merged and deployed.
2. In the Bedrock console, complete Anthropic's one-time model-access/use-case form if the account
   has not used Anthropic models before.
3. Run `aws bedrock get-inference-profile --profile workshop --region ap-southeast-1
   --inference-profile-identifier global.anthropic.claude-haiku-4-5-20251001-v1:0` after refreshing
   SSO, and confirm it succeeds.
4. Replace `DISABLED` with the exact comma-separated list above. Never use `*`.
5. In GitHub Actions, run the **CI** workflow on `main`. The deployment grants the three exact
   resources and sets the Lambda's independent Bedrock switch without enabling live data APIs.
6. Confirm the stack output `BedrockStatus=CONNECTED`, then submit one short planning request from
   the browser. Verify non-zero Bedrock token metrics and that the returned itinerary still passes
   deterministic validation.
7. Restore `ADAPTSG_BEDROCK_MODEL_ARNS=DISABLED` and rerun the workflow when inference is no longer
   needed.

The `global.` profile can route prompts outside Singapore to supported commercial AWS Regions. Do
not use it for data with a Singapore-only residency requirement; select a suitable geographic or
in-region model profile and adjust the exact ARN set instead.

## 8. Remove resources

Delete the application stack first. The evidence and web buckets must be empty before CloudFormation
can delete them. Then empty the SAM artifact bucket and delete the bootstrap stack if CI/CD is no
longer needed.

```powershell
aws cloudformation delete-stack --stack-name adaptsg-demo --profile workshop
aws cloudformation wait stack-delete-complete --stack-name adaptsg-demo --profile workshop
aws s3 rm s3://<SamArtifactBucketName> --recursive --profile workshop
aws cloudformation delete-stack --stack-name adaptsg-cicd-bootstrap --profile workshop
```

These deletes are intentionally manual; the CI workflow never tears down data or infrastructure.
