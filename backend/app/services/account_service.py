"""Account lifecycle: export what we hold, and delete it on request.

Both operations exist to answer data-subject requests (GDPR access and
erasure). Keep them in step with the schema: a new table holding personal
data needs a matching branch here, or the export quietly under-reports.
"""

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.core.security import verify_password
from app.models.api_key import ApiKey
from app.models.audit_log import AuditLog
from app.models.auth_session import AuthSession
from app.models.subscription import Subscription
from app.models.token import OneTimeToken
from app.models.usage_event import UsageEvent
from app.models.user import User
from app.models.webhook_endpoint import WebhookEndpoint

logger = logging.getLogger("app.account")


class InvalidPasswordError(Exception):
    pass


def export_user_data(db: Session, user: User) -> dict[str, Any]:
    """Everything we hold about this user, as JSON-serialisable data.

    Password hashes and token hashes are deliberately excluded: they are
    credentials, not user data, and exporting them would only widen the
    blast radius of a leaked export file.
    """
    subscription = user.subscription
    token_count = (
        db.query(OneTimeToken).filter(OneTimeToken.user_id == user.id).count()
    )
    events = (
        db.query(UsageEvent)
        .filter(UsageEvent.user_id == user.id)
        .order_by(UsageEvent.occurred_at.desc())
        .all()
    )
    api_keys = (
        db.query(ApiKey)
        .filter(ApiKey.user_id == user.id)
        .order_by(ApiKey.created_at.desc())
        .all()
    )
    sessions = (
        db.query(AuthSession)
        .filter(AuthSession.user_id == user.id)
        .order_by(AuthSession.created_at.desc())
        .all()
    )
    webhook_endpoints = (
        db.query(WebhookEndpoint)
        .filter(WebhookEndpoint.user_id == user.id)
        .order_by(WebhookEndpoint.created_at.desc())
        .all()
    )
    # Only rows where this account is the *subject*. Rows where they were
    # the actor describe what they did to somebody else's account, and
    # exporting those would put another person's identifiers into this
    # person's file — an access request is not a licence to read across.
    audit_entries = (
        db.query(AuditLog)
        .filter(AuditLog.user_id == user.id)
        .order_by(AuditLog.created_at.desc())
        .all()
    )

    return {
        "account": {
            "id": str(user.id),
            "email": user.email,
            "full_name": user.full_name,
            "role": user.role,
            "is_active": user.is_active,
            "is_verified": user.is_verified,
            "created_at": user.created_at.isoformat() if user.created_at else None,
        },
        "subscription": (
            {
                "status": subscription.status,
                "stripe_customer_id": subscription.stripe_customer_id,
                "current_period_end": (
                    subscription.current_period_end.isoformat()
                    if subscription.current_period_end
                    else None
                ),
                "created_at": (
                    subscription.created_at.isoformat()
                    if subscription.created_at
                    else None
                ),
            }
            if subscription is not None
            else None
        ),
        "pending_email_tokens": token_count,
        # Metadata only. The digest is a credential like the password hash,
        # so it is excluded for the same reason — and the raw secret was
        # never stored to begin with.
        "api_keys": [
            {
                "name": key.name,
                "prefix": key.prefix,
                "created_at": key.created_at.isoformat() if key.created_at else None,
                "last_used_at": (
                    key.last_used_at.isoformat() if key.last_used_at else None
                ),
                "expires_at": key.expires_at.isoformat() if key.expires_at else None,
                "revoked_at": key.revoked_at.isoformat() if key.revoked_at else None,
            }
            for key in api_keys
        ],
        # Sessions hold an IP address and a User-Agent per device, which is
        # personal data by any reading, and the table postdates the first
        # version of this export — exactly the drift the module docstring
        # warns about. Metadata only: the digest is a credential, excluded
        # for the same reason the password hash and the key digests are.
        "sessions": [
            {
                "created_at": (
                    session.created_at.isoformat() if session.created_at else None
                ),
                "last_used_at": (
                    session.last_used_at.isoformat() if session.last_used_at else None
                ),
                "expires_at": (
                    session.expires_at.isoformat() if session.expires_at else None
                ),
                "revoked_at": (
                    session.revoked_at.isoformat() if session.revoked_at else None
                ),
                "user_agent": session.user_agent,
                "ip_address": session.ip_address,
            }
            for session in sessions
        ],
        # The URLs this account asked us to send their data to. Held about
        # them by any reading, and the one part of the export that says
        # where their data has been going — which is exactly what a subject
        # access request is for. The signing secret is excluded: it is a
        # credential, like the password hash and the key digests, and the
        # owner can already read it from the endpoint that exists to serve
        # it. Deliveries themselves are not included; they are copies of
        # events already in this file, addressed to an endpoint listed here.
        "webhook_endpoints": [
            {
                "url": endpoint.url,
                "description": endpoint.description,
                "event_types": list(endpoint.event_types),
                "is_active": endpoint.is_active,
                "created_at": (
                    endpoint.created_at.isoformat() if endpoint.created_at else None
                ),
                "disabled_at": (
                    endpoint.disabled_at.isoformat() if endpoint.disabled_at else None
                ),
                "disabled_reason": endpoint.disabled_reason,
                "last_success_at": (
                    endpoint.last_success_at.isoformat()
                    if endpoint.last_success_at
                    else None
                ),
            }
            for endpoint in webhook_endpoints
        ],
        # The security trail is data held about them too, and it is the
        # part they are most likely to be asking for: every sign-in, from
        # which address, and every refusal.
        "audit_log": [
            {
                "action": entry.action,
                "outcome": entry.outcome,
                "ip_address": entry.ip_address,
                "user_agent": entry.user_agent,
                # A bare "somebody else did this" rather than who. Naming
                # the admin would hand out an internal identifier the
                # subject has no claim to.
                "by_another_account": entry.actor_user_id is not None,
                "context": entry.context,
                "created_at": entry.created_at.isoformat(),
            }
            for entry in audit_entries
        ],
        # Behaviour recorded about someone is data held about them, so the
        # export has to include it or it under-reports.
        "usage_events": [
            {
                "name": event.name,
                "source": event.source,
                "properties": event.properties,
                "occurred_at": event.occurred_at.isoformat(),
            }
            for event in events
        ],
    }


