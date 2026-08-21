import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps_rate_limit import api_key_create_rate_limit
from app.database import get_db
from app.deps import get_current_user
from app.models.usage_event import EventName
from app.models.user import User
from app.schemas.api_key import ApiKeyCreate, ApiKeyCreated, ApiKeyRead
from app.services.analytics_service import record_event
from app.services.api_key_service import (
    ApiKeyLimitExceededError,
    ApiKeyNotFoundError,
    create_api_key,
    list_api_keys,
    revoke_api_key,
)

# Deliberately gated on get_current_user (a session JWT) rather than on the
# key-or-session dependency the rest of the API uses: a leaked key can read
# and write the product, but it cannot mint further keys or revoke the ones
# that would let the owner lock it out.
router = APIRouter(prefix="/api-keys", tags=["api-keys"])


@router.get("", response_model=list[ApiKeyRead])
def list_keys(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ApiKeyRead]:
    """This account's keys, newest first, revoked ones included."""
    return list_api_keys(db, current_user)


@router.post(
    "",
    response_model=ApiKeyCreated,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(api_key_create_rate_limit)],
)
def create_key(
    data: ApiKeyCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiKeyCreated:
    """Mint a key and return it once.

    The response body is the only place the secret ever appears — it is not
    stored, so a client that drops it has to create another.
    """
    try:
        api_key, raw_key = create_api_key(
            db,
            current_user,
            name=data.name,
            expires_in_days=data.expires_in_days,
        )
    except ApiKeyLimitExceededError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc

    record_event(
        db,
        EventName.API_KEY_CREATED,
        user=current_user,
        properties={"expires": data.expires_in_days is not None},
    )
    return ApiKeyCreated(**ApiKeyRead.model_validate(api_key).model_dump(), key=raw_key)


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_key(
    key_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """Stop a key working. Idempotent, and irreversible by design."""
    try:
        revoke_api_key(db, current_user, key_id)
    except ApiKeyNotFoundError as exc:
        # 404 whether the key belongs to someone else or doesn't exist:
        # confirming the former would leak other accounts' key ids.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="API key not found"
        ) from exc

    record_event(db, EventName.API_KEY_REVOKED, user=current_user)
