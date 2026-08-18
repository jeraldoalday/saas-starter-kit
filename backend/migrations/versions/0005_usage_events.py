"""add usage_events table

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-19

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "usage_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        # Nullable so pre-signup events survive; CASCADE so an account
        # deletion takes its event history with it.
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("source", sa.String(16), nullable=False, server_default="backend"),
        sa.Column("properties", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_usage_events_user_id_occurred_at", "usage_events", ["user_id", "occurred_at"]
    )
    op.create_index(
        "ix_usage_events_name_occurred_at", "usage_events", ["name", "occurred_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_usage_events_name_occurred_at", table_name="usage_events")
    op.drop_index("ix_usage_events_user_id_occurred_at", table_name="usage_events")
    op.drop_table("usage_events")
