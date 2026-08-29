"""Retention jobs must not care which zone their clock is expressed in.

Every timestamp this application writes is UTC, and every one of these
helpers takes an optional `now` so a scheduled job or a test can control it.
That combination has a trap in it: SQLite stores naive wall-clock UTC and
its driver drops tzinfo when binding, so an aware datetime in any other zone
is compared as though its *local* reading were UTC. The rows deleted are
then off by the offset — silently, and only on the dialect the tests run on,
which is the worst possible place for the difference to live.

`count_all_active_sessions` already documented the hazard and normalised for
it. These are the helpers that did not.
"""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.core.security import hash_password
from app.models.api_key import ApiKey
from app.models.audit_log import AuditAction, AuditLog
from app.models.usage_event import UsageEvent
from app.models.user import User
from app.services.analytics_service import prune_events
from app.services.api_key_service import purge_revoked_keys
from app.services.audit_service import purge_audit_logs
from app.services.entitlement_service import month_start

# Far enough east that a wall-clock reading taken as UTC lands most of a day
# out — and, at a month boundary, in the previous calendar month.
TOKYO = timezone(timedelta(hours=9))

# One fixed instant, expressed two ways. Everything below asserts the two
# spellings produce the same answer.
INSTANT = datetime(2026, 6, 15, 2, 0, tzinfo=UTC)
SAME_INSTANT_IN_TOKYO = INSTANT.astimezone(TOKYO)

RETENTION_DAYS = 30


@pytest.fixture
def user(db_session) -> User:
    account = User(
        email="retention@example.com", hashed_password=hash_password("s3cret-password")
    )
    db_session.add(account)
    db_session.commit()
    db_session.refresh(account)
    return account


# Rows are placed a few hours either side of the cutoff rather than days,
# because the whole failure is an offset-sized one: with a nine-hour skew, a
# row that is a week clear of the boundary is classified the same way by the
# broken code and the fixed code, and the test would pass either way.
MARGIN = timedelta(hours=4)


def _ages(now: datetime) -> tuple[datetime, datetime]:
    """One timestamp just inside the window, one just outside it."""
    cutoff = now - timedelta(days=RETENTION_DAYS)
    return cutoff + MARGIN, cutoff - MARGIN


@pytest.mark.parametrize("now", [INSTANT, SAME_INSTANT_IN_TOKYO])
def test_pruning_events_ignores_the_clock_s_zone(db_session, user, now):
    fresh, stale = _ages(INSTANT)
    db_session.add_all(
        [
            UsageEvent(user_id=user.id, name="a.b", occurred_at=fresh),
            UsageEvent(user_id=user.id, name="a.b", occurred_at=stale),
        ]
    )
    db_session.commit()

    assert prune_events(db_session, older_than_days=RETENTION_DAYS, now=now) == 1
    assert db_session.query(UsageEvent).count() == 1


@pytest.mark.parametrize("now", [INSTANT, SAME_INSTANT_IN_TOKYO])
def test_purging_revoked_keys_ignores_the_clock_s_zone(db_session, user, now):
    fresh, stale = _ages(INSTANT)
    db_session.add_all(
        [
            ApiKey(
                user_id=user.id, name="fresh", prefix="sk_live_aaaa",
                token_hash="a" * 64, revoked_at=fresh,
            ),
            ApiKey(
                user_id=user.id, name="stale", prefix="sk_live_bbbb",
                token_hash="b" * 64, revoked_at=stale,
            ),
        ]
    )
    db_session.commit()

    assert purge_revoked_keys(db_session, older_than_days=RETENTION_DAYS, now=now) == 1
    assert [key.name for key in db_session.query(ApiKey).all()] == ["fresh"]


@pytest.mark.parametrize("now", [INSTANT, SAME_INSTANT_IN_TOKYO])
def test_purging_the_audit_trail_ignores_the_clock_s_zone(db_session, user, now):
    fresh, stale = _ages(INSTANT)
    db_session.add_all(
        [
            AuditLog(user_id=user.id, action=AuditAction.LOGIN.value, created_at=fresh),
            AuditLog(user_id=user.id, action=AuditAction.LOGIN.value, created_at=stale),
        ]
    )
    db_session.commit()

    assert purge_audit_logs(db_session, older_than_days=RETENTION_DAYS, now=now) == 1
    assert db_session.query(AuditLog).count() == 1


def test_month_start_is_the_utc_month_not_the_caller_s():
    # 2026-07-01T05:00+09:00 is still June the 30th in UTC, so the allowance
    # it belongs to is June's. Taking the caller's calendar month here would
    # reset a customer's monthly budget nine hours early, once a month.
    early_july_in_tokyo = datetime(2026, 7, 1, 5, 0, tzinfo=TOKYO)

    assert month_start(early_july_in_tokyo) == datetime(2026, 6, 1, tzinfo=UTC)


def test_month_start_agrees_with_itself_across_zones():
    assert month_start(INSTANT) == month_start(SAME_INSTANT_IN_TOKYO)
