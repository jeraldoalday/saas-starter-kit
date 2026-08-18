"""CLI: delete usage events past the retention window.

The usage_events table is append-only in the request path, so something has
to take the other side of that. Run this on a schedule (cron, a Kubernetes
CronJob, a Fly machine) at whatever cadence keeps the table comfortable:

    python -m app.scripts.prune_usage_events            # uses ANALYTICS_RETENTION_DAYS
    python -m app.scripts.prune_usage_events --days 90  # override
    python -m app.scripts.prune_usage_events --dry-run  # count without deleting
"""

import argparse
import sys
from datetime import UTC, datetime, timedelta

from app.core.config import get_settings
from app.database import SessionLocal
from app.models.usage_event import UsageEvent
from app.services.analytics_service import prune_events


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prune old usage events.")
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="Retention window in days (defaults to ANALYTICS_RETENTION_DAYS).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report how many rows would be deleted without deleting them.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    days = args.days if args.days is not None else get_settings().analytics_retention_days

    if days < 1:
        print("Retention window must be at least 1 day.", file=sys.stderr)
        return 1

    session = SessionLocal()
    try:
        if args.dry_run:
            cutoff = datetime.now(UTC) - timedelta(days=days)
            doomed = (
                session.query(UsageEvent).filter(UsageEvent.occurred_at < cutoff).count()
            )
            print(f"{doomed} event(s) older than {days} day(s) would be deleted.")
            return 0

        deleted = prune_events(session, older_than_days=days)
        print(f"Deleted {deleted} event(s) older than {days} day(s).")
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
