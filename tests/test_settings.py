import pytest

from adaptsg.settings import Settings


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
