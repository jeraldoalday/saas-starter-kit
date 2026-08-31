import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.role import Role

if TYPE_CHECKING:
    from app.models.api_key import ApiKey
    from app.models.auth_session import AuthSession
    from app.models.subscription import Subscription
    from app.models.webhook_endpoint import WebhookEndpoint


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    role: Mapped[str] = mapped_column(
        String(32), default=Role.MEMBER.value, server_default=Role.MEMBER.value
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    subscription: Mapped["Subscription | None"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    # Cascade so deleting an account takes its credentials with it; without
    # it the rows would outlive the user and their key would keep working
    # right up until the foreign key complained.
    api_keys: Mapped[list["ApiKey"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    # Same reasoning as api_keys: a deleted account must not leave behind
    # rows that would still authenticate a refresh.
    sessions: Mapped[list["AuthSession"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    # Same again, with one extra consequence: an endpoint outliving its
    # account would keep a queue of deliveries pointed at a URL nobody is
    # left to answer for, signed with a secret nobody can rotate.
    webhook_endpoints: Mapped[list["WebhookEndpoint"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
