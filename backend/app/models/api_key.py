import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.user import User

# Shown in the UI so a human can tell two keys apart ("sk_live_8f2a…").
# Long enough to be recognisable, short enough to be useless on its own.
PREFIX_LENGTH = 12

# A label the owner chooses ("CI deploy", "Zapier"). Bounded because it is
# rendered straight back into the dashboard.
MAX_NAME_LENGTH = 64


class ApiKey(Base):
    """A long-lived credential for calling the API without a browser session.

    Only the SHA-256 digest of the secret is stored, and the raw value is
    returned exactly once at creation — the same bargain as
    :class:`app.models.token.OneTimeToken`, for the same reason: a database
    leak should not hand the attacker working credentials.

    SHA-256 rather than bcrypt here on purpose. Bcrypt's cost exists to slow
    down guessing a *human-chosen* password; an API key is 32 bytes from
    `secrets`, so there is nothing to guess, and paying ~100ms of KDF on
    every authenticated API call would be a self-inflicted rate limit. The
    digest is also what makes lookup a single indexed query rather than a
    scan that bcrypt-compares every row.
    """

    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(MAX_NAME_LENGTH), nullable=False)
    # Not unique: a prefix collision is possible and harmless, since it is a
    # display aid rather than a lookup key.
    prefix: Mapped[str] = mapped_column(String(PREFIX_LENGTH), nullable=False)
    token_hash: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    # Updated lazily on use (see api_key_service.LAST_USED_RESOLUTION): the
    # point is answering "is this key still in use?", not per-request audit.
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Null means "no expiry". An optional lifetime is what lets a key handed
    # to a contractor stop working without anyone remembering to revoke it.
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Revocation is a tombstone rather than a delete, so a leaked key stays
    # visibly listed as revoked instead of quietly vanishing from the audit
    # trail.
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user: Mapped["User"] = relationship(back_populates="api_keys")

    __table_args__ = (
        # The listing query: this account's keys, newest first.
        Index("ix_api_keys_user_id_created_at", "user_id", "created_at"),
    )

    def is_usable(self, now: datetime | None = None) -> bool:
        """Whether this key may authenticate a request right now."""
        if self.revoked_at is not None:
            return False
        if self.expires_at is None:
            return True

        now = now or datetime.now(UTC)
        expires_at = self.expires_at
        # SQLite hands back naive datetimes; treat them as UTC so the
        # comparison below doesn't raise.
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        return expires_at > now

    @property
    def is_active(self) -> bool:
        return self.is_usable()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ApiKey {self.prefix}… user={self.user_id} revoked={self.revoked_at}>"
