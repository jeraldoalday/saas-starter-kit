"""add role column to users

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-15

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # server_default backfills existing rows; the column is NOT NULL from the
    # start because every user has exactly one role.
    op.add_column(
        "users",
        sa.Column(
            "role", sa.String(32), nullable=False, server_default="member"
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "role")
