"""CLI: delete audit trail past the retention window.

The trail is append-only, so somebody has to take the other side of the
deal. Run it on the same schedule as the other retention prunes:

    python -m app.scripts.prune_audit_logs             # AUDIT_RETENTION_DAYS
    python -m app.scripts.prune_audit_logs --days 365  # override
    python -m app.scripts.prune_audit_logs --dry-run   # count without deleting

The default window sits between the other two on purpose. Longer than the
session one, because an incident noticed months late still needs a trail to
reconstruct from and a session list is read for recognition rather than
history. Shorter than the analytics one, because these rows are a record of
where people were when they signed in, and keeping that for over a year to
answer questions nobody is asking is a liability rather than an asset.

Given its own schedule entry rather than folded into another prune — unlike
the one-time tokens that ride along with prune_sessions — precisely because
the window is a policy decision somebody will want to change without
touching how long dead sessions are kept.
"""

import argparse
import sys
from datetime import UTC, datetime, timedelta

from app.core.config import get_settings
from app.database import SessionLocal
from app.models.audit_log import AuditLog
from app.services.audit_service import purge_audit_logs


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prune audit log entries past the retention window."
    )
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="Retention window in days (defaults to AUDIT_RETENTION_DAYS).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report how many rows would be deleted without deleting them.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    days = args.days if args.days is not None else get_settings().audit_retention_days

    if days < 1:
        # Refused rather than clamped: "--days 0" is somebody expecting the
        # table emptied, and quietly keeping a day would look like the
        # command did nothing.
        print("Retention window must be at least 1 day.", file=sys.stderr)
        return 1

    session = SessionLocal()
    try:
        if args.dry_run:
            cutoff = datetime.now(UTC) - timedelta(days=days)
            doomed = (
                session.query(AuditLog).filter(AuditLog.created_at < cutoff).count()
            )
            print(
                f"{doomed} audit entr(ies) older than {days} day(s) would be deleted."
            )
            return 0

        deleted = purge_audit_logs(session, older_than_days=days)
        print(f"Deleted {deleted} audit entr(ies) older than {days} day(s).")
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
