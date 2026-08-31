"""add webhook_endpoints and webhook_deliveries tables

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-31

Outbound webhooks: the URLs an account asks us to notify, and the queue of
things waiting to be sent to them.

Two tables rather than one because they have different lifetimes and
different owners. An endpoint is configuration a customer edits perhaps
twice a year; a delivery is a row per event per subscriber, written at the
rate the product emits events and pruned on a retention window. Storing the
attempt history on the endpoint would mean either one row overwritten (no
history) or an unbounded column (no prune).

`processed_webhook_events` already exists from 0002 and is the mirror image
of these: it records the Stripe webhooks that arrive *here*, so that
replaying one cannot double-apply it. Nothing about the two shares a shape
beyond the word — inbound is idempotency, outbound is a delivery queue.

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "webhook_endpoints",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        # CASCADE: an endpoint that outlived its account would hold a queue
        # aimed at a URL nobody is left to answer for, signed with a secret
        # nobody can rotate.
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("description", sa.String(120), nullable=False),
        # Readable rather than hashed, unlike api_keys.token_hash: this
        # secret signs our requests to the customer, so the plaintext has to
        # exist at send time. See the model for the full argument.
        sa.Column("secret", sa.String(64), nullable=False),
        # JSON rather than JSONB, and rather than a join table: read whole
        # on every dispatch, never queried into.
        sa.Column("event_types", sa.JSON(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        # Distinct from is_active on purpose: "the owner switched it off"
        # and "delivery gave up on it" want different words in the UI.
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disabled_reason", sa.String(255), nullable=True),
        sa.Column(
            "consecutive_failures", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failure_at", sa.DateTime(timezone=True), nullable=True),
    )
    # The listing query: this account's endpoints, newest first.
    op.create_index(
        "ix_webhook_endpoints_user_id_created_at",
        "webhook_endpoints",
        ["user_id", "created_at"],
    )

    op.create_table(
        "webhook_deliveries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "endpoint_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("webhook_endpoints.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        # Null once terminal, where "next" means nothing.
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_status_code", sa.Integer(), nullable=True),
        sa.Column("last_error", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
    )
    # The worker's claim query: pending and due, oldest first. Status leads
    # so the scan stays off the delivered rows, which are almost all of the
    # table on a healthy deployment.
    op.create_index(
        "ix_webhook_deliveries_status_next_attempt",
        "webhook_deliveries",
        ["status", "next_attempt_at"],
    )
    # The owner-facing log for one endpoint.
    op.create_index(
        "ix_webhook_deliveries_endpoint_id_created_at",
        "webhook_deliveries",
        ["endpoint_id", "created_at"],
    )
    # The retention prune's cutoff scan.
    op.create_index(
        "ix_webhook_deliveries_created_at", "webhook_deliveries", ["created_at"]
    )


def downgrade() -> None:
    # Deliveries first: they hold the foreign key. Dropping these loses the
    # queue, which unlike the audit trail is a real operational loss rather
    # than a historical one — anything pending at the moment of the rollback
    # is never sent, and the customer's handler simply never runs for it.
    op.drop_index("ix_webhook_deliveries_created_at", table_name="webhook_deliveries")
    op.drop_index(
        "ix_webhook_deliveries_endpoint_id_created_at", table_name="webhook_deliveries"
    )
    op.drop_index(
        "ix_webhook_deliveries_status_next_attempt", table_name="webhook_deliveries"
    )
    op.drop_table("webhook_deliveries")

    op.drop_index(
        "ix_webhook_endpoints_user_id_created_at", table_name="webhook_endpoints"
    )
    op.drop_table("webhook_endpoints")
