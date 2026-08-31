import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# Bounded because the value is rendered straight back into the dashboard and
# indexed alongside a timestamp; a free-form action name would let one caller
# blow up the index's cardinality.
MAX_ACTION_LENGTH = 64

# Wide enough for an IPv6 address with an embedded IPv4 suffix, matching
# app.models.auth_session.
MAX_IP_LENGTH = 45

MAX_USER_AGENT_LENGTH = 255


class AuditAction(StrEnum):
    """The security-relevant things that can happen to an account.

    A closed enum rather than the free-form strings `usage_events` uses. An
    audit trail is read by someone reconstructing an incident, and a typo'd
    action name is a row they will never find; product analytics can afford
    a new name without a code change, this cannot.
    """

    LOGIN = "auth.login"
    # The one the analytics table deliberately does not carry: a failed
    # login is a security signal rather than a product one, and until this
    # table existed it reached only the rate limiter and the log.
    LOGIN_FAILED = "auth.login_failed"
    LOGIN_BLOCKED = "auth.login_blocked"
    LOGOUT = "auth.logout"
    PASSWORD_RESET_REQUESTED = "auth.password_reset_requested"
    PASSWORD_RESET_COMPLETED = "auth.password_reset_completed"
    EMAIL_VERIFIED = "auth.email_verified"

    ACCOUNT_REGISTERED = "account.registered"
    ACCOUNT_DELETED = "account.deleted"
    ACCOUNT_EXPORTED = "account.exported"
    ROLE_CHANGED = "account.role_changed"
    STATUS_CHANGED = "account.status_changed"

    API_KEY_CREATED = "api_key.created"
    API_KEY_REVOKED = "api_key.revoked"

    SESSION_REVOKED = "session.revoked"
    SESSIONS_REVOKED_ALL = "session.revoked_all"

    # Outbound webhooks. The endpoint changes are here because registering
    # a URL to be notified at is a change to where the account's data goes;
    # the secret ones are here because that secret is what lets a receiver
    # tell our requests from a forgery, so revealing or replacing it is a
    # credential event even though it grants nothing on this system.
    WEBHOOK_ENDPOINT_CREATED = "webhook.endpoint_created"
    WEBHOOK_ENDPOINT_UPDATED = "webhook.endpoint_updated"
    WEBHOOK_ENDPOINT_DELETED = "webhook.endpoint_deleted"
    WEBHOOK_SECRET_ROTATED = "webhook.secret_rotated"
    WEBHOOK_SECRET_REVEALED = "webhook.secret_revealed"


class AuditOutcome(StrEnum):
    """Whether the attempt succeeded.

    Kept as its own column rather than folded into the action name, so
    "everything that happened to this account" stays one index scan and
    "everything that was *refused*" is a filter rather than a set of names
    the reader has to know in advance.
    """

    SUCCESS = "success"
    FAILURE = "failure"


class AuditLog(Base):
    """One security-relevant event, append-only.

    Distinct from :class:`app.models.usage_event.UsageEvent` on purpose,
    even though both are append-only event tables, because they answer
    different questions for different readers:

    * Usage events answer "how is the product doing" for the team. They are
      best-effort by design — `ANALYTICS_ENABLED` can switch recording off
      entirely and a failed insert is swallowed — and they record successes,
      because a funnel is built out of things that worked.
    * Audit rows answer "who did what to my account, and from where" for the
      account's owner and for whoever is reconstructing an incident. They
      record the refusals too, they carry the caller's IP and User-Agent so
      an unfamiliar sign-in is recognisable as one, and they are not
      switchable off by the flag that governs product analytics.

    Collapsing the two would mean either exposing the product's analytics
    table to end users or letting an analytics kill-switch silently disable
    the security trail. Neither is a trade worth making to save a table.

    Rows cascade away with the account, like usage events, because the
    erasure promise in `account_service` has to mean what it says. The
    deletion itself is recorded as an actor-less row so that churn — and the
    fact that an erasure happened — survives the account it describes.
    """

    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    # The account the event is *about*. Nullable because some events have no
    # account to attach to: a failed login against an address that was never
    # registered, or the tombstone left behind by an erasure.
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    # Who caused it, when that is someone other than the subject — an admin
    # deactivating a member, say. Null when the account acted on itself,
    # which is the common case and is left implicit rather than duplicated.
    #
    # SET NULL rather than CASCADE, unlike user_id: deleting an admin's
    # account must not quietly erase the record of what that admin did to
    # *other* people's accounts. The action stays, the attribution goes.
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    action: Mapped[str] = mapped_column(String(MAX_ACTION_LENGTH), nullable=False)
    outcome: Mapped[str] = mapped_column(
        String(16), nullable=False, default=AuditOutcome.SUCCESS.value
    )

    # Both client-supplied and neither trustworthy on its own (see
    # app/api/request_info.py). They are here because "signed in from an IP
    # in another country" is the single most useful thing an owner can spot
    # in their own trail, not because either is evidence.
    ip_address: Mapped[str | None] = mapped_column(
        String(MAX_IP_LENGTH), nullable=True
    )
    user_agent: Mapped[str | None] = mapped_column(
        String(MAX_USER_AGENT_LENGTH), nullable=True
    )

    # Small, denormalised specifics: which role was granted, how many
    # sessions a bulk revoke ended. Never credentials, and never the email
    # address a failed login was attempted against — see audit_service for
    # why that one is deliberately left out.
    context: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    __table_args__ = (
        # "This account's trail, newest first" — the owner-facing page.
        Index("ix_audit_logs_user_id_created_at", "user_id", "created_at"),
        # "Every failed login in the last hour" — the incident query, which
        # spans accounts and so cannot use the index above.
        Index("ix_audit_logs_action_created_at", "action", "created_at"),
        # The unfiltered admin feed, and the retention prune's cutoff scan.
        Index("ix_audit_logs_created_at", "created_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<AuditLog {self.action} outcome={self.outcome} "
            f"user={self.user_id} at={self.created_at}>"
        )
