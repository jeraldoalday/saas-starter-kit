"""The drift check between the migrations and the models.

The check itself only means something against Postgres, where the real
migration chain can run — the migrations use `postgresql.UUID`, so they
cannot be applied to SQLite. What is asserted here is the machinery: that a
schema matching the models reports nothing, that a schema missing something
reports it, and that Alembic's own bookkeeping table is never mistaken for
drift. CI points the same function at a migrated Postgres.
"""

import pytest
from sqlalchemy import Column, MetaData, String, Table, create_engine, text

from app.database import Base
from app.scripts import check_migration_drift


@pytest.fixture
def database_url(tmp_path) -> str:
    """A file-backed SQLite database the script can open by URL.

    A file rather than :memory:, because the script creates its own engine
    and an in-memory database would not survive the trip.
    """
    return f"sqlite:///{tmp_path / 'drift.db'}"


def _build_from_models(database_url: str) -> None:
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    engine.dispose()


def test_a_schema_built_from_the_models_reports_nothing(database_url):
    _build_from_models(database_url)

    assert check_migration_drift.find_drift(database_url) == []


def test_a_missing_table_is_drift(database_url):
    _build_from_models(database_url)
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE audit_logs"))
    engine.dispose()

    diffs = check_migration_drift.find_drift(database_url)

    # This is the failure the check exists for: a model the migrations
    # never created, which the unit suite cannot see because it builds its
    # schema from the models in the first place.
    assert diffs != []
    assert "audit_logs" in str(diffs)


def test_a_missing_column_is_drift(database_url):
    _build_from_models(database_url)
    engine = create_engine(database_url)
    with engine.begin() as connection:
        # SQLite can drop a column in recent versions; recreating the table
        # without it is the portable way to stage the same situation.
        connection.execute(text("ALTER TABLE audit_logs DROP COLUMN ip_address"))
    engine.dispose()

    assert "ip_address" in str(check_migration_drift.find_drift(database_url))


def test_alembics_own_table_is_not_reported(database_url):
    _build_from_models(database_url)
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
        )
    engine.dispose()

    # It is not in the models and never should be, so every comparison would
    # otherwise report it as an extra table and the check would never pass.
    assert check_migration_drift.find_drift(database_url) == []


def test_an_unmigrated_extra_table_is_drift(database_url):
    _build_from_models(database_url)
    engine = create_engine(database_url)
    extra = MetaData()
    Table("left_behind", extra, Column("id", String(8), primary_key=True))
    extra.create_all(engine)
    engine.dispose()

    # A table a downgrade failed to remove is drift in the other direction,
    # and is what the "upgrade, downgrade, upgrade again" CI sequence is
    # trying to catch.
    assert "left_behind" in str(check_migration_drift.find_drift(database_url))


def test_the_command_succeeds_on_a_matching_schema(database_url, capsys):
    _build_from_models(database_url)

    assert check_migration_drift.main(["--database-url", database_url]) == 0
    assert "agree" in capsys.readouterr().out


def test_the_command_fails_loudly_on_drift(database_url, capsys):
    _build_from_models(database_url)
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE audit_logs"))
    engine.dispose()

    exit_code = check_migration_drift.main(["--database-url", database_url])

    captured = capsys.readouterr()
    # It runs unattended in CI, so the output has to say what to do about
    # it as well as what is wrong.
    assert exit_code == 1
    assert "audit_logs" in captured.err
    assert "Add the migration" in captured.err


def test_type_comparison_is_off_unless_asked_for(database_url):
    """The same column can be spelled differently without either side being wrong.

    A model's `Mapped[uuid.UUID]` and a migration's `postgresql.UUID`
    describe one Postgres column, and a check that reports every such pair
    is one somebody switches off within the week. The structural comparison
    — a table or a column one side has and the other does not — is the part
    that is unambiguous, so it is the part that runs by default.
    """
    _build_from_models(database_url)
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE audit_logs ADD COLUMN spare INTEGER"))
    engine.dispose()

    # Structural drift is still reported with types off.
    assert "spare" in str(check_migration_drift.find_drift(database_url))
    assert (
        "spare"
        in str(check_migration_drift.find_drift(database_url, compare_types=True))
    )
