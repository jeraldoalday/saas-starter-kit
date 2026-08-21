"""CLI: check readiness from outside the app and page on-call if it fails.

The app raises its own alerts from the request path, which covers every
failure it is healthy enough to notice. This covers the ones it isn't: a
process wedged hard enough not to serve a request cannot report that it
can't, and an orchestrator's own health check only stops routing traffic —
it tells no human anything.

Run it on a schedule (cron, a Kubernetes CronJob, a Fly machine) at
whatever cadence matches how fast you want to notice an outage:

    python -m app.scripts.check_health_and_alert                # alert on failure
    python -m app.scripts.check_health_and_alert --always-alert # smoke test

It checks the database directly rather than calling the HTTP readiness
endpoint, so it works the same whether it runs alongside the app or as a
separate watcher process with only DATABASE_URL in common.
"""

import argparse
import sys

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.database import SessionLocal
from app.services.alerts import Severity, get_alert_router, notify


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

    # Each run is a fresh process, so suppression state never survives to
    # the next one anyway — but a smoke test must deliver even if that ever
    # changes, or it stops proving anything on the second run.
    get_alert_router().reset()

    if failure is not None:
        delivered = notify(
            "Readiness check failed: database unreachable",
            failure,
            severity=Severity.CRITICAL,
            key="health.database_unreachable",
            checked_by="watcher",
        )
        print(
            f"alert: database unreachable (delivered={delivered})",
            file=sys.stderr,
        )
        # Non-zero so the scheduler's own failure reporting agrees with the
        # alert, rather than recording a successful run of a job that just
        # discovered an outage.
        return 1

    delivered = notify(
        "Alert routing test",
        "Readiness check passed; this is a --always-alert smoke test.",
        severity=Severity.CRITICAL,
        key="watcher.smoke_test",
        checked_by="watcher",
    )
    print(f"ok: database reachable; test alert delivered={delivered}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
