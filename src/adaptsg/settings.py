"""Environment-backed application configuration for live provider operation."""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="",
        extra="ignore",
    )

    adaptsg_mode: Literal["demo", "live"] = "live"
    adaptsg_log_level: str = "INFO"
    aws_region: str = "us-east-1"
    aws_profile: str | None = None
    bedrock_model_id: str = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
    bedrock_max_tokens: int = Field(default=1_200, ge=128, le=4_096)
    adaptsg_use_bedrock: bool = False
    adaptsg_approval_cost_increase_sgd: float = Field(default=8, ge=0)
    adaptsg_max_replans: int = Field(default=2, ge=1, le=3)
    adaptsg_journeys_table: str | None = None
    adaptsg_journey_ttl_hours: int = Field(default=24, ge=1, le=168)
    onemap_api_token: str | None = None
    onemap_bfa_enabled: bool = False
    data_gov_sg_api_key: str | None = None
    lta_account_key: str | None = None
    # Sensitive capabilities are intentionally opt-in and remain unavailable until policy
    # approvals and production retention evidence are recorded.
    adaptsg_booking_read_enabled: bool = False
    adaptsg_medical_intake_enabled: bool = False
    adaptsg_medical_clinician_enabled: bool = False
    adaptsg_emergency_live_enabled: bool = False
    adaptsg_multi_agent_enabled: bool = False
    adaptsg_production_retention_configured: bool = False
    adaptsg_authentication_configured: bool = False
    adaptsg_encryption_configured: bool = False
    adaptsg_audit_storage_configured: bool = False
    adaptsg_authentication_mode: Literal["demo", "cognito"] = "demo"
    adaptsg_cognito_issuer: str | None = None
    adaptsg_cognito_audience: str | None = None
    adaptsg_consent_policy_version: str = ""
    adaptsg_consent_categories: str = (
        "journey_input,mobility_accessibility,location_routing,provider_processing"
    )
    adaptsg_audit_retention_days: int | None = Field(default=None, ge=1, le=3650)
    adaptsg_revoked_consent_retention_days: int | None = Field(default=None, ge=1, le=3650)
    adaptsg_catalog_version: str = ""
    adaptsg_live_catalog_configured: bool = False
    adaptsg_cost_model_version: str = ""
    adaptsg_input_token_tariff_sgd: float | None = Field(default=None, ge=0)
    adaptsg_output_token_tariff_sgd: float | None = Field(default=None, ge=0)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
