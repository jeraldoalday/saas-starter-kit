"""CLI: delete dead auth sessions past the retention window.

A revoked session keeps its row so that "that device was signed out on the
3rd" stays answerable after an incident, and an expired one keeps its row
for the same reason. Both answers decay; the rows do not. Run this on the
same schedule as the other retention prunes:

    python -m app.scripts.prune_sessions             # uses SESSION_RETENTION_DAYS
    python -m app.scripts.prune_sessions --days 14   # override
    python -m app.scripts.prune_sessions --dry-run   # count without deleting

Only sessions that already stopped working are touched, and the window is
measured from the moment each one died — revocation for revoked rows,
expiry for the rest — so a session revoked on day one is not kept alive in
the table by an expiry it never reached.

Spent one-time tokens are pruned here too. They are the same kind of debris
from the same subsystem, they are far more numerous (one per signup, one per
reset request), and giving them their own schedule entry would be one more
thing to forget to install.
"""

import argparse
import sys
from datetime import UTC, datetime, timedelta

from app.core.config import get_settings
from app.core.timeutils import as_utc
from app.database import SessionLocal
from app.models.auth_session import AuthSession
from app.services.session_service import purge_expired_sessions
from app.services.token_service import purge_expired


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prune expired and revoked auth sessions."
    )
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="Retention window in days (defaults to SESSION_RETENTION_DAYS).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report how many rows would be deleted without deleting them.",
    )
    return parser.parse_args(argv)


def _would_delete(session, cutoff: datetime) -> int:
    """Count dead sessions without touching them.

    Counted in Python for the same reason the service deletes in Python:
    which timestamp marks the death depends on how the session died.
    """
    rows = session.query(AuthSession).all()
    doomed = 0
    for row in rows:
        died = as_utc(row.revoked_at) or as_utc(row.expires_at)
        if died is not None and died < cutoff:
            doomed += 1
    return doomed


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    days = args.days if args.days is not None else get_settings().session_retention_days

    if days < 1:
        print("Retention window must be at least 1 day.", file=sys.stderr)
        return 1

    session = SessionLocal()
    try:
        if args.dry_run:
            cutoff = datetime.now(UTC) - timedelta(days=days)
            doomed = _would_delete(session, cutoff)
            print(f"{doomed} dead session(s) older than {days} day(s) would be deleted.")
            return 0

        deleted = purge_expired_sessions(session, older_than_days=days)
        print(f"Deleted {deleted} dead session(s) older than {days} day(s).")

        # Not gated on --days: a one-time token is dead the moment it is
        # spent or lapses, and there is nothing to learn from keeping it.
        tokens = purge_expired(session)
        print(f"Deleted {tokens} spent or expired one-time token(s).")
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
