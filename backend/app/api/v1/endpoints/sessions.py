import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.api.request_info import request_origin
from app.database import get_db
from app.deps import get_current_user
from app.models.audit_log import AuditAction
from app.models.usage_event import EventName
from app.models.user import User
from app.schemas.session import RevokeAllRequest, RevokeAllResponse, SessionRead
from app.services.analytics_service import record_event
from app.services.audit_service import record as record_audit
from app.services.session_service import (
    SessionNotFoundError,
    authenticate,
    list_sessions,
    revoke_all,
    revoke_session,
)

# Gated on get_current_user (a session JWT) rather than on the key-or-session
# dependency, for the reason api_keys gives: a leaked API key can read and
# write the product, but it must not be able to sign the real owner out of
# every device and keep the account to itself.
router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.get("", response_model=list[SessionRead])
def list_account_sessions(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[SessionRead]:
    """This account's sessions, newest first, revoked ones included.

    Revoked rows stay in the list on purpose: after an incident, "that
    device was signed out on the 3rd" is the answer someone is looking for.
    """
    return list_sessions(db, current_user)


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_one(
    session_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """Sign one device out. Idempotent, and irreversible by design.

    A caller may revoke the session they are currently using; there is no
    guard against it, because "this row is the one I'm holding" is not
    something an access token can establish, and refusing would break the
    legitimate case of ending a session from the device it belongs to.
    """
    try:
        revoke_session(db, current_user, session_id)
    except SessionNotFoundError as exc:
        # 404 whether the session belongs to someone else or doesn't exist:
        # confirming the former would leak other accounts' session ids.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Session not found"
        ) from exc

    record_event(db, EventName.SESSION_REVOKED, user=current_user)
    record_audit(
        db,
        AuditAction.SESSION_REVOKED,
        user=current_user,
        context={"session_id": str(session_id)},
        **request_origin(request),
    )


@router.post("/revoke-all", response_model=RevokeAllResponse)
def revoke_every_session(
    data: RevokeAllRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RevokeAllResponse:
    """Sign out everywhere — the button for "I lost my laptop".

    A caller who wants to stay signed in here sends the refresh token it
    holds, and that one session is spared. An unrecognised token is not an
    error: it simply spares nothing, which fails in the safe direction.
    """
    keep_session_id: uuid.UUID | None = None
    if data.keep_current_refresh_token is not None:
        session = authenticate(db, data.keep_current_refresh_token)
        # Only honour a token belonging to the caller. Without this check a
        # session id from another account could be passed in and, while it
        # could not be revoked from here anyway, the intent is worth
        # refusing explicitly.
        if session is not None and session.user_id == current_user.id:
            keep_session_id = session.id

    revoked = revoke_all(db, current_user, except_session_id=keep_session_id)

    record_event(
        db,
        EventName.SESSIONS_REVOKED_ALL,
        user=current_user,
        properties={"revoked": revoked, "kept_current": keep_session_id is not None},
    )
    # The count is the part worth keeping: "I signed out 4 devices" is
    # what makes a later "and one signed straight back in" legible.
    record_audit(
        db,
        AuditAction.SESSIONS_REVOKED_ALL,
        user=current_user,
        context={"revoked": revoked, "kept_current": keep_session_id is not None},
        **request_origin(request),
    )
    return RevokeAllResponse(revoked=revoked)
