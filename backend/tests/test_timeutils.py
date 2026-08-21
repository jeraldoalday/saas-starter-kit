"""The UTC normalisation the expiry checks depend on.

Worth its own file because the bug it prevents is invisible on Postgres and
fatal on SQLite (and vice versa for the naive-is-local mistake): timestamps
only ever get compared in the branch that expires something, which is the
branch nobody exercises by hand.
"""

from datetime import UTC, datetime, timedelta, timezone

from app.core.timeutils import as_utc, now_utc


def test_none_passes_through():
    assert as_utc(None) is None


def test_a_naive_value_is_assumed_to_be_utc():
    naive = datetime(2026, 8, 21, 12, 0)

    assert as_utc(naive) == datetime(2026, 8, 21, 12, 0, tzinfo=UTC)


def test_an_aware_value_keeps_its_instant():
    aware = datetime(2026, 8, 21, 12, 0, tzinfo=timezone(timedelta(hours=5)))

    converted = as_utc(aware)

    assert converted == aware
    assert converted.tzinfo is UTC
    assert converted.hour == 7


def test_a_utc_value_is_unchanged():
    aware = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)

    assert as_utc(aware) == aware


def test_the_result_is_always_comparable_to_now():
    """The whole point: this comparison raises TypeError without as_utc."""
    stored_by_sqlite = datetime(2026, 8, 21, 12, 0)

    assert as_utc(stored_by_sqlite) < now_utc() + timedelta(days=365 * 100)


def test_now_is_aware_and_utc():
    assert now_utc().tzinfo is UTC
