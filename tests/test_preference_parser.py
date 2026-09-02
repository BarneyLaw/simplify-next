from datetime import date, time
from typing import Any

import pytest
from botocore.exceptions import ClientError

from adaptsg.preference_parser import (
    BedrockPreferenceParser,
    DeterministicPreferenceParser,
)
from adaptsg.settings import Settings
from adaptsg.tools.catalog import VenueCatalog


class FakeBedrockClient:
    def __init__(self, response: dict[str, Any] | None = None, *, fail: bool = False) -> None:
        self.response = response or {}
        self.fail = fail
        self.calls: list[dict[str, Any]] = []

    def converse(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self.fail:
            raise ClientError({"Error": {"Code": "Denied", "Message": "no"}}, "Converse")
        return self.response


def test_deterministic_parser_extracts_hard_and_soft_constraints() -> None:
    parser = DeterministicPreferenceParser(VenueCatalog())
    outcome = parser.parse(
        "Plan 9:30 am to 4 pm starting from Bishan. Must visit National Gallery. "
        "We would like Gardens by the Bay, wheelchair access, walk no more than 250 metres, "
        "lunch by 12:30 pm, rest every 60 minutes, budget under $55, avoid crowds.",
        journey_date=date(2026, 9, 2),
    )
    request = outcome.request
    assert request.start_label == "Bishan"
    assert request.start_time == time(9, 30)
    assert request.hard.finish_by == time(16)
    assert request.hard.max_walking_distance_m == 250
    assert request.hard.lunch_latest == time(12, 30)
    assert request.hard.rest_interval_minutes == 60
    assert request.hard.total_budget_sgd == 55
    assert request.hard.required_venue_ids == frozenset({"national-gallery"})
    assert request.soft.preferred_venue_ids == frozenset({"gardens-bay-outdoor"})
    assert request.soft.avoid_crowds


@pytest.mark.parametrize(
    "wording",
    (
        "We must visit Gardens by the Bay.",
        "Gardens by the Bay is required.",
        "We cannot miss Gardens by the Bay.",
    ),
)
def test_gardens_is_required_only_with_explicit_language(wording: str) -> None:
    parser = DeterministicPreferenceParser(VenueCatalog())
    outcome = parser.parse(wording, journey_date=date(2026, 9, 2))
    assert outcome.request.hard.required_venue_ids == frozenset({"gardens-bay-outdoor"})
    assert outcome.request.soft.preferred_venue_ids == frozenset()


def test_deterministic_parser_uses_conservative_defaults() -> None:
    parser = DeterministicPreferenceParser(VenueCatalog())
    outcome = parser.parse("A quiet local day", journey_date=date(2026, 9, 2))
    assert outcome.request.hard.wheelchair_accessible_required
    assert outcome.request.start_time == time(10)
    assert outcome.request.hard.finish_by == time(17)
    assert outcome.warnings


def test_explicit_no_wheelchair_requirement_is_respected() -> None:
    parser = DeterministicPreferenceParser(VenueCatalog())
    outcome = parser.parse(
        "Wheelchair not required, plan 10 am-5 pm.", journey_date=date(2026, 9, 2)
    )
    assert not outcome.request.hard.wheelchair_accessible_required


def test_bedrock_parser_accepts_fenced_json_and_usage() -> None:
    client = FakeBedrockClient(
        {
            "output": {
                "message": {
                    "content": [
                        {
                            "text": '```json\n{"start_label":"Bishan",'
                            '"max_walking_distance_m":300}\n```'
                        }
                    ]
                }
            },
            "usage": {"inputTokens": 120, "outputTokens": 40},
        }
    )
    parser = BedrockPreferenceParser(
        settings=Settings(adaptsg_mode="live"),
        catalog=VenueCatalog(),
        client=client,
    )
    outcome = parser.parse("Plan it", journey_date=date(2026, 9, 2))
    assert outcome.source.startswith("bedrock:")
    assert outcome.request.start_label == "Bishan"
    assert outcome.request.hard.max_walking_distance_m == 300
    assert outcome.token_usage.input_tokens == 120
    assert client.calls[0]["inferenceConfig"]["temperature"] == 0
    system_prompt = client.calls[0]["system"][0]["text"]
    assert "would like to visit" in system_prompt
    assert "only when the user explicitly says must" in system_prompt


def test_bedrock_failure_falls_back_safely() -> None:
    parser = BedrockPreferenceParser(
        settings=Settings(adaptsg_mode="live"),
        catalog=VenueCatalog(),
        client=FakeBedrockClient(fail=True),
    )
    outcome = parser.parse("Wheelchair, max walking 350 m", journey_date=date(2026, 9, 2))
    assert outcome.source == "deterministic_fallback_v1"
    assert "failed" in outcome.warnings[0].casefold()


def test_bedrock_failure_can_be_strict() -> None:
    parser = BedrockPreferenceParser(
        settings=Settings(adaptsg_mode="live"),
        catalog=VenueCatalog(),
        client=FakeBedrockClient(fail=True),
        allow_fallback=False,
    )
    with pytest.raises(ClientError):
        parser.parse("Plan it", journey_date=date(2026, 9, 2))


def test_clean_json_rejects_non_json() -> None:
    with pytest.raises(ValueError, match="did not contain"):
        BedrockPreferenceParser._clean_json("not structured")


def test_demo_mode_never_calls_bedrock() -> None:
    client = FakeBedrockClient(fail=True)
    parser = BedrockPreferenceParser(
        settings=Settings(adaptsg_mode="demo"),
        catalog=VenueCatalog(),
        client=client,
    )
    parser.parse("Plan it", journey_date=date(2026, 9, 2))
    assert client.calls == []
