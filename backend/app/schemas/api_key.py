import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.api_key import MAX_NAME_LENGTH
from app.services.api_key_service import MAX_EXPIRY_DAYS


class ApiKeyCreate(BaseModel):
    """Request to mint a key.

    The name is required rather than optional: an unnamed key is one nobody
    dares revoke later, because no one remembers what it was for.
    """

    name: str = Field(min_length=1, max_length=MAX_NAME_LENGTH)
    expires_in_days: int | None = Field(default=None, ge=1, le=MAX_EXPIRY_DAYS)

    @field_validator("name")
    @classmethod
    def _require_visible_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("name cannot be blank")
        return stripped


class ApiKeyRead(BaseModel):
    """A key as it can safely be shown again: metadata, never the secret."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    prefix: str
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime | None
    revoked_at: datetime | None
    # Derived from the model rather than stored, so "expired" needs no
    # background job to become true.
    is_active: bool


class ApiKeyCreated(ApiKeyRead):
    """The one response that carries the secret.

    Separate from ApiKeyRead so the field can't be returned by accident from
    the listing endpoint: showing it once is a decision, not a default.
    """

    key: str = Field(description="The raw secret. Shown once and never retrievable.")
