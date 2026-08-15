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

    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_price_id_monthly: str = ""

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
