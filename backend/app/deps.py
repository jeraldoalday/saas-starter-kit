import uuid

from fastapi import Depends, HTTPException, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import TokenType, decode_token
from app.database import get_db
from app.models.role import Role, satisfies
from app.models.user import User
from app.services.api_key_service import KEY_PREFIX, authenticate_api_key

bearer_scheme = HTTPBearer(auto_error=False)

# A dedicated header as well as `Authorization: Bearer sk_live_...`, because
# both conventions are in the wild and a caller shouldn't have to read the
# source to find out which one this API picked.
API_KEY_HEADER_NAME = "X-API-Key"
api_key_scheme = APIKeyHeader(name=API_KEY_HEADER_NAME, auto_error=False)


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _user_from_access_token(token: str, db: Session) -> User | None:
    payload = decode_token(token)
    if payload is None or payload.get("type") != TokenType.ACCESS.value:
        return None

    try:
        user_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError):
        return None

    user = db.get(User, user_id)
    if user is None or not user.is_active:
        return None

    return user


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    """Require a browser session (a JWT access token).

    Routes that manage credentials themselves stay on this rather than on
    get_api_or_session_user, so a leaked API key cannot mint more keys.
    """
    if credentials is None:
        raise _unauthorized()

    user = _user_from_access_token(credentials.credentials, db)
    if user is None:
        raise _unauthorized()

    return user


def get_api_or_session_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    api_key: str | None = Depends(api_key_scheme),
    db: Session = Depends(get_db),
) -> User:
    """Accept either a session JWT or an API key.

    Order matters only for the error message: an `Authorization` header that
    carries a key prefix is treated as a key, so a caller who put their key
    in the conventional place isn't told their JWT is malformed.
    """
    presented_key = api_key
    if presented_key is None and credentials is not None:
        if credentials.credentials.startswith(KEY_PREFIX):
            presented_key = credentials.credentials

    if presented_key is not None:
        user = authenticate_api_key(db, presented_key)
        if user is None:
            raise _unauthorized()
        return user

    return get_current_user(credentials, db)


class RequireRole:
    """Dependency requiring at least `minimum_role`.

    Roles are ranked, so a route asks for the least privilege that will do
    and anything higher is admitted automatically.
    """

    def __init__(self, minimum_role: Role) -> None:
        self.minimum_role = minimum_role

    def __call__(self, current_user: User = Depends(get_current_user)) -> User:
        if not satisfies(current_user.role, self.minimum_role):
            # 403 not 404: the caller is authenticated, just not permitted.
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires {self.minimum_role.value} role or higher",
            )
        return current_user


require_admin = RequireRole(Role.ADMIN)
require_owner = RequireRole(Role.OWNER)


def get_verified_user(current_user: User = Depends(get_current_user)) -> User:
    """Require a confirmed email address.

    Gate routes that act on the outside world (billing, invites) on this
    rather than on get_current_user, so an unconfirmed address can't be used
    to drive them. REQUIRE_EMAIL_VERIFICATION turns the gate off for local
    development, where the console email transport is the only delivery.
    """
    if get_settings().require_email_verification and not current_user.is_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Email address must be verified first",
        )
    return current_user
