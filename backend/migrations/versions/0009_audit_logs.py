"""add audit_logs table

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-28

Security-relevant events had nowhere to live. `usage_events` records what
the product does — successfully, and only while ANALYTICS_ENABLED is on —
which makes it the wrong table for a failed login, an admin changing
somebody's role, or the IP a session was started from. Those need to be
readable by the account's owner, to survive an analytics kill-switch, and to
keep the refusals as well as the successes.

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        # Nullable: a failed login against an unregistered address has no
        # account to point at, and neither does the row an erasure leaves.
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
        # SET NULL, not CASCADE: deleting an admin must not erase the record
        # of what they did to other people's accounts.
        sa.Column(
            "actor_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column(
            "outcome", sa.String(16), nullable=False, server_default="success"
        ),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.String(255), nullable=True),
        # JSON rather than JSONB: this column is read back alongside its row
        # and never queried into, so the indexing JSONB pays for is wasted.
        sa.Column("context", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    # The owner-facing page: this account's trail, newest first.
    op.create_index(
        "ix_audit_logs_user_id_created_at", "audit_logs", ["user_id", "created_at"]
    )
    # The incident query — "every failed login in the last hour" — which
    # spans accounts and so cannot use the index above.
    op.create_index(
        "ix_audit_logs_action_created_at", "audit_logs", ["action", "created_at"]
    )
    # The unfiltered admin feed, and the retention prune's cutoff scan.
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])


def downgrade() -> None:
    # Dropping the table loses history that cannot be reconstructed, which
    # is the cost of rolling back past the migration that started keeping
    # it. Nothing authenticates against these rows, so a downgrade weakens
    # the record rather than opening a door.
    op.drop_index("ix_audit_logs_created_at", table_name="audit_logs")
    op.drop_index("ix_audit_logs_action_created_at", table_name="audit_logs")
    op.drop_index("ix_audit_logs_user_id_created_at", table_name="audit_logs")
    op.drop_table("audit_logs")
