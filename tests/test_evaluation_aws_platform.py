"""Evaluation coverage for the AWS Lambda observability boundary."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from adaptsg import aws_handler

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
    assert "AuthType: AWS_IAM" in template
    assert "AuthType: NONE" not in template
    assert 'AllowOrigins: ["*"]' not in template
    assert "Type: AWS::DynamoDB::Table" in template
    assert "BillingMode: PAY_PER_REQUEST" in template
    assert "AttributeName: expires_at" in template
    assert "Type: AWS::S3::Bucket" in template
    assert "BlockPublicPolicy: true" in template
    assert "ReservedConcurrentExecutions: 5" in template
    assert "Type: AWS::CloudWatch::Dashboard" in template


def test_aws_pipeline_uses_oidc_and_forces_bedrock_off() -> None:
    workflow = (REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    bootstrap = (REPOSITORY_ROOT / "infra" / "aws" / "bootstrap.yaml").read_text(encoding="utf-8")
    assert "id-token: write" in workflow
    assert "aws-actions/configure-aws-credentials@v6" in workflow
    assert '"BedrockModelArns=DISABLED"' in workflow
    assert "AWS_ACCESS_KEY_ID" not in workflow
    assert "sts:AssumeRoleWithWebIdentity" in bootstrap
    assert "token.actions.githubusercontent.com:aud: sts.amazonaws.com" in bootstrap
    assert "repo:${GitHubRepository}:environment:${GitHubEnvironment}" in bootstrap
    assert "repo:${GitHubImmutableRepositoryPattern}:environment:${GitHubEnvironment}" in bootstrap
