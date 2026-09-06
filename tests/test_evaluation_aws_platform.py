"""Evaluation coverage for the AWS Lambda observability boundary."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import adaptsg.aws_handler as aws_handler
from infra.aws.verify_deployment import AwsReader, verify_deployment

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _response(payload: dict[str, Any], status_code: int = 200) -> dict[str, Any]:
    return {"statusCode": status_code, "body": json.dumps(payload)}


def test_lambda_metrics_do_not_emit_sensitive_response_content(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    response = _response(
        {
            "journey_id": "sensitive-id",
            "prompt": "sensitive care request",
            "token_usage": {"input_tokens": 0, "output_tokens": 0},
            "pending_initial_itinerary": {"replan_count": 0},
        }
    )
    monkeypatch.setattr(aws_handler, "_APPLICATION_HANDLER", lambda _event, _context: response)
    monkeypatch.setenv("ADAPTSG_MODE", "demo")

    result = aws_handler.handler(
        {"rawPath": "/api/journeys"},
        SimpleNamespace(function_name="adaptsg-test"),
    )

    assert result == response
    emitted = json.loads(capsys.readouterr().out)
    assert emitted["Mode"] == "demo"
    assert emitted["_aws"]["CloudWatchMetrics"][0]["Namespace"] == "AdaptSG"
    assert emitted["ValidatedItinerary"] == 1
    assert emitted["BedrockInputTokens"] == 0
    assert "sensitive-id" not in json.dumps(emitted)
    assert "sensitive care request" not in json.dumps(emitted)


def test_lambda_metrics_measure_retention_monitoring_and_loop_caps() -> None:
    proposal = _response(
        {
            "current_itinerary": {"replan_count": 1},
            "latest_replan_proposal": {
                "itinerary": {"segments": [{}, {}, {}]},
                "changes": [{"segment_index": 2}],
            },
        }
    )
    proposal_metrics = aws_handler._application_metrics({}, proposal, 12.5)
    assert proposal_metrics["RetainedSegmentsPercent"] == pytest.approx(66.67, abs=0.01)
    assert proposal_metrics["RequestLatency"] == 12.5

    monitor_metrics = aws_handler._application_metrics(
        {"rawPath": "/api/journeys/id/monitor"}, _response({}), 1
    )
    assert monitor_metrics["ToolVerificationSuccess"] == 1

    capped_metrics = aws_handler._application_metrics(
        {}, _response({"code": "replan_limit_reached"}, 422), 1
    )
    assert capped_metrics["LoopCapHit"] == 1
    assert capped_metrics["Http4xx"] == 1


def test_sam_stack_defaults_to_token_free_private_durable_resources() -> None:
    template = (REPOSITORY_ROOT / "infra" / "aws" / "template.yaml").read_text(encoding="utf-8")
    assert "Default: DISABLED" in template
    assert "ADAPTSG_MODE: !Ref ApplicationMode" in template
    assert "ADAPTSG_PROVIDER_MODE: !Ref ApplicationMode" in template
    assert 'ADAPTSG_BEDROCK_ENABLED: !If [BedrockInferenceEnabled, "true", "false"]' in template
    assert "foundation-model/*" not in template
    assert "Resource: !Ref BedrockModelArns" in template
    assert "AuthType: AWS_IAM" in template
    assert "AuthType: NONE" not in template
    assert 'AllowOrigins: ["*"]' not in template
    assert "Type: AWS::DynamoDB::Table" in template
    assert 'TableName: !Sub "${AWS::StackName}-state-v2"' in template
    assert "BillingMode: PAY_PER_REQUEST" in template
    assert "AttributeName: expires_at" in template
    assert "EnableDeletionProtection:" in template
    assert "DeletionProtectionEnabled: !If" in template
    assert "Type: AWS::S3::Bucket" in template
    assert "BlockPublicPolicy: true" in template
    assert "LambdaReservedConcurrency:" in template
    assert "Default: 5" in template
    assert "HasLambdaReservedConcurrency" in template
    assert "ReservedConcurrentExecutions: !If" in template
    assert "Type: AWS::CloudWatch::Dashboard" in template
    assert "OperationsAlarmTopic:" in template
    assert "Type: AWS::SNS::Topic" in template
    assert "Service: cloudwatch.amazonaws.com" in template
    assert "AlarmActions: [!Ref OperationsAlarmTopic]" in template
    assert "OKActions: [!Ref OperationsAlarmTopic]" in template
    assert "Type: AWS::Cognito::UserPool" in template
    assert "EnableSelfSignUp:" in template
    assert 'Default: "false"' in template
    assert "AllowAdminCreateUserOnly: !If [SelfSignUpEnabled, false, true]" in template
    assert "EnabledMfas:" in template
    assert "SOFTWARE_TOKEN_MFA" in template
    assert "SoftwareTokenMfaConfiguration" not in template
    assert "GenerateSecret: false" in template
    assert "CallbackURLs: [!Ref CognitoCallbackUrl]" in template
    assert "LogoutURLs: [!Ref CognitoLogoutUrl]" in template
    assert "Type: AWS::Serverless::HttpApi" in template
    assert "DefaultAuthorizer: CognitoJwtAuthorizer" in template
    assert "IdentitySource: $request.header.Authorization" in template
    assert "Path: /{proxy+}" not in template
    assert "AuthorizationScopes: [adaptsg/journeys.read]" in template
    assert "AuthorizationScopes: [adaptsg/journeys.write]" in template
    assert "AuthorizationScopes: [adaptsg/consents.manage]" in template
    assert "AuthorizationScopes: [adaptsg/audit.read]" in template
    assert "AllowOrigins: [!Ref AllowedCorsOrigin]" in template
    assert "AccessLogSettings:" in template
    assert "DetailedMetricsEnabled: true" in template
    assert "ThrottlingBurstLimit: !Ref ApiThrottleBurstLimit" in template
    assert "ThrottlingRateLimit: !Ref ApiThrottleRateLimit" in template
    access_log = template.split("AccessLogSettings:", 1)[1].split("DefaultRouteSettings:", 1)[0]
    assert "prompt" not in access_log
    assert "production.invalid" not in template
    assert "SecretString:ONEMAP_API_TOKEN" in template
    assert "SecretString:LTA_ACCOUNT_KEY" in template
    assert "WebBucket:" in template
    assert "Type: AWS::CloudFront::OriginAccessControl" in template
    assert "SigningBehavior: always" in template
    assert "Type: AWS::CloudFront::Distribution" in template
    assert "PathPattern: /api/*" in template
    assert "OriginRequestPolicyId: b689b0a8-53d0-40ab-baf2-68738e2966ac" in template
    assert "Principal:\n              Service: cloudfront.amazonaws.com" in template


def test_aws_pipeline_uses_oidc_and_forces_bedrock_off() -> None:
    workflow = (REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    bootstrap = (REPOSITORY_ROOT / "infra" / "aws" / "bootstrap.yaml").read_text(encoding="utf-8")
    assert "id-token: write" in workflow
    assert "aws-actions/configure-aws-credentials@v6" in workflow
    assert '"BedrockModelArns=DISABLED"' in workflow
    assert '"LambdaReservedConcurrency=-1"' in workflow
    assert '"EnableDeletionProtection=false"' in workflow
    assert '"EnableSelfSignUp=true"' in workflow
    assert 'deploy_stack "${web_app_url}" "${web_app_url}/" "${web_app_url}/"' in workflow
    assert "ADAPTSG_ALARM_NOTIFICATION_EMAIL" in workflow
    assert "Verify public health and protected user routes" in workflow
    assert "Publish static web app and public runtime configuration" in workflow
    assert "runtime-config.json" in workflow
    assert "web_source=public" in workflow
    assert "cloudfront create-invalidation" in workflow
    assert "Verify the public AWS web URL and same-origin API" in workflow
    assert '[[ "${protected_status}" == "401" ]]' in workflow
    assert "AWS_ACCESS_KEY_ID" not in workflow
    assert "sts:AssumeRoleWithWebIdentity" in bootstrap
    assert "token.actions.githubusercontent.com:aud: sts.amazonaws.com" in bootstrap
    assert "repo:${GitHubRepository}:environment:${GitHubEnvironment}" in bootstrap
    assert "repo:${GitHubImmutableRepositoryPattern}:environment:${GitHubEnvironment}" in bootstrap
    execution_role = bootstrap.split("CloudFormationExecutionRole:", 1)[1].split(
        "GitHubDeployRole:", 1
    )[0]
    assert "cloudformation:CreateChangeSet" in execution_role
    assert "aws:transform/Serverless-2016-10-31" in execution_role
    assert "cognito-idp:CreateUserPool" in execution_role
    assert "cloudfront:CreateDistribution" in execution_role
    assert "cloudfront:CreateDistributionWithTags" in execution_role
    assert "cloudfront:CreateOriginAccessControl" in execution_role
    assert "sns:CreateTopic" in execution_role
    assert "sns:Subscribe" in execution_role
    assert "apigateway:TagResource" in execution_role
    assert "apigateway:UntagResource" in execution_role
    assert "apigateway:POST" in execution_role
    for logs_action in (
        "logs:CreateLogDelivery",
        "logs:DeleteLogDelivery",
        "logs:DescribeLogGroups",
        "logs:DescribeLogStreams",
        "logs:DescribeResourcePolicies",
        "logs:FilterLogEvents",
        "logs:GetLogDelivery",
        "logs:GetLogEvents",
        "logs:ListLogDeliveries",
        "logs:PutResourcePolicy",
        "logs:UpdateLogDelivery",
    ):
        assert logs_action in execution_role

    deploy_role = bootstrap.split("GitHubDeployRole:", 1)[1].split("Outputs:", 1)[0]
    assert "PublishStaticWebAssets" in deploy_role
    assert "cloudfront:CreateInvalidation" in deploy_role
    assert "cloudfront:GetInvalidation" in deploy_role


def test_static_web_placeholder_is_infrastructure_only_and_runtime_config_driven() -> None:
    placeholder = (REPOSITORY_ROOT / "infra" / "aws" / "web" / "index.html").read_text(
        encoding="utf-8"
    )
    assert "ready to be published by the UI team" in placeholder
    assert 'href="/api/health"' in placeholder
    assert "runtime-config.json" not in placeholder


class _FakeAwsReader(AwsReader):
    def __init__(self, responses: dict[tuple[str, ...], dict[str, Any]]) -> None:
        self.responses = responses

    def read(self, service: str, operation: str, *arguments: str) -> dict[str, Any]:
        return self.responses[(service, operation, *arguments)]


def _deployed_posture_responses() -> dict[tuple[str, ...], dict[str, Any]]:
    stack_name = "adaptsg-demo"
    region = "ap-southeast-1"
    account = "138851097788"
    web_bucket = f"{account}-{region}-{stack_name}-web"
    evidence_bucket = f"{account}-{region}-{stack_name}-evidence"
    web_domain = "dexample.cloudfront.net"
    web_url = f"https://{web_domain}"
    api_id = "api123"
    user_pool_id = f"{region}_pool"
    client_id = "public-client"
    operations_topic_arn = f"arn:aws:sns:{region}:{account}:{stack_name}-operations-alarms"
    responses: dict[tuple[str, ...], dict[str, Any]] = {
        (
            "cloudformation",
            "describe-stacks",
            "--stack-name",
            stack_name,
        ): {
            "Stacks": [
                {
                    "StackStatus": "UPDATE_COMPLETE",
                    "Parameters": [
                        {"ParameterKey": "ApplicationMode", "ParameterValue": "demo"},
                        {"ParameterKey": "BedrockModelArns", "ParameterValue": "DISABLED"},
                    ],
                    "Outputs": [
                        {"OutputKey": "BedrockStatus", "OutputValue": "DISABLED"},
                        {"OutputKey": "WebBucketName", "OutputValue": web_bucket},
                        {"OutputKey": "EvidenceBucketName", "OutputValue": evidence_bucket},
                        {"OutputKey": "WebAppUrl", "OutputValue": web_url},
                        {"OutputKey": "WebDistributionId", "OutputValue": "DIST123"},
                        {
                            "OutputKey": "AdaptSgHttpApiUrl",
                            "OutputValue": f"https://{api_id}.execute-api.{region}.amazonaws.com",
                        },
                        {"OutputKey": "JourneyTableName", "OutputValue": f"{stack_name}-state-v2"},
                        {"OutputKey": "CognitoUserPoolId", "OutputValue": user_pool_id},
                        {"OutputKey": "CognitoClientId", "OutputValue": client_id},
                        {
                            "OutputKey": "OperationsAlarmTopicArn",
                            "OutputValue": operations_topic_arn,
                        },
                    ],
                }
            ]
        },
        ("cloudfront", "get-distribution", "--id", "DIST123"): {
            "Distribution": {
                "Status": "Deployed",
                "DomainName": web_domain,
                "DistributionConfig": {
                    "Enabled": True,
                    "Origins": {
                        "Items": [
                            {
                                "Id": "WebBucketOrigin",
                                "DomainName": f"{web_bucket}.s3.{region}.amazonaws.com",
                                "OriginAccessControlId": "OAC123",
                            },
                            {
                                "Id": "ApiGatewayOrigin",
                                "DomainName": f"{api_id}.execute-api.{region}.amazonaws.com",
                            },
                        ]
                    },
                    "DefaultCacheBehavior": {"ViewerProtocolPolicy": "redirect-to-https"},
                    "CacheBehaviors": {
                        "Items": [
                            {
                                "PathPattern": "/api/*",
                                "TargetOriginId": "ApiGatewayOrigin",
                                "ViewerProtocolPolicy": "redirect-to-https",
                                "CachePolicyId": "4135ea2d-6df8-44a3-9df3-4b5a84be39ad",
                            }
                        ]
                    },
                },
            }
        },
        (
            "apigatewayv2",
            "get-stage",
            "--api-id",
            api_id,
            "--stage-name",
            "$default",
        ): {
            "AutoDeploy": True,
            "DefaultRouteSettings": {
                "DetailedMetricsEnabled": True,
                "ThrottlingBurstLimit": 20,
                "ThrottlingRateLimit": 10.0,
            },
            "AccessLogSettings": {
                "DestinationArn": "arn:aws:logs:ap-southeast-1:123:log-group:api",
                "Format": "safe-json-format",
            },
        },
        ("sns", "get-topic-attributes", "--topic-arn", operations_topic_arn): {
            "Attributes": {
                "Policy": json.dumps(
                    {
                        "Statement": [
                            {
                                "Principal": {"Service": "cloudwatch.amazonaws.com"},
                                "Resource": operations_topic_arn,
                                "Condition": {
                                    "ArnLike": {
                                        "AWS:SourceArn": (
                                            f"arn:aws:cloudwatch:{region}:{account}:alarm:"
                                            f"{stack_name}-*"
                                        )
                                    }
                                },
                            }
                        ]
                    }
                ),
            }
        },
        ("dynamodb", "describe-table", "--table-name", f"{stack_name}-state-v2"): {
            "Table": {
                "TableStatus": "ACTIVE",
                "BillingModeSummary": {"BillingMode": "PAY_PER_REQUEST"},
                "SSEDescription": {"Status": "ENABLED"},
            }
        },
        (
            "dynamodb",
            "describe-time-to-live",
            "--table-name",
            f"{stack_name}-state-v2",
        ): {
            "TimeToLiveDescription": {
                "TimeToLiveStatus": "ENABLED",
                "AttributeName": "expires_at",
            }
        },
        ("cognito-idp", "describe-user-pool", "--user-pool-id", user_pool_id): {
            "UserPool": {
                "UsernameAttributes": ["email"],
                "AdminCreateUserConfig": {"AllowAdminCreateUserOnly": False},
            }
        },
        (
            "cognito-idp",
            "describe-user-pool-client",
            "--user-pool-id",
            user_pool_id,
            "--client-id",
            client_id,
        ): {
            "UserPoolClient": {
                "AllowedOAuthFlows": ["code"],
                "AllowedOAuthScopes": [
                    "openid",
                    "email",
                    "adaptsg/journeys.read",
                    "adaptsg/journeys.write",
                    "adaptsg/consents.manage",
                    "adaptsg/audit.read",
                ],
                "CallbackURLs": [f"{web_url}/"],
                "LogoutURLs": [f"{web_url}/"],
            }
        },
    }
    for bucket in (web_bucket, evidence_bucket):
        responses[("s3api", "get-public-access-block", "--bucket", bucket)] = {
            "PublicAccessBlockConfiguration": {
                "BlockPublicAcls": True,
                "BlockPublicPolicy": True,
                "IgnorePublicAcls": True,
                "RestrictPublicBuckets": True,
            }
        }
        responses[("s3api", "get-bucket-policy-status", "--bucket", bucket)] = {
            "PolicyStatus": {"IsPublic": False}
        }
        responses[("s3api", "get-bucket-encryption", "--bucket", bucket)] = {
            "ServerSideEncryptionConfiguration": {
                "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]
            }
        }
        responses[("s3api", "get-bucket-versioning", "--bucket", bucket)] = {"Status": "Enabled"}
    return responses


def test_deployment_posture_verifier_accepts_private_token_free_stack() -> None:
    checks = verify_deployment(
        _FakeAwsReader(_deployed_posture_responses()),
        stack_name="adaptsg-demo",
        region="ap-southeast-1",
    )

    assert len(checks) == 23
    assert all(check.passed for check in checks)
    assert any(check.name == "Bedrock stack output is disabled" for check in checks)
    assert any(
        check.name == "CloudFront uses signed access to the private web bucket" for check in checks
    )


def test_deployment_posture_verifier_reports_public_bucket_and_bedrock_drift() -> None:
    responses = _deployed_posture_responses()
    stack = responses[("cloudformation", "describe-stacks", "--stack-name", "adaptsg-demo")][
        "Stacks"
    ][0]
    stack["Outputs"][0]["OutputValue"] = "CONNECTED"
    web_bucket = "138851097788-ap-southeast-1-adaptsg-demo-web"
    responses[("s3api", "get-bucket-policy-status", "--bucket", web_bucket)]["PolicyStatus"][
        "IsPublic"
    ] = True

    checks = verify_deployment(
        _FakeAwsReader(responses),
        stack_name="adaptsg-demo",
        region="ap-southeast-1",
    )
    failed_names = {check.name for check in checks if not check.passed}

    assert failed_names == {
        "Bedrock stack output is disabled",
        "web asset bucket bucket policy is private",
    }


def test_deployment_posture_verifier_reports_unscoped_notification_topic() -> None:
    responses = _deployed_posture_responses()
    topic_key = next(key for key in responses if key[:2] == ("sns", "get-topic-attributes"))
    responses[topic_key]["Attributes"]["Policy"] = "{}"

    checks = verify_deployment(
        _FakeAwsReader(responses),
        stack_name="adaptsg-demo",
        region="ap-southeast-1",
    )

    assert {check.name for check in checks if not check.passed} == {
        "operations notification topic accepts scoped CloudWatch alarms"
    }


def test_aws_pipeline_runs_read_only_deployment_posture_verification() -> None:
    workflow = (REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    bootstrap = (REPOSITORY_ROOT / "infra" / "aws" / "bootstrap.yaml").read_text(encoding="utf-8")
    deploy_role = bootstrap.split("GitHubDeployRole:", 1)[1].split("Outputs:", 1)[0]

    assert "Verify deployed AWS security and service posture" in workflow
    assert "python infra/aws/verify_deployment.py" in workflow
    for read_action in (
        "s3:GetBucketPolicyStatus",
        "s3:GetBucketPublicAccessBlock",
        "s3:GetBucketVersioning",
        "s3:GetEncryptionConfiguration",
        "cloudfront:GetDistribution",
        "apigateway:GET",
        "dynamodb:DescribeTable",
        "dynamodb:DescribeTimeToLive",
        "cognito-idp:DescribeUserPool",
        "cognito-idp:DescribeUserPoolClient",
        "sns:GetTopicAttributes",
    ):
        assert read_action in deploy_role
