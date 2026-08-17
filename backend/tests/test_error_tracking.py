from unittest.mock import patch

from app.core.error_tracking import configure_error_tracking


def test_noop_without_dsn():
    with patch("app.core.error_tracking.sentry_sdk.init") as mock_init:
        configure_error_tracking(dsn="", environment="production")

    mock_init.assert_not_called()


def test_initializes_sentry_when_dsn_is_set():
    with patch("app.core.error_tracking.sentry_sdk.init") as mock_init:
        configure_error_tracking(
            dsn="https://public@sentry.example.com/1",
            environment="production",
            traces_sample_rate=0.1,
        )

    mock_init.assert_called_once_with(
        dsn="https://public@sentry.example.com/1",
        environment="production",
        traces_sample_rate=0.1,
    )


def test_defaults_traces_sample_rate_to_zero():
    with patch("app.core.error_tracking.sentry_sdk.init") as mock_init:
        configure_error_tracking(dsn="https://public@sentry.example.com/1", environment="staging")

    mock_init.assert_called_once_with(
        dsn="https://public@sentry.example.com/1",
        environment="staging",
        traces_sample_rate=0.0,
    )
