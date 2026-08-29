import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.audit_log import AuditAction, AuditOutcome


class AuditEntryRead(BaseModel):
    """One trail entry as it can safely be shown back to its subject.

    Carries no identifier for whoever caused it — only whether it was
    somebody else. Naming the admin would hand an internal identifier to a
    member who has no claim to it, which is the same line the data export
    draws for the same reason.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    action: str
    outcome: str
    ip_address: str | None
    user_agent: str | None
    context: dict[str, Any]
    created_at: datetime
    by_another_account: bool = Field(
        description=(
            "Whether an account other than this one caused the event — an "
            "administrator, typically."
        )
    )

    @classmethod
    def from_entry(cls, entry) -> "AuditEntryRead":
        return cls(
            id=entry.id,
            action=entry.action,
            outcome=entry.outcome,
            ip_address=entry.ip_address,
            user_agent=entry.user_agent,
            context=entry.context,
            created_at=entry.created_at,
            by_another_account=entry.actor_user_id is not None,
        )


class AdminAuditEntryRead(AuditEntryRead):
    """The same entry with both parties named, for an administrator.

    The extra fields are the whole point of the admin view: correlating one
    person's actions across accounts is what reconstructing an incident
    consists of, and it cannot be done from a subject-scoped trail.
    """

    user_id: uuid.UUID | None
    actor_user_id: uuid.UUID | None

    @classmethod
    def from_entry(cls, entry) -> "AdminAuditEntryRead":
        return cls(
            id=entry.id,
            action=entry.action,
            outcome=entry.outcome,
            ip_address=entry.ip_address,
            user_agent=entry.user_agent,
            context=entry.context,
            created_at=entry.created_at,
            by_another_account=entry.actor_user_id is not None,
            user_id=entry.user_id,
            actor_user_id=entry.actor_user_id,
        )


class AuditPage(BaseModel):
    """A page of trail, with the total behind it.

    The total is what lets a client show "showing 50 of 812" and know
    whether another page exists. Computed with the same filters as the page
    itself, so the two can never disagree about what is being counted.
    """

    entries: list[AuditEntryRead]
    total: int
    limit: int
    offset: int
    history_days: int | None = Field(
        default=None,
        description=(
            "How far back this account's plan lets it read, in days, or "
            "null on a plan with no limit. Served so the interface can say "
            "why the trail stops where it does — an account that sees 30 "
            "days of history and no explanation assumes the older rows were "
            "deleted."
        ),
    )


class AdminAuditPage(BaseModel):
    entries: list[AdminAuditEntryRead]
    total: int
    limit: int
    offset: int


class AuditSummary(BaseModel):
    """Deployment-wide security activity over a window."""

    window_days: int
    total_entries: int
    failed_logins: int = Field(
        description="Refused sign-ins in the window, across every account."
    )
    entries_by_action: dict[str, int]


# Exposed so a client can build a filter control without hardcoding the
# list, and so the two spellings cannot drift apart.
KNOWN_ACTIONS = [action.value for action in AuditAction]
KNOWN_OUTCOMES = [outcome.value for outcome in AuditOutcome]
