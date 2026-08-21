import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SessionRead(BaseModel):
    """A signed-in device as it can safely be shown back to its owner.

    Carries no token material — not even a prefix. Unlike an API key, whose
    prefix is how someone tells two keys apart in a list, a session is
    identified by where and when it signed in, so there is nothing to gain
    from exposing any part of the secret.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    last_used_at: datetime
    expires_at: datetime
    revoked_at: datetime | None
    user_agent: str | None
    ip_address: str | None
    # Derived from the model rather than stored, so "expired" needs no
    # background job to become true.
    is_active: bool


class RevokeAllRequest(BaseModel):
    """Ask for every session to end, optionally sparing the caller's own.

    The caller authenticates with an access token, which carries no session
    identity — deliberately, since access tokens are stateless. So a client
    that wants "sign out my *other* devices" has to say which session is its
    own, and the only way it can prove that is by presenting the refresh
    token it holds.

    Omitting it means "sign out everywhere, including me", which is the
    right default for the button someone presses after losing a laptop.
    """

    keep_current_refresh_token: str | None = Field(
        default=None,
        description=(
            "The caller's own refresh token. Its session is left alone; "
            "omit to sign out every device including this one."
        ),
    )


class RevokeAllResponse(BaseModel):
    revoked: int = Field(description="How many live sessions were ended.")
