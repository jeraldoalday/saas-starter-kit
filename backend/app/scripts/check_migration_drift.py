"""CLI: fail if the migrations and the models describe different schemas.

The test suite builds its schema with `create_all` from the models, so it
proves the models work and says nothing about whether the migrations
produce the same thing. CI's migrations job runs `alembic upgrade head`,
which proves the migrations *run* and says nothing about what they leave
behind. Between the two there is a gap wide enough to drive a production
incident through: a column added to a model with no matching migration
passes both, and is only discovered by the first query that references a
column the deployed database does not have.

This closes it by asking Alembic the question it already knows how to
answer — "what would you autogenerate against this database?" — and
failing if the answer is anything at all.

    DATABASE_URL=postgresql+psycopg://... python -m app.scripts.check_migration_drift

Run it against a database already at head. It compares, it never writes.

Column *types* are compared only with `--compare-types`, and it is off by
default deliberately. The gap this exists to close is structural — a table
or a column that one side has and the other does not — and that comparison
is unambiguous. Type comparison is not: a model's `Mapped[uuid.UUID]` and a
migration's `postgresql.UUID` describe the same Postgres column while
spelling it differently, and a check that cries wolf on every such pair is a
check somebody switches off within the week. Turn it on once you have looked
at what it says on your own schema.
"""

import argparse
import sys
from collections.abc import Iterable

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine

from app.core.config import get_settings
from app.database import Base

# Alembic's own bookkeeping table. It is not in the models and never should
# be, so every comparison would otherwise report it as an extra table.
IGNORED_TABLES = frozenset({"alembic_version"})


def _table_of(diff) -> str | None:
    """The table a diff entry concerns, across Alembic's two entry shapes.

    A diff is either a tuple whose second-or-third element identifies the
    table, or a list of such tuples when several changes affect one table.
    Rather than enumerate every operation name, this reads the first string
    that looks like a table name — which is all the filtering below needs.
    """
    if isinstance(diff, list):
        return _table_of(diff[0]) if diff else None

    for element in diff[1:]:
        if isinstance(element, str):
            return element
        table = getattr(element, "table", None)
        if table is not None:
            return table.name
        name = getattr(element, "name", None)
        if isinstance(name, str) and hasattr(element, "columns"):
            return name
    return None


def find_drift(database_url: str, *, compare_types: bool = False) -> list:
    """Differences between the live schema and the models, as Alembic sees them."""
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(
                connection,
                opts={"compare_type": compare_types},
            )
            diffs = compare_metadata(context, Base.metadata)
    finally:
        engine.dispose()

    return [diff for diff in diffs if _table_of(diff) not in IGNORED_TABLES]


def describe(diffs: Iterable) -> str:
    """One line per difference, in the order Alembic reported them."""
    return "\n".join(f"  - {diff}" for diff in diffs)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fail if the migrations and the models disagree."
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="Database to compare against (defaults to DATABASE_URL).",
    )
    parser.add_argument(
        "--compare-types",
        action="store_true",
        help=(
            "Also report column type differences. Off by default: the same "
            "Postgres column can be spelled differently by a model and a "
            "migration without either being wrong."
        ),
    )
    args = parser.parse_args(argv)
    database_url = args.database_url or get_settings().database_url

    diffs = find_drift(database_url, compare_types=args.compare_types)
    if not diffs:
        print("Migrations and models agree.")
        return 0

    print(
        f"{len(diffs)} difference(s) between the migrated schema and the models:",
        file=sys.stderr,
    )
    print(describe(diffs), file=sys.stderr)
    print(
        "\nA model changed without a matching migration, or a migration does "
        "not do what its model says. Add the migration, or correct it.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
