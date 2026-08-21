"""Timezone normalisation for values that come back out of the database.

Every timestamp this application writes is timezone-aware UTC, and Postgres
returns it that way. SQLite — which the test suite runs on — drops the
tzinfo and hands back a naive datetime, so any comparison against
`datetime.now(UTC)` raises `TypeError: can't compare offset-naive and
offset-aware datetimes`.

That difference only shows up in the branch being compared, which is how it
tends to reach production: the code path that expires a token or a key is
exactly the one nobody exercises by hand. Normalising in one place, at the
point of comparison, is cheaper than remembering to do it at each.
"""

from datetime import UTC, datetime


def as_utc(value: datetime | None) -> datetime | None:
    """Return `value` as an aware UTC datetime, or None.

    A naive value is *assumed* to be UTC rather than local: everything
    written here is UTC, so a naive datetime is a driver artefact, not a
    local timestamp. Attaching the local zone instead would shift stored
    times by the deploy machine's offset.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def now_utc() -> datetime:
    """The current time, aware and in UTC.

    A named function rather than `datetime.now(UTC)` inline so tests that
    need to control the clock have one place to patch.
    """
    return datetime.now(UTC)
