from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AGENTOS_", env_file=".env", extra="ignore")

    env: str = "development"
    database_url: str = "sqlite+aiosqlite:///./data/agentos.db"
    redis_url: str | None = None

    jwt_secret: str = "dev-only-change-me-0123456789abcdef0123456789"
    jwt_ttl_minutes: int = 12 * 60
    # Fernet key (urlsafe base64, 32 bytes). Derived from jwt_secret in dev when unset.
    secrets_key: str | None = None

    embedded_workers: int = 4
    worker_lease_seconds: int = 120
    recovery_interval_seconds: int = 15

    object_storage_path: str = "./data/objects"
    s3_bucket: str | None = None

    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    # Comma-separated provider preference; providers without credentials are skipped.
    provider_order: str = "openai,anthropic,local"
    local_model_latency_ms: int = 120

    embedding_provider: str = "local"
    vector_backend: str = "sql"

    max_delegation_depth: int = 3
    max_children_per_task: int = 8
    max_tasks_per_project: int = 200
    default_quality_threshold: float = 0.75
    max_revisions: int = 2

    rate_limit_per_minute: int = 600
    cors_origins: str = "http://localhost:5173,http://localhost:8080"

    otel_exporter_endpoint: str | None = None
    seed_demo: bool = True
    log_level: str = "INFO"

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    return Settings()
