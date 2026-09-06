from datetime import date, time
from typing import Any
from unittest.mock import Mock, patch

import pytest
from botocore.exceptions import ClientError

from adaptsg.preference_parser import (
    BedrockPreferenceParser,
    DeterministicPreferenceParser,
)
from adaptsg.settings import Settings
from adaptsg.tools.catalog import VenueCatalog
from adaptsg.tools.origin import DEFAULT_ORIGIN_LABEL


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


def test_deterministic_parser_preserves_qualified_location_labels() -> None:
    parser = DeterministicPreferenceParser(VenueCatalog())

    outcome = parser.parse(
        "Plan a day starting from Toa Payoh MRT Station (NS19).",
        journey_date=date(2026, 9, 2),
    )

    assert outcome.request.start_label == "Toa Payoh MRT Station (NS19)"


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


def test_required_language_for_another_venue_does_not_promote_gardens() -> None:
    parser = DeterministicPreferenceParser(VenueCatalog())
    outcome = parser.parse(
        "We would like Gardens by the Bay and must visit National Gallery.",
        journey_date=date(2026, 9, 2),
    )
    assert outcome.request.hard.required_venue_ids == frozenset({"national-gallery"})
    assert outcome.request.soft.preferred_venue_ids == frozenset({"gardens-bay-outdoor"})


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
        settings=Settings(adaptsg_mode="live", adaptsg_bedrock_enabled=True),
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


def test_bedrock_parser_preserves_explicit_qualified_start_label() -> None:
    client = FakeBedrockClient(
        {
            "output": {
                "message": {
                    "content": [
                        {"text": '{"start_label":"Toa Payoh","max_walking_distance_m":400}'}
                    ]
                }
            },
            "usage": {"inputTokens": 100, "outputTokens": 30},
        }
    )
    parser = BedrockPreferenceParser(
        settings=Settings(adaptsg_mode="live", adaptsg_bedrock_enabled=True),
        catalog=VenueCatalog(),
        client=client,
    )

    outcome = parser.parse(
        "Plan a day starting from Toa Payoh MRT Station (NS19).",
        journey_date=date(2026, 9, 8),
    )

    assert outcome.source.startswith("bedrock:")
    assert outcome.request.start_label == "Toa Payoh MRT Station (NS19)"


def test_bedrock_failure_falls_back_safely() -> None:
    parser = BedrockPreferenceParser(
        settings=Settings(adaptsg_mode="live", adaptsg_bedrock_enabled=True),
        catalog=VenueCatalog(),
        client=FakeBedrockClient(fail=True),
    )
    outcome = parser.parse("Wheelchair, max walking 350 m", journey_date=date(2026, 9, 2))
    assert outcome.source == "deterministic_fallback_v1"
    assert "failed" in outcome.warnings[0].casefold()


def test_bedrock_failure_can_be_strict() -> None:
    parser = BedrockPreferenceParser(
        settings=Settings(adaptsg_mode="live", adaptsg_bedrock_enabled=True),
        catalog=VenueCatalog(),
        client=FakeBedrockClient(fail=True),
        allow_fallback=False,
    )
    with pytest.raises(ClientError):
        parser.parse("Plan it", journey_date=date(2026, 9, 2))


def test_live_mode_does_not_call_bedrock_without_explicit_opt_in() -> None:
    client = FakeBedrockClient(fail=True)
    parser = BedrockPreferenceParser(
        settings=Settings(adaptsg_mode="live"),
        catalog=VenueCatalog(),
        client=client,
    )
    outcome = parser.parse("Plan it", journey_date=date(2026, 9, 2))
    assert outcome.source == "deterministic_fallback_v1"
    assert client.calls == []


def test_bedrock_client_uses_its_explicit_runtime_region() -> None:
    client = object()
    session = Mock()
    session.client.return_value = client
    parser = BedrockPreferenceParser(
        settings=Settings(
            _env_file=None,
            aws_region="ap-southeast-1",
            bedrock_region="us-east-1",
            adaptsg_bedrock_enabled=True,
        ),
        catalog=VenueCatalog(),
    )

    with patch("adaptsg.preference_parser.boto3.Session", return_value=session) as factory:
        assert parser._bedrock_client() is client

    factory.assert_called_once_with(profile_name=None, region_name="us-east-1")
    session.client.assert_called_once_with("bedrock-runtime")


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


def test_explicit_start_label_stops_at_the_clause_boundary() -> None:
    """Regression: the capture used to run to the full stop and swallow the rest."""
    parser = DeterministicPreferenceParser(VenueCatalog())
    outcome = parser.parse(
        "Plan a day, start at Dhoby Ghaut MRT (NS24/NE6/CC1) and finish by 5pm.",
        journey_date=date(2026, 9, 8),
    )
    assert outcome.request.start_label == "Dhoby Ghaut MRT (NS24/NE6/CC1)"


def test_prompt_without_an_origin_uses_the_documented_default_hub() -> None:
    parser = DeterministicPreferenceParser(VenueCatalog())
    outcome = parser.parse(
        "Plan a full-day outing for two people in Singapore, including an elderly "
        "wheelchair user. Start and end at a convenient MRT station.",
        journey_date=date(2026, 9, 8),
    )
    assert outcome.request.start_label == DEFAULT_ORIGIN_LABEL


def test_a_defaulted_origin_is_disclosed_not_assumed() -> None:
    """A day starting somewhere the traveller never named has to say so."""
    parser = DeterministicPreferenceParser(VenueCatalog())
    defaulted = parser.parse(
        "Plan a full-day outing for two people, keeping walking distances short.",
        journey_date=date(2026, 9, 8),
    )
    assert defaulted.request.start_label == DEFAULT_ORIGIN_LABEL
    assert any(DEFAULT_ORIGIN_LABEL in warning for warning in defaulted.warnings)

    named = parser.parse(
        "Plan a day starting from Bishan MRT Station.", journey_date=date(2026, 9, 8)
    )
    assert not any("No starting point was named" in warning for warning in named.warnings)
