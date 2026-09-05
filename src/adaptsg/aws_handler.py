"""AWS Lambda adapter and low-cardinality CloudWatch telemetry boundary."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Mapping
from typing import Any, cast

from mangum import Mangum
from mangum.types import LambdaContext

from adaptsg.web_api import app

_APPLICATION_HANDLER = Mangum(app, lifespan="off")


def _response_payload(response: Mapping[str, Any]) -> dict[str, Any]:
    body = response.get("body")
    if not isinstance(body, str):
        return {}
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return {}
    return cast(dict[str, Any], payload) if isinstance(payload, dict) else {}


def _application_metrics(
    event: Mapping[str, Any], response: Mapping[str, Any], duration_ms: float
) -> dict[str, float]:
    """Derive bounded metrics without logging prompts, journey IDs, or response bodies."""
    status_code = response.get("statusCode", 500)
    status = status_code if isinstance(status_code, int) else 500
    metrics = {
        "RequestCount": 1.0,
        "RequestLatency": duration_ms,
        "Http4xx": 1.0 if 400 <= status < 500 else 0.0,
        "Http5xx": 1.0 if status >= 500 else 0.0,
    }
    payload = _response_payload(response)
    if payload.get("code") == "replan_limit_reached":
        metrics["LoopCapHit"] = 1.0

    token_usage = payload.get("token_usage")
    if isinstance(token_usage, dict):
        input_tokens = token_usage.get("input_tokens")
        output_tokens = token_usage.get("output_tokens")
        if isinstance(input_tokens, int):
            metrics["BedrockInputTokens"] = float(input_tokens)
        if isinstance(output_tokens, int):
            metrics["BedrockOutputTokens"] = float(output_tokens)

    itinerary = payload.get("current_itinerary") or payload.get("pending_initial_itinerary")
    if isinstance(itinerary, dict) and 200 <= status < 300:
        metrics["ValidatedItinerary"] = 1.0
        replan_count = itinerary.get("replan_count")
        if isinstance(replan_count, int):
            metrics["ReplanCount"] = float(replan_count)

    proposal = payload.get("latest_replan_proposal")
    if isinstance(proposal, dict):
        proposed_itinerary = proposal.get("itinerary")
        changes = proposal.get("changes")
        if isinstance(proposed_itinerary, dict) and isinstance(changes, list):
            segments = proposed_itinerary.get("segments")
            if isinstance(segments, list) and segments:
                changed_indexes = {
                    item.get("segment_index")
                    for item in changes
                    if isinstance(item, dict) and isinstance(item.get("segment_index"), int)
                }
                retained = max(len(segments) - len(changed_indexes), 0)
                metrics["RetainedSegmentsPercent"] = 100.0 * retained / len(segments)

    path = event.get("rawPath")
    if isinstance(path, str) and path.endswith("/monitor"):
        metrics["ToolVerificationSuccess"] = 1.0 if 200 <= status < 300 else 0.0
    return metrics


def _emit_metrics(function_name: str, mode: str, metrics: Mapping[str, float]) -> None:
    units = {
        "RequestLatency": "Milliseconds",
        "RetainedSegmentsPercent": "Percent",
    }
    definitions = [{"Name": name, "Unit": units.get(name, "Count")} for name in sorted(metrics)]
    payload: dict[str, Any] = {
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [
                {
                    "Namespace": "AdaptSG",
                    "Dimensions": [["FunctionName", "Mode"]],
                    "Metrics": definitions,
                }
            ],
        },
        "FunctionName": function_name,
        "Mode": mode,
        **metrics,
    }
    print(json.dumps(payload, separators=(",", ":"), sort_keys=True))


def handler(event: dict[str, Any], context: LambdaContext) -> dict[str, Any]:
    """Invoke FastAPI and emit privacy-safe Embedded Metric Format telemetry."""
    started = time.monotonic()
    response = _APPLICATION_HANDLER(event, context)
    duration_ms = round((time.monotonic() - started) * 1000, 2)
    mode = os.getenv("ADAPTSG_MODE", "demo")
    if mode not in {"demo", "live"}:
        mode = "unknown"
    _emit_metrics(
        context.function_name,
        mode,
        _application_metrics(event, response, duration_ms),
    )
    return response
