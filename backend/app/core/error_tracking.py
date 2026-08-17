"""Sentry error tracking.

Reports uncaught exceptions (and, optionally, a sample of request traces) to
Sentry once `SENTRY_DSN` is configured. With no DSN set - the default for
local dev - `configure_error_tracking` is a no-op, so nothing is sent
anywhere and no Sentry project is required to run the app.
"""

import logging

import sentry_sdk

logger = logging.getLogger("app.error_tracking")


def configure_error_tracking(
    dsn: str,
    environment: str,
    traces_sample_rate: float = 0.0,
) -> None:
    """Initialize the Sentry SDK. Safe to call with an empty `dsn`."""
    if not dsn:
        return

    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        traces_sample_rate=traces_sample_rate,
    )
    logger.info("error tracking enabled", extra={"environment": environment})
