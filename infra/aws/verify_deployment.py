"""Verify the deployed AdaptSG AWS demo boundary.

This script intentionally reads configuration only. It never reads application data,
provider secrets, Cognito users, or Lambda environment variables.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlparse


class DeploymentVerificationError(RuntimeError):
    """Raised when AWS configuration cannot be read or is structurally incomplete."""


class AwsReader(Protocol):
    """Minimal read-only AWS command boundary used by the verifier."""

    def read(self, service: str, operation: str, *arguments: str) -> dict[str, Any]: ...


@dataclass(frozen=True)
class VerificationCheck:
    """One independently reportable deployment assertion."""

    name: str
    passed: bool
    detail: str


class AwsCliReader:
    """Read AWS JSON responses through the installed AWS CLI."""

    def __init__(self, *, region: str, profile: str | None = None) -> None:
        self._region = region
        self._profile = profile

    def read(self, service: str, operation: str, *arguments: str) -> dict[str, Any]:
        command = [
            "aws",
            service,
            operation,
            *arguments,
            "--region",
            self._region,
            "--output",
            "json",
        ]
        if self._profile:
            command.extend(("--profile", self._profile))
        completed = subprocess.run(command, capture_output=True, check=False, text=True)
        if completed.returncode != 0:
            message = completed.stderr.strip() or completed.stdout.strip()
            raise DeploymentVerificationError(
                f"AWS read failed for {service} {operation}: {message}"
            )
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise DeploymentVerificationError(
                f"AWS returned invalid JSON for {service} {operation}"
            ) from exc
        if not isinstance(payload, dict):
            raise DeploymentVerificationError(
                f"AWS returned a non-object for {service} {operation}"
            )
        return payload


def _object(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DeploymentVerificationError(f"{context} is missing or is not an object")
    return value


def _items(value: Any, context: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise DeploymentVerificationError(f"{context} is missing or is not an object list")
    return value


def _named_values(items: list[dict[str, Any]], key: str, value: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for item in items:
        item_key = item.get(key)
        item_value = item.get(value)
        if isinstance(item_key, str):
            result[item_key] = item_value
    return result


def _record(checks: list[VerificationCheck], name: str, condition: bool, detail: str) -> None:
    checks.append(VerificationCheck(name=name, passed=condition, detail=detail))


def _require_string(values: dict[str, Any], key: str, context: str) -> str:
    value = values.get(key)
    if not isinstance(value, str) or not value:
        raise DeploymentVerificationError(f"{context} does not contain {key}")
    return value


def _verify_bucket(
    reader: AwsReader, checks: list[VerificationCheck], bucket: str, label: str
) -> None:
    public_access = _object(
        reader.read("s3api", "get-public-access-block", "--bucket", bucket).get(
            "PublicAccessBlockConfiguration"
        ),
        f"{label} public access block",
    )
    public_controls = (
        "BlockPublicAcls",
        "BlockPublicPolicy",
        "IgnorePublicAcls",
        "RestrictPublicBuckets",
    )
    _record(
        checks,
        f"{label} blocks every public access path",
        all(public_access.get(control) is True for control in public_controls),
        bucket,
    )
    policy_status = _object(
        reader.read("s3api", "get-bucket-policy-status", "--bucket", bucket).get("PolicyStatus"),
        f"{label} policy status",
    )
    _record(
        checks,
        f"{label} bucket policy is private",
        policy_status.get("IsPublic") is False,
        bucket,
    )
    encryption = _object(
        reader.read("s3api", "get-bucket-encryption", "--bucket", bucket).get(
            "ServerSideEncryptionConfiguration"
        ),
        f"{label} encryption",
    )
    rules = _items(encryption.get("Rules"), f"{label} encryption rules")
    algorithms = {
        _object(rule.get("ApplyServerSideEncryptionByDefault"), "encryption rule").get(
            "SSEAlgorithm"
        )
        for rule in rules
    }
    _record(
        checks,
        f"{label} has encryption at rest",
        bool(algorithms.intersection({"AES256", "aws:kms"})),
        ", ".join(sorted(str(item) for item in algorithms)),
    )
    versioning = reader.read("s3api", "get-bucket-versioning", "--bucket", bucket)
    _record(
        checks,
        f"{label} retains object versions",
        versioning.get("Status") == "Enabled",
        str(versioning.get("Status")),
    )


def verify_deployment(
    reader: AwsReader,
    *,
    stack_name: str,
    region: str,
    expected_bedrock_model_arns: str = "DISABLED",
) -> tuple[VerificationCheck, ...]:
    """Return all security and service-wiring checks for a deployed demo stack."""

    if not expected_bedrock_model_arns:
        raise DeploymentVerificationError("expected Bedrock model ARNs cannot be empty")
    if expected_bedrock_model_arns != "DISABLED":
        model_arns = expected_bedrock_model_arns.split(",")
        if any(
            not arn.startswith("arn:aws:bedrock:") or "*" in arn or arn != arn.strip()
            for arn in model_arns
        ):
            raise DeploymentVerificationError(
                "expected Bedrock resources must be exact comma-separated ARNs"
            )
    expected_bedrock_status = (
        "DISABLED" if expected_bedrock_model_arns == "DISABLED" else "CONNECTED"
    )

    checks: list[VerificationCheck] = []
    stack_response = reader.read("cloudformation", "describe-stacks", "--stack-name", stack_name)
    stacks = _items(stack_response.get("Stacks"), "CloudFormation stacks")
    if len(stacks) != 1:
        raise DeploymentVerificationError(
            f"expected exactly one CloudFormation stack, received {len(stacks)}"
        )
    stack = stacks[0]
    outputs = _named_values(
        _items(stack.get("Outputs"), "stack outputs"), "OutputKey", "OutputValue"
    )
    parameters = _named_values(
        _items(stack.get("Parameters"), "stack parameters"),
        "ParameterKey",
        "ParameterValue",
    )
    _record(
        checks,
        "CloudFormation stack is stable",
        stack.get("StackStatus") in {"CREATE_COMPLETE", "UPDATE_COMPLETE"},
        str(stack.get("StackStatus")),
    )
    _record(
        checks,
        "application providers remain deterministic",
        parameters.get("ApplicationMode") == "demo",
        str(parameters.get("ApplicationMode")),
    )
    _record(
        checks,
        "Bedrock deployment parameter matches the protected environment",
        parameters.get("BedrockModelArns") == expected_bedrock_model_arns,
        str(parameters.get("BedrockModelArns")),
    )
    _record(
        checks,
        "Bedrock stack output matches the expected connection state",
        outputs.get("BedrockStatus") == expected_bedrock_status,
        str(outputs.get("BedrockStatus")),
    )

    operations_topic_arn = _require_string(outputs, "OperationsAlarmTopicArn", "stack outputs")
    topic_attributes = _object(
        reader.read("sns", "get-topic-attributes", "--topic-arn", operations_topic_arn).get(
            "Attributes"
        ),
        "SNS operations topic attributes",
    )
    topic_policy = str(topic_attributes.get("Policy", ""))
    _record(
        checks,
        "operations notification topic accepts scoped CloudWatch alarms",
        "cloudwatch.amazonaws.com" in topic_policy
        and operations_topic_arn in topic_policy
        and stack_name in topic_policy,
        operations_topic_arn,
    )

    web_bucket = _require_string(outputs, "WebBucketName", "stack outputs")
    evidence_bucket = _require_string(outputs, "EvidenceBucketName", "stack outputs")
    _verify_bucket(reader, checks, web_bucket, "web asset bucket")
    _verify_bucket(reader, checks, evidence_bucket, "evidence bucket")

    web_url = _require_string(outputs, "WebAppUrl", "stack outputs")
    distribution_id = _require_string(outputs, "WebDistributionId", "stack outputs")
    distribution_response = reader.read("cloudfront", "get-distribution", "--id", distribution_id)
    distribution = _object(distribution_response.get("Distribution"), "CloudFront distribution")
    distribution_config = _object(
        distribution.get("DistributionConfig"), "CloudFront distribution configuration"
    )
    _record(
        checks,
        "CloudFront distribution is deployed",
        distribution.get("Status") == "Deployed" and distribution_config.get("Enabled") is True,
        str(distribution.get("Status")),
    )
    _record(
        checks,
        "CloudFront URL matches the deployed distribution",
        urlparse(web_url).hostname == distribution.get("DomainName"),
        web_url,
    )
    origins_container = _object(distribution_config.get("Origins"), "CloudFront origins")
    origins = {
        str(item.get("Id")): item
        for item in _items(origins_container.get("Items"), "CloudFront origin items")
    }
    web_origin = _object(origins.get("WebBucketOrigin"), "CloudFront web bucket origin")
    _record(
        checks,
        "CloudFront uses signed access to the private web bucket",
        bool(web_origin.get("OriginAccessControlId"))
        and str(web_origin.get("DomainName", "")).startswith(f"{web_bucket}.s3.")
        and "s3-website" not in str(web_origin.get("DomainName", "")),
        str(web_origin.get("DomainName")),
    )
    default_behavior = _object(
        distribution_config.get("DefaultCacheBehavior"), "CloudFront default behavior"
    )
    _record(
        checks,
        "web traffic is redirected to HTTPS",
        default_behavior.get("ViewerProtocolPolicy") == "redirect-to-https",
        str(default_behavior.get("ViewerProtocolPolicy")),
    )
    cache_behaviors = _object(
        distribution_config.get("CacheBehaviors"), "CloudFront cache behaviors"
    )
    api_behaviors = [
        item
        for item in _items(cache_behaviors.get("Items"), "CloudFront cache behavior items")
        if item.get("PathPattern") == "/api/*"
    ]
    api_behavior = api_behaviors[0] if len(api_behaviors) == 1 else {}
    _record(
        checks,
        "same-origin API behavior is HTTPS-only and uncached",
        len(api_behaviors) == 1
        and api_behavior.get("TargetOriginId") == "ApiGatewayOrigin"
        and api_behavior.get("ViewerProtocolPolicy") == "redirect-to-https"
        and api_behavior.get("CachePolicyId") == "4135ea2d-6df8-44a3-9df3-4b5a84be39ad",
        "/api/*",
    )

    api_url = _require_string(outputs, "AdaptSgHttpApiUrl", "stack outputs")
    api_hostname = urlparse(api_url).hostname
    api_id = api_hostname.split(".")[0] if api_hostname else ""
    if not api_id:
        raise DeploymentVerificationError("could not derive API ID from AdaptSgHttpApiUrl")
    stage = reader.read("apigatewayv2", "get-stage", "--api-id", api_id, "--stage-name", "$default")
    route_settings = _object(stage.get("DefaultRouteSettings"), "API default route settings")
    access_logs = _object(stage.get("AccessLogSettings"), "API access log settings")
    _record(
        checks,
        "API stage is auto-deployed, metered, throttled, and logged",
        stage.get("AutoDeploy") is True
        and route_settings.get("DetailedMetricsEnabled") is True
        and float(route_settings.get("ThrottlingBurstLimit", 0)) > 0
        and float(route_settings.get("ThrottlingRateLimit", 0)) > 0
        and bool(access_logs.get("DestinationArn"))
        and bool(access_logs.get("Format")),
        "$default",
    )

    table_name = _require_string(outputs, "JourneyTableName", "stack outputs")
    table = _object(
        reader.read("dynamodb", "describe-table", "--table-name", table_name).get("Table"),
        "DynamoDB table",
    )
    billing = _object(table.get("BillingModeSummary"), "DynamoDB billing mode")
    sse = _object(table.get("SSEDescription"), "DynamoDB encryption")
    _record(
        checks,
        "DynamoDB state is active, on-demand, and encrypted",
        table.get("TableStatus") == "ACTIVE"
        and billing.get("BillingMode") == "PAY_PER_REQUEST"
        and sse.get("Status") == "ENABLED",
        table_name,
    )
    ttl = _object(
        reader.read("dynamodb", "describe-time-to-live", "--table-name", table_name).get(
            "TimeToLiveDescription"
        ),
        "DynamoDB TTL",
    )
    _record(
        checks,
        "DynamoDB retention TTL is enabled",
        ttl.get("TimeToLiveStatus") == "ENABLED" and ttl.get("AttributeName") == "expires_at",
        str(ttl.get("TimeToLiveStatus")),
    )

    user_pool_id = _require_string(outputs, "CognitoUserPoolId", "stack outputs")
    user_pool = _object(
        reader.read("cognito-idp", "describe-user-pool", "--user-pool-id", user_pool_id).get(
            "UserPool"
        ),
        "Cognito user pool",
    )
    admin_create = _object(
        user_pool.get("AdminCreateUserConfig"), "Cognito admin create-user configuration"
    )
    _record(
        checks,
        "Cognito email self-signup is enabled for the demo",
        admin_create.get("AllowAdminCreateUserOnly") is False
        and "email" in user_pool.get("UsernameAttributes", []),
        user_pool_id,
    )
    client_id = _require_string(outputs, "CognitoClientId", "stack outputs")
    client = _object(
        reader.read(
            "cognito-idp",
            "describe-user-pool-client",
            "--user-pool-id",
            user_pool_id,
            "--client-id",
            client_id,
        ).get("UserPoolClient"),
        "Cognito user pool client",
    )
    expected_redirect = f"{web_url}/"
    required_scopes = {
        "openid",
        "email",
        "adaptsg/journeys.read",
        "adaptsg/journeys.write",
        "adaptsg/consents.manage",
        "adaptsg/audit.read",
    }
    _record(
        checks,
        "Cognito browser client is public authorization-code/PKCE configuration",
        "ClientSecret" not in client
        and "code" in client.get("AllowedOAuthFlows", [])
        and expected_redirect in client.get("CallbackURLs", [])
        and expected_redirect in client.get("LogoutURLs", [])
        and required_scopes.issubset(set(client.get("AllowedOAuthScopes", []))),
        client_id,
    )
    return tuple(checks)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify the deployed deterministic-provider AdaptSG AWS demo."
    )
    parser.add_argument("--stack-name", default="adaptsg-demo")
    parser.add_argument("--region", default="ap-southeast-1")
    parser.add_argument("--profile")
    parser.add_argument("--expected-bedrock-model-arns", default="DISABLED")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    reader = AwsCliReader(region=args.region, profile=args.profile)
    try:
        checks = verify_deployment(
            reader,
            stack_name=args.stack_name,
            region=args.region,
            expected_bedrock_model_arns=args.expected_bedrock_model_arns,
        )
    except DeploymentVerificationError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2
    for check in checks:
        status = "PASS" if check.passed else "FAIL"
        print(f"[{status}] {check.name}: {check.detail}")
    failures = [check for check in checks if not check.passed]
    if failures:
        print(f"Deployment posture failed {len(failures)} check(s).", file=sys.stderr)
        return 1
    print(f"Deployment posture passed all {len(checks)} checks.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
