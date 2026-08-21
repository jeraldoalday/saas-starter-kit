"""CLI: delete revoked API keys past the retention window.

Revocation is a tombstone rather than a delete, so that "this key was
revoked on the 3rd" stays answerable after an incident. That answer decays;
the row does not. Run this on the same schedule as the usage-event prune:

    python -m app.scripts.prune_api_keys             # uses API_KEY_RETENTION_DAYS
    python -m app.scripts.prune_api_keys --days 30   # override
    python -m app.scripts.prune_api_keys --dry-run   # count without deleting

Only revoked keys are ever touched. An expired key is left alone: it stops
working on its own, and the owner still needs to see it in the list to
understand why their script started failing.
"""

import argparse
import sys
from datetime import UTC, datetime, timedelta

from app.core.config import get_settings
from app.database import SessionLocal
from app.models.api_key import ApiKey
from app.services.api_key_service import purge_revoked_keys


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prune revoked API keys.")
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="Retention window in days (defaults to API_KEY_RETENTION_DAYS).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report how many rows would be deleted without deleting them.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    days = args.days if args.days is not None else get_settings().api_key_retention_days

    if days < 1:
        print("Retention window must be at least 1 day.", file=sys.stderr)
        return 1

    session = SessionLocal()
    try:
        if args.dry_run:
            cutoff = datetime.now(UTC) - timedelta(days=days)
            doomed = (
                session.query(ApiKey)
                .filter(ApiKey.revoked_at.is_not(None), ApiKey.revoked_at < cutoff)
                .count()
            )
            print(f"{doomed} revoked key(s) older than {days} day(s) would be deleted.")
            return 0

        deleted = purge_revoked_keys(session, older_than_days=days)
        print(f"Deleted {deleted} revoked key(s) older than {days} day(s).")
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
