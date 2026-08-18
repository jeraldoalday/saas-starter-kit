from pydantic import BaseModel, Field

from app.core.security import MAX_PASSWORD_BYTES, MIN_PASSWORD_LENGTH
from app.schemas.fields import NormalizedEmail


class LoginRequest(BaseModel):
    email: NormalizedEmail
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class VerifyEmailRequest(BaseModel):
    token: str


class ForgotPasswordRequest(BaseModel):
    email: NormalizedEmail


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(
        min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_BYTES
    )


class MessageResponse(BaseModel):
    detail: str
