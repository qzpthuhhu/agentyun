"""Application configuration."""
import os
from typing import Optional

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Cloud service settings.

    Reads from environment variables prefixed with AGENTCLOUD_.
    """

    # Service
    service_name: str = "agentcloud-cloud"
    debug: bool = False
    api_prefix: str = "/v1"

    # Database
    database_url: str = "sqlite:///./agentcloud.db"

    # JWT for session tokens (issued after key login)
    jwt_secret: str = "CHANGE_ME_IN_PRODUCTION_USE_ENV_VAR"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24 * 30  # 30 days

    # Storage for assets
    asset_storage_dir: str = "./assets_data"
    max_asset_size: int = 100 * 1024 * 1024  # 100MB

    # CORS
    cors_origins: list[str] = ["*"]

    class Config:
        env_prefix = "AGENTCLOUD_"
        env_file = ".env"
        case_sensitive = False


settings = Settings()