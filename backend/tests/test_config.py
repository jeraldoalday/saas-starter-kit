import pytest
from pydantic import ValidationError

from app.core.config import MIN_PRODUCTION_BCRYPT_ROUNDS, MIN_SECRET_KEY_LENGTH, Settings

SECURE_SECRET_KEY = "x" * MIN_SECRET_KEY_LENGTH


def _production_kwargs(**overrides) -> dict:
    kwargs = {
        "environment": "production",
        "debug": False,
        "secret_key": SECURE_SECRET_KEY,
        "stripe_secret_key": "sk_live_realvalue",
        "stripe_webhook_secret": "whsec_realvalue",
        "stripe_price_id_monthly": "price_realvalue",
        # Explicit rather than left to the ambient environment: this suite
        # itself runs with BCRYPT_ROUNDS lowered for speed (see the
        # Makefile), and a "secure production config" fixture should not
        # become insecure just because of the setting the test runner
        # happens to export.
        "bcrypt_rounds": MIN_PRODUCTION_BCRYPT_ROUNDS,
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


def test_development_default_bcrypt_rounds(monkeypatch):
    # Isolated from the ambient environment: this suite itself runs with
    # BCRYPT_ROUNDS lowered for speed (see the Makefile), which is exactly
    # the override this test must not pick up when checking the real
    # default.
    monkeypatch.delenv("BCRYPT_ROUNDS", raising=False)

    assert Settings().bcrypt_rounds == 12


def test_production_rejects_bcrypt_rounds_below_the_floor():
    with pytest.raises(ValidationError, match="bcrypt_rounds"):
        Settings(**_production_kwargs(bcrypt_rounds=MIN_PRODUCTION_BCRYPT_ROUNDS - 1))


def test_production_accepts_bcrypt_rounds_at_the_floor():
    settings = Settings(**_production_kwargs(bcrypt_rounds=MIN_PRODUCTION_BCRYPT_ROUNDS))

    assert settings.bcrypt_rounds == MIN_PRODUCTION_BCRYPT_ROUNDS


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


# --- alert routing --------------------------------------------------------
#
# Every one of these misconfigurations delivers nothing while looking fine,
# and would only be discovered by the incident that needed the page.


def test_alert_defaults_are_valid():
    settings = Settings()

    assert settings.alert_backend == "log"
    assert settings.alert_min_severity == "warning"


def test_console_is_accepted_as_a_synonym_for_log():
    """Both spellings have shipped; a deployment setting either must boot."""
    assert Settings(alert_backend="console").alert_backend == "console"


def test_an_unknown_alert_backend_is_rejected():
    """It would otherwise fall through to the logging transport, so a typo in
    ALERT_BACKEND reads as "configured" and pages nobody."""
    with pytest.raises(ValidationError, match="alert_backend"):
        Settings(alert_backend="slak")


def test_an_unknown_alert_severity_is_rejected():
    """An unrecognised severity outranks every real one, so a typo here drops
    every alert below critical without saying so."""
    with pytest.raises(ValidationError, match="alert_min_severity"):
        Settings(alert_min_severity="warnin")


def test_the_webhook_backend_requires_a_url():
    with pytest.raises(ValidationError, match="alert_webhook_url"):
        Settings(alert_backend="webhook", alert_webhook_url="")


def test_a_configured_webhook_backend_is_accepted():
    settings = Settings(
        alert_backend="webhook", alert_webhook_url="https://hooks.example.com/abc"
    )

    assert settings.alert_backend == "webhook"


@pytest.mark.parametrize("severity", ["info", "warning", "critical"])
def test_every_severity_is_accepted(severity):
    assert Settings(alert_min_severity=severity).alert_min_severity == severity
