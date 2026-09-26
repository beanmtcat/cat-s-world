from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """所有运行时配置均来自环境；生产环境没有不安全默认值。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        enable_decoding=False,
    )

    app_env: Literal["development", "test", "production"] = "development"
    app_base_url: AnyHttpUrl = "http://localhost:5173"
    api_base_url: AnyHttpUrl = "http://localhost:8000"
    database_url: str
    redis_url: str
    trusted_hosts: list[str] = Field(default_factory=list)
    cors_origins: list[str] = Field(default_factory=list)
    # `__Host-` cookies are intentionally rejected by browsers unless Secure
    # is set.  Local Vite/FastAPI development is HTTP, so it needs a distinct
    # non-prefixed name; production is validated below and must use __Host-.
    session_cookie_name: str = "mmcat-session"
    csrf_cookie_name: str = "mmcat-csrf"
    session_secret: SecretStr
    hash_ip_secret: SecretStr
    s3_endpoint: AnyHttpUrl
    s3_region: str
    s3_bucket: str
    s3_access_key_id: SecretStr
    s3_secret_access_key: SecretStr
    s3_public_base_url: AnyHttpUrl
    s3_quarantine_prefix: str = "quarantine"
    s3_public_prefix: str = "public"
    smtp_host: str
    smtp_port: int = 587
    smtp_username: SecretStr
    smtp_password: SecretStr
    mail_from: str
    search_engine_secret_provider: str
    search_engine_secret_namespace: str

    @field_validator("trusted_hosts", "cors_origins", mode="before")
    @classmethod
    def split_csv(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, list):
            return value
        return [item.strip() for item in value.split(",") if item.strip()]

    def require_production_safety(self) -> None:
        if self.app_env != "production":
            return
        if len(self.session_secret.get_secret_value()) < 32:
            raise ValueError("SESSION_SECRET must be at least 32 bytes in production")
        if len(self.hash_ip_secret.get_secret_value()) < 32:
            raise ValueError("HASH_IP_SECRET must be at least 32 bytes in production")
        if not self.trusted_hosts or not self.cors_origins:
            raise ValueError("TRUSTED_HOSTS and CORS_ORIGINS are required in production")
        if not self.session_cookie_name.startswith("__Host-"):
            raise ValueError("SESSION_COOKIE_NAME must use the __Host- prefix in production")


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.require_production_safety()
    return settings
