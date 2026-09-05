# AdaptSG AWS deployment runbook

The AWS deployment is deliberately token-free by default. `BedrockModelArns=DISABLED` removes
`bedrock:InvokeModel` from the Lambda role, and `ApplicationMode=demo` keeps routing and
environment inputs deterministic until the provider credentials and independent Bedrock switch
are ready.

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
| `ADAPTSG_ALLOWED_CORS_ORIGIN` | exact trusted UI origin, never `*` |
| `ADAPTSG_COGNITO_CALLBACK_URL` | exact OAuth callback URL; may include a callback path |
| `ADAPTSG_COGNITO_LOGOUT_URL` | exact browser destination after logout |
| `ADAPTSG_PROVIDER_SECRET_NAME` | leave empty until `adaptsg/demo/providers` exists |

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
stack exist. The automated demo remains in `ADAPTSG_MODE=demo`, so these values are connected but
not called. Turning on live OneMap/LTA/data.gov.sg independently of Bedrock requires the Role 1
contract change recorded at the end of this runbook.

## 4. Deploy

Every push to `main` first runs correctness, Docker, and SAM gates. If the OIDC variables are
configured, the `Deploy AWS demo` job then assumes the short-lived deployment role, deploys the
stack with Bedrock disabled, and invokes a deterministic planning request. CI creates CloudFront on
the first pass, then updates CORS and Cognito callback/logout URLs to the assigned CloudFront URL.
It publishes `public/` when a static UI exists, otherwise the infrastructure placeholder, plus a
generated `/runtime-config.json`. Smoke tests cover the DynamoDB-backed journey path, zero Bedrock
tokens, public AWS URL, same-origin health route, and private evidence upload.

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
    BedrockModelArns=DISABLED `
    EnablePointInTimeRecovery=false `
    EnableDeletionProtection=false `
    LambdaReservedConcurrency=-1
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
client secret. Implementing those browser controls remains the Role 3 handoff.

## 6. Verify and operate

Confirm the deployed outputs and resource state:

```powershell
aws cloudformation describe-stacks --stack-name adaptsg-demo --query "Stacks[0].Outputs" --profile workshop
aws dynamodb describe-time-to-live --table-name adaptsg-demo-state-v2 --profile workshop
aws lambda get-function-url-config --function-name adaptsg-demo-api --profile workshop
aws apigatewayv2 get-apis --profile workshop
aws cognito-idp list-user-pools --max-results 10 --profile workshop
```

Open the `WebAppUrl` output to view the AWS-hosted page. The private web bucket is not a website
endpoint and is deliberately inaccessible directly; CloudFront is the only public entry point.

The Function URL uses `AWS_IAM`; HTTP callers must sign requests with SigV4 and have both
`lambda:InvokeFunctionUrl` and `lambda:InvokeFunction`. The CI smoke test invokes the function
through the Lambda API instead, which is intentionally the only invocation granted to its role.

Inspect `<stack-name>-operations` in CloudWatch. The Lambda emits low-cardinality EMF metrics for
request latency/errors, validated itineraries, retained segments, tool verification, loop-cap
hits, replans, and Bedrock tokens. It never emits prompts, journey IDs, or response bodies.

## 7. Enable Bedrock later

Do not change this during the token-constrained phase. When the team approves inference usage:

1. confirm model access in the chosen region;
2. identify every exact inference-profile and foundation-model ARN required by that model;
3. deploy with those comma-separated ARNs in `BedrockModelArns` and the matching
   `BedrockModelId`;
4. run a capped test and verify the input/output-token metrics;
5. restore `BedrockModelArns=DISABLED` after the live window if inference is no longer needed.

Never replace the ARN list with `*`.

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

## Required Role 1 contract handoff

```text
CONTRACT CHANGE | Settings/src/adaptsg/settings.py and parser construction in src/adaptsg/agent.py | add an independently configurable ADAPTSG_BEDROCK_ENABLED=false switch so live routing/environment providers can run without even attempting Bedrock | roles 1 and 4 | default false in AWS, retain existing demo behavior, fail closed or use the existing conservative parser when disabled | parser/service selection tests plus live-provider mocks
```
