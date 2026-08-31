"""CLI: delete finished webhook deliveries past the retention window.

Every event queued for every endpoint leaves a row, so this table grows with
traffic rather than with the number of customers — which makes it the one
retention job that matters at low volume as well as high. Run it alongside
the other prunes:

    python -m app.scripts.prune_webhook_deliveries              # WEBHOOK_DELIVERY_RETENTION_DAYS
    python -m app.scripts.prune_webhook_deliveries --days 7     # override
    python -m app.scripts.prune_webhook_deliveries --dry-run    # count only

Only delivered and abandoned rows are touched. A pending delivery older than
the window is left alone however old it is: it is still owed to somebody,
and deleting it would be quietly dropping a promise to save a row. Pending
rows piling up past the retention window mean the delivery worker is not
running, which is a problem this job must surface rather than tidy away.
"""

import argparse
import sys
from datetime import UTC, datetime, timedelta

from app.core.config import get_settings
from app.database import SessionLocal
from app.models.webhook_delivery import DeliveryStatus, WebhookDelivery
from app.services.webhooks.dispatch import purge_deliveries

TERMINAL_STATUSES = (DeliveryStatus.DELIVERED.value, DeliveryStatus.FAILED.value)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prune finished webhook deliveries.")
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="Retention window in days (defaults to WEBHOOK_DELIVERY_RETENTION_DAYS).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report how many rows would be deleted without deleting them.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    days = (
        args.days
        if args.days is not None
        else get_settings().webhook_delivery_retention_days
    )

    if days < 1:
        print("Retention window must be at least 1 day.", file=sys.stderr)
        return 1

    session = SessionLocal()
    try:
        if args.dry_run:
            cutoff = datetime.now(UTC) - timedelta(days=days)
            doomed = (
                session.query(WebhookDelivery)
                .filter(
                    WebhookDelivery.status.in_(TERMINAL_STATUSES),
                    WebhookDelivery.created_at < cutoff,
                )
                .count()
            )
            print(
                f"{doomed} finished delivery(s) older than {days} day(s) "
                "would be deleted."
            )
            return 0

        deleted = purge_deliveries(session, older_than_days=days)
        print(f"Deleted {deleted} finished delivery(s) older than {days} day(s).")
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
