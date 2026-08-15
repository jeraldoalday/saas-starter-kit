import uuid

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.core.security import MAX_PASSWORD_BYTES, MIN_PASSWORD_LENGTH
from app.models.role import Role


class UserCreate(BaseModel):
    email: EmailStr
    # max_length counts characters while bcrypt's limit is bytes, so this is a
    # first-line guard; hash_password() enforces the real byte limit.
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_BYTES)
    full_name: str | None = None


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    full_name: str | None
    is_active: bool
    is_verified: bool
    role: str


class UserRoleUpdate(BaseModel):
    role: Role


class DeleteAccountRequest(BaseModel):
    # Re-authentication, so a stolen access token alone can't delete an account.
    password: str
