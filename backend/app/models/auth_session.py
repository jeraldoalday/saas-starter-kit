import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.timeutils import as_utc
from app.database import Base

if TYPE_CHECKING:
    from app.models.user import User

# A browser's self-reported User-Agent, kept only so the owner can tell one
# row from another in "where am I signed in?". Truncated rather than
# rejected: it is decoration, and a header nobody validates should not be
# able to fail a login.
MAX_USER_AGENT_LENGTH = 255

# Wide enough for an IPv6 address with an embedded IPv4 suffix.
MAX_IP_LENGTH = 45


class AuthSession(Base):
    """One signed-in device, keyed by the refresh token it holds.

    Access tokens stay stateless — they live half an hour, and checking a
    database row on every request to save at most that much exposure is a
    bad trade. Refresh tokens are the opposite case: they live for weeks,
    they are what actually keeps someone signed in, and until this table
    existed there was no way to stop one. A stolen refresh token outlived a
    password reset, and "sign out my other devices" had nothing to act on.

    So the refresh token is still a JWT — same format, same signature check —
    but its digest is also a row here, and `authenticate` fails closed if
    that row is missing, revoked or expired. Revocation becomes a single
    UPDATE instead of a key rotation that signs out every account at once.

    Only the SHA-256 digest is stored, for the reason
    :class:`app.models.api_key.ApiKey` gives at length: a leaked database
    should hand an attacker a list of digests and no way to use them. The
    same reasoning about SHA-256 over bcrypt applies — the token is a
    high-entropy value from `secrets`, not a human-chosen password.
    """

    __tablename__ = "auth_sessions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # The digest of the *current* refresh token. Rotation overwrites it in
    # place, which is what retires the previous token: its digest is simply
    # no longer anywhere in the table.
    token_hash: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    # Written on every rotation, which is the only moment the session is
    # touched — unlike an API key there is no per-request write to debounce.
    last_used_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    # Mirrors the JWT's own exp. Duplicated rather than decoded on lookup so
    # expiry is a WHERE clause the prune job can use, and so a session stops
    # working even if the signing key's claims are ever widened.
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    # A tombstone, like a revoked API key: "signed out on the 3rd" is the
    # answer someone wants after an incident, and a deleted row cannot give
    # it.
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    user_agent: Mapped[str | None] = mapped_column(
        String(MAX_USER_AGENT_LENGTH), nullable=True
    )
    ip_address: Mapped[str | None] = mapped_column(String(MAX_IP_LENGTH), nullable=True)

    user: Mapped["User"] = relationship(back_populates="sessions")

    __table_args__ = (
        # The listing query: this account's sessions, newest first.
        Index("ix_auth_sessions_user_id_created_at", "user_id", "created_at"),
    )

    def is_usable(self, now: datetime | None = None) -> bool:
        """Whether this session may still mint an access token."""
        if self.revoked_at is not None:
            return False

        now = now or datetime.now(UTC)
        expires_at = as_utc(self.expires_at)
        return expires_at is not None and expires_at > now

    @property
    def is_active(self) -> bool:
        return self.is_usable()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<AuthSession {self.id} user={self.user_id} revoked={self.revoked_at}>"
