"""add auth_sessions table

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-22

Refresh tokens were stateless: signed, long-lived, and impossible to stop.
A leaked one stayed valid for REFRESH_TOKEN_EXPIRE_DAYS regardless of what
the account did afterwards — a password reset did not touch it, and there
was no sign-out to invalidate it because there was nothing to invalidate.
This table gives every refresh token a row, so revocation is an UPDATE
rather than a global key rotation.

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "auth_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Uniqueness is carried by the index below rather than by a column
        # constraint, so Postgres builds one index instead of two identical
        # ones — the same choice 0007 made for api_keys.token_hash.
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("user_agent", sa.String(255), nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    # Unique: the digest is the lookup key on every refresh, and two rows
    # sharing one would mean two sessions answering to the same token.
    op.create_index(
        "ix_auth_sessions_token_hash", "auth_sessions", ["token_hash"], unique=True
    )
    op.create_index(
        "ix_auth_sessions_user_id_created_at", "auth_sessions", ["user_id", "created_at"]
    )


def downgrade() -> None:
    # Dropping the table makes every outstanding refresh token unusable,
    # because the code that survives a downgrade still requires a row. That
    # signs everyone out; it does not let anyone in, which is the direction
    # a rollback should fail in.
    op.drop_index("ix_auth_sessions_user_id_created_at", table_name="auth_sessions")
    op.drop_index("ix_auth_sessions_token_hash", table_name="auth_sessions")
    op.drop_index("ix_auth_sessions_user_id", table_name="auth_sessions")
    op.drop_table("auth_sessions")
