"""Application configuration using Pydantic Settings."""


from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore"
    )

    # API Configuration
    app_name: str = "FreightWatch API"
    app_version: str = "0.1.0"
    app_env: str = "development"
    debug: bool = False
    api_prefix: str = "/api"

    # Database
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/freightwatch"
    db_ssl_required: bool = True
    db_echo: bool = False
    db_pool_size: int = 10
    db_max_overflow: int = 5

    # Anthropic API
    anthropic_api_key: str = ""
    vision_model: str = "claude-sonnet-4-6"
    # Prompt versions — bump to roll out a new prompt version
    invoice_extractor_prompt_version: str = "v1.1.0"
    tariff_extractor_prompt_version: str = "v1.1.1"

    # JWT Authentication
    jwt_auth_enabled: bool = True
    jwt_secret_key: str = "INSECURE_DEFAULT_SECRET_CHANGE_IN_PRODUCTION"
    jwt_algorithm: str = "HS256"
    jwt_expiration_days: int = 90

    # Business Rules
    benchmark_tolerance_pct: float = 5.0  # ±5% threshold for 'im_markt' classification
    invoice_total_tolerance_pct: float = 2.0  # Acceptable invoice total rounding tolerance

    # Upload / Processing
    max_upload_size_mb: int = 10
    upload_processing_concurrency: int = 5

    # CORS
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:3000"]
    cors_allowed_methods: list[str] = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
    cors_allowed_headers: list[str] = [
        "Content-Type",
        "Authorization",
        "X-Request-ID",
        "X-Tenant-ID",
        "Cache-Control",
    ]
    cors_max_age: int = 600

    # Security
    security_headers_enabled: bool = True

    # Cookie (used by JWT middleware for cookie-based auth fallback)
    cookie_name: str = "freightwatch_session"

    # Logging
    log_level: str = "INFO"
    log_format: str = "json"
    log_file: str = "logs/freightwatch.log"  # path relative to backend/ working dir
    log_file_max_mb: int = 10               # rotate when file exceeds this size
    log_file_backup_count: int = 7          # keep this many rotated files

    # Sentry (optional)
    sentry_enabled: bool = False
    sentry_dsn: str | None = None

    @model_validator(mode="after")
    def validate_jwt_secret_in_production(self) -> "Settings":
        _insecure = "INSECURE_DEFAULT_SECRET_CHANGE_IN_PRODUCTION"
        if self.jwt_auth_enabled and self.jwt_secret_key == _insecure and self.app_env != "test":
            raise ValueError(
                "JWT_SECRET_KEY must be set to a secure random value when jwt_auth_enabled=True. "
                "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
            )
        return self


settings = Settings()
