"""CLI: check readiness and page on-call if the instance is unhealthy.

Fly's own health check only stops routing to a bad instance; nothing yet
tells a human. Run this on a schedule (cron, a Kubernetes CronJob, a Fly
machine) at whatever cadence matches how fast you want to notice an outage:

    python -m app.scripts.check_health_and_alert            # alerts on failure
    python -m app.scripts.check_health_and_alert --always-alert  # smoke test

It checks the database directly rather than calling the HTTP readiness
endpoint, so it works the same whether it runs alongside the app or as a
separate watcher process with only DATABASE_URL in common.
"""

import argparse
import sys

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.database import SessionLocal
from app.services.alerting import AlertMessage, get_alert_sender


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check readiness and alert on-call.")
    parser.add_argument(
        "--always-alert",
        action="store_true",
        help="Send a test alert even when the check passes, to confirm routing works.",
    )
    return parser.parse_args(argv)


def check_database() -> str | None:
    """Return None if the database is reachable, otherwise a failure reason."""
    session = SessionLocal()
    try:
        session.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        return str(exc)
    finally:
        session.close()
    return None


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    failure = check_database()

    if failure is None and not args.always_alert:
        print("ok: database reachable")
        return 0

    sender = get_alert_sender()
    if failure is not None:
        message = AlertMessage(
            subject="Readiness check failed: database unreachable",
            body=failure,
            severity="critical",
        )
    else:
        message = AlertMessage(
            subject="Alert routing test",
            body="Readiness check passed; this is a --always-alert smoke test.",
            severity="info",
        )

    delivered = sender.send(message)
    if failure is not None:
        print(f"alert: {message.subject} (delivered={delivered})", file=sys.stderr)
        return 1

    print(f"ok: database reachable; test alert delivered={delivered}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
