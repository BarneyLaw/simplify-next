"""Environment-backed application configuration with safe demo defaults."""

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

    adaptsg_mode: Literal["demo", "live"] = "demo"
    adaptsg_log_level: str = "INFO"
    aws_region: str = "us-east-1"
    aws_profile: str | None = None
    bedrock_model_id: str = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
    bedrock_max_tokens: int = Field(default=1_200, ge=128, le=4_096)
    adaptsg_approval_cost_increase_sgd: float = Field(default=8, ge=0)
    adaptsg_max_replans: int = Field(default=2, ge=1, le=3)
    adaptsg_journeys_table: str | None = None
    adaptsg_journey_ttl_hours: int = Field(default=24, ge=1, le=168)
    onemap_api_token: str | None = None
    onemap_bfa_enabled: bool = False
    data_gov_sg_api_key: str | None = None
    lta_account_key: str | None = None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
