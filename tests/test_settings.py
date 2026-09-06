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


def test_blank_optional_numeric_settings_load_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """.env.example ships these blank, so a copied .env must not fail validation."""
    for name in BLANK_OPTIONAL_NUMERIC_VARS:
        monkeypatch.setenv(name, "")

    settings = Settings(_env_file=None)

    assert settings.adaptsg_audit_retention_days is None
    assert settings.adaptsg_revoked_consent_retention_days is None
    assert settings.adaptsg_input_token_tariff_sgd is None
    assert settings.adaptsg_output_token_tariff_sgd is None


def test_populated_optional_numeric_settings_still_parse(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADAPTSG_AUDIT_RETENTION_DAYS", "90")
    monkeypatch.setenv("ADAPTSG_REVOKED_CONSENT_RETENTION_DAYS", "30")
    monkeypatch.setenv("ADAPTSG_INPUT_TOKEN_TARIFF_SGD", "0.0011")
    monkeypatch.setenv("ADAPTSG_OUTPUT_TOKEN_TARIFF_SGD", "0.0055")

    settings = Settings(_env_file=None)

    assert settings.adaptsg_audit_retention_days == 90
    assert settings.adaptsg_revoked_consent_retention_days == 30
    assert settings.adaptsg_input_token_tariff_sgd == pytest.approx(0.0011)
    assert settings.adaptsg_output_token_tariff_sgd == pytest.approx(0.0055)


def test_out_of_range_optional_numeric_settings_still_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ADAPTSG_AUDIT_RETENTION_DAYS", "0")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)
