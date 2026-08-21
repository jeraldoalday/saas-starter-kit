import uuid
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.timeutils import as_utc
from app.database import Base


class TokenPurpose(StrEnum):
    EMAIL_VERIFICATION = "email_verification"
    PASSWORD_RESET = "password_reset"


class OneTimeToken(Base):
    """A single-use, expiring token delivered by email.

    Only the SHA-256 hash of the token is stored. The raw value exists just
    long enough to go into an email, so a database leak doesn't hand the
    attacker working verification or password-reset links.
    """

    __tablename__ = "one_time_tokens"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    token_hash: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    def is_usable(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(UTC)
        return self.used_at is None and as_utc(self.expires_at) > now
