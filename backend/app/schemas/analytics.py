import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.usage_event import MAX_EVENT_NAME_LENGTH

# Dotted, lowercase, namespaced: "billing.checkout_started". Enforcing a
# shape at the edge is what keeps the name column's cardinality bounded -
# without it, a typo or an interpolated id becomes a permanent new series.
EVENT_NAME_PATTERN = r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)*$"

# Properties are context for reading an event back, not a document store.
MAX_PROPERTY_KEYS = 12
MAX_PROPERTY_VALUE_LENGTH = 200

ScalarProperty = str | int | float | bool | None


class EventIngest(BaseModel):
    """One event submitted by a client."""

    name: str = Field(
        min_length=1, max_length=MAX_EVENT_NAME_LENGTH, pattern=EVENT_NAME_PATTERN
    )
    properties: dict[str, ScalarProperty] = Field(default_factory=dict)

    @field_validator("properties")
    @classmethod
    def _bound_properties(
        cls, value: dict[str, ScalarProperty]
    ) -> dict[str, ScalarProperty]:
        if len(value) > MAX_PROPERTY_KEYS:
            raise ValueError(f"at most {MAX_PROPERTY_KEYS} properties per event")

        for key, item in value.items():
            if isinstance(item, str) and len(item) > MAX_PROPERTY_VALUE_LENGTH:
                raise ValueError(
                    f"property {key!r} exceeds {MAX_PROPERTY_VALUE_LENGTH} characters"
                )
        return value


class EventBatchIngest(BaseModel):
    """A flush of the client's event buffer.

    Client-supplied timestamps are deliberately absent: the server stamps
    occurred_at itself, so a wrong clock (or a forged one) can't move an
    event into a window it did not happen in.
    """

    events: list[EventIngest] = Field(min_length=1)


class IngestResponse(BaseModel):
    """What actually happened to a submitted batch.

    Three numbers rather than one because a batch can be partly accepted:
    the plan's monthly allowance clips an over-budget flush instead of
    refusing it, so a client that only read a 202 would believe it had
    delivered events that were never stored.
    """

    recorded: int = Field(
        description="How many events were stored. Zero when analytics is off."
    )
    dropped: int = Field(
        default=0,
        description=(
            "How many were turned away because the monthly allowance ran "
            "out mid-batch. The client should stop flushing until it resets."
        ),
    )
    remaining: int | None = Field(
        default=None,
        description=(
            "Events left in this month's allowance, or null on a plan with "
            "no ceiling."
        ),
    )


class DailyCountRead(BaseModel):
    day: date
    count: int


class UsageSummaryRead(BaseModel):
    window_days: int
    total_events: int
    active_users: int
    events_by_name: dict[str, int]
    daily: list[DailyCountRead]


class UsageEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID | None
    name: str
    source: str
    properties: dict[str, Any]
    occurred_at: datetime
