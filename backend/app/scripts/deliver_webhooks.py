"""CLI: send the outbound webhooks that are due.

Dispatch queues rows inside the request that caused the event; nothing is
POSTed until this runs. Put it on a schedule at whatever latency your
customers should see:

    python -m app.scripts.deliver_webhooks               # one pass, then exit
    python -m app.scripts.deliver_webhooks --limit 200   # a bigger pass
    python -m app.scripts.deliver_webhooks --loop --interval 10
    python -m app.scripts.deliver_webhooks --dry-run     # count what is due

One pass per invocation is the default because that is what a cron entry or
a Kubernetes CronJob wants: a process that starts, does a bounded amount of
work, and exits with a status. `--loop` is for running it as a long-lived
worker instead, which is the better shape once the queue is busy enough that
a per-minute cron leaves deliveries waiting most of a minute.

**Run one at a time.** There is no locking: two workers polling the same
queue will both claim the same due rows and both POST them, and while the
delivery id in the payload means a correctly-written receiver discards the
duplicate, "correctly-written receiver" is not a thing to bet a customer's
billing logic on. `SELECT ... FOR UPDATE SKIP LOCKED` is the standard answer
when one worker is no longer enough; it is deliberately not here, because a
Postgres-only claim query in a scaffold that runs its tests on SQLite would
be a lock nobody could test.
"""

import argparse
import sys
import time

from app.core.config import get_settings
from app.database import SessionLocal
from app.services.webhooks.dispatch import deliver_due, due_deliveries

# Floor on --interval. A worker polling faster than this is spending more
# time asking the database whether anything is due than a webhook's latency
# budget can possibly justify.
MIN_INTERVAL_SECONDS = 1


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Deliver queued outbound webhooks.")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Deliveries to attempt in one pass (defaults to WEBHOOK_DELIVERY_BATCH_SIZE).",
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Keep running, sleeping --interval seconds between passes.",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=10.0,
        help="Seconds between passes when --loop is set (default: 10).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report how many deliveries are due without sending any.",
    )
    return parser.parse_args(argv)


def _one_pass(limit: int | None) -> str:
    session = SessionLocal()
    try:
        run = deliver_due(session, limit=limit)
        return str(run)
    finally:
        session.close()


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    settings = get_settings()

    if not settings.webhooks_enabled:
        # Worth saying rather than exiting silently: the flag stops new
        # deliveries being *queued*, and someone running this to drain a
        # backlog should know the queue is no longer being fed.
        print(
            "WEBHOOKS_ENABLED is false — no new deliveries are being queued. "
            "Draining whatever is already queued.",
            file=sys.stderr,
        )

    if args.dry_run:
        session = SessionLocal()
        try:
            # Asks for one more than the batch so the report can say
            # "at least" rather than implying the batch size is the backlog.
            batch = args.limit or settings.webhook_delivery_batch_size
            due = due_deliveries(session, limit=batch + 1)
            more = " or more" if len(due) > batch else ""
            print(f"{min(len(due), batch)}{more} delivery(s) due now.")
            return 0
        finally:
            session.close()

    if not args.loop:
        print(_one_pass(args.limit))
        return 0

    interval = max(args.interval, MIN_INTERVAL_SECONDS)
    print(f"Delivering webhooks every {interval}s. Ctrl-C to stop.", file=sys.stderr)
    try:
        while True:
            result = _one_pass(args.limit)
            print(result, flush=True)
            time.sleep(interval)
    except KeyboardInterrupt:
        # A clean exit rather than a traceback: this is the normal way a
        # worker started by hand is stopped, and an operator should not have
        # to read a stack trace to find out they pressed Ctrl-C.
        print("Stopped.", file=sys.stderr)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
