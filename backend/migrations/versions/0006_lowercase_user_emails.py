"""fold existing user emails to lowercase

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-19

Addresses are normalised at the API edge from this release on. Rows written
before it may hold mixed case, and those accounts would stop matching at
login once lookups start comparing against the folded form — so the stored
data has to be folded too.

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    connection = op.get_bind()

    # Fold first, ask questions later is exactly wrong here: if two rows
    # differ only by case, the UPDATE would collide with the unique index
    # and abort mid-migration with a constraint error that says nothing
    # about which accounts are involved. Report them instead, and let a
    # human decide which one keeps the address.
    duplicates = connection.execute(
        sa.text(
            """
            SELECT lower(email) AS folded, count(*) AS total
            FROM users
            GROUP BY lower(email)
            HAVING count(*) > 1
            """
        )
    ).fetchall()

    if duplicates:
        listed = ", ".join(f"{row.folded} ({row.total} accounts)" for row in duplicates)
        raise RuntimeError(
            "Cannot fold user emails to lowercase: these addresses are held by "
            f"more than one account and would collide — {listed}. Merge or "
            "delete the duplicates, then run this migration again."
        )

    connection.execute(
        sa.text("UPDATE users SET email = lower(email) WHERE email <> lower(email)")
    )


def downgrade() -> None:
    # Irreversible by nature: the original casing is gone once folded, and
    # inventing one back would be worse than leaving the addresses as they
    # are. Nothing depends on the old spelling, so this is a no-op.
    pass
