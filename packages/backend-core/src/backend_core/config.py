"""Application settings.

Scope note: PHASE 1 introduces only the settings its own infrastructure clients
cannot work without (database, Redis, object storage). P2-T01 formalises and
extends this into the full configuration surface. Parsing environment variables
ad hoc inside each client and replacing it later would be strictly worse.

Secret values are typed ``SecretStr`` so they cannot be leaked by an accidental
``repr()`` of the settings object — taskbook §63 forbids logging API keys and
credentials.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-backed configuration, validated once at process start."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Application ------------------------------------------------------
    app_env: Literal["development", "test", "staging", "production"] = "development"
    log_level: str = "INFO"

    # --- Database (§4.4) --------------------------------------------------
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/aipvs"
    database_pool_size: int = Field(default=10, ge=1)
    database_max_overflow: int = Field(default=20, ge=0)
    database_pool_timeout_seconds: int = Field(default=30, ge=1)
    # Recycle below typical proxy/database idle timeouts so a pooled connection
    # is never handed out after the server has already dropped it.
    database_pool_recycle_seconds: int = Field(default=1800, ge=60)
    database_echo: bool = False

    # --- Redis (§4.5) -----------------------------------------------------
    redis_url: str = "redis://localhost:6379/0"
    redis_max_connections: int = Field(default=50, ge=1)
    redis_socket_timeout_seconds: float = Field(default=5.0, gt=0)

    # --- Object storage (§4.6, §11) ---------------------------------------
    s3_endpoint: str | None = None
    s3_region: str = "us-east-1"
    s3_bucket: str = "aipvs-dev"
    s3_access_key_id: SecretStr = SecretStr("")
    s3_secret_access_key: SecretStr = SecretStr("")
    s3_public_base_url: str | None = None
    # MinIO and most self-hosted S3 implementations need path-style addressing.
    s3_force_path_style: bool = True
    s3_signed_url_ttl_seconds: int = Field(default=900, ge=60, le=604800)

    # --- Feature flags (§122, §170) ---------------------------------------
    use_mock_providers: bool = True
    enable_credits: bool = False

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings, parsed once.

    Cached because validation is not free and settings never change within a
    process. Tests that need different values call ``get_settings.cache_clear()``.
    """
    return Settings()
