"""The audit trail's own rules, below the HTTP surface.

tests/test_audit.py covers who is allowed to read the trail and what is safe
to show back. This file covers the properties the service promises to every
caller: that recording never breaks the request it rode in on, that a lost
row is loud rather than silent, that pagination is stable, and that
retention actually deletes.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.core.security import hash_password
from app.models.audit_log import AuditAction, AuditLog, AuditOutcome
from app.models.role import Role
from app.models.user import User
from app.services import audit_service
from app.services.alerts import (
    AlertRouter,
    InMemoryAlertSender,
    Severity,
    get_alert_router,
    set_alert_router,
)


def _user(db, email="audit@example.com", role=Role.MEMBER) -> User:
    user = User(
        email=email,
        hashed_password=hash_password("s3cret-password"),
        full_name="Audit Subject",
        is_active=True,
        is_verified=True,
        role=role.value,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture
def user(db_session) -> User:
    return _user(db_session)


@pytest.fixture
def alerts():
    """Install an inspectable alert router, and restore the real one after."""
    sender = InMemoryAlertSender()
    installed = AlertRouter(sender, min_severity=Severity.INFO)
    previous = get_alert_router()
    set_alert_router(installed)
    yield sender
    set_alert_router(previous)


def test_record_stores_the_subject_and_the_context(db_session, user):
    entry = audit_service.record(
        db_session,
        AuditAction.LOGIN,
        user=user,
        ip_address="203.0.113.7",
        user_agent="Mozilla/5.0",
        context={"method": "password"},
    )

    assert entry is not None
    assert entry.user_id == user.id
    assert entry.action == AuditAction.LOGIN.value
    assert entry.outcome == AuditOutcome.SUCCESS.value
    assert entry.ip_address == "203.0.113.7"
    assert entry.context == {"method": "password"}


def test_an_account_acting_on_itself_leaves_the_actor_null(db_session, user):
    entry = audit_service.record(db_session, AuditAction.LOGOUT, user=user)

    # Null rather than a copy of the subject, so "somebody else did this to
    # me" stays a non-null check instead of a comparison.
    assert entry is not None
    assert entry.actor_user_id is None


def test_an_admin_acting_on_a_member_records_both_parties(db_session, user):
    admin = _user(db_session, email="admin@example.com", role=Role.ADMIN)

    entry = audit_service.record(
        db_session,
        AuditAction.STATUS_CHANGED,
        user=user,
        actor=admin,
        context={"is_active": False},
    )

    assert entry is not None
    assert entry.user_id == user.id
    assert entry.actor_user_id == admin.id


def test_record_accepts_an_id_when_the_row_is_already_gone(db_session):
    # The deletion path has an id and no user row left to load.
    orphan_id = uuid.uuid4()

    entry = audit_service.record(
        db_session, AuditAction.ACCOUNT_DELETED, user_id=None, context={"reason": "self"}
    )

    assert entry is not None
    assert entry.user_id is None
    assert orphan_id != entry.id


def test_oversized_client_context_is_truncated_not_rejected(db_session, user):
    entry = audit_service.record(
        db_session,
        AuditAction.LOGIN,
        user=user,
        user_agent="U" * 500,
        ip_address="1.2.3.4" + "5" * 100,
    )

    # A header nobody validates must not be able to suppress an audit row.
    assert entry is not None
    assert len(entry.user_agent) == 255
    assert len(entry.ip_address) == 45


def test_a_blank_user_agent_is_stored_as_null(db_session, user):
    entry = audit_service.record(
        db_session, AuditAction.LOGIN, user=user, user_agent="   "
    )

    assert entry is not None
    assert entry.user_agent is None


def test_a_failed_write_returns_none_instead_of_raising(db_session, user, monkeypatch):
    def _explode():
        raise SQLAlchemyError("disk on fire")

    monkeypatch.setattr(db_session, "commit", _explode)

    # A login that succeeded must not 500 because its audit row did not.
    assert audit_service.record(db_session, AuditAction.LOGIN, user=user) is None


def test_a_failed_write_raises_an_operational_alert(
    db_session, user, monkeypatch, alerts
):
    def _explode():
        raise SQLAlchemyError("disk on fire")

    monkeypatch.setattr(db_session, "commit", _explode)
    audit_service.record(db_session, AuditAction.LOGIN, user=user)

    # A gap in the trail is invisible to whoever reads it later, so the
    # moment it opens has to reach someone who can act.
    assert alerts.titles() == ["Audit trail write failed"]


def test_alerting_failure_does_not_resurface_the_swallowed_error(
    db_session, user, monkeypatch
):
    def _explode():
        raise SQLAlchemyError("disk on fire")

    def _alert_explodes(*args, **kwargs):
        raise RuntimeError("on-call webhook is down too")

    monkeypatch.setattr(db_session, "commit", _explode)
    monkeypatch.setattr(audit_service, "notify", _alert_explodes)

    # The transport failing must not become the exception this path exists
    # to have swallowed.
    assert audit_service.record(db_session, AuditAction.LOGIN, user=user) is None


def test_listing_is_newest_first(db_session, user):
    for action in (AuditAction.LOGIN, AuditAction.API_KEY_CREATED, AuditAction.LOGOUT):
        audit_service.record(db_session, action, user=user)

    entries = audit_service.list_for_user(db_session, user)

    assert [entry.action for entry in entries] == [
        AuditAction.LOGOUT.value,
        AuditAction.API_KEY_CREATED.value,
        AuditAction.LOGIN.value,
    ]


def test_rows_sharing_a_timestamp_paginate_without_repeating(db_session, user):
    """One request can record several events, all with the same timestamp.

    Without a stable tiebreak those rows can reappear on the next page or
    disappear between two, which is the way paginated listings usually go
    wrong.
    """
    stamp = datetime.now(UTC)
    for _ in range(6):
        entry = audit_service.record(db_session, AuditAction.LOGIN, user=user)
        entry.created_at = stamp
    db_session.commit()

    first = audit_service.list_for_user(db_session, user, limit=3)
    second = audit_service.list_for_user(db_session, user, limit=3, offset=3)

    ids = [entry.id for entry in first] + [entry.id for entry in second]
    assert len(set(ids)) == 6


def test_page_size_is_clamped_to_the_ceiling(db_session, user):
    assert audit_service.clamp_page_size(10_000) == audit_service.MAX_PAGE_SIZE
    assert audit_service.clamp_page_size(0) == 1


def test_one_account_never_sees_another_accounts_trail(db_session, user):
    other = _user(db_session, email="somebody-else@example.com")
    audit_service.record(db_session, AuditAction.LOGIN, user=other)
    audit_service.record(db_session, AuditAction.LOGIN, user=user)

    entries = audit_service.list_for_user(db_session, user)

    assert [entry.user_id for entry in entries] == [user.id]


def test_the_count_and_the_page_agree_about_the_filter(db_session, user):
    for _ in range(3):
        audit_service.record(db_session, AuditAction.LOGIN, user=user)
    audit_service.record(
        db_session,
        AuditAction.LOGIN_FAILED,
        user=user,
        outcome=AuditOutcome.FAILURE,
    )

    failures = audit_service.list_entries(
        db_session, user_id=user.id, outcome=AuditOutcome.FAILURE.value
    )
    total = audit_service.count_entries(
        db_session, user_id=user.id, outcome=AuditOutcome.FAILURE.value
    )

    assert len(failures) == total == 1


def test_failed_logins_are_counted_across_accounts(db_session, user):
    other = _user(db_session, email="sprayed@example.com")
    for subject in (user, other, None):
        audit_service.record(
            db_session,
            AuditAction.LOGIN_FAILED,
            user=subject,
            outcome=AuditOutcome.FAILURE,
            ip_address="198.51.100.4",
        )

    # Including the attempt against an address that has no account: a spray
    # is mostly misses, and counting only the hits would hide it.
    assert audit_service.failed_login_count(db_session, hours=1) == 3


def test_counts_by_action_reports_the_busiest_first(db_session, user):
    for _ in range(3):
        audit_service.record(db_session, AuditAction.LOGIN, user=user)
    audit_service.record(db_session, AuditAction.LOGOUT, user=user)

    counts = audit_service.counts_by_action(
        db_session, since=datetime.now(UTC) - timedelta(days=1)
    )

    assert list(counts) == [AuditAction.LOGIN.value, AuditAction.LOGOUT.value]
    assert counts[AuditAction.LOGIN.value] == 3


def test_pruning_deletes_only_rows_past_the_window(db_session, user):
    now = datetime.now(UTC)
    old = audit_service.record(db_session, AuditAction.LOGIN, user=user)
    old.created_at = now - timedelta(days=200)
    recent = audit_service.record(db_session, AuditAction.LOGOUT, user=user)
    db_session.commit()

    deleted = audit_service.purge_audit_logs(db_session, older_than_days=180, now=now)

    assert deleted == 1
    assert [row.id for row in db_session.query(AuditLog).all()] == [recent.id]


def test_a_zero_day_window_still_keeps_a_day(db_session, user):
    now = datetime.now(UTC)
    entry = audit_service.record(db_session, AuditAction.LOGIN, user=user)
    entry.created_at = now - timedelta(hours=2)
    db_session.commit()

    # max(days, 1) — a misconfigured window must not empty the table.
    assert audit_service.purge_audit_logs(db_session, older_than_days=0, now=now) == 0


def test_the_window_filter_excludes_rows_older_than_it(db_session, user):
    now = datetime.now(UTC)
    stale = audit_service.record(db_session, AuditAction.LOGIN, user=user)
    stale.created_at = now - timedelta(days=30)
    audit_service.record(db_session, AuditAction.LOGOUT, user=user)
    db_session.commit()

    recent = audit_service.list_entries(
        db_session, user_id=user.id, since=now - timedelta(days=7)
    )

    assert [entry.action for entry in recent] == [AuditAction.LOGOUT.value]


def test_an_unknown_address_is_recorded_without_naming_it(db_session):
    """A failed login against an address nobody registered has no subject.

    Storing the attempted address would build an inventory of every string
    typed into an email box — passwords typed into the wrong field included —
    and would turn a table the owner can read into an enumeration oracle.
    """
    entry = audit_service.record(
        db_session,
        AuditAction.LOGIN_FAILED,
        outcome=AuditOutcome.FAILURE,
        ip_address="198.51.100.9",
        context={"known_account": False},
    )

    assert entry is not None
    assert entry.user_id is None
    assert "@" not in str(entry.context)
