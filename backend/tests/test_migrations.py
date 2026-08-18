"""Guards on the Alembic revision graph.

Migrations are the one part of the codebase where a merge conflict can
resolve cleanly and still be wrong: two branches each adding a revision
that points at the same parent produces a valid file pair and a database
that can never be upgraded, because `alembic upgrade head` refuses to
choose between two heads. These tests fail at review time instead.
"""

from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

BACKEND_ROOT = Path(__file__).resolve().parents[1]
VERSIONS_DIR = BACKEND_ROOT / "migrations" / "versions"


@pytest.fixture(scope="module")
def script_directory() -> ScriptDirectory:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    return ScriptDirectory.from_config(config)


def test_there_is_exactly_one_head(script_directory):
    heads = script_directory.get_heads()

    assert len(heads) == 1, (
        f"Expected a single migration head, found {heads}. Two branches added a "
        "revision with the same parent; one of them needs its down_revision "
        "repointed at the other."
    )


def test_every_revision_is_reachable_from_the_head(script_directory):
    head = script_directory.get_current_head()
    reachable = {
        revision.revision
        for revision in script_directory.walk_revisions(base="base", head=head)
    }
    on_disk = {
        script_directory.get_revision(path.stem.split("_")[0]).revision
        for path in VERSIONS_DIR.glob("[0-9]*.py")
    }

    assert on_disk - reachable == set(), "Some migrations are orphaned from the chain"


def test_revision_ids_match_their_filenames(script_directory):
    """The numeric prefix is how a human reads the order; keep it honest."""
    for path in sorted(VERSIONS_DIR.glob("[0-9]*.py")):
        expected = path.stem.split("_")[0]
        assert script_directory.get_revision(expected).revision == expected


def test_every_revision_defines_a_downgrade(script_directory):
    """Even a deliberate no-op downgrade, so a rollback doesn't error."""
    for revision in script_directory.walk_revisions():
        module = revision.module
        assert hasattr(module, "downgrade"), f"{revision.revision} has no downgrade()"
