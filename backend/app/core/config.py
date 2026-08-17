from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Placeholder values shipped in .env.example. Fine for local dev (where they
# make the app boot with zero config), but if any of these are still in
# place once ENVIRONMENT=production, secrets were never actually set for the
# deployment and the app is running on values anyone can read in this repo.
_INSECURE_DEFAULTS = {
    "secret_key": "change-me-to-a-random-64-char-string",
    "stripe_secret_key": "sk_test_placeholder",
    "stripe_webhook_secret": "whsec_placeholder",
    "stripe_price_id_monthly": "price_placeholder",
}
MIN_SECRET_KEY_LENGTH = 32


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

    # Off by default: with the console email transport the verification link
    # only reaches the logs, so gating routes on it would block local dev.
    # Turn on wherever real email is configured.
    require_email_verification: bool = False

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

    # Empty disables error tracking entirely (the default for local dev).
    sentry_dsn: str = ""
    # Fraction (0.0-1.0) of requests to sample for performance tracing.
    # Errors are captured regardless of this setting.
    sentry_traces_sample_rate: float = 0.0

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @model_validator(mode="after")
    def _forbid_insecure_production_config(self) -> "Settings":
        if self.environment != "production":
            return self

        problems = [
            f"{field} is still set to its .env.example placeholder value"
            for field, placeholder in _INSECURE_DEFAULTS.items()
            if getattr(self, field) == placeholder
        ]
        if len(self.secret_key) < MIN_SECRET_KEY_LENGTH:
            problems.append(
                f"secret_key is only {len(self.secret_key)} chars "
                f"(minimum {MIN_SECRET_KEY_LENGTH})"
            )
        if self.debug:
            problems.append("debug is enabled")

        if problems:
            raise ValueError(
                "Refusing to start with ENVIRONMENT=production and insecure config: "
                + "; ".join(problems)
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
