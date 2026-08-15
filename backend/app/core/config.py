from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "development"
    debug: bool = True

    secret_key: str = "change-me-to-a-random-64-char-string"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 14

    database_url: str = "postgresql+psycopg://saas:saas@localhost:5432/saas"

    cors_origins: str = "http://localhost:3000"

    # Public base URL of the frontend, used to build Stripe redirect targets.
    frontend_base_url: str = "http://localhost:3000"

    # Send HSTS. Enable only where TLS actually terminates in front of the app.
    enable_hsts: bool = False

    rate_limit_enabled: bool = True
    # Only enable behind a proxy that overwrites X-Forwarded-For; otherwise a
    # client can spoof the header and sidestep per-IP limits.
    trust_proxy_headers: bool = False

    log_level: str = "INFO"
    # JSON logs for log collectors in production; plain text is easier to read
    # while developing.
    log_json: bool = True

    # "console" logs emails instead of sending; "smtp" delivers for real.
    email_backend: str = "console"
    email_from: str = "no-reply@example.com"
    smtp_host: str = "localhost"
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True

    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_price_id_monthly: str = ""

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
