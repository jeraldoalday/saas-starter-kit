import pytest
from pydantic import ValidationError

from app.core.config import MIN_SECRET_KEY_LENGTH, Settings

SECURE_SECRET_KEY = "x" * MIN_SECRET_KEY_LENGTH


def _production_kwargs(**overrides) -> dict:
    kwargs = {
        "environment": "production",
        "debug": False,
        "secret_key": SECURE_SECRET_KEY,
        "stripe_secret_key": "sk_live_realvalue",
        "stripe_webhook_secret": "whsec_realvalue",
        "stripe_price_id_monthly": "price_realvalue",
    }
    kwargs.update(overrides)
    return kwargs


def test_development_defaults_are_allowed():
    settings = Settings()

    assert settings.environment == "development"
    assert settings.debug is True


def test_production_with_secure_config_is_allowed():
    settings = Settings(**_production_kwargs())

    assert settings.environment == "production"


def test_production_rejects_default_secret_key():
    with pytest.raises(ValidationError, match="secret_key"):
        Settings(**_production_kwargs(secret_key="change-me-to-a-random-64-char-string"))


def test_production_rejects_short_secret_key():
    with pytest.raises(ValidationError, match="secret_key"):
        Settings(**_production_kwargs(secret_key="too-short"))


def test_production_rejects_debug_enabled():
    with pytest.raises(ValidationError, match="debug"):
        Settings(**_production_kwargs(debug=True))


@pytest.mark.parametrize(
    "field",
    ["stripe_secret_key", "stripe_webhook_secret", "stripe_price_id_monthly"],
)
def test_production_rejects_placeholder_stripe_values(field):
    placeholder = {
        "stripe_secret_key": "sk_test_placeholder",
        "stripe_webhook_secret": "whsec_placeholder",
        "stripe_price_id_monthly": "price_placeholder",
    }[field]

    with pytest.raises(ValidationError, match=field):
        Settings(**_production_kwargs(**{field: placeholder}))
