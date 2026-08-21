"""add api_keys table

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-21

Programmatic access previously required minting a JWT through the login
endpoint and refreshing it every half hour, which is workable for a browser
and hostile to a cron job. This table backs long-lived keys instead: hashed
at rest, individually revocable, optionally expiring.

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "api_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("prefix", sa.String(12), nullable=False),
        # Uniqueness is carried by the index below rather than by a column
        # constraint: Postgres would otherwise build one index for the
        # constraint and a second identical one for the explicit index.
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_api_keys_user_id", "api_keys", ["user_id"])
    # Unique: the digest is the lookup key on every authenticated API call,
    # so it has to be indexed, and two rows sharing one would mean two
    # accounts answering to the same secret.
    op.create_index("ix_api_keys_token_hash", "api_keys", ["token_hash"], unique=True)
    op.create_index("ix_api_keys_user_id_created_at", "api_keys", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_api_keys_user_id_created_at", table_name="api_keys")
    op.drop_index("ix_api_keys_token_hash", table_name="api_keys")
    op.drop_index("ix_api_keys_user_id", table_name="api_keys")
    op.drop_table("api_keys")
