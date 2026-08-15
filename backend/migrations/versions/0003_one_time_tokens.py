"""single-use email tokens for verification and password reset

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-15

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "one_time_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_one_time_tokens_token_hash", "one_time_tokens", ["token_hash"], unique=True
    )
    op.create_index("ix_one_time_tokens_user_id", "one_time_tokens", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_one_time_tokens_user_id", table_name="one_time_tokens")
    op.drop_index("ix_one_time_tokens_token_hash", table_name="one_time_tokens")
    op.drop_table("one_time_tokens")
