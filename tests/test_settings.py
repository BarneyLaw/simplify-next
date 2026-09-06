import pytest
from pydantic import ValidationError

from adaptsg.settings import Settings

BLANK_OPTIONAL_NUMERIC_VARS = (
    "ADAPTSG_AUDIT_RETENTION_DAYS",
    "ADAPTSG_REVOKED_CONSENT_RETENTION_DAYS",
    "ADAPTSG_INPUT_TOKEN_TARIFF_SGD",
    "ADAPTSG_OUTPUT_TOKEN_TARIFF_SGD",
)


def test_clean_settings_default_to_live_provider_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ADAPTSG_MODE", raising=False)
    assert Settings(_env_file=None).adaptsg_mode == "live"


def test_bedrock_switch_accepts_deployed_and_legacy_environment_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ADAPTSG_BEDROCK_ENABLED", "true")
    assert Settings(_env_file=None).adaptsg_bedrock_enabled
    monkeypatch.delenv("ADAPTSG_BEDROCK_ENABLED")
    monkeypatch.setenv("ADAPTSG_USE_BEDROCK", "true")
    assert Settings(_env_file=None).adaptsg_bedrock_enabled