def delete_account(db: Session, user: User, password: str) -> None:
    """Permanently delete the user after re-authenticating them.

    Requiring the password means a stolen access token alone cannot destroy
    an account. Subscriptions and tokens go via ON DELETE CASCADE.

    Note this does NOT cancel a live Stripe subscription - Stripe is the
    system of record for billing and deleting our row would otherwise leave
    the customer being charged. Cancel in Stripe first; see ROADMAP.
    """
    if not verify_password(password, user.hashed_password):
        raise InvalidPasswordError()

    user_id = user.id

    # Tokens and usage events have no ORM relationship on User, and SQLite
    # doesn't enforce foreign keys by default, so leaving these to ON DELETE
    # CASCADE would behave differently under test than in production. API
    # keys do have a relationship (with delete-orphan), so they go with the
    # user row below — but they are the credential most worth being sure
    # about, hence the test in tests/test_account.py.
    db.query(OneTimeToken).filter(OneTimeToken.user_id == user_id).delete(
        synchronize_session=False
    )
    db.query(UsageEvent).filter(UsageEvent.user_id == user_id).delete(
        synchronize_session=False
    )
    db.query(AuditLog).filter(AuditLog.user_id == user_id).delete(
        synchronize_session=False
    )
    # Rows where they were the *actor* are kept and disowned, matching the
    # SET NULL the schema declares. Deleting an admin must not erase the
    # record of what that admin did to other people's accounts — the action
    # stays, the attribution goes — and doing it explicitly means the
    # behaviour is the same under SQLite, which does not enforce foreign
    # keys, as it is in production.
    db.query(AuditLog).filter(AuditLog.actor_user_id == user_id).update(
        {AuditLog.actor_user_id: None}, synchronize_session=False
    )

    # The subscription goes via the relationship's delete-orphan cascade;
    # deleting it here too would issue a second DELETE for a gone row.
    db.delete(user)
    db.commit()
    logger.info("account deleted", extra={"user_id": str(user_id)})


def has_active_subscription(user: User) -> bool:
    subscription: Subscription | None = user.subscription
    return subscription is not None and subscription.status in ("active", "trialing")
